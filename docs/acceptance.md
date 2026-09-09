# Acceptance coverage

How the specification's criteria map to executable tests. Run everything with
`python3 -m unittest discover -s tests -t tests`.

## Milestone criteria

| ID | Done when | Covered by | State |
|---|---|---|---|
| C01 | Package imports and tests run outside the personal vault; no personal paths or private content included | `test_c01_package.PackageTest` (import, module entry points, personal-reference scan, no root derived from package location, core imports without optional dependencies), `test_c01_package.FixtureTest` | met |
| C02 | Two independent test workspaces cannot contaminate each other's results; rebuild preserves original files | `test_c02_storage.IsolationTest`, `test_c02_storage.RebuildSafetyTest`, `test_c02_storage.PathContainmentTest`, `test_c02_storage.SidecarIdentityTest`, `test_c02_storage.RecordStorageTest` | met |
| C03 | A fresh MCP client gets cited Cedar evidence and explicit no-evidence and failure states | `test_c03_mcp.HandshakeTest`, `test_c03_mcp.EvidenceTest`, `test_c03_mcp.FailureStateTest`, `test_c03_mcp_sdk.SDKClientTest`, `test_c03_recall` | met |

## Cedar scenarios

The specification lists eight scenarios. C01 to C03 cover the retrieval half;
the rest needs the capture adapters (C04) and the reviewed lifecycle (C05).

| # | Scenario | State | Evidence |
|---|---|---|---|
| 1 | Record a decision in client A: one persisted event, one linked proposal, then explicit acceptance | partial | Storage and idempotency exist (`RecordStorageTest`); no client capture adapter yet, so the "one persisted event" half is C04 |
| 2 | Client B asks for the date and gets the accepted answer with source and revision | met at the protocol level | `EvidenceTest.test_a_fresh_client_gets_cited_cedar_evidence`, `SDKClientTest`; a second real client is C04 |
| 3 | A pending proposal does not change the accepted answer and stays distinguishable | met in storage and retrieval | `StateTest.test_superseded_revisions_are_hidden_unless_history_is_requested`, `RecordStorageTest.test_correction_keeps_history_and_moves_the_current_pointer`; the review queue is C05 |
| 4 | Accept the correction: both clients read the new date, history still exposes the old one | met in storage and retrieval | same tests; `include_history` returns the superseded revision |
| 5 | Replaying an event writes no duplicate; a distinct event with identical wording stays distinct | met | `RecordStorageTest.test_replaying_an_event_does_not_write_a_second_record`, `test_a_distinct_event_with_identical_wording_is_a_distinct_record` |
| 6 | A question the corpus never answers ends in insufficient evidence, not an invented amount | met at the retrieval level | `CitedEvidenceTest.test_a_term_absent_from_the_corpus_is_reported`, `EvidenceTest.test_a_question_the_corpus_never_answers_is_visible_as_such`, `EvidenceTest.test_an_empty_scope_returns_an_explicit_no_evidence_state`. Retrieval reports unmatched query terms and a machine-readable status; the final refusal is the client model's, and the response instructs it explicitly |
| 7 | Interrupt indexing: the previous usable index survives and the state is explicitly stale or failed | met | `RebuildSafetyTest.test_failed_build_keeps_the_published_index_byte_for_byte`, `test_interrupted_build_keeps_serving_and_reports_stale`, `test_a_concurrent_build_is_refused_rather_than_interleaved` |
| 8 | Pause capture: no new prompt persistence from that client | not started | Needs C04; status currently reports capture as `not_configured` |

## Known gap: a record correcting an imported note

Retrieval ranks by relevance. The current-version pointer hides superseded
*revisions of the same record*, but nothing tells Carry that a record
contradicts a claim in an imported file. In a mixed corpus, an accepted
correction can therefore be returned below the older imported statement:

```text
records:  Cedar pilot delivery is October 22, 2026.   (rec_..., rev 2, accepted)
imported: Cedar pilot delivery is October 15, 2026.   (Cedar Pilot Plan.md)
query:    "Cedar pilot delivery date"  ->  imported passage ranks first
```

The specification's scenarios 3 and 4 pass because the decision there lives only
in Carry records, which is why the suite did not catch this. The product case is
unresolved and needs a policy decision before C05:

1. Weight state and recency in the fusion so an accepted record outranks an
   imported passage. Blunt: it also wins when the imported note is genuinely
   more relevant.
2. Let a correction name what it corrects (`carry_corrects: <source_id>:<path>`
   or a record id), then demote or annotate that passage at retrieval time. This
   matches the specification's requirement that a correction proposal carries a
   target, and is the precise answer.
3. Change no ranking and report the conflict in diagnostics, so the client model
   sees that an accepted record may supersede an imported passage.

Option 3 is the honest stopgap and option 2 the real fix. Neither is implemented.

## Deliberate non-claims

- Retrieval quality is not evaluated here. The suite checks contracts and
  states, not ranking accuracy against held-out questions. That evaluation
  belongs with a frozen question set and stated budgets.
- The lexical hashing provider used in tests is deterministic and offline. It is
  not a semantic model, and every status payload that exposes it says so.
- Secret masking is asserted for common shapes only; the tests do not claim
  complete detection.
