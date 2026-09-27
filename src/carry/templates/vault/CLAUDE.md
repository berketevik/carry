@LLM-GUIDE.md
@VAULT-RULES.md

All vault rules live in the imported **LLM-GUIDE.md** (the core) and **VAULT-RULES.md** (this vault's local layer). This file only carries Claude Code wiring:

- **Recall**: before answering a substantive question about the owner's history, decisions or projects, call `{{RECALL_TOOL}}` from the `carry` server (`.mcp.json`) with the question and `queries`: 2-3 short keyword variants (the key terms as the notes would write them in {{LANGUAGE}}, an English variant, synonyms and likely names). When its search state shows `reranker: jev` the passages are already judged: ground the answer in them, and treat empty evidence as "no record in the vault". When it shows any other reranker, the passages are unfiltered: delegate the search to the `carry-recall` subagent (`.claude/agents/`, a small model), which keeps only the passages that answer.
- **Capture**: Claude Code can write files, so durable knowledge goes straight into `notes/` as a `draft: true` note (Capture & File in LLM-GUIDE). Prompt capture, when enabled, is Carry's job.
- **Continuity**: follow "Continuity between threads" in LLM-GUIDE. The session start already shows recent commits; write results to notes and commit after each step, then `git push` when the vault has a remote (it is the backup).
