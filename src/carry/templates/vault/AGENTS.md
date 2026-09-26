# AGENTS.md: Codex surface adapter

This file contains **no vault rules**. They live in **`LLM-GUIDE.md`** (the core) and **`VAULT-RULES.md`** (this vault's local layer) in this directory. Read both, the core first, before writing or editing any file.

## Codex wiring

- **Session start:** run `git log -10 --oneline` and read the current-state section of the effort note the owner names, before answering. This is how a new thread learns what earlier threads decided.
- **Recall:** before answering a substantive question about the owner's history, decisions or projects, spawn the `carry-recall` agent (`.codex/agents/`, a small model): it calls `{{RECALL_TOOL}}` from the `carry` MCP server, keeps only the passages that answer and returns them quoted. If that agent is unavailable, call `{{RECALL_TOOL}}` yourself and discard passages that do not answer the question. Ground the answer in the cited passages; no answering passage means the vault has no record.
- **Capture:** write durable knowledge directly into `notes/` as a `draft: true` note (Capture & File in LLM-GUIDE). Prompt capture, when enabled, is Carry's job.
- **After each step:** update the relevant note or `log/`, then commit. Do not leave results only in the chat.
- **Cost:** prefer a new thread per task and avoid driving the desktop or browser when a file-based route exists.
