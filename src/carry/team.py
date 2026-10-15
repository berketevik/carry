"""A team knowledge base stays the same for every member who connects it.

`carry setup --team` connects the team repository as a local git clone with scope
"team". Maintenance keeps that clone current with a fast-forward pull on the GitHub
sync cadence, and only when nothing of the member's own could be touched: a clean
working tree and no unpushed commits. Otherwise it fetches, counts how far behind the
clone is, and leaves the tree alone; status says why.
"""
from pathlib import Path

from .errors import CarryError
from .update import _git


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
        if _git(root, "status", "--porcelain"):
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
