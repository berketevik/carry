# Acceptance coverage

Updated 2026-09-10; see [independent revalidation and C04](C04.md), [C05](C05.md)
and the [C06 app spike](C06.md).

How the specification's criteria map to executable tests. Run everything with
`python3 -m unittest discover -s tests -t tests`.

## Milestone criteria

| ID | Done when | Covered by | State |
|---|---|---|---|
| C01 | Package imports and tests run outside the personal vault; no personal paths or private content included | `test_c01_package.PackageTest` (import, module entry points, personal-reference scan, no root derived from package location, core imports without optional dependencies), `test_c01_package.FixtureTest` | met |
| C02 | Two independent test workspaces cannot contaminate each other's results; rebuild preserves original files | `test_c02_storage.IsolationTest`, `test_c02_storage.RebuildSafetyTest`, `test_c02_storage.PathContainmentTest`, `test_c02_storage.SidecarIdentityTest`, `test_c02_storage.RecordStorageTest` | met |
| C03 | A fresh MCP client gets cited Cedar evidence and explicit no-evidence and failure states | `test_c03_mcp.HandshakeTest`, `test_c03_mcp.EvidenceTest`, `test_c03_mcp.FailureStateTest`, `test_c03_mcp_sdk.SDKClientTest`, `test_c03_recall` | met |
| C04 | Actual supported clients each persist a test event exactly once; unsupported configurations show actionable status | `test_c04_capture`, native-client receipts in `c04-native-verification.json`; both real clients also tested paused | met for documented CLI versions and user-prompt events |
| C05 | Cedar lifecycle scenarios, including concurrent-change refusal | `test_c05_lifecycle` (32 tests), native capture plus fresh client-bound MCP evidence in `c05-native-verification.json` | met for CLI/core/protocol workflow; model quality evaluation remains separate |
| C06 | Clean-machine dependency/size/startup measurements; working setup, status, activity and review screens | `test_c06_desktop` (19 tests), native view/bridge tests and relocated-bundle measurements in C06.md | implementation present; pristine-machine and interactive acceptance open |

## Cedar scenarios

The eight scenarios are covered at the storage, CLI and MCP contract level.
Native capture was exercised in both clients. Model-driven synthesis and final
answer quality remain a separate evaluation, not a unit-test claim.

| # | Scenario | State | Evidence |
|---|---|---|---|
| 1 | Record a decision in client A: one persisted event, one linked proposal, then explicit acceptance | met | Both native events refined the same linked draft through MCP; explicit diff review and acceptance then fresh MCP retrieval verified |
| 2 | Client B asks for the date and gets the accepted answer with source and revision | met at the protocol level | `EvidenceTest.test_a_fresh_client_gets_cited_cedar_evidence`, `SDKClientTest` and native capture followed by fresh MCP retrieval; model-driven cross-client answering remains an evaluation task |
| 3 | A pending proposal does not change the accepted answer and stays distinguishable | met in C05 | `test_pending_draft_never_changes_accepted_answer`, `test_drafts_do_not_starve_accepted_evidence_before_ranking`, two-client protocol cycle |
| 4 | Accept the correction: both clients read the new date, history still exposes the old one | met in C05 | `test_diff_token_acceptance_and_history`, `test_two_fresh_stdio_clients_observe_reviewed_correction`, native event cycle; current/history pointers derive from one accepted commit |
| 5 | Replaying an event writes no duplicate; a distinct event with identical wording stays distinct | met | `RecordStorageTest.test_replaying_an_event_does_not_write_a_second_record`, `test_a_distinct_event_with_identical_wording_is_a_distinct_record` |
| 6 | A question the corpus never answers ends in insufficient evidence, not an invented amount | met at the retrieval level | `CitedEvidenceTest.test_a_term_absent_from_the_corpus_is_reported`, `EvidenceTest.test_a_question_the_corpus_never_answers_is_visible_as_such`, `EvidenceTest.test_an_empty_scope_returns_an_explicit_no_evidence_state`. Retrieval reports unmatched query terms and a machine-readable status; the final refusal is the client model's, and the response instructs it explicitly |
| 7 | Interrupt indexing: the previous usable index survives and the state is explicitly stale or failed | met | `RebuildSafetyTest.test_failed_build_keeps_the_published_index_byte_for_byte`, `test_interrupted_build_keeps_serving_and_reports_stale`, `test_a_concurrent_build_is_refused_rather_than_interleaved` |
| 8 | Pause capture: no new prompt persistence from that client | met | Per-client pause checked before input read; actual Claude/Codex pause tests preserved record bytes |

## Explicit correction targets

C05 closes the earlier imported-note ranking gap **when the proposal names its
target**. An accepted target link hides that exact historical snapshot from
current recall without editing the source. An external target change becomes an
explicit conflict. `test_imported_target_is_replaced_without_editing_source` and
`test_accepted_target_edited_later_is_visible_as_a_conflict` cover these cases.

Targets currently replace whole records/notes. Similar wording without an explicit
target does not establish supersession. Automatic contradiction detection and
paragraph-level anchors are not implemented.

## Deliberate non-claims

- Retrieval quality is not evaluated here. The suite checks contracts and
  states, not ranking accuracy against held-out questions. That evaluation
  belongs with a frozen question set and stated budgets.
- The lexical hashing provider used in tests is deterministic and offline. It is
  not a semantic model, and every status payload that exposes it says so.
- Secret masking is asserted for common shapes only; the tests do not claim
  complete detection.

## C06 acceptance addendum (2026-09-10)

The app and local packaging spike are implemented; **the full C06 acceptance
condition is still open**. Nineteen new Python tests, native SwiftUI view/bridge
tests and relocated-bundle checks passed. A pristine macOS install and interactive
mouse/keyboard setup were not verified. See [C06 evidence and limits](C06.md).
The prior C01–C05 scenario evidence above is preserved.
