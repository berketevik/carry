"""Sidecar identity map for imported Markdown.

Carry does not edit a user's files to insert IDs. Instead it remembers which
record ID belongs to which (source, path, content). A rename with identical
content carries its ID over; when two candidates match equally well the move is
recorded for reconciliation rather than resolved by guessing.
"""
import json
import os
from pathlib import Path

from .records import imported_record_id

VERSION = 1


def _key(source_id, relative_path):
    return f"{source_id}\t{relative_path}"


def load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": VERSION, "entries": {}, "reconcile": []}
    if data.get("version") != VERSION:
        return {"version": VERSION, "entries": {}, "reconcile": []}
    data.setdefault("entries", {})
    data.setdefault("reconcile", [])
    return data


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def lookup(data, source_id, relative_path):
    entry = data["entries"].get(_key(source_id, relative_path))
    return entry.get("record_id") if entry else None


def reconcile(data, snapshot):
    """Update the map against a fresh corpus snapshot.

    `snapshot` maps (source_id, relative_path) -> content digest. Returns the
    map; ambiguous moves land in `reconcile` and get fresh IDs, so nothing is
    silently attributed to the wrong record.
    """
    entries = data["entries"]
    present = {_key(s, p): digest for (s, p), digest in snapshot.items()}
    gone = {k: v for k, v in entries.items() if k not in present}
    fresh = {k: d for k, d in present.items() if k not in entries}

    by_digest = {}
    for key, entry in gone.items():
        by_digest.setdefault(entry.get("digest"), []).append(key)

    for key, digest in sorted(fresh.items()):
        candidates = by_digest.get(digest, [])
        arrivals = [k for k, d in fresh.items() if d == digest]
        if len(candidates) == 1 and len(arrivals) == 1:
            moved = entries.pop(candidates[0])
            entries[key] = {"record_id": moved["record_id"], "digest": digest}
            by_digest[digest] = []
            continue
        if len(candidates) > 1 or (candidates and len(arrivals) > 1):
            data["reconcile"].append({"path": key, "candidates": sorted(candidates),
                                      "reason": "ambiguous_move"})
        source_id, relative_path = key.split("\t", 1)
        entries[key] = {"record_id": imported_record_id(source_id, relative_path),
                        "digest": digest}

    for key in list(entries):
        if key in present:
            entries[key]["digest"] = present[key]
        elif key in gone:
            entries.pop(key, None)
    return data
