"""When each file of a team knowledge base last changed in its repository.

An archive snapshot's files carry the time they were unpacked on this computer, and a
clone's files the time of the checkout or pull. A reader asking when a note changed means
the last commit that touched it. For a GitHub archive source Carry reads that from GitHub
at sync time and keeps it next to the snapshot; for a team clone it comes from the
clone's own log. Personal notes have no such date: their files are the original.

Nothing here raises: a source whose history cannot be read simply has no commit dates.
"""
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from .errors import CarryError

BATCH = 100  # paths per GraphQL query
_snapshots = {}
_clones = {}


def sidecar(root):
    """Commit dates of a snapshot sit beside it, so the snapshot stays exactly the repository."""
    root = Path(root)
    return root.parent / (root.name + ".commits.json")


def _local(value):
    """An ISO commit time in this computer's time zone, or None."""
    try:
        return datetime.fromisoformat(str(value)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def epoch(value):
    return datetime.fromisoformat(value).timestamp()


def load(root):
    """{path: ISO time} recorded for a snapshot, or {} when none was recorded."""
    path = sidecar(root)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {}
    cached = _snapshots.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        files = json.loads(path.read_text(encoding="utf-8")).get("files") or {}
    except (OSError, ValueError, AttributeError):
        files = {}
    files = {k: t for k, t in ((k, _local(v)) for k, v in files.items() if isinstance(k, str)) if t}
    _snapshots[path] = (stamp, files)
    return files


def _same(a, b):
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def record_snapshot(repository, commit, folder, root, previous_root=None):
    """Read each file's last commit at `commit` from GitHub and keep it beside the snapshot.

    A file whose bytes match the previous snapshot keeps its recorded date without a request,
    so a sync costs queries only for what changed. Returns how many files got a date, or None
    when GitHub could not be asked; the sync itself never fails for this."""
    from . import github
    root = Path(root)
    paths = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md") if p.is_file())
    old = load(previous_root) if previous_root else {}
    dates, ask = {}, []
    for rel in paths:
        if rel in old and _same(root / rel, Path(previous_root) / rel):
            dates[rel] = old[rel]
        else:
            ask.append(rel)
    try:
        for start in range(0, len(ask), BATCH):
            batch = ask[start:start + BATCH]
            found = github.last_commits(repository, commit, ["/".join(filter(None, (folder, rel))) for rel in batch])
            dates.update({rel: when for rel, when in zip(batch, found) if when})
    except CarryError:
        return None
    target = sidecar(root)
    temp = target.with_name(target.name + ".tmp")
    try:
        temp.write_text(json.dumps(dict(commit=commit, files=dates), ensure_ascii=False), encoding="utf-8")
        os.replace(temp, target)
    except OSError:
        return None
    return len(dates)


def _git(root, *args):
    """Raw output: NUL-separated listings must keep their leading spaces."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "-c", "core.quotePath=false", *args],
                              capture_output=True, text=True, encoding="utf-8", timeout=60,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _clone_dates(root):
    head = _git(root, "rev-parse", "HEAD")
    if not head:
        return {}
    cached = _clones.get(root)
    if cached and cached[0] == head:
        return cached[1]
    log = _git(root, "log", "--format=%x01%cI", "--name-only", "-z", "HEAD")
    if log is None:
        return {}
    dates, when = {}, None
    for token in log.split("\0"):
        if token.startswith("\x01"):
            when = _local(token[1:].strip())
            continue
        name = token[1:] if token.startswith("\n") else token
        if name and when and name not in dates:  # newest commit first
            dates[name] = when
    _clones[root] = (head, dates)
    return dates


def for_source(source):
    """{path: ISO time of the last commit to the file} for a GitHub source or a team clone."""
    if source.github:
        return load(source.root)
    from .team import is_clone
    if is_clone(source):
        return _clone_dates(Path(source.root).expanduser().resolve())
    return {}


def changed_locally(source):
    """Files of a team clone with uncommitted edits: their own file time is the newer truth."""
    from .team import is_clone
    if source.github or not is_clone(source):
        return set()
    status = _git(Path(source.root).expanduser().resolve(), "status", "--porcelain", "-z", "--untracked-files=no")
    if not status:
        return set()
    changed, tokens = set(), status.split("\0")
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) > 3:
            changed.add(entry[3:])
            if "R" in entry[:2] or "C" in entry[:2]:
                i += 1  # the original path of a rename or copy
    return changed
