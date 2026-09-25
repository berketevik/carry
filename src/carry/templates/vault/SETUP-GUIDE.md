---
type: topic-index
summary: "How this vault was created by Carry and how to use it day to day."
created: {{CREATED}}
---

# SETUP-GUIDE

This vault was created by `carry vault init` (template {{TEMPLATE_VERSION}}). The stamp in `.carry/vault.json` records which files Carry wrote.

## Daily use

1. Open this folder as the project in Claude Code or Codex. Both read `LLM-GUIDE.md` through `CLAUDE.md` / `AGENTS.md`.
2. Start a new thread for each task. Name the note you are continuing ("continue from X").
3. Ask the agent to write results into notes and commit. The next thread continues from the files.
4. Optional: open the folder in Obsidian to browse and edit notes.

## Recall and capture

- Recall runs through the Carry MCP server. The client asks you to approve the server on first use.
- Prompt capture is opt-in: `carry --workspace <state> capture configure --client claude|codex`, then connect the hook from the Carry app.
- `.mcp.json` and `.codex/config.toml` contain paths for this machine and are not committed. On another machine run `carry --workspace <state> vault init <this folder>` again.

## Upgrading

Running `carry vault init` again updates only files Carry wrote that you have not edited. An edited file is reported as a conflict and left as it is.
