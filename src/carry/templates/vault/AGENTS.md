# AGENTS.md: Codex surface adapter

This file contains **no vault rules**. They live in **`LLM-GUIDE.md`** (the core) and **`VAULT-RULES.md`** (this vault's local layer) in this directory. Read both, the core first, before writing or editing any file.

## Codex wiring

- **Session start:** Codex's SessionStart hook adds Carry's state pack to the context; if it is not there, run `carry context` in this folder (recent commits, open items and decisions from recent chats, drafts waiting for review) and read the current-state section of the effort note the owner names, before answering. This is how a new thread learns what earlier threads decided.
- **Recall:** before answering a substantive question about the owner's history, decisions or projects, call `{{RECALL_TOOL}}` from the `carry` MCP server with the question and `queries`: 2-3 short keyword variants (the key terms as the notes would write them in {{LANGUAGE}}, an English variant, synonyms and likely names). When its search state shows `reranker: jev` the passages are already judged; ground the answer in them, and no passage means the vault has no record. Otherwise spawn the `carry-recall` agent (`.codex/agents/`, a small model) to keep only the passages that answer, or discard non-answering passages yourself if it is unavailable.
- **Capture:** write durable knowledge directly into `notes/` as a `draft: true` note (Capture & File in LLM-GUIDE). Prompt capture, when enabled, is Carry's job.
- **Session end:** a SessionEnd hook drafts what the chat produced into `+/` in the background (`carry harvest`).
- **After each step:** update the relevant note or `log/`, then commit, and `git push` when the vault has a remote (it is the backup). Do not leave results only in the chat.
- **Cost:** prefer a new thread per task and avoid driving the desktop or browser when a file-based route exists.
