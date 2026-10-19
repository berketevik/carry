"""A team knowledge base stays the same for every member who connects it.

`carry setup --team` connects the team repository as a local git clone with scope
"team". Maintenance keeps that clone current with a fast-forward pull on the GitHub
sync cadence, and only when nothing of the member's own could be touched: a clean
working tree and no unpushed commits. Otherwise it fetches, counts how far behind the
clone is, and leaves the tree alone; status says why.

The repository may also carry `.carry/team.json`: the search settings every member
should use and the oldest Carry version that searches it the same way. Setup applies
them, status reports any difference, and `carry team --apply` applies them later. They
are never applied silently, since a member's workspace may hold their own notes too.
The file comes from a repository the member does not control: unknown keys and bad
values are reported, never fatal.
"""
import json
import re
from dataclasses import replace
from pathlib import Path

from .errors import CarryError
from .update import _git

SETTINGS_PATH = Path(".carry") / "team.json"
MAX_SETTINGS = 64_000
# What a query returns depends on these and none of them changes the vectors, so a team
# can set them without invalidating its pack. Chunking and the model are in the index
# fingerprint and stay out.
RETRIEVAL_KEYS = {"top_k": int, "max_chars": int, "max_per_document": int,
                  "branch_candidates": int, "rrf_k": int, "vector_min_score": float}
VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}$")


def _valid(kind, value):
    if isinstance(value, bool):
        return False
    if kind is int:
        return isinstance(value, int) and value > 0
    return isinstance(value, (int, float)) and -1.0 <= value <= 1.0


def load_settings(source):
    """(settings, warnings) from `<root>/.carry/team.json`; settings is None when the file
    is absent or unusable."""
    path = Path(source.root).expanduser() / SETTINGS_PATH
    try:
        if not path.is_file():
            return None, []
        if path.stat().st_size > MAX_SETTINGS:
            return None, ["team_settings_too_large"]
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, ["team_settings_unreadable"]
    if not isinstance(data, dict):
        return None, ["team_settings_unreadable"]
    warnings = [f"team_settings_unknown_key:{k}" for k in data if k not in ("min_carry_version", "retrieval")]
    retrieval = {}
    raw = data.get("retrieval", {})
    if not isinstance(raw, dict):
        warnings.append("team_settings_invalid:retrieval")
        raw = {}
    for key, value in raw.items():
        kind = RETRIEVAL_KEYS.get(key)
        if kind is None:
            warnings.append(f"team_settings_unknown_key:retrieval.{key}")
        elif not _valid(kind, value):
            warnings.append(f"team_settings_invalid:retrieval.{key}")
        else:
            retrieval[key] = kind(value)
    version = data.get("min_carry_version")
    if version is not None and not (isinstance(version, str) and VERSION_RE.match(version)):
        warnings.append("team_settings_invalid:min_carry_version")
        version = None
    return dict(retrieval=retrieval, min_carry_version=version), warnings


