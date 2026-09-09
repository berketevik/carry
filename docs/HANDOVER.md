# Carry handover: C01 to C03

Written 2026-09-09. Everything below is verified on this machine, not planned.
The specification this implements is the Carry MVP specification note in the
owner's vault (`log/2026-09-09 — Carry MVP Specification.md`); this document is
the engineering handover for the first three backlog items.

## State in one paragraph

Carry is a local, private-alpha memory layer for coding agents. Markdown files
are canonical, the SQLite index is derived and disposable, and an MCP server
exposes read tools over stdio. C01, C02 and C03 are complete and tested: a
standalone package with a frozen synthetic corpus, workspace-scoped sources with
Markdown record storage and a derived index, and recall plus status over a CLI
and an MCP server. Nothing is captured automatically yet (C04), there is no
review interface (C05), and there is no packaged installer (C06). Nothing has
been published or pushed, no personal data was copied, and no name availability
or licence check has been done.

- Location: `~/Documents/carry`, its own git repository, 3 commits, no remote.
- Environment: `.venv` built with `uv` and Python 3.13 (Homebrew Python 3.14
  cannot bootstrap pip into a venv on this machine).
- Tests: 80, all passing, in two environments. Details below.

## How to pick it up

```sh
cd ~/Documents/carry
python3 -m unittest discover -s tests -t tests        # 80 tests, standard library only
.venv/bin/python -m unittest discover -s tests -t tests  # same suite with numpy, PyYAML, MCP SDK

# End to end on the synthetic corpus, using the local Ollama model
PYTHONPATH=src python3 -m carry.cli --workspace /tmp/ws demo --into /tmp/cedar --embedding ollama
PYTHONPATH=src python3 -m carry.cli --workspace /tmp/ws status --probe
PYTHONPATH=src python3 -m carry.cli --workspace /tmp/ws recall "When is the Cedar pilot delivery?"

python3 scripts/measure_baseline.py                   # reproduce the measurements
```

Register the MCP server with any stdio client:

```json
{ "command": "/path/to/.venv/bin/carry-mcp",
  "env": { "CARRY_WORKSPACE": "/path/to/workspace" } }
```

## C01: standalone project, package skeleton, synthetic fixture

**Done when:** package imports and tests run outside the personal vault; no
personal paths or private content included. **Met.**

- Package at `src/carry/`, 14 modules, no required dependencies. Optional
  extras: `[embed]` numpy, `[yaml]` PyYAML, `[rerank]` reserved. Both optional
  paths have working fallbacks (a built-in frontmatter parser, a pure-Python
  cosine ranking), and a test asserts the two vector backends rank identically.
- Synthetic corpus at `src/carry/fixtures/cedar/`: 8 invented notes. One states
  the delivery date once (October 15, 2026), a decoy project carries a different
  date (November 3, 2026), and no file mentions a budget or any monetary amount,
  which is what makes the no-evidence scenario testable. The corpus ships as
  package data, so a demo works from an installed wheel.
- An automated scan fails the suite on personal paths, vault references, or the
  old "derive the corpus root from my own location" pattern, so the extraction
  cannot quietly grow a dependency back on the vault it came from.

**Tests: 9.** `PackageTest` (5): imports and version, both module entry points
run, personal-reference scan, no root derived from package location, core
imports with optional dependencies removed. `FixtureTest` (4): corpus present
and frozen, delivery date stated exactly once, no budget or monetary amount,
decoy has a different date.

## C02: configured sources, Markdown storage, derived index

**Done when:** two independent test workspaces cannot contaminate each other's
results; a rebuild preserves the original files. **Met.**

- Every entry point takes an explicit `Workspace`. There are no module-level
  roots, no implicit default folder and no path derived from the package
  location. All derived state (index, status, sidecar, event ledger) lives in
  the workspace state directory, which validation refuses to place inside a
  source root.
- A source declares an id, a root, read or write capability and a scope label.
  Scope is attribution, not authorization. Validation rejects duplicate ids,
  nested roots and unwritable write destinations.
- Paths resolve inside their root; traversal, absolute paths and symlink escape
  are refused, and the corpus walk never follows symlinks, so linked content
  cannot enter the index.
