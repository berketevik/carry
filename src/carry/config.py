"""Workspace configuration: explicit sources, explicit state directory.

Nothing here reads a module-level root. Every path a caller can reach comes from
a Workspace instance, which is what keeps two workspaces from contaminating each
other.
"""
import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .errors import SourceError, WorkspaceError
from .paths import is_contained, resolve_root

CONFIG_NAME = "workspace.json"
CONFIG_VERSION = 1
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SCOPES = ("personal", "team")

DEFAULT_CHUNK_CHARS = 1100
DEFAULT_CHUNK_OVERLAP = 150


@dataclass(frozen=True)
class SourceConfig:
    """A configured Markdown root.

    `scope` is a label for attribution and display. It is not authorization; the
    product must not treat "team" as permission to read or publish anything.
    """
    source_id: str
    root: Path
    writable: bool = False
    scope: str = "personal"
    records_dir: str = "carry"
    extensions: tuple = (".md",)
    github: dict = field(default_factory=dict)
    exclude: tuple = ()
    # Opt-in to sharing approved items into this GitHub source's inbox (share.py); off when empty.
    share: dict = field(default_factory=dict)
    # No longer used: Jev, when chosen, judges every source (secret notes excepted).
    # Kept so older workspace files still load.
    external_judge: bool = True

    def validate(self):
        if not SOURCE_ID_RE.match(self.source_id or ""):
            raise WorkspaceError("invalid_source_id")
        if self.scope not in SCOPES:
            raise WorkspaceError("invalid_scope")
        if self.github and self.writable:
            raise WorkspaceError("github_source_must_be_read_only")
        if not isinstance(self.exclude, (list, tuple)) or not all(isinstance(p, str) for p in self.exclude):
            raise WorkspaceError("invalid_source_exclusions")
        if self.share:
            if not self.github:
                raise WorkspaceError("share_requires_github_source")
            if self.share.get("mode", "direct") not in ("direct", "pr"):
                raise WorkspaceError("invalid_share_mode")
        resolve_root(self.root)
        if self.writable and not os.access(self.root, os.W_OK):
            raise WorkspaceError("source_not_writable")
        return self

    def to_json(self):
        return dict(source_id=self.source_id, root=str(self.root), writable=self.writable,
                    scope=self.scope, records_dir=self.records_dir,
                    extensions=list(self.extensions), github=self.github, exclude=list(self.exclude),
                    share=self.share, external_judge=self.external_judge)

    @staticmethod
    def from_json(data):
        return SourceConfig(
            source_id=data["source_id"], root=Path(data["root"]).expanduser(),
            writable=bool(data.get("writable", False)), scope=data.get("scope", "personal"),
            records_dir=data.get("records_dir", "carry"),
            extensions=tuple(data.get("extensions", (".md",))),
            github=dict(data.get("github", {})), exclude=tuple(data.get("exclude", ())),
            share=dict(data.get("share", {})), external_judge=bool(data.get("external_judge", True)))


@dataclass(frozen=True)
class EmbeddingConfig:
    """Provider selection. `hashing` is deterministic and offline: it is a test
    and degraded-mode provider, not a semantic model, and status says so."""
    provider: str = "ollama"
    model: str = "nomic-embed-text"
    endpoint: str = "http://localhost:11434"
    prefixes: bool = True
    revision: str = "unspecified"
    timeout: float = 15.0
    dim: int = 256           # hashing provider only

    def to_json(self):
        return dict(provider=self.provider, model=self.model, endpoint=self.endpoint,
                    prefixes=self.prefixes, revision=self.revision, timeout=self.timeout,
                    dim=self.dim)

    @staticmethod
    def from_json(data):
        fields = {k: v for k, v in (data or {}).items() if k in EmbeddingConfig.__dataclass_fields__}
        if fields.get("provider") == "onnx":
            # 0.9-0.10 ran the built-in model through onnxruntime; llama.cpp replaced it.
            fields["provider"] = "llama"
        return EmbeddingConfig(**fields)


@dataclass(frozen=True)
class RetrievalConfig:
    chunk_chars: int = DEFAULT_CHUNK_CHARS
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    branch_candidates: int = 20      # per lexical/vector branch
    rrf_k: int = 60
    top_k: int = 6
    max_chars: int = 7000
    max_per_document: int = 2
    reranker: str = "off"            # off | cross | jev (TypeSafe API, opt-in)
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_min_score: float = -4.0
    judge_model: str = "jev-1.13.0"  # pinned: jev-latest may change behaviour without a code change
    vector_min_score: float = 0.45
    auto_refresh: bool = True
    refresh_seconds: int = 60
    github_sync_seconds: int = 300

    def to_json(self):
        return {f: getattr(self, f) for f in self.__dataclass_fields__}

    @staticmethod
    def from_json(data):
        return RetrievalConfig(**{k: v for k, v in (data or {}).items()
                                  if k in RetrievalConfig.__dataclass_fields__})


