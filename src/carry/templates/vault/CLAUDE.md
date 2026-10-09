@LLM-GUIDE.md
@VAULT-RULES.md

All vault rules live in the imported **LLM-GUIDE.md** (the core) and **VAULT-RULES.md** (this vault's local layer). This file only carries Claude Code wiring:

- **Recall**: before answering a substantive question about the owner's history, decisions or projects, call `{{RECALL_TOOL}}` from the `carry` server (`.mcp.json`) with the question and `queries`: 2-3 short keyword variants (the key terms as the notes would write them in {{LANGUAGE}}, an English variant, synonyms and likely names). The passages are candidates: delegate the search to the `carry-recall` subagent (`.claude/agents/`), which keeps only the passages that answer, and treat `NO_EVIDENCE` as "no record in the vault". Only when the search state shows `answerability: judged` are the passages already checked; then answer from them directly.
- **Capture**: Claude Code can write files, so durable knowledge goes straight into `notes/` as a `draft: true` note (Capture & File in LLM-GUIDE). Prompt capture, when enabled, is Carry's job.
- **Continuity**: follow "Continuity between threads" in LLM-GUIDE. A SessionStart hook injects Carry's state pack (recent commits, open items and decisions from recent chats, drafts waiting for review), and a SessionEnd hook drafts what this chat produced into `+/` in the background; write results to notes and commit after each step, then `git push` when the vault has a remote (it is the backup).