def _version(text):
    parts = [int(p) for p in text.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def settings_report(workspace, version=None):
    """How the workspace stands against its team sources' settings; None when no team
    source carries any."""
    if version is None:
        from . import __version__ as version
    loaded = {s.source_id: load_settings(s) for s in workspace.sources if s.scope == "team"}
    found = {sid: settings for sid, (settings, _) in loaded.items() if settings}
    warnings = [w for _, (_, ws) in sorted(loaded.items()) for w in ws]
    if not found and not warnings:
        return None
    distinct = {json.dumps(s["retrieval"], sort_keys=True) for s in found.values()}
    conflict = len(distinct) > 1
    retrieval = {} if conflict or not found else next(iter(found.values()))["retrieval"]
    differ = {k: dict(team=v, local=getattr(workspace.retrieval, k))
              for k, v in sorted(retrieval.items()) if getattr(workspace.retrieval, k) != v}
    versions = [s["min_carry_version"] for s in found.values() if s["min_carry_version"]]
    minimum = max(versions, key=_version) if versions else None
    # A checkout that is not installed reports 0.0.0: unknown, not old.
    too_old = bool(minimum) and version != "0.0.0" and _version(version) < _version(minimum)
    return dict(sources=sorted(found), retrieval=retrieval, differ=differ, conflict=conflict,
                min_carry_version=minimum, carry_version=version, too_old=too_old, warnings=warnings,
                apply="carry team --apply" if differ and not conflict else None)


def settings_flags(report):
    if not report:
        return []
    flags = []
    if report["conflict"]:
        flags.append("team_settings_conflict")
    if report["differ"]:
        flags.append("team_settings_differ")
    if report["too_old"]:
        flags.append("carry_older_than_team_minimum")
    if report["warnings"]:
        flags.append("team_settings_invalid")
    return flags


def pack_report(workspace):
    """For each team source that ships a pack: is it usable by this workspace? A pack built
    with another model or chunking is skipped by the index without a word; this says so."""
    import sqlite3
    from contextlib import closing
    from .embedding import build_provider
    from .index import PACK_PATH, fingerprint, open_readonly
    packs = {s.source_id: Path(s.root).expanduser() / PACK_PATH for s in workspace.sources if s.scope == "team"}
    packs = {sid: p for sid, p in packs.items() if p.is_file()}
    if not packs:
        return {}
    try:
        expected = fingerprint(workspace, build_provider(workspace.embedding))
    except Exception:
        return {}
    report = {}
    for source_id, path in packs.items():
        try:
            with closing(open_readonly(path)) as con:
                found = dict(con.execute("SELECT key,value FROM meta")).get("fingerprint")
        except sqlite3.Error:
            found = None
        report[source_id] = "usable" if found == expected else "incompatible"
    return report


def pack_flags(report):
    return ["team_pack_incompatible"] if "incompatible" in (report or {}).values() else []


def apply_settings(workspace):
    """Write the team's search settings into the workspace. Conflicting team sources apply nothing."""
    report = settings_report(workspace)
    if report and report["conflict"]:
        raise CarryError("team_settings_conflict")
    if not report or not report["retrieval"]:
        return dict(applied={}, report=report)
    replace(workspace, retrieval=replace(workspace.retrieval, **report["retrieval"])).save()
    return dict(applied={k: v["team"] for k, v in report["differ"].items()}, report=report)


def is_clone(source):
    """A team source that is a local git clone rather than a GitHub archive source."""
    return source.scope == "team" and not source.github and (Path(source.root).expanduser() / ".git").exists()


def pull(source):
    """Bring a team clone up to its upstream when that is safe. Never raises."""
    root = Path(source.root).expanduser()
    try:
        _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    except CarryError:
        return dict(status="skipped", reason="no_upstream")
    try:
        _git(root, "fetch", "-q")
    except CarryError:
        return dict(status="failed", reason="fetch_failed")
    try:
        ahead = int(_git(root, "rev-list", "--count", "@{u}..HEAD"))
        behind = int(_git(root, "rev-list", "--count", "HEAD..@{u}"))
        if ahead:
            return dict(status="skipped", reason="unpushed_commits", behind=behind, commit=_git(root, "rev-parse", "HEAD"))
        # Untracked files do not count: Carry's own connection files (.mcp.json, the carry-recall
        # agent) sit untracked in every connected clone. A fast-forward that would overwrite one
        # is refused by git itself and reported as failed.
        if _git(root, "status", "--porcelain", "--untracked-files=no"):
            return dict(status="skipped", reason="local_changes", behind=behind, commit=_git(root, "rev-parse", "HEAD"))
        if behind:
            _git(root, "merge", "--ff-only", "-q", "@{u}")
        return dict(status="pulled" if behind else "current", behind=0, commit=_git(root, "rev-parse", "HEAD"))
    except (CarryError, ValueError):
        return dict(status="failed", reason="git_failed")


def clone_degradations(states, source_ids):
    """Status flags for team clones that are not current."""
    flags = []
    for source_id in source_ids:
        state = states.get(source_id) or {}
        if state.get("kind") != "team_clone":
            continue
        if state.get("reason") == "local_changes":
            flags.append("team_clone_has_local_changes")
        elif state.get("reason") == "unpushed_commits":
            flags.append("team_clone_has_unpushed_commits")
        elif state.get("status") == "failed":
            flags.append("team_clone_sync_failed")
        if state.get("behind"):
            flags.append("team_clone_behind")
    return list(dict.fromkeys(flags))
