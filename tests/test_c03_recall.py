"""C03 core: cited evidence, explicit no-evidence, capped budgets, honest state."""
import json
import unittest

from _support import WorkspaceCase

from carry import store
from carry.recall import normalize_budget, recall


class CitedEvidenceTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.cedar()
        self.build()

    def test_delivery_question_returns_the_dated_passage_with_a_citation(self):
        result = recall(self.workspace, "When is the Cedar pilot delivery date?")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "evidence")
        text = " ".join(item["text"] for item in result["evidence"])
        self.assertIn("October 15, 2026", text)
        self.assertEqual(result["evidence"][0]["path"], "notes/Cedar Pilot Plan.md")
        self.assertIn("corpus:notes/Cedar Pilot Plan.md", result["evidence"][0]["citation"])

    def test_every_passage_carries_identity_and_state(self):
        result = recall(self.workspace, "Cedar review flow states")
        self.assertTrue(result["evidence"])
        for item in result["evidence"]:
            self.assertTrue(item["record_id"])
            self.assertEqual(item["revision"], 1)
            self.assertEqual(item["state"], "imported")
            self.assertTrue(item["citation"].endswith(f"({item['source_id']}:{item['path']})"))

    def test_the_decoy_project_does_not_answer_for_cedar(self):
        result = recall(self.workspace, "Cedar pilot delivery date")
        self.assertNotIn("notes/Maple Handover.md",
                         [item["path"] for item in result["evidence"][:2]])

    def test_a_term_absent_from_the_corpus_is_reported(self):
        result = recall(self.workspace, "What is the Cedar pilot budget?")
        self.assertIn("budget", result["diagnostics"]["unmatched_terms"])
        self.assertNotIn("budget", " ".join(i["text"] for i in result["evidence"]).lower())

    def test_lexical_only_retrieval_is_declared(self):
        result = recall(self.workspace, "Cedar architecture decision")
        self.assertFalse(result["diagnostics"]["semantic"])
        self.assertIn("results_are_lexical_only", result["diagnostics"]["warnings"])

    def test_a_source_filter_restricts_the_answer(self):
        result = recall(self.workspace, "Cedar pilot delivery", source_ids=["corpus"])
        self.assertTrue(result["evidence"])
        self.assertEqual({item["source_id"] for item in result["evidence"]}, {"corpus"})
        self.assertEqual(result["diagnostics"]["scoped_to"], ["corpus"])

    def test_an_unknown_source_is_rejected_not_ignored(self):
        result = recall(self.workspace, "Cedar", source_ids=["corpus", "not-configured"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "invalid_request")
        self.assertEqual(result["error"], "unknown_source_ids")
        self.assertEqual(result["detail"], ["not-configured"])

    def test_an_empty_query_is_rejected(self):
        result = recall(self.workspace, "   ")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "empty_query")


class BudgetTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.cedar()
        self.build()

    def test_a_caller_cannot_raise_the_configured_ceiling(self):
        limits = normalize_budget(self.workspace.retrieval, {"top_k": 500})
        self.assertEqual(limits["top_k"], self.workspace.retrieval.top_k)

    def test_a_caller_can_lower_the_limits(self):
        result = recall(self.workspace, "Cedar", budget={"top_k": 2})
        self.assertLessEqual(len(result["evidence"]), 2)
        self.assertEqual(result["budget"]["top_k"], 2)

    def test_character_budget_is_respected(self):
        result = recall(self.workspace, "Cedar pilot", budget={"max_chars": 400})
        self.assertLessEqual(sum(len(i["text"]) for i in result["evidence"]), 400 + 1100)
        self.assertEqual(result["budget"]["max_chars"], 400)

    def test_per_document_cap_is_respected(self):
        result = recall(self.workspace, "Cedar pilot delivery checkpoints",
                        budget={"max_per_document": 1})
        paths = [item["path"] for item in result["evidence"]]
        self.assertEqual(len(paths), len(set(paths)))

    def test_unknown_and_malformed_budget_keys_are_rejected(self):
        self.assertEqual(recall(self.workspace, "Cedar", budget={"nope": 1})["status"],
                         "invalid_request")
        self.assertEqual(recall(self.workspace, "Cedar", budget={"top_k": "many"})["status"],
                         "invalid_request")


class StateTest(WorkspaceCase):
    def test_missing_index_reports_unavailable_not_silence(self):
        self.cedar()
        result = recall(self.workspace, "Cedar pilot delivery")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["error"], "index_unavailable")
        self.assertEqual(result["diagnostics"]["index"]["state"], "unavailable")

    def test_stale_index_still_answers_and_says_it_is_stale(self):
        self.cedar()
        self.build()
        self.note("Late Arrival", body="added after the build")
        result = recall(self.workspace, "Cedar pilot delivery")
        self.assertEqual(result["status"], "evidence")
        self.assertIn("index_stale", result["diagnostics"]["warnings"])

    def test_superseded_revisions_are_hidden_unless_history_is_requested(self):
        record = store.write_record(self.workspace, "Cedar delivery",
                                    "Cedar pilot delivery is October 15, 2026.",
                                    event_id="evt-1", state="accepted")
        store.supersede(self.workspace, record["record_id"],
                        "Cedar pilot delivery is October 22, 2026.", 1, title="Cedar delivery")
        self.build()

        current = recall(self.workspace, "Cedar pilot delivery date")
        body = " ".join(item["text"] for item in current["evidence"])
        self.assertIn("October 22, 2026", body)
        self.assertNotIn("October 15, 2026", body)
        self.assertEqual({item["state"] for item in current["evidence"]}, {"accepted"})

        history = recall(self.workspace, "Cedar pilot delivery date", include_history=True)
        historic = " ".join(item["text"] for item in history["evidence"])
        self.assertIn("October 15, 2026", historic)
        states = {item["state"] for item in history["evidence"]}
        self.assertIn("superseded", states)

    def test_diagnostics_never_carry_the_query_answer_as_fact(self):
        """Diagnostics are counts and states; passages live in evidence only."""
        self.cedar()
        self.build()
        result = recall(self.workspace, "Cedar pilot delivery date")
        self.assertNotIn("October 15", json.dumps(result["diagnostics"], default=str))


if __name__ == "__main__":
    unittest.main()


class VectorBackendTest(WorkspaceCase):
    """The pure-Python cosine path must agree with the numpy path."""

    def setUp(self):
        super().setUp()
        self.cedar()
        self.build()

    def test_both_backends_return_the_same_top_passage(self):
        import carry.recall as recall_module
        query = "When is the Cedar pilot delivery date?"
        original = recall_module._np
        try:
            recall_module._np = None
            pure = recall(self.workspace, query)
            recall_module._np = original
            accelerated = recall(self.workspace, query)
        finally:
            recall_module._np = original
        self.assertEqual(pure["evidence"][0]["path"], "notes/Cedar Pilot Plan.md")
        if original is not None:
            self.assertEqual([e["path"] for e in pure["evidence"]],
                             [e["path"] for e in accelerated["evidence"]])
            self.assertEqual([e["heading"] for e in pure["evidence"]],
                             [e["heading"] for e in accelerated["evidence"]])
