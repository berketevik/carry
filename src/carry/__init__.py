"""Carry: portable local memory for coding agents.

Markdown stays canonical; the SQLite index is derived and disposable. Every
entry point takes an explicit Workspace, so two workspaces on one machine never
share state.
"""
try:  # one source of truth: the version in pyproject.toml, as installed
    from importlib.metadata import version as _version
    __version__ = _version("carry")
except Exception:  # running from a source tree that was never installed
    __version__ = "0.0.0"

from .config import SourceConfig, Workspace, WorkspaceError  # noqa: F401

__all__ = ["Workspace", "SourceConfig", "WorkspaceError", "__version__"]
