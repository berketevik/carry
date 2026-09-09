---
type: note
summary: "Cedar retrieval: hybrid lexical and vector search, and what degradation must be shown."
created: 2026-08-27
---

# Cedar Retrieval Notes

Cedar runs a lexical branch and a vector branch and fuses the two rankings. Each
branch can fail on its own.

## Degradation

When the vector branch is unavailable, Cedar reports lexical-only results rather
than presenting them as semantic search. When no passage matches, Cedar returns
an explicit no-evidence state; it never fills the gap with a guess.

## Evidence limits

Callers may lower the returned passage count and the character allowance. They
cannot raise either above the configured maximum.
