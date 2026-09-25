# Current addendum: GitHub internal pilot (2026-09-14)

Read [PILOT.md](PILOT.md). Managed GitHub snapshots, background maintenance,
model downloads, multilingual prompt contracts, real cross-encoder ranking,
source exclusions and file inspection, commit-pinned citations and native setup
are implemented. Earlier sections below are historical delivery records.

The user chose an unsigned internal pilot. Developer ID signing/notarization is
not a gate for this iteration. New-user and second-machine testing remain
explicit external acceptance checks. No repository writes or pilot invitations
were performed. Runtime/package validation and fixed synthetic retrieval results
are recorded separately; do not equate retrieval scores with final answer accuracy.

---

# Current addendum: local semantic activation (2026-09-10)

The personal workspace now uses the existing local Ollama provider with
`nomic-embed-text`, replacing its hashing configuration. This is a configuration
activation, not a new retrieval implementation. Original source permissions and
other settings were preserved; the previous workspace configuration was backed
up before changing it.

- Rebuilt 554 files / 7,089 chunks; semantic index fresh, about 36 MB. First build
  plus two queries took 314.5 seconds on this Mac with the model already installed.
- Actual project-configured, bundled MCP command returned `semantic: true`,
  `mode: hybrid`, cited evidence and no degradation warnings. Three real-corpus
  query samples took 557–613 ms; this is not a latency benchmark.
- 71 storage/recall/MCP regression tests passed in this pass. Four synthetic
  English/Turkish paraphrases found an expected topic in the first three results;
  only two ranked it first. Broader ranking quality is still unverified.
- Capture and automatic index refresh remain off. Legacy `legacy personal recall` and the
  vault's instructions remain installed; duplicate tool selection is not fixed
  by enabling semantics. No publish or push.

See [semantic-setup.md](semantic-setup.md) and the sanitized
[verification report](semantic-verification.json). Earlier setup statements
below describe their dated delivery runs, not this updated personal setup.

---

# Current addendum: C06 local app and packaging spike (2026-09-10)

Read [C06.md](C06.md). C01–C05 changes are preserved. **C06 implementation is
present; its pristine-machine and interactive acceptance gates remain open.**

- Native SwiftUI app, bundled standalone Python, private-pipe supervised worker.
  Local final artifact: `build/c06/Carry.app`. This is an arm64 development
  build, not a signed/notarized pilot release.
- Source/workspace setup, exact client-settings preview/apply/rollback, opt-in
  prompt capture and proposals, per-client pause, separate MCP/capture/index
  states, activity and revision/token-checked graphical review.
- Settings changes are journaled and compared before writing. Rollback refuses
  later edits. Normal personal client configurations were not installed/modified.
- 19 C06 tests; full suite 166: all pass in the project environment, 165 pass plus
  one optional SDK skip on standard Python. Bundled hook/MCP commands passed
  synthetic-payload replay/accept/pause checks for both supported clients.
- Native view/bridge tests rendered SwiftUI screens and accepted a synthetic
  correction via the actual app model/worker. Worker restart and local MCP passed.
  Computer-use's native pipe did not start, so mouse/keyboard navigation, folder
  panels and actual editor-opening remain unverified interactively.
- App about 49 MB; worker snapshot p50 2.34 ms versus fresh Python-process p50
  202.53 ms. Optional model 274 MB, reported resident model size about 370 MB.
  Raw packaging and native startup measurements are linked in C06.md.
- Relocated app works outside the checkout without PATH Python or optional Python
  packages. **This was the development Mac, not a pristine macOS machine.** Model
  download duration, minimum-OS installation and Gatekeeper still need validation.
- Next: close the two C06 external acceptance gaps, then C07 signed pilot build,
  disconnect/export and user-tested setup. No publication, push or pilot invites.

---

# Current addendum: C05 reviewed lifecycle (2026-09-10)

Read [C05.md](C05.md) for commands, acceptance semantics and remaining limits.
The C04 changes below are preserved. No publication, push or personal client
configuration installation was performed.

- Optional, per-client MCP `carry_propose`; captured events refine their one
  existing draft. Client/manual synthesis stays unaccepted until local review.
- CLI proposal activity, source inspection, unified diff, token/revision checked
  accept/reject, and automatic reindex with explicit `accepted_index_pending`.
- Corrections target a source/record/revision/content snapshot. One accepted
  Markdown file commits the correction; the target remains immutable. Effective
  current/history pointers are derived from accepted links, including imports.
- Pending and rejected proposals cannot change default recall. History is
  explicit. A stale index cannot reintroduce an accepted correction's old answer.
- Clean wheel installed without optional dependencies; installed CLI review and
  both client-bound MCP correction/history flows passed outside the checkout.
- 32 C05 tests added; full suite: 147 tests, all passing with project dependencies;
  standard Python: 146 pass and one optional SDK skip.
- Native Claude/Codex events plus deterministic synthesis over fresh MCP sessions
  verified pending, accept, correction, replay and history. This is protocol
  validation, not a model answer-quality evaluation. See the sanitized evidence
  in `c05-native-verification.json`.
- Remaining: model-driven synthesis/quality evaluation, paragraph-level targets,
  and the C06 packaging/minimal macOS app spike. Legacy low-level `supersede`
  retains its C02 API; reviewed CLI corrections now always create a draft first.

---

# Current addendum: C04 and independent revalidation (2026-09-10)

Read [C04.md](C04.md) for setup, storage, diagnostics and current limits. The
original handover below is historical; its results were rechecked, not assumed.

- Before changes: 80 tests rerun in both environments (79 pass + SDK skip on
  standard Python, 80 pass in the project environment).
- Independent checks exposed six gaps in C02/C03; all are reproduced and fixed.
  The exact findings and regression tests are described in C04.md.
- C04: native Claude Code and Codex adapters, explicit whole-prompt opt-in,
  per-client pause, one raw Markdown event and linked draft, safe receipts,
  bounded locking, replay recovery and actionable unsupported states.
- Actual Claude Code 2.1.267 and Codex CLI 0.153.4 emitted native events, persisted
  exactly once on replay, and stopped persisting prompts when paused. Explicit
  acceptance followed by fresh MCP processes returned cited native records.
- No client settings were installed into the owner's normal configuration. Only
  isolated synthetic connection-test settings were used. No publication or push.
- The automatic review/synthesis UI, correction-target policy, model-driven
  cross-client quality evaluation and packaging spike remain open for C05/C06.
- Updated full suite: 115 tests; standard Python passes 114 with one optional SDK
  skip; project Python passes all 115. Wheel built offline and installed into a
  clean environment; installed CLI demo and cited recall verified outside the
  checkout. The installed hook/MCP checks are included in the final local audit.

---

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
