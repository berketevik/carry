"""Open Knowledge Format v0.2: the frontmatter contract Carry reads and writes.

Spec: https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md

Readers accept the keys notes written before OKF may still carry (`summary`, `draft: true`,
`valid_until`, an effort's intensity in `status`, plain strings in `sources`); writers emit OKF
only. Consumer rules follow spec §5 and §11: a bare `verified` mapping is a one-element list,
trust tiers and staleness come only from the spec's fields, and no note is rejected for a
missing or unknown field.
"""
import datetime as dt
import json
from pathlib import Path
import re

from .errors import CarryError

OKF_VERSION = "0.2"
LIFECYCLE = ("draft", "stable", "deprecated")
RESERVED = ("index.md", "log.md")
INTENSITY = ("on", "ongoing", "simmering", "sleeping")
LEGACY_KEYS = ("summary", "draft", "valid_until")
OWNER_NAME = "owner.json"
DEFAULT_OWNER = "human:owner"
OWNER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
WIKILINK = re.compile(r"^\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]$")
TOP_KEY = re.compile(r"^([A-Za-z0-9_.\-]+)\s*:")


# -- reading -------------------------------------------------------------------

def json_default(value):
    """JSON for frontmatter values PyYAML parses into objects: an unquoted timestamp is a
    datetime, a date is a date. Both become ISO 8601 text."""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def plain(value):
    """A frontmatter value with every date and time as ISO 8601 text, safe to JSON-encode."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return value


def _text(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def description(front):
    """The note's one-sentence description: `description`, else the legacy `summary`."""
    front = front or {}
    for key in ("description", "summary"):
        text = _text(front.get(key))
        if text:
            return text
    return ""


def intensity(front):
    """An effort's intensity: `intensity`, else a legacy intensity value in `status`."""
    front = front or {}
    value = front.get("intensity")
    if value is True:  # YAML 1.1 reads an unquoted `on` as true
        return "on"
    if _text(value):
        return _text(value)
    raw = front.get("status")
    if raw is True or (isinstance(raw, str) and raw.strip().lower() in INTENSITY):
        return "on" if raw is True else raw.strip().lower()
    return ""


def _flag(value):
    return value is True or (isinstance(value, str) and value.strip().lower() in ("true", "yes"))


def lifecycle(front):
    """(status, known). status is draft, stable or deprecated; known is False when the note
    carries a `status` value outside OKF's set, which is then read as stable (§5.4, §11)."""
    front = front or {}
    raw = front.get("status")
    if isinstance(raw, str) and raw.strip().lower() in LIFECYCLE:
        return raw.strip().lower(), True
    if _flag(front.get("draft")):
        return "draft", True
    if raw is None or raw == "" or intensity(dict(status=raw)):
        return "stable", True
    return "stable", False


def is_draft(front):
    return lifecycle(front)[0] == "draft"


def is_deprecated(front):
    return lifecycle(front)[0] == "deprecated"


def _event(value):
    if not isinstance(value, dict):
        return None
    by = _text(value.get("by"))
    return dict(by=by, at=_text(value.get("at"))) if by else None


def generated(front):
    return _event((front or {}).get("generated"))


def verified(front):
    """Verification events as a list; a single mapping counts as a one-element list (§5.2)."""
    value = (front or {}).get("verified")
    items = [value] if isinstance(value, dict) else value if isinstance(value, list) else []
    return [e for e in map(_event, items) if e]


def trust(front):
    """unverified, machine-confirmed or human-reviewed, from `verified` alone (§5.3)."""
    events = verified(front)
    if not events:
        return "unverified"
    return "human-reviewed" if any(e["by"].startswith("human:") for e in events) else "machine-confirmed"


def instant(value):
    """An aware datetime for an ISO 8601 instant or date (a date or a naive time is local)."""
    if isinstance(value, dt.datetime):
        moment = value
    elif isinstance(value, dt.date):
        moment = dt.datetime.combine(value, dt.time())
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            moment = dt.datetime.fromisoformat(text)
        except ValueError:
            try:
                moment = dt.datetime.combine(dt.date.fromisoformat(text[:10]), dt.time())
            except ValueError:
                return None
    else:
        return None
    return moment if moment.tzinfo else moment.astimezone()


def stale_after(front):
    """When the content goes stale: `stale_after`, else the legacy `valid_until` date (from its
    first instant, local time)."""
    front = front or {}
    return instant(front.get("stale_after")) or instant(front.get("valid_until"))


