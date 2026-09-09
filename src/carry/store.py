"""Markdown record storage.

Markdown files are canonical: a record is a file with identity in its
frontmatter, and history is kept as additional files rather than by overwriting.
Writes are idempotent per event and revision-checked, so a concurrent change
produces a conflict instead of a silent overwrite.
"""
import datetime as dt
import json
import os
import re
from pathlib import Path

from .errors import RevisionConflict, SourceError
from .mask import mask
from .markdown import parse_frontmatter
from .paths import resolve_within
from .records import STATES, new_record_id

EVENT_LEDGER = "events.json"


def _slug(text, limit=48):
    text = re.sub(r"[^\w\s-]", "", (text or "").lower(), flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:limit] or "record"


def _load_ledger(workspace):
    try:
        data = json.loads((Path(workspace.state_dir) / EVENT_LEDGER).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_ledger(workspace, data):
    path = Path(workspace.state_dir) / EVENT_LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _frontmatter_block(fields):
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        elif isinstance(value, str) and (":" in value or value.startswith(("[", "{"))):
            lines.append(f'{key}: "{value}"')
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def record_path(workspace, source, record_id, title, revision):
    directory = resolve_within(Path(source.root).expanduser().resolve(), source.records_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{dt.date.today().isoformat()}-{_slug(title)}-{record_id.split('_')[-1][:8]}"
    if revision > 1:
        stem += f"-r{revision}"
    path = directory / (stem + ".md")
    counter = 2
    while path.exists():
        path = directory / f"{stem}-{counter}.md"
        counter += 1
    return path


def _assert_correctable(frontmatter):
    """A record that already has a replacement is history, not a target.

    Without this check a second correction against the same revision number
    would silently fork the history instead of being refused.
    """
    replacement = str(frontmatter.get("carry_superseded_by") or "")
    if replacement:
        raise RevisionConflict("record_superseded_by_" + replacement)
    if frontmatter.get("carry_current", True) is False:
        raise RevisionConflict("record_not_current")


def find_record(workspace, record_id):
    """Locate the current file for a record ID by reading frontmatter.

    Deliberately independent of the derived index: a write must not depend on
    index freshness.
    """
    hits = []
    for source in workspace.sources:
        root = Path(source.root).expanduser().resolve()
        directory = root / source.records_dir
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            if path.is_symlink():
                continue
            frontmatter, _ = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            if str(frontmatter.get("carry_record") or "") == record_id:
                hits.append((path, frontmatter, source))
    if not hits:
        raise SourceError("record_not_found")
    current = [h for h in hits if h[1].get("carry_current", True)]
    chosen = max(current or hits, key=lambda h: int(h[1].get("carry_revision", 1) or 1))
    return chosen


def write_record(workspace, title, content, source_refs=(), event_id="", state="draft",
                 author="", surface="", source_id=None, summary=""):
    """Create a record file. Returns a dict describing what was written.

    Replaying the same `event_id` returns the existing record instead of writing
    a second one. A different event with identical wording is a distinct record:
    identity comes from the event, not from the text.
    """
    if state not in STATES:
        raise ValueError("invalid_state")
    source = workspace.writable_source(source_id)
    ledger = _load_ledger(workspace)
    if event_id and event_id in ledger:
        existing = ledger[event_id]
        return dict(existing, duplicate=True)

    masked_content, categories = mask(content or "")
    masked_title, title_categories = mask(title or "Record")
    record_id = new_record_id()
    path = record_path(workspace, source, record_id, masked_title, 1)
    now = dt.datetime.now().isoformat(timespec="seconds")
    frontmatter = {
        "carry_record": record_id,
        "carry_revision": 1,
        "carry_state": state,
        "carry_current": True,
        "carry_event": event_id,
        "carry_author": author,
        "carry_surface": surface,
        "type": "record",
        "summary": summary or masked_title,
        "created": now,
        "updated": now,
        "sources": list(source_refs),
    }
    body = f"{_frontmatter_block(frontmatter)}\n\n# {masked_title}\n\n{masked_content.strip()}\n"
    path.write_text(body, encoding="utf-8")
    result = dict(record_id=record_id, revision=1, state=state,
                  source_id=source.source_id,
                  path=str(path.relative_to(Path(source.root).expanduser().resolve())),
                  masked=sorted(set(categories + title_categories)), duplicate=False)
    if event_id:
        ledger[event_id] = {k: v for k, v in result.items() if k != "duplicate"}
        _save_ledger(workspace, ledger)
    return result


def set_state(workspace, record_id, state, expected_revision):
    """Change a record's state under a revision precondition."""
    if state not in STATES:
        raise ValueError("invalid_state")
    path, frontmatter, source = find_record(workspace, record_id)
    _assert_correctable(frontmatter)
    revision = int(frontmatter.get("carry_revision", 1) or 1)
    if int(expected_revision) != revision:
        raise RevisionConflict(f"expected_revision_{expected_revision}_found_{revision}")
    text = path.read_text(encoding="utf-8")
    updated = re.sub(r"(?m)^carry_state:.*$", f"carry_state: {state}", text, count=1)
    if updated == text:
        raise SourceError("record_frontmatter_unwritable")
    path.write_text(updated, encoding="utf-8")
    return dict(record_id=record_id, revision=revision, state=state,
                source_id=source.source_id,
                path=str(path.relative_to(Path(source.root).expanduser().resolve())))


def supersede(workspace, record_id, content, expected_revision, title=None, event_id="",
              author="", surface=""):
    """Publish a corrected revision. History is retained: the previous file stays
    on disk, marked not current and pointing at its replacement."""
    path, frontmatter, source = find_record(workspace, record_id)
    _assert_correctable(frontmatter)
    revision = int(frontmatter.get("carry_revision", 1) or 1)
    if int(expected_revision) != revision:
        raise RevisionConflict(f"expected_revision_{expected_revision}_found_{revision}")

    masked_content, categories = mask(content or "")
    heading = title or str(frontmatter.get("summary") or path.stem)
    new_id = new_record_id()
    new_path = record_path(workspace, source, new_id, heading, revision + 1)
    now = dt.datetime.now().isoformat(timespec="seconds")
    new_frontmatter = {
        "carry_record": new_id,
        "carry_revision": revision + 1,
        "carry_state": "accepted",
        "carry_current": True,
        "carry_event": event_id,
        "carry_author": author,
        "carry_surface": surface,
        "carry_supersedes": record_id,
        "type": "record",
        "summary": heading,
        "created": now,
        "updated": now,
        "sources": frontmatter.get("sources") or [],
    }
    new_path.write_text(
        f"{_frontmatter_block(new_frontmatter)}\n\n# {heading}\n\n{masked_content.strip()}\n",
        encoding="utf-8")

    old = path.read_text(encoding="utf-8")
    old = re.sub(r"(?m)^carry_current:.*$", "carry_current: false", old, count=1)
    old = re.sub(r"(?m)^carry_state:.*$", "carry_state: superseded", old, count=1)
    if "carry_superseded_by:" in old:
        old = re.sub(r"(?m)^carry_superseded_by:.*$", f"carry_superseded_by: {new_id}", old, count=1)
    else:
        old = old.replace("carry_current: false", f"carry_current: false\ncarry_superseded_by: {new_id}", 1)
    path.write_text(old, encoding="utf-8")
    return dict(record_id=new_id, revision=revision + 1, state="accepted",
                supersedes=record_id, source_id=source.source_id,
                path=str(new_path.relative_to(Path(source.root).expanduser().resolve())),
                masked=sorted(set(categories)))
