"""Path containment. A configured root is a boundary, not a hint."""
import os
from pathlib import Path

from .errors import SourceError

SKIP_DIR_NAMES = {".git", ".obsidian", ".trash", "node_modules", "__pycache__", ".venv"}


def resolve_root(path):
    """Resolve a configured root; it must exist and be a real directory."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise SourceError("root_not_a_directory")
    return resolved


def resolve_within(root, relative):
    """Resolve `relative` inside `root`, rejecting traversal and symlink escape.

    The check runs on the resolved path, so a symlink pointing outside the root
    fails even though its lexical path looks contained.
    """
    root = Path(root).resolve()
    candidate = Path(relative)
    if candidate.is_absolute():
        raise SourceError("absolute_path_rejected")
    if any(part == ".." for part in candidate.parts):
        raise SourceError("traversal_rejected")
    target = (root / candidate)
    resolved = Path(os.path.realpath(target))
    if resolved != root and root not in resolved.parents:
        raise SourceError("escapes_source_root")
    return resolved


def is_contained(root, other):
    """True when `other` is `root` itself or lives underneath it."""
    root, other = Path(root).resolve(), Path(other).resolve()
    return root == other or root in other.parents


def walk_markdown(root, extensions=(".md",), max_bytes=2_000_000):
    """Yield (relative_path, absolute_path) for indexable files under `root`.

    Symlinked files and directories are skipped rather than followed: a link can
    point outside the configured root, and the corpus digest must stay stable.
    """
    root = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIR_NAMES and not d.startswith(".")
            and not (current / d).is_symlink()
        )
        for name in sorted(filenames):
            path = current / name
            if path.is_symlink() or path.suffix.lower() not in extensions:
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            yield str(path.relative_to(root)), path
