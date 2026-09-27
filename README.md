# Carry

Portable local memory for coding agents. Your context goes with you: record a
decision in one client, retrieve it with a citation in another, correct it, and
keep the historical record.

Markdown files are canonical. The search index is derived state that can be
deleted and rebuilt at any time. Client models receive requested evidence.
If you opt into TypeSafe Jev, the question and up to 32 candidate passages also
go to TypeSafe for relevance judging; secret masking is best effort.

**Status: internal pilot under verification.** Local folders and read-only GitHub
repositories, background synchronization/indexing, multilingual search with an
optional relevance model, and a native macOS interface. Existing capture/review
features remain opt-in. See [pilot setup and limits](docs/PILOT.md).

The full Apple silicon package includes Python, the official GitHub CLI, Ollama
and Python search dependencies. Models download on explicit setup. This pilot
is not Developer ID signed/notarized; second-machine acceptance is still a user
pilot check, not a completed automated test.

## Install and set up (recommended)

```sh
gh auth login && gh auth setup-git        # the repository is private for now
uv tool install "git+https://github.com/<owner>/carry"   # the repository you were given
carry setup
```

`carry setup` is a terminal wizard: it creates the workspace (warning when the
folder is synced by iCloud), a new vault from the template or an existing Markdown
folder as a read-only source, an optional GitHub team repository, the search
setting (no model on the device by default; TypeSafe Jev with a key stored in the
Keychain; or semantic search through Ollama), the Claude Code / Codex wiring, and
the index. `carry setup --yes --vault ~/Vault` takes every default. Update with
`uv tool upgrade carry`. Claude Code can run these steps for you: ask it to install
Carry from this README.

## Install from a checkout

```sh
uv venv --python 3.13 .venv          # or: python3 -m venv .venv
.venv/bin/python -m pip install -e . # core has no required dependencies
```

Optional extras: `.[embed]` adds numpy (faster vector search), `.[yaml]` adds
PyYAML (broader frontmatter support), `.[rerank]` enables the local cross-encoder runtime. The core runs without all of them and reports the resulting degradation.

Local semantic embeddings currently come from an Ollama model
(`nomic-embed-text` by default). Without it, retrieval falls back to a lexical
provider and says so in every status payload. The app starts with offline keyword search and can install the multilingual
model and relevance model, then rebuild its index in the background.

## Quick start

```sh
# Install the frozen synthetic corpus and index it
carry --workspace ~/.carry/demo demo --into ~/carry-demo --embedding ollama

# What is connected, what is fresh, what is degraded
carry --workspace ~/.carry/demo status --probe

# Cited evidence, with record identity and revision
carry --workspace ~/.carry/demo recall "When is the Cedar pilot delivery?"
```

Point it at your own folder instead:

```sh
carry --workspace ~/.carry/personal init --source notes=/path/to/markdown \
    --writable-source records=/path/to/markdown-writes
carry --workspace ~/.carry/personal index
```

A source is read-only unless declared writable. Carry writes records only into a
writable source, under its `carry/` subfolder, and never reorganises what is
already there.

## MCP

The server speaks newline-delimited JSON-RPC on stdin and stdout and needs no
SDK at runtime. Register it with any stdio MCP client:

```json
{
  "command": "/path/to/.venv/bin/carry-mcp",
  "env": { "CARRY_WORKSPACE": "/path/to/workspace" }
}
```

Tools:

- `carry_recall(query, source_ids?, budget?, include_history?)` returns passages
  with citation, record id, revision, state, and a machine-readable search state
  including index freshness and active degradations. Budgets are validated and
  clamped to the workspace configuration; a caller can lower a limit, never
  raise it.
- `carry_status(probe?)` returns integration and index status with no note
  contents and no credentials.

`carry_propose` is additionally available only for a server-bound client with
explicit workspace write opt-in. It creates/refines drafts; it cannot accept them.
Normal recall excludes drafts and history. Use `include_drafts` or
`include_history` explicitly when inspecting them.

Retrieved passages are data. The response says so, and asks the client model to
cite what it uses and to report insufficient evidence rather than guessing.

## Design rules

- **Markdown canonical, index disposable.** A rebuild reads files and writes
  only inside the workspace state directory.
- **Atomic publication.** Builds land in a side file and publish with one
  rename, so a failed or interrupted build leaves the previous index serving.
- **Containment.** Every path resolves inside its configured root; traversal and
  symlink escape are refused, and symlinks are never followed into the corpus.
- **Isolation.** All state hangs off an explicit workspace, so two workspaces on
  one machine cannot contaminate each other's results.
- **Identity without editing.** Records written by Carry carry their id, revision
  and state in frontmatter. Imported files are left untouched and tracked in a
  sidecar; a rename with identical content keeps its id, and an ambiguous move is
  flagged for reconciliation instead of guessed.
- **History over overwrite.** A correction writes a new revision, marks the
  previous one superseded and points it at its replacement. A write against a
  stale revision is refused.
- **Honest degradation.** Lexical-only retrieval, a stale index and unavailable
  providers are reported, never smoothed over. Query terms that appear nowhere in
  the corpus are listed next to the results.

## Limits

- Capture is disabled until explicitly configured with prompt opt-in and connected
  in the client. Only user prompts are supported. Captured drafts are verbatim
  review candidates; optional client synthesis refines them through `carry_propose`.
  Carry does not run an independent synthesis model.
- Graphical and CLI review use the same token-checked diff lifecycle. The local
  macOS app has not passed pristine-machine or interactive UI acceptance yet.
  Decision synthesis is supplied by the client or user.
- Secret masking is best effort. Common token shapes are caught; a novel format
  can pass through. Do not treat it as a guarantee.
- Search quality depends on the selected model and corpus. The default legacy
  Nomic model has weak cross-language recall; use Accurate multilingual search.
  See the fixed evaluation report for remaining misses.
- Local arm64 app packaging exists; no signed/notarized release or verified
  pristine-machine installation yet.
- Scope labels on sources are attribution, not authorization. Multi-user access
  control is out of scope.

## Measurements

Baseline on the synthetic corpus (8 files, 27 chunks), Python 3.13, Apple
silicon, local Ollama: full build 0.4 to 1.6 s, incremental rebuild with no
changes 0.005 s, in-process query p50 32 ms, one-shot process wall time p50
128 ms (interpreter startup included), warm query p50 17 ms. The lexical
provider builds in 0.012 s and answers in about 1 ms. Reproduce with
`python3 scripts/measure_baseline.py`; raw output in `docs/`.

These numbers are a baseline for setting budgets, not a promise. They come from
one machine, one small corpus and a warm model.

## Tests

```sh
python3 -m unittest discover -s tests -t tests
```

The core regression suite runs on the standard library alone; the test that drives the
server with the official MCP SDK skips when the SDK is absent. See
[docs/acceptance.md](docs/acceptance.md) for the mapping from specification
scenarios to tests.
