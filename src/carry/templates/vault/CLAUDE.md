@LLM-GUIDE.md

All vault rules live in the imported **LLM-GUIDE.md**. This file only carries Claude Code wiring:

- **Recall**: the `{{RECALL_TOOL}}` MCP tool from the `carry` server (`.mcp.json`). Call it before answering a substantive question about the owner's history, decisions or projects.
- **Capture**: Claude Code can write files, so durable knowledge goes straight into `notes/` as a `draft: true` note (Capture & File in LLM-GUIDE). Prompt capture, when enabled, is Carry's job.
- **Continuity**: follow "Continuity between threads" in LLM-GUIDE. The session start already shows recent commits; write results to notes and commit after each step.
