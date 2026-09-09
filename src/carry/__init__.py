"""Carry: portable local memory for coding agents.

Markdown stays canonical; the SQLite index is derived and disposable. Every
entry point takes an explicit Workspace, so two workspaces on one machine never
share state.
"""
__version__ = "0.1.0.dev0"

from .config import SourceConfig, Workspace, WorkspaceError  # noqa: F401

__all__ = ["Workspace", "SourceConfig", "WorkspaceError", "__version__"]
