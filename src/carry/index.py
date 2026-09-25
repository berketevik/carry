"""Derived index: build, publish, health.

The index is disposable. A build writes to a side file and publishes with a
single atomic rename, so an interrupted or failed build always leaves the
previous usable index in place. Nothing in this module writes inside a source
root.
"""
import fcntl
import hashlib
import json
import os
import sqlite3
import struct
import tempfile
import time
from contextlib import closing
from pathlib import Path

from . import sidecar as sidecar_map
from .embedding import build_provider
from .errors import ProviderUnavailable
from .markdown import chunk_body, parse_document
from .paths import walk_markdown
from .records import read_record_meta

SCHEMA_VERSION = 1


# --- low level helpers ---
def pack(vector):
    return struct.pack(f"{len(vector)}f", *vector)


def unpack(blob, dim):
    return struct.unpack(f"{dim}f", blob)


def open_readonly(path):
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)


def db_is_usable(path):
    """True when the published index can serve a query.

    Schema presence is the test, not row count: an empty workspace is a valid
    state that must answer "no evidence", not "index unavailable". Only complete
    builds are ever published to this path.
    """
    if not os.path.isfile(path):
        return False
    try:
        with closing(open_readonly(path)) as con:
            con.execute(
                "SELECT id,source_id,path,title,heading,text,dim,vec FROM chunks LIMIT 1"
            ).fetchall()
            con.execute("SELECT rowid,text FROM chunks_fts LIMIT 0").fetchall()
            con.execute("SELECT key,value FROM meta LIMIT 0").fetchall()
            con.execute("SELECT source_id,path,digest,record_id FROM files LIMIT 0").fetchall()
            return True
    except (OSError, sqlite3.Error):
        return False


def init_db(con):
    con.executescript("""
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS chunks_fts;
        DROP TABLE IF EXISTS files;
        DROP TABLE IF EXISTS meta;
        CREATE TABLE files(
            source_id TEXT, path TEXT, digest TEXT, metadata TEXT,
            record_id TEXT, revision INTEGER, state TEXT, current INTEGER,
            event_id TEXT, origin TEXT, superseded_by TEXT,
            PRIMARY KEY (source_id, path));
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE chunks(
            id INTEGER PRIMARY KEY,
            source_id TEXT, path TEXT, title TEXT, heading TEXT,
            text TEXT, dim INTEGER, vec BLOB);
        CREATE INDEX chunks_file ON chunks(source_id, path);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='id');
    """)


def fingerprint(workspace, provider):
    """Everything that changes vector meaning. A mismatch forces a full rebuild."""
    retrieval = workspace.retrieval
    return json.dumps([SCHEMA_VERSION, provider.fingerprint,
                       retrieval.chunk_chars, retrieval.chunk_overlap])


def corpus_snapshot(workspace):
    """Content digests for every indexable file: catches edits, deletions and
    preserved mtimes that a timestamp scan would miss."""
    snapshot = {}
    for source in workspace.sources:
        root = Path(source.root).expanduser().resolve()
        if not root.is_dir():
            continue
        for relative, path in walk_markdown(root, extensions=tuple(source.extensions), exclude=source.exclude):
            try:
                data = path.read_bytes()
            except OSError:
                continue
            snapshot[(source.source_id, relative)] = hashlib.sha256(data).hexdigest()
    return snapshot


def read_manifest(con):
    try:
        config = dict(con.execute("SELECT key,value FROM meta"))
        files = {(s, p): d for s, p, d in con.execute("SELECT source_id,path,digest FROM files")}
        return config, files
    except sqlite3.Error:
        return {}, {}


def write_status(workspace, status, **fields):
    """Diagnostics only: no prompts, passages, exception messages or secrets."""
    path = Path(workspace.status_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(status=status, at=time.time(), **fields)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".carry-status-")
    try:
        with os.fdopen(handle, "w") as out:
            json.dump(payload, out)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return payload


