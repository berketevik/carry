"""Markdown parsing and chunking.

PyYAML is used when installed. The built-in fallback covers the frontmatter
shapes Carry writes and the common scalar/list forms found in user notes; it
reports the shapes it cannot represent instead of guessing.
"""
import re
import json
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", re.S)
HEADING_SPLIT_RE = re.compile(r"(?m)^(#{1,6}\s+.*)$")

try:  # optional dependency
    import yaml as _yaml
except ImportError:  # pragma: no cover - exercised on installs without PyYAML
    _yaml = None


PAIR_RE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*:(?:\s+(.*))?$", re.S)


def _split_flow(inner):
    """Split a flow collection's inside on top-level commas (quotes and nesting kept whole)."""
    parts, depth, quote, buf = [], 0, None, ""
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
            continue
        buf += ch
    if buf.strip():
        parts.append(buf)
    return parts


def _scalar(raw):
    text = raw.strip()
    if not text:
        return ""
    if text[0] in "\"'" and text[-1] == text[0] and len(text) > 1:
        if text[0] == '"':
            try:
                return json.loads(text)
            except ValueError:
                pass
        return text[1:-1]
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if text in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    if text.startswith("{") and text.endswith("}"):
        # An inline JSON object (a harvest digest's `harvest:` line) is a YAML flow mapping:
        # read it as PyYAML would, or a digest is taken for a plain draft. OKF's
        # `generated: { by: ..., at: ... }` is the same mapping with plain keys.
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else text
        except ValueError:
            pass
        mapping = {}
        for part in _split_flow(text[1:-1]):
            pair = PAIR_RE.match(part.strip())
            if not pair:
                return text
            mapping[pair.group(1)] = _scalar(pair.group(2) or "")
        return mapping
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(p) for p in _split_flow(inner)] if inner else []
    return text


def _fallback_frontmatter(text):
    """Top-level keys with scalar or flow values, block lists (of scalars, flow mappings or
    `key: value` mappings) and one level of nested mapping: the shapes OKF notes use."""
    data, key, item, item_indent = {}, None, None, 0
    for line in text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        dash = stripped == "-" or stripped.startswith("- ")
        if indent == 0 and not dash:
            match = re.match(r"^([A-Za-z0-9_.\-]+)\s*:\s*(.*)$", line)
            item = None
            if not match:
                key = None
                continue
            name, value = match.group(1), match.group(2)
            data[name] = [] if value.strip() == "" else _scalar(value)
            key = name if value.strip() == "" else None
            continue
        if key is None:
            continue
        if dash:
            if not isinstance(data[key], list):
                continue
            content = stripped[1:].strip()
            pair = PAIR_RE.match(content) if content[:1] not in "\"'[{" else None
            if pair:
                item, item_indent = {pair.group(1): _scalar(pair.group(2) or "")}, indent + 2
                data[key].append(item)
            else:
                item = None
                data[key].append(_scalar(content))
            continue
        pair = PAIR_RE.match(stripped)
        if not pair:
            continue
        if item is not None and indent >= item_indent:
            item[pair.group(1)] = _scalar(pair.group(2) or "")
        elif data[key] == [] or isinstance(data[key], dict):
            if data[key] == []:
                data[key] = {}
            data[key][pair.group(1)] = _scalar(pair.group(2) or "")
    return data


def parse_frontmatter(raw):
    """Return (frontmatter_dict, body). Malformed frontmatter yields an empty
    mapping and the untouched body: a note is never dropped over its metadata."""
    raw = raw.replace("\r\n", "\n")
    match = FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    block, body = match.group(1), raw[match.end():]
    data = None
    if _yaml is not None:
        try:
            # libyaml's safe loader when present: same safety, several times faster on
            # the per-recall lifecycle scan of every file's frontmatter.
            loaded = _yaml.load(block, Loader=getattr(_yaml, 'CSafeLoader', _yaml.SafeLoader))
            data = loaded if isinstance(loaded, dict) else None
        except Exception:
            data = None
    if data is None:
        data = _fallback_frontmatter(block)
    return data, body


def parse_document(raw, path):
    frontmatter, body = parse_frontmatter(raw)
    title = str(frontmatter.get("title") or Path(path).stem)
    return title, frontmatter, body


def chunk_body(title, summary, body, chunk_chars=1100, overlap=150):
    """Split on headings, window long sections, prefix every window with its
    heading path so a retrieved chunk still says where it came from."""
    if overlap >= chunk_chars:
        raise ValueError("invalid_chunking")
    chunks = []
    if summary:
        chunks.append(("summary", f"{title} - {summary}"))

    def flush(head, text):
        text = text.strip()
        if not text:
            return
        label = head.lstrip("#").strip() or "body"
        index = 0
        while index < len(text):
            window = text[index:index + chunk_chars]
            context = title if not label or label == "body" else f"{title} > {label}"
            chunks.append((label, f"{context}\n{window}"))
            if index + chunk_chars >= len(text):
                break
            index += chunk_chars - overlap

    current, buffer = "", ""
    for part in HEADING_SPLIT_RE.split(body):
        if HEADING_SPLIT_RE.fullmatch(part or ""):
            flush(current, buffer)
            current, buffer = part, ""
        else:
            buffer += part
    flush(current, buffer)
    return chunks