- Records written by Carry carry identity in frontmatter: record id, revision,
  state, current pointer, event id, author and surface. Writes go only into a
  writable source, under its `carry/` subfolder. Secrets are masked on write as
  a documented best effort, not a guarantee.
- Imported files are never edited. Their identity lives in a sidecar keyed by
  source and path; a rename with identical content carries the id over, and an
  ambiguous move is recorded for reconciliation instead of guessed.
- The index builds incrementally (unchanged files embed nothing, an appended
  file reuses its unchanged windows), refuses a corpus that changed mid-build,
  and publishes with one atomic rename. A failed or interrupted build leaves the
  previous index byte for byte and reports `failed` with an error type only, no
  message text. A concurrent build is refused through a per-workspace advisory
  lock rather than interleaved.
- A correction writes a new revision, marks the previous one superseded, points
  it at its replacement and keeps it on disk. A write against a stale revision,
  or against an already superseded record, is refused.

**Tests: 36.** `ConfigurationTest` (6), `PathContainmentTest` (4),
`IsolationTest` (4: neither workspace returns the other's corpus, per-workspace
state files, rebuilding one leaves the other untouched, an empty scope reports
no evidence), `RebuildSafetyTest` (10: file preservation, no state in a source
root, no-op rebuild embeds nothing, append reuse, deletion, byte-for-byte
preservation on failure, stale reporting after interruption, concurrent build
refusal, no leftover build file, empty workspace answers no evidence),
`SidecarIdentityTest` (3), `RecordStorageTest` (9).

## C03: recall and status over CLI and MCP

**Done when:** a fresh MCP client gets cited Cedar evidence and explicit
no-evidence and failure states. **Met.**

- `recall(query, source_ids?, budget?, include_history?)` fuses a lexical FTS5
  branch and a vector branch with reciprocal rank fusion, then deduplicates,
  caps per document and respects a character budget. Each passage returns its
  citation, source id, path, record id, revision, state and current flag.
- Budgets are validated and clamped to the workspace configuration: a caller can
  lower a limit, never raise it. Unknown budget keys, malformed values, unknown
  source ids and empty queries are rejected as invalid requests rather than
  silently ignored.
- Degradation is reported, not smoothed: lexical-only retrieval, a stale or
  unavailable index, an unavailable provider, and the query terms that appear
  nowhere in the corpus. Superseded revisions are hidden unless history is
  requested explicitly.
- The MCP server is a thin adapter over the core: newline-delimited JSON-RPC on
  the standard library, so the alpha installs with no dependency tree. It
  exposes `carry_recall` and `carry_status`, negotiates the protocol version,
  answers notifications with silence, unknown methods with -32601 and a
  malformed line with -32700 without dying. Every recall response carries a
  machine-readable status next to a grounding preamble that tells the client
  model to treat passages as data, cite what it uses and report insufficient
  evidence rather than guessing.
- `carry_status` reports sources, index freshness, provider and active
  degradations with no note contents and no credentials. It still answers when
  the index is missing, which is when a user most needs it.

**Tests: 35.** `CitedEvidenceTest` (8), `BudgetTest` (5), `StateTest` (4),
`VectorBackendTest` (1), `HandshakeTest` (5), `EvidenceTest` (8),
`FailureStateTest` (3), `SDKClientTest` (1, skipped when the official MCP SDK is
absent).

## Test results

| Suite | Tests | Asserts |
|---|---|---|
| C01 package and fixture | 9 | standalone import, no personal content, frozen corpus |
| C02 configuration, paths, isolation, rebuild, sidecar, records | 36 | contamination, file preservation, atomic failure, identity, revisions |
| C03 recall, budgets, state, MCP transport, MCP evidence, failures, SDK client | 35 | citations, explicit states, capped budgets, protocol correctness |
| **Total** | **80** | all passing |

Verified in three ways:

1. Bare Python 3.14, no optional packages: 80 tests, 79 pass and the SDK test
   skips. This is the dependency-free path.
2. Python 3.13 with numpy, PyYAML and the official MCP SDK: 80 pass, including a
   real SDK client driving the server through initialize, list_tools and four
   tool calls.
