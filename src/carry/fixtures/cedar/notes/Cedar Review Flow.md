---
type: note
summary: "How a Cedar proposal moves from draft to accepted, and how corrections keep history."
created: 2026-08-03
---

# Cedar Review Flow

A proposal enters as a draft. Nothing captured is treated as accepted fact until
a reviewer accepts it.

## States

- draft: recorded, visible, not yet an answer.
- accepted: returned as the current answer, with its revision.
- superseded: kept on disk, marked historical, pointing at its replacement.

## Corrections

A correction names the record it replaces and the revision it expects. If the
record moved on in the meantime, the correction is refused and the reviewer sees
the newer version instead of overwriting it.
