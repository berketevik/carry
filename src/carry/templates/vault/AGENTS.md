# AGENTS.md: Codex surface adapter

This file contains **no vault rules**. The single source of truth is **`LLM-GUIDE.md`** in this directory. Read it before writing or editing any file.

## Codex wiring

- **Session start:** run `git log -10 --oneline` and read the current-state section of the effort note the owner names, before answering. This is how a new thread learns what earlier threads decided.
- **Recall:** the project config exposes `{{RECALL_TOOL}}` from the `carry` MCP server. Call it before answering a substantive question about the owner's history, decisions or projects, and ground the answer in its cited passages.
- **Capture:** write durable knowledge directly into `notes/` as a `draft: true` note (Capture & File in LLM-GUIDE). Prompt capture, when enabled, is Carry's job.
- **After each step:** update the relevant note or `log/`, then commit. Do not leave results only in the chat.
- **Cost:** prefer a new thread per task and avoid driving the desktop or browser when a file-based route exists.