3. Built wheel installed into a clean virtual environment and driven through the
   installed console scripts: demo, index against local Ollama, a recall with a
   citation, and the MCP entry point returning the dated passage. The first
   attempt at this exposed a packaging gap (the corpus was missing from the
   wheel) that a source checkout hides.

Tests stub nothing but the embedding provider: real SQLite, real filesystem,
real subprocesses. The deterministic hashing provider used in tests is lexical,
not semantic, and every status payload that exposes it says so.

## Measurements

Synthetic corpus, 8 files and 27 chunks, Python 3.13, Apple silicon, local
Ollama with `nomic-embed-text`:

| Provider | Full build | No-op rebuild | Query p50 in process | One-shot process wall p50 | Warm p50 / p95 |
|---|---|---|---|---|---|
| ollama (semantic) | 0.39 s | 0.005 s | 32 ms | 128 ms | 17 / 23 ms |
| hashing (lexical) | 0.012 s | 0.004 s | 2 ms | 89 ms | 1 / 1 ms |

Reproduce with `python3 scripts/measure_baseline.py`; raw output in
`docs/baseline-2026-09-09.json`. This is a baseline for setting budgets, not a
promise: one machine, one small corpus, a warm model. Model download size,
cold-start on a machine with no model, and memory under a real corpus are
unmeasured and belong to the packaging spike (C06).

## Decisions taken while implementing

1. **MCP without the SDK.** The stdio server is hand-written JSON-RPC on the
   standard library, so the alpha has zero runtime dependencies. Protocol
   correctness is verified against the official SDK client in a test that skips
   when the SDK is absent.
2. **Optional dependencies with real fallbacks** rather than hard requirements,
   so a clean machine gets a working core and an honest degradation message.
3. **Two embedding providers behind one interface.** Ollama is the semantic
   provider; the hashing provider is deterministic, offline and declared
   non-semantic everywhere it appears.
4. **An empty workspace answers no-evidence, not unavailable.** Index usability
   is a schema question, not a row count, so "no notes yet" and "index broken"
   stopped being the same state.
5. **A superseded record refuses further correction.** Without this a second
   correction against the same revision number forked history silently.
6. **The fixture README stays out of the indexed root**, because a document
   about the corpus changed what the acceptance queries matched.
7. **A replayed event reports the record's current state**, re-read from disk
   rather than from the ledger snapshot.

## Open items for whoever continues

1. **A record that corrects an imported note does not outrank it.** Found by
   running the lifecycle by hand on a mixed corpus: the accepted correction
   (October 22) came back below the older imported statement (October 15).
   Retrieval ranks by relevance, and the current-version pointer only covers
   revisions of the same record. The specification's scenarios keep decisions
   inside Carry records, which is why the suite passed. Three options are
   written out in `docs/acceptance.md`: weight state and recency in the fusion,
   let a correction name its target and demote that passage, or leave ranking
   alone and report the conflict in diagnostics. The second is the precise fix,
   the third the honest stopgap. **This is a product decision and it is open.**
2. **Retrieval quality is unevaluated.** The suite checks contracts and states,
   not ranking accuracy against held-out questions. Budgets should be set from a
   baseline run before any held-out evaluation, and the evaluation must not be
   tuned on its own cases.
3. **Cross-language recall is weak without a reranker.** A Turkish question
   against the English corpus ranked the right note third. The rerank path is
   declared and unimplemented.
4. **Capture does not exist.** Status reports capture as `not_configured`
   because that is the truth. C04 is the next step and it turns Cedar scenarios
   1 and 8 from partial into covered.
5. **Masking is best effort.** Common credential shapes are caught; a novel
   format passes through. The audit of these limits is a release task.
6. **Packaging is untouched.** No installer, no signing, no clean-machine
   dependency path. A DMG would not remove the model and runtime dependency.

## Boundaries respected in this pass

No publication, no push, no remote, no pilot invitations, no external system
touched, no purchase, no name availability or licence check, and no personal
notes copied into the project. The owner's vault was read for reuse only and
still reports a healthy index. Scenario coverage per specification item is in
`docs/acceptance.md`.
