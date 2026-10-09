## <img src="assets/carry-icon.png" width="96" alt="Carry icon">

🇹🇷 [Türkçe](README.tr.md) · 🤖 Assistants installing or explaining Carry: read [AGENTS.md](AGENTS.md) first.

**Claude Code and Codex start every chat from zero. Carry gives them a memory made of your own notes.**

In September you find that a client's user IDs are broken. In November you sit down to fix them, and your assistant checks your notes first: it reminds you what you found, with a link to where it's written. Your notes stay on your computer as plain Markdown (`.md`) files.

**Version 0.11.0 · macOS 14+ · pilot**

## Get started

Give this page's link to Claude Code or Codex and say "install Carry". It installs what is needed and tells you what to do next.

Or install it yourself (needs git, [uv](https://docs.astral.sh/uv/) and `xcode-select --install`):

```sh
uv tool install "git+https://github.com/berketevik/carry"
carry setup
```

Setup asks two things: what to connect (your own notes, your team's knowledge base, or both) and, for each, whether to start fresh or use what you already have. Then open Claude Code (or Codex) in the folder it names and ask: "What is in my notes?"

**Team leads:** give teammates one command, `carry setup --team owner/repo`, with your team's GitHub repository of Markdown notes. Whoever wants only the team's notes picks that in setup; nothing else is added.

## How it works

![How Carry works: notes are indexed on your computer; a question is searched by words and by meaning, your assistant keeps the passages that answer; chats become drafts you review](assets/how-it-works.svg)

1. **Your notes become searchable.** Carry splits them into short passages and keeps a search index on your computer, by words and by meaning. The meaning model (about 330 MB) runs inside Carry, on your Mac's GPU; if [Ollama](https://ollama.com) is installed, Carry uses it instead.
2. **Your assistant checks your notes first.** It searches them, keeps only the passages that really answer the question and replies with the source of each. “Last week” or “in September” narrows the search to that period. Each chat also opens with a short brief of recent decisions and unfinished work.
3. **Chats turn into drafts you review.** When a chat ends, Carry drafts its decisions and open tasks into your notes folder's `+/` inbox. In the Carry app you accept, fix or skip each item; nothing becomes a note without you.

## Your data

Carry doesn't upload your notes anywhere and uses no online service of its own. The passages your assistant reads go only to that assistant (Claude Code or Codex, with your own account), and chat drafts are made through it too.

<details>
<summary><b>Later, if you need it</b></summary>

| To | Run |
|---|---|
| Add your team's knowledge base later | `carry setup --team owner/repo` |
| Ship the team repo's search index with it, so teammates search at once (run where the repo is cloned, then commit and push) | `carry github pack --id team --out <clone>/.carry/index.db` |
| Get chat drafts every evening at 21:30 | `carry harvest --install-schedule` |
| Turn search by meaning on or off | `carry search --semantic on` (or `off`) |
| Update Carry | `carry update`, then `carry app install` |
| Check that everything works | `carry status --probe` |

Commands that read notes need Carry's own folder: `export CARRY_WORKSPACE="$HOME/CarryState"` or `carry --workspace <folder> …`. Run `carry <command> --help` for the rest.

</details>

<details>
<summary><b>For assistants (MCP)</b></summary>

Claude Code or Codex starts Carry's local server, `carry.mcp_server`, with three tools: `carry_recall` (search with citations), `carry_catalog` (list files) and `carry_status` (index health). If you allow corrections for that assistant, it also gets `carry_propose` (draft a correction; only you can accept it). The `carry-recall` subagent (Claude Sonnet in Claude Code) keeps the passages that answer. Retrieved text is data, not instructions.

</details>

<details>
<summary><b>For developers</b></summary>

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/python -m unittest discover -s tests -q
```

Needs Python 3.11+. Search by meaning uses `llama-cpp-python` (llama.cpp, built from source at install, so Xcode Command Line Tools are needed; not installed on Windows, which uses Ollama or word search) and NumPy. Extra: `.[yaml]` (PyYAML). Notes are the original data; the index can always be rebuilt.

</details>

<details>
<summary><b>Limits</b></summary>

- **Pilot:** the app is built on your Mac and ad-hoc signed; there is no notarized release.
- Works with Claude Code and Codex. Reads `.md` files only.
- Search and chat drafts can miss or misread things; check drafts against their sources.
- Secret masking is best effort.

</details>

<sub>Made by **Berke Tevik**. Team knowledge base conventions by **İsmail Aykut**.</sub>