def is_stale(front, now=None):
    moment = stale_after(front)
    return moment is not None and (now or dt.datetime.now().astimezone()) >= moment


def _flatten(value):
    # An unquoted `- [[Note]]` is read by YAML as a list inside a list.
    while isinstance(value, list) and len(value) == 1:
        value = value[0]
    return value if isinstance(value, str) else None


def source_entries(front):
    """`sources` as OKF entries, each a mapping with `resource` (§5.1). A legacy string (a
    wikilink, a URL or a path) becomes an entry whose resource is that string."""
    value = (front or {}).get("sources")
    items = value if isinstance(value, list) else [] if value in (None, "", {}) else [value]
    out = []
    for item in items:
        if isinstance(item, dict):
            resource = _text(item.get("resource"))
            if resource:
                out.append(dict(item, resource=resource))
        elif isinstance(item, str) and item.strip():
            out.append(dict(resource=item.strip()))
        elif isinstance(item, list) and _flatten(item):
            out.append(dict(resource=f"[[{_flatten(item)}]]"))
    return out


def source_refs(front):
    return [e["resource"] for e in source_entries(front)]


def as_wikilink(resource):
    """A body link for a resource: wikilinks stay, a bundle path becomes [[its stem]], a URL stays."""
    resource = (resource or "").strip()
    if not resource or WIKILINK.match(resource) or re.match(r"^[a-z][a-z0-9+.-]*://", resource):
        return resource
    return f"[[{Path(resource).stem}]]"


def view(front, now=None):
    """What a consumer derives from a note's frontmatter: lifecycle, trust and freshness."""
    status, known = lifecycle(front)
    out = dict(status=status, trust=trust(front), stale=is_stale(front, now))
    moment = stale_after(front)
    if moment is not None:
        out["stale_after"] = moment.isoformat()
    if not known:
        out["status_unknown"] = _text((front or {}).get("status"))
    if intensity(front):
        out["intensity"] = intensity(front)
    if description(front):
        out["description"] = description(front)
    return out


def is_reserved_listing(relative, front):
    """index.md and log.md are listings, not concepts (§3.1): skipped by search unless the file
    carries a `type`, i.e. is a note that took the name (conformance reports that)."""
    return Path(relative).name in RESERVED and not _text((front or {}).get("type"))


# -- writing -------------------------------------------------------------------

def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def carry_actor():
    from . import __version__
    return f"carry/{__version__}"


def event(by, at=None):
    return {"by": by, "at": at or now_iso()}


def flow(value):
    """A mapping as one YAML flow line; JSON is valid YAML and reads back without PyYAML."""
    return json.dumps(value, ensure_ascii=False)


def entry(relative, title=None):
    """A `sources` entry for a file in the same bundle (a URL stays as it is)."""
    relative = str(relative).strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", relative):
        return dict(resource=relative)
    relative = "/" + relative.lstrip("/")
    return dict(resource=relative, title=title or Path(relative).stem)


def ref_entries(refs, own_source_id):
    """OKF `sources` entries for Carry's `source_id:path` references: a file in the record's own
    folder is a bundle path, one in another source stays a scope descriptor (§5.1)."""
    out = []
    for ref in refs:
        sid, _, relative = str(ref).partition(":")
        if relative and sid == own_source_id:
            out.append(entry(relative))
        else:
            out.append(dict(resource=str(ref), title=Path(relative or str(ref)).stem))
    return out


def carry_refs(front):
    """Carry's own `source_id:path` references of a record (`carry_source_refs`), else the
    legacy plain strings of `sources`."""
    refs = (front or {}).get("carry_source_refs")
    if isinstance(refs, list):
        return [r for r in refs if isinstance(r, str)]
    return [r for r in ((front or {}).get("sources") or []) if isinstance(r, str)]


def source_lines(entries):
    return ["sources:"] + [f"  - {flow(e)}" for e in entries] if entries else []


def owner(state_dir):
    """The owner's actor (`human:<id>`), set with `carry owner --set <id>`."""
    try:
        actor = json.loads((Path(state_dir) / OWNER_NAME).read_text(encoding="utf-8")).get("actor")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_OWNER
    if isinstance(actor, str) and actor.startswith("human:") and OWNER_ID_RE.match(actor[6:]):
        return actor
    return DEFAULT_OWNER


