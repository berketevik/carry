"""Record identity: IDs, revisions, state and the current-version pointer.

Carry-written notes carry their identity in frontmatter. Imported notes are left
untouched and tracked in a sidecar, because rewriting a user's files to insert
IDs is not something an import should do.
"""
import re
import uuid
from dataclasses import dataclass

STATES = ("draft", "accepted", "corrected", "superseded", "rejected")
RECORD_ID_RE = re.compile(r"^(rec|imp)_[0-9a-f]{12,32}$")

FRONTMATTER_KEYS = (
    "carry_record", "carry_revision", "carry_state", "carry_current",
    "carry_event", "carry_author", "carry_surface",
    "carry_supersedes", "carry_superseded_by",
)


def new_record_id():
    return "rec_" + uuid.uuid4().hex[:16]


def imported_record_id(source_id, relative_path):
    """Stable synthetic ID for a file Carry did not write. Derived from location
    so a re-import of an unchanged workspace does not renumber everything."""
    import hashlib
    digest = hashlib.blake2b(f"{source_id}\t{relative_path}".encode("utf-8"),
                             digest_size=8).hexdigest()
    return "imp_" + digest


@dataclass(frozen=True)
class RecordMeta:
    record_id: str
    revision: int = 1
    state: str = "imported"
    current: bool = True
    event_id: str = ""
    origin: str = "imported"        # imported | carry
    superseded_by: str = ""
    supersedes: str = ""

    def to_row(self):
        return (self.record_id, self.revision, self.state, int(self.current),
                self.event_id, self.origin, self.superseded_by)


def read_record_meta(frontmatter, source_id, relative_path, sidecar_id=None):
    """Derive record identity for one file.

    Frontmatter written by Carry wins. Otherwise the sidecar ID (if the file was
    seen before, possibly under another name) wins over a freshly derived one.
    """
    frontmatter = frontmatter or {}
    record_id = str(frontmatter.get("carry_record") or "").strip()
    if record_id and RECORD_ID_RE.match(record_id):
        state = str(frontmatter.get("carry_state") or "draft")
        if state not in STATES:
            state = "draft"
        try:
            revision = int(frontmatter.get("carry_revision", 1))
        except (TypeError, ValueError):
            revision = 1
        current = frontmatter.get("carry_current", True)
        return RecordMeta(
            record_id=record_id, revision=max(1, revision), state=state,
            current=bool(current) and not frontmatter.get("carry_superseded_by"),
            event_id=str(frontmatter.get("carry_event") or ""), origin="carry",
            superseded_by=str(frontmatter.get("carry_superseded_by") or ""),
            supersedes=str(frontmatter.get("carry_supersedes") or ""))
    return RecordMeta(record_id=sidecar_id or imported_record_id(source_id, relative_path),
                      revision=1, state="imported", current=True, origin="imported")
