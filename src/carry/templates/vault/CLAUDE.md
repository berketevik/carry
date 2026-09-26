@LLM-GUIDE.md
@VAULT-RULES.md

All vault rules live in the imported **LLM-GUIDE.md** (the core) and **VAULT-RULES.md** (this vault's local layer). This file only carries Claude Code wiring:

- **Recall**: before answering a substantive question about the owner's history, decisions or projects, delegate the search to the `carry-recall` subagent (`.claude/agents/`, a small model): it calls `{{RECALL_TOOL}}` from the `carry` server (`.mcp.json`), keeps only the passages that answer and returns them quoted. Ground the answer in what it returns; `NO_EVIDENCE` means the vault has no record. Call `{{RECALL_TOOL}}` directly only when the subagent is unavailable, and then discard passages that do not answer the question.
- **Capture**: Claude Code can write files, so durable knowledge goes straight into `notes/` as a `draft: true` note (Capture & File in LLM-GUIDE). Prompt capture, when enabled, is Carry's job.
- **Continuity**: follow "Continuity between threads" in LLM-GUIDE. The session start already shows recent commits; write results to notes and commit after each step.
