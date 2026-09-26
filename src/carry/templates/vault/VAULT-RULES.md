---
type: agent-instruction
audience: llm
summary: "This vault's local rules, loaded after the LLM-GUIDE core."
created: {{CREATED}}
---

# VAULT-RULES: local layer of this vault

> Loaded right after `LLM-GUIDE.md` (the core) by `CLAUDE.md` and `AGENTS.md`. Carry writes this file once and never changes it; it belongs to the owner. Rules here may add to the core and narrow its permissions, never relax them.

Add sections as the vault needs them, for example:

- **Devices**: which machines hold a clone of the vault and how they sync.
- **Surfaces**: which client uses which recall and capture path.
- **Unattended agents**: what an agent may do without the owner present, and what needs written authorization.
- **Domain conventions**: extra `type:` values, folders, naming or language rules.
