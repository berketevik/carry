"""`carry eval`: a labelled Markdown question table scored against recall, saved and replayed."""
import json

from _support import WorkspaceCase

from carry import evaluate

SET = """# Questions

| id | Question | Answerable | Expected | Note | Check |
|---|---|---|---|---|---|
| c1 | When is the Cedar pilot delivery date? | yes | [[Cedar Pilot Plan]] | date | [x] |
| c2 | Who owns the Maple handover? | partial | `corpus:notes/Maple Handover.md` | owner \\| team | [ ] |
| c3 | What is the zebra quarantine budget? | no | — | absent | [ ] |
| header | not a row | maybe | [[x]] | ignored | [ ] |
"""


class EvaluateTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.cedar()
        self.build()
        self.set_path = self.base / "set.md"
        self.set_path.write_text(SET, encoding="utf-8")

    def test_the_table_is_read_by_column_and_bad_rows_are_skipped(self):
        questions = evaluate.load_set(self.set_path)
        self.assertEqual([q["id"] for q in questions], ["c1", "c2", "c3"])
        self.assertEqual(questions[0]["expected"], [("*", "Cedar Pilot Plan")])
        self.assertEqual(questions[1]["expected"], [("corpus", "notes/Maple Handover.md")])
        self.assertTrue(questions[0]["confirmed"])
        self.assertFalse(questions[1]["confirmed"])

    def test_a_run_ranks_the_expected_file_and_counts_abstention(self):
        report = evaluate.run(self.workspace, evaluate.load_set(self.set_path))
        rows = {r["id"]: r for r in report["rows"]}
        self.assertEqual(rows["c1"]["rank"], 1)
        summary = report["summary"]
        self.assertEqual(summary["questions"], 3)
        self.assertEqual(summary["answerable"]["n"], 2)
        self.assertEqual(summary["abstain"]["n"], 1)
        self.assertGreater(summary["payload_mean"], 0)
        part_sum = sum(rows["c1"]["payload"][k] for k in ("grounding", "state", "metadata", "passages", "other"))
        self.assertEqual(part_sum, rows["c1"]["payload"]["total"])

    def test_a_saved_run_replays_to_the_same_scores(self):
        saved = self.base / "raw.json"
        questions = evaluate.load_set(self.set_path)
        first = evaluate.run(self.workspace, questions, save=saved)
        again = evaluate.replay(questions, saved)
        self.assertEqual([(r["id"], r["rank"], r["payload"]) for r in first["rows"]],
                         [(r["id"], r["rank"], r["payload"]) for r in again["rows"]])

    def test_front_matter_is_sent_once_per_document_without_its_sources(self):
        from carry.mcp_server import _format_recall
        meta = {"type": "note", "summary": "Plan", "sources": ["[[A very long provenance list]]"]}
        item = dict(source_id="corpus", path="notes/Plan.md", citation="Plan (corpus:notes/Plan.md)",
                    heading="One", record_id="imp_1", revision=1, state="imported", metadata=meta, text="first")
        result = dict(ok=True, status="evidence", diagnostics={"reranker": "jev", "judge_usage": {"input_tokens": 9}},
                      evidence=[item, dict(item, heading="Two", text="second")])
        text, error = _format_recall(result)
        self.assertFalse(error)
        self.assertEqual(text.count('"summary": "Plan"'), 1)
        self.assertNotIn("provenance list", text)
        self.assertNotIn("judge_usage", text)
        self.assertIn('"reranker": "jev"', text)

    def test_a_chat_log_passage_holding_the_question_is_an_echo(self):
        question = "what is the zebra quarantine budget"
        raw = dict(path="sources/2026-10-01 — chat-raw (claude@laptop).md", metadata={"type": "chat-raw"},
                   text="**10:02** What is the Zebra quarantine budget?")
        note = dict(raw, path="notes/Budget.md", metadata={"type": "note"})
        self.assertTrue(evaluate.is_echo(raw, question))
        self.assertFalse(evaluate.is_echo(note, question))
        self.assertFalse(evaluate.is_echo(dict(raw, text="the budget was approved"), question))