@dataclass(frozen=True)
class Workspace:
    """State directory plus configured sources.

    The state directory holds configuration and derived data (index, receipts,
    status, sidecar). Canonical records and captured events live in sources. It must
    live outside every source root so a rebuild can never touch user files.
    """
    state_dir: Path
    sources: tuple = field(default_factory=tuple)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)

    # --- derived paths ---
    @property
    def db_path(self):
        return self.state_dir / "index.db"

    @property
    def lock_path(self):
        return self.state_dir / "index.db.lock"

    @property
    def status_path(self):
        return self.state_dir / "index.status.json"

    @property
    def sidecar_path(self):
        return self.state_dir / "sidecar.json"

    @property
    def config_path(self):
        return self.state_dir / CONFIG_NAME

    def source(self, source_id):
        for src in self.sources:
            if src.source_id == source_id:
                return src
        raise SourceError("unknown_source")

    def writable_source(self, source_id=None):
        candidates = [s for s in self.sources if s.writable]
        if source_id is not None:
            src = self.source(source_id)
            if not src.writable:
                raise SourceError("source_not_writable")
            return src
        if len(candidates) != 1:
            raise SourceError("ambiguous_write_destination" if candidates else "no_writable_source")
        return candidates[0]

    def with_sources(self, sources):
        return replace(self, sources=tuple(sources)).validate()

    def validate(self):
        state = Path(self.state_dir).expanduser().resolve()
        seen = set()
        roots = []
        for src in self.sources:
            src.validate()
            if src.source_id in seen:
                raise WorkspaceError("duplicate_source_id")
            seen.add(src.source_id)
            root = resolve_root(src.root)
            if is_contained(root, state):
                raise WorkspaceError("state_dir_inside_source_root")
            for other in roots:
                if is_contained(other, root) or is_contained(root, other):
                    raise WorkspaceError("nested_source_roots")
            roots.append(root)
        if self.retrieval.reranker not in ("off", "cross", "jev"):
            raise WorkspaceError("invalid_reranker")
        if not -1 <= self.retrieval.vector_min_score <= 1:
            raise WorkspaceError("invalid_vector_threshold")
        if self.retrieval.refresh_seconds < 10 or self.retrieval.github_sync_seconds < 30:
            raise WorkspaceError("invalid_refresh_interval")
        if self.retrieval.chunk_overlap >= self.retrieval.chunk_chars:
            raise WorkspaceError("invalid_chunking")
        return self

    # --- persistence ---
    def to_json(self):
        return dict(version=CONFIG_VERSION, sources=[s.to_json() for s in self.sources],
                    embedding=self.embedding.to_json(), retrieval=self.retrieval.to_json())

    def save(self):
        self.validate()
        Path(self.state_dir).mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8", newline="\n")
        os.replace(tmp, self.config_path)
        return self

    @staticmethod
    def load(state_dir):
        state_dir = Path(state_dir).expanduser().resolve()
        path = state_dir / CONFIG_NAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise WorkspaceError("workspace_not_initialized")
        except ValueError:
            raise WorkspaceError("workspace_config_unreadable")
        if data.get("version") != CONFIG_VERSION:
            raise WorkspaceError("unsupported_config_version")
        return Workspace(
            state_dir=state_dir,
            sources=tuple(SourceConfig.from_json(s) for s in data.get("sources", [])),
            embedding=EmbeddingConfig.from_json(data.get("embedding")),
            retrieval=RetrievalConfig.from_json(data.get("retrieval"))).validate()

    @staticmethod
    def create(state_dir, sources=(), embedding=None, retrieval=None, exist_ok=False):
        state_dir = Path(state_dir).expanduser()
        config = state_dir / CONFIG_NAME
        if config.exists() and not exist_ok:
            raise WorkspaceError("workspace_already_initialized")
        return Workspace(state_dir=state_dir.resolve(), sources=tuple(sources),
                         embedding=embedding or EmbeddingConfig(),
                         retrieval=retrieval or RetrievalConfig()).save()


def open_workspace(state_dir=None):
    """Resolve a workspace from an explicit path or CARRY_WORKSPACE."""
    state_dir = state_dir or os.environ.get("CARRY_WORKSPACE")
    if not state_dir:
        raise WorkspaceError("workspace_not_specified")
    return Workspace.load(state_dir)
