---
type: note
summary: "Cedar keeps Markdown canonical and treats the search index as disposable derived state."
created: 2026-07-22
---

# Cedar Architecture Decision

Markdown files remain the record of truth for Cedar. The search index is derived
and may be deleted at any time; a rebuild must reproduce it from the files alone.

## Consequences

- A failed rebuild leaves the previous index serving queries.
- Retrieval quality changes never require rewriting user files.
- Import is read-only until a destination folder is chosen explicitly.

## Rejected alternatives

A database-first store was rejected because it makes the corpus unreadable
without Cedar itself.
