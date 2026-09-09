"""Error types shared by the core. Diagnostics carry types, never note text."""


class CarryError(Exception):
    """Base class; message text is safe for logs but not for status payloads."""


class WorkspaceError(CarryError):
    """Configuration is missing, malformed or unsafe."""


class SourceError(CarryError):
    """A path escapes its source root, or the source is not writable."""


class ProviderUnavailable(CarryError):
    """An embedding or rerank provider cannot serve this request."""


class RevisionConflict(CarryError):
    """A write lost a precondition check; the caller must re-read and retry."""