def last_build_status(workspace):
    try:
        return json.loads(Path(workspace.status_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "unknown"}


def health(workspace, con=None):
    """Freshness of the published index against the current corpus."""
    own = con is None
    provider = None
    try:
        provider = build_provider(workspace.embedding)
    except ProviderUnavailable:
        provider = None
    try:
        if own:
            if not os.path.isfile(workspace.db_path):
                raise OSError("missing_index")
            con = open_readonly(workspace.db_path)
        config, manifest = read_manifest(con)
        snapshot = corpus_snapshot(workspace)
        expected = fingerprint(workspace, provider) if provider else None
        compatible = bool(expected) and config.get("fingerprint") == expected
        changed = sum(manifest.get(key) != digest for key, digest in snapshot.items())
        removed = len(manifest.keys() - snapshot.keys())
        state = "fresh" if compatible and not changed and not removed else "stale"
        return dict(state=state, compatible=compatible, changed_files=changed,
                    removed_files=removed, indexed_files=len(manifest),
                    corpus_files=len(snapshot), built_at=config.get("built_at"),
                    last_build=last_build_status(workspace))
    except (OSError, sqlite3.Error):
        return dict(state="unavailable", compatible=False, indexed_files=0,
                    corpus_files=len(corpus_snapshot(workspace)),
                    last_build=last_build_status(workspace))
    finally:
        if own and con is not None:
            con.close()


def build(workspace):
    """Rebuild the derived index incrementally and publish it atomically.

    Returns a status dict. Failure is reported, never raised, so a caller that
    is serving queries can keep serving the previous index.
    """
    workspace.validate()
    Path(workspace.state_dir).mkdir(parents=True, exist_ok=True)
    # Kernel-owned advisory lock, scoped to this workspace and released on exit.
    # The lock file is kept: unlinking it would create a second lock domain.
    with open(workspace.lock_path, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "reindexing"}
        return _build_locked(workspace)


def _build_locked(workspace):
    db_path = str(workspace.db_path)
    building = db_path + ".building"
    stage = "provider"
    con = None
    try:
        provider = build_provider(workspace.embedding)
        write_status(workspace, "building", pid=os.getpid())
        stage = "snapshot"
        snapshot = corpus_snapshot(workspace)
        if os.path.exists(building):
            os.unlink(building)
        con = sqlite3.connect(building)
        stage = "prepare"
        expected = fingerprint(workspace, provider)
        previous = {}
        reusable = False
        if db_is_usable(db_path):
            old = open_readonly(db_path)
            try:
                config, previous = read_manifest(old)
                reusable = config.get("fingerprint") == expected
                if reusable:
                    old.backup(con)
            finally:
                old.close()
        if not reusable:
            init_db(con)
            previous = {}
        sidecar = sidecar_map.reconcile(sidecar_map.load(workspace.sidecar_path), snapshot)

        changed = [key for key, digest in snapshot.items() if previous.get(key) != digest]
        removed = previous.keys() - snapshot.keys()
        embedded = reused = 0
        by_id = {s.source_id: s for s in workspace.sources}
        for source_id, relative in [*sorted(removed), *sorted(changed)]:
            # Reuse unchanged windows of an appended file instead of re-embedding it.
            cache = {text: (dim, vec) for text, dim, vec in con.execute(
                "SELECT text,dim,vec FROM chunks WHERE source_id=? AND path=?",
                (source_id, relative))}
            con.execute("DELETE FROM chunks WHERE source_id=? AND path=?", (source_id, relative))
            con.execute("DELETE FROM files WHERE source_id=? AND path=?", (source_id, relative))
            if (source_id, relative) not in snapshot:
                continue
            path = Path(by_id[source_id].root).expanduser().resolve() / relative
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != snapshot[(source_id, relative)]:
                raise RuntimeError("corpus_changed_during_build")
            stage = "parse"
            title, frontmatter, body = parse_document(data.decode("utf-8", "replace"), path)
            record = read_record_meta(frontmatter, source_id, relative,
                                      sidecar_map.lookup(sidecar, source_id, relative))
            for heading, text in chunk_body(title, frontmatter.get("summary", ""), body,
                                            chunk_chars=workspace.retrieval.chunk_chars,
                                            overlap=workspace.retrieval.chunk_overlap):
                if text in cache:
                    dim, blob = cache[text]
                    reused += 1
                else:
                    stage = "embed"
                    vector = provider.embed_document(text)
                    dim, blob = len(vector), pack(vector)
                    embedded += 1
                    if embedded % 250 == 0:
                        write_status(workspace, "building", pid=os.getpid(),
                                     embedded_chunks=embedded, changed_files=len(changed))
                con.execute(
                    "INSERT INTO chunks(source_id,path,title,heading,text,dim,vec)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (source_id, relative, title, heading, text, dim, blob))
            con.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (source_id, relative, snapshot[(source_id, relative)],
                         json.dumps(frontmatter, ensure_ascii=False, default=str),
                         *record.to_row()))
        stage = "validate"
        dims = con.execute("SELECT DISTINCT dim FROM chunks").fetchall()
        if len(dims) > 1:
            raise ValueError("mixed_embedding_dimensions")
        con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        con.execute("INSERT OR REPLACE INTO meta VALUES('fingerprint',?)", (expected,))
        con.execute("INSERT OR REPLACE INTO meta VALUES('built_at',?)", (str(time.time()),))
        con.execute("INSERT OR REPLACE INTO meta VALUES('provider',?)", (provider.name,))
        con.execute("INSERT OR REPLACE INTO meta VALUES('semantic',?)",
                    (str(int(provider.semantic)),))
        # A corpus edited mid-build would publish a mixed snapshot; refuse instead.
        if corpus_snapshot(workspace) != snapshot:
            raise RuntimeError("corpus_changed_during_build")
        con.commit()
        chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
        con.close()
        con = None
        stage = "publish"
        os.replace(building, db_path)
        sidecar_map.save(workspace.sidecar_path, sidecar)
        return write_status(workspace, "built", changed_files=len(changed),
                            removed_files=len(removed), embedded_chunks=embedded,
                            reused_chunks=reused, files=len(snapshot), chunks=chunks,
                            provider=provider.name, semantic=provider.semantic,
                            needs_reconciliation=len(sidecar.get("reconcile", [])))
    except Exception as exc:
        return write_status(workspace, "failed", stage=stage, error_type=type(exc).__name__)
    finally:
        if con is not None:
            con.close()
        if os.path.exists(building):
            os.unlink(building)

def maybe_refresh(workspace, spawn=True, backoff=60.0):
    """Opt-in background refresh.

    Serving a query never blocks on a rebuild. A failed build backs off instead
    of restarting on every call, and the caller still sees the stale state.
    """
    import subprocess
    import sys as _sys
    if not db_is_usable(workspace.db_path):
        return build(workspace)["status"] if not spawn else _spawn(workspace, subprocess, _sys)
    if health(workspace)["state"] == "fresh":
        return "fresh"
    last = last_build_status(workspace)
    if last.get("status") == "failed" and time.time() - float(last.get("at", 0)) < backoff:
        return "retry_pending"
    if not spawn:
        return build(workspace)["status"]
    return _spawn(workspace, subprocess, _sys)


def _spawn(workspace, subprocess, _sys):
    subprocess.Popen([_sys.executable, "-m", "carry.cli", "--workspace",
                      str(workspace.state_dir), "index"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return "spawned"
