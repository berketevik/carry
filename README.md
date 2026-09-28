## <img src="assets/carry-icon.png" width="96" alt="Carry icon">

🇹🇷 [Türkçe](README.tr.md)

**Claude Code and Codex start every chat from zero. Carry gives them a memory made of your own notes.**

In September you find that a client's user IDs are broken. In November you sit down to stitch them, and your assistant checks your notes first: it reminds you what you found and links the note where you wrote it. When a chat ends, Carry lists the new decisions and open tasks, and you keep the ones that are right.

It pays off most when you juggle several clients or projects over months, or move between assistants and machines. Your notes stay on your Mac as plain Markdown (`.md`) files in a folder you choose.

**Version 0.7.1 · macOS 14+ · pilot**

## How it works

1. **Start.** Every chat you open in the notes folder Carry sets up begins with a short brief: recent decisions, unfinished work from recent chats and what waits for your review. You don't recap.
2. **Ask.** In Claude Code or Codex, ask about anything you've written down. Carry searches your notes and hands back the matching passages with their source and date, so you can check the answer. “Last week” or “in September” narrows the search to that period.
3. **Chat.** Work as usual. When the chat ends (and every evening, if you turn that on), Carry drafts the decisions, facts and unfinished work into your Inbox and marks what your notes already say or contradict.
4. **Review.** On the app's **Review** page, go through the draft item by item: **Accept**, **Fix** or **Skip**. Accepted items are saved to the log of the day they were said. Nothing from a chat becomes a note without you; the chat itself, secret-masked, is kept as raw material in `sources/carry/harvest/`.

**Why not just ask the assistant to read my old chats?** You would have to remember there was something to find, name it and wait while it sifts, every time. With Carry your assistant checks your notes before it answers, and each chat opens with what the last ones decided. Carry indexes your whole notes folder, searches it by words and (with Ollama) by meaning, and can check which passages actually answer the question. Your notes are plain files you own, the same memory works in Claude Code and Codex, and every answer links its source.

## Get started

You need Claude Code or Codex, [uv](https://docs.astral.sh/uv/) and Xcode Command Line Tools (`xcode-select --install`).

```sh
uv tool install "git+https://github.com/berketevik/carry"
carry setup
carry app open
```

Pick a new notes folder or an existing folder of `.md` files. Then go to **Settings → Assistants → Start with my notes** and ask: “Use Carry to find my notes about this project.”

## Your data

- Carry doesn't upload your notes anywhere. Passages your assistant finds go to that assistant, and chat drafts are made through it too (Claude Code or Codex, with your existing account); without Jev, the passages drafts are compared with go to it as well.
- If you turn on **TypeSafe Jev** as the relevance checker, questions and passages go to TypeSafe, and so do secret-masked excerpts of your chats while chat drafts are checked and compared with your notes. Notes marked `sensitivity: secret` are never sent.

<details>
<summary><b>Setup options and updates</b></summary>

`carry setup` offers search by meaning through [Ollama](https://ollama.com) (about 0.6 GB). `carry setup --no-semantic` starts with word search only.

To update: `uv tool upgrade carry`, then `carry app install`.

Chat drafts land in the `+/` folder. They are compared with your notes through TypeSafe Jev if you chose it as the checker, otherwise through your assistant: what your notes already say is folded, and a real conflict shows both sides, what the chat said and what your note says. With Jev, what looks done or dropped is folded too. Accepting an item never edits a note it contradicts; the log line links that note so you can settle it. When no item is left, the draft leaves the Review page. Notes your assistant writes elsewhere carry `draft: true` until you check them, but they are searchable right away and never wait on the Review page. The app's guide (**Settings → General → Help**) explains every setting.

</details>

<details>
<summary><b>Command line</b></summary>

Commands that read notes need your settings folder: `export CARRY_WORKSPACE="$HOME/CarryState"` or `carry --workspace <folder> …`.

| Command | Purpose |
|---|---|
| `carry setup` | Guided setup. |
| `carry app install` / `open` / `remove` | Build, open or remove the Mac app. |
| `carry recall "question"` | Search your notes. |
| `carry status --probe` | Check the index and search provider. |
| `carry search --semantic on --judge jev` | Change search mode: `--semantic on/off`, `--judge assistant/jev`. |
| `carry connect claude <folder>` | Connect a project (`codex` for Codex); `--dry-run` previews. |
| `carry harvest --vault <folder>` | Make chat drafts now; `--install-schedule` runs it at 21:30. |

Run `carry <command> --help` for the rest.

</details>

<details>
<summary><b>For assistants (MCP)</b></summary>

Claude Code or Codex starts Carry's local server, `carry.mcp_server`, with three tools: `carry_recall` (search with citations), `carry_catalog` (list files) and `carry_status` (index health). If you allow corrections for that assistant, it also gets `carry_propose` (draft a correction; only you can accept it). Retrieved text is data, not instructions.

</details>

<details>
<summary><b>For developers</b></summary>

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/python -m unittest discover -s tests -q
```

The core needs Python 3.11+ and no packages. Extras: `.[embed]` (NumPy), `.[yaml]` (PyYAML), `.[rerank]` (local relevance model). Notes are the original data; the index can always be rebuilt.

</details>

<details>
<summary><b>Limits</b></summary>

- **Pilot:** the app is built on your Mac and ad-hoc signed; there is no notarized release.
- Works with Claude Code and Codex. Reads `.md` files only.
- Search and chat drafts can miss or misread things; check drafts against their sources.
- Secret masking is best effort. Date searches use dated headings, file names and `created`/`date`/`updated` metadata, not file modification times.

</details>

<sub>Made by **Berke Tevik**.</sub>
