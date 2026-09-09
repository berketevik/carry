# Carry

Portable local memory for coding agents. Your context goes with you: record a
decision in one client, retrieve it with a citation in another, correct it, and
keep the historical record.

Markdown files are canonical. The search index is derived state that can be
deleted and rebuilt at any time. Nothing leaves the machine except the passages
a client model asks for.

**Status: private alpha, milestones C01 to C03.** Configured sources, Markdown
record storage, a derived hybrid index, a CLI and a stdio MCP server with recall
and status. Capture adapters, the review interface and packaging are not built
yet; see [Limits](#limits).

## Install

```sh
uv venv --python 3.13 .venv          # or: python3 -m venv .venv
.venv/bin/python -m pip install -e . # core has no required dependencies
```

Optional extras: `.[embed]` adds numpy (faster vector search), `.[yaml]` adds
PyYAML (broader frontmatter support), `.[rerank]` reserves the cross-encoder
path. The core runs without all of them and reports the resulting degradation.

Local semantic embeddings currently come from an Ollama model
(`nomic-embed-text` by default). Without it, retrieval falls back to a lexical
provider and says so in every status payload. Managing that dependency for a
non-technical install is an open packaging task, not a solved one.

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

- No capture adapters yet: nothing is recorded automatically from a client. The
  status payload reports capture as `not_configured` because that is the truth.
- No review interface. The draft, accept and correct operations exist in the CLI
  and the storage layer, not in a reviewed lifecycle with receipts.
- Secret masking is best effort. Common token shapes are caught; a novel format
  can pass through. Do not treat it as a guarantee.
- Cross-language recall is weak without a reranker: an English corpus queried in
  another language ranks poorly today.
- No packaging, signing or clean-machine install path.
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

79 tests. They run on the standard library alone; the test that drives the
server with the official MCP SDK skips when the SDK is absent. See
[docs/acceptance.md](docs/acceptance.md) for the mapping from specification
scenarios to tests.
