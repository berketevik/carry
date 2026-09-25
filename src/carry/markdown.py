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
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(p) for p in inner.split(",")] if inner else []
    return text


def _fallback_frontmatter(text):
    data, key, block = {}, None, None
    for line in text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and line.lstrip().startswith("- ") and key:
            block = block if block is not None else []
            block.append(_scalar(line.lstrip()[2:]))
            data[key] = block
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)\s*:\s*(.*)$", line)
        if not match:
            continue
        if block is not None:
            block = None
        key, value = match.group(1), match.group(2)
        data[key] = [] if value.strip() == "" else _scalar(value)
        block = data[key] if value.strip() == "" else None
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
            loaded = _yaml.safe_load(block)
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