def set_owner(state_dir, ident):
    """Store the owner's id. A short name, never an email address."""
    ident = (ident or "").strip()
    ident = ident[6:] if ident.startswith("human:") else ident
    ident = ident.lower()
    if "@" in ident or not OWNER_ID_RE.match(ident):
        raise CarryError("invalid_owner_id")
    from .persistence import atomic_text
    actor = "human:" + ident
    atomic_text(Path(state_dir) / OWNER_NAME, json.dumps(dict(actor=actor)) + "\n")
    return actor


def _split(raw):
    """(head lines, rest) of a note with frontmatter, or (None, raw) without it."""
    nl = "\r\n" if "\r\n" in raw[:200] else "\n"
    if not raw.startswith("---" + nl):
        return None, raw, nl
    end = raw.find(nl + "---", 3)
    if end == -1:
        return None, raw, nl
    return raw[len("---" + nl):end].split(nl), raw[end:], nl


def _span(lines, key):
    """(start, end) of a top-level key and its continuation lines, or None."""
    for i, line in enumerate(lines):
        m = TOP_KEY.match(line)
        if m and m.group(1) == key:
            j = i + 1
            while j < len(lines) and not TOP_KEY.match(lines[j]):
                j += 1
            return i, j
    return None


def approve(raw, actor, at=None):
    """The owner checked a draft: `status: draft` (or a legacy `draft: true`) becomes
    `status: stable` and the owner's check joins `verified`. Every other line stays as written.
    Returns the new text, or None when the note is not a draft Carry can change line by line."""
    from .markdown import parse_frontmatter
    head, rest, nl = _split(raw)
    if head is None:
        return None
    front, _ = parse_frontmatter(raw)
    if not is_draft(front):
        return None
    status = _span(head, "status")
    draft = _span(head, "draft")
    if status and str(front.get("status", "")).strip().lower() == "draft":
        head[status[0]] = "status: stable"
        if draft:
            del head[draft[0]:draft[1]]
    elif draft and _flag(front.get("draft")):
        if status:      # a legacy, non-OKF status stays; stable is the default
            del head[draft[0]:draft[1]]
        else:
            head[draft[0]:draft[1]] = ["status: stable"]
    else:
        return None
    events = verified(front) + [event(actor, at)]
    old = _span(head, "verified")
    block = [f"verified: {flow(events[0])}"] if len(events) == 1 else ["verified:"] + [f"  - {flow(e)}" for e in events]
    if old:
        head[old[0]:old[1]] = block
    else:
        head += block
    return "---" + nl + nl.join(head) + rest


# -- conformance (§11) -------------------------------------------------------------

def conformance(root, extensions=(".md",)):
    """Counts of what keeps a folder from being a conformant OKF bundle, plus notes that still
    carry pre-OKF keys. The whole tree counts, not just what search reads; dot-directories are
    tool configuration and are skipped."""
    from .markdown import FRONTMATTER_RE, parse_frontmatter
    from .paths import walk_markdown
    counts = dict(files=0, no_frontmatter=0, no_type=0, reserved_misuse=0, invalid_status=0, legacy_keys=0)
    root = Path(root).expanduser()
    if not root.is_dir():
        return dict(counts, conformant=False, available=False)
    for relative, path in walk_markdown(root, extensions=tuple(extensions), max_bytes=float("inf")):
        counts["files"] += 1
        try:
            raw = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        except OSError:
            continue
        front, _ = parse_frontmatter(raw)
        has_front = FRONTMATTER_RE.match(raw) is not None
        if Path(relative).name in RESERVED:
            allowed = {"okf_version"} if relative == "index.md" else set()
            if has_front and set(front) - allowed:
                counts["reserved_misuse"] += 1
            continue
        if not has_front:
            counts["no_frontmatter"] += 1
            continue
        if not _text(front.get("type")):
            counts["no_type"] += 1
        if not lifecycle(front)[1] or (front.get("status") not in (None, "") and intensity(dict(status=front.get("status")))):
            counts["invalid_status"] += 1
        if any(k in front for k in LEGACY_KEYS) or any(not isinstance(s, dict) for s in (front.get("sources") or [])
                                                       if isinstance(front.get("sources"), list)):
            counts["legacy_keys"] += 1
    conformant = not (counts["no_frontmatter"] or counts["no_type"] or counts["reserved_misuse"])
    return dict(counts, conformant=conformant, available=True)
