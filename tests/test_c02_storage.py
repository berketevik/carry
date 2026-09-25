"""C02: configured sources, Markdown storage and a derived index.

Acceptance: two independent workspaces cannot contaminate each other, and a
rebuild leaves the original files untouched.
"""
import fcntl
import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from _support import HASHING, WorkspaceCase, tree_digest

from carry import index as index_module
from carry import sidecar as sidecar_map
from carry import store
from carry.config import RetrievalConfig, SourceConfig, Workspace
from carry.errors import RevisionConflict, SourceError, WorkspaceError
from carry.paths import resolve_within, walk_markdown
from carry.recall import recall


class ConfigurationTest(WorkspaceCase):
    def test_state_directory_inside_a_source_is_rejected(self):
        with self.assertRaises(WorkspaceError):
            Workspace(state_dir=self.corpus / "state",
                      sources=(SourceConfig("corpus", self.corpus),)).validate()

    def test_duplicate_source_id_is_rejected(self):
        other = self.base / "second"
        other.mkdir()
        with self.assertRaises(WorkspaceError):
            self.workspace.with_sources([SourceConfig("corpus", self.corpus),
                                         SourceConfig("corpus", other)])

    def test_nested_source_roots_are_rejected(self):
        nested = self.corpus / "inner"
        nested.mkdir()
        with self.assertRaises(WorkspaceError):
            self.workspace.with_sources([SourceConfig("corpus", self.corpus),
                                         SourceConfig("inner", nested)])

    def test_invalid_identifier_and_scope_are_rejected(self):
        for bad in (SourceConfig("Corpus Two", self.corpus),
                    SourceConfig("corpus", self.corpus, scope="everyone")):
            with self.assertRaises(WorkspaceError):
                bad.validate()

    def test_configuration_round_trips_on_disk(self):
        reopened = Workspace.load(self.workspace.state_dir)
        self.assertEqual([s.source_id for s in reopened.sources], ["corpus", "records"])
        self.assertEqual(reopened.embedding.provider, "hashing")
        self.assertTrue(reopened.source("records").writable)

    def test_unwritable_source_refuses_a_write_destination(self):
        with self.assertRaises(SourceError):
            self.workspace.writable_source("corpus")


class PathContainmentTest(WorkspaceCase):
    def test_traversal_and_absolute_paths_are_rejected(self):
        for candidate in ("../outside.md", "a/../../outside.md", "/etc/hosts"):
            with self.assertRaises(SourceError):
                resolve_within(self.corpus, candidate)

    def test_symlink_escape_is_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.corpus / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(SourceError):
            resolve_within(self.corpus, "link/secret.md")

    def test_walk_skips_symlinked_files_and_directories(self):
        self.note("Inside")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "Secret.md").write_text("secret evidence", encoding="utf-8")
        (self.corpus / "linked").symlink_to(outside, target_is_directory=True)
        (self.corpus / "Secret Link.md").symlink_to(outside / "Secret.md")
        found = {relative for relative, _ in walk_markdown(self.corpus)}
        self.assertEqual(found, {"Inside.md"})

    def test_symlinked_content_never_reaches_the_index(self):
        self.note("Inside")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "Secret.md").write_text("---\ntype: note\n---\nsecret sentinel", encoding="utf-8")
        (self.corpus / "linked").symlink_to(outside, target_is_directory=True)
        self.build()
        result = recall(self.workspace, "secret sentinel")
        self.assertNotIn("sentinel", json.dumps(result["evidence"], default=str))
        self.assertEqual(result["evidence"], [])
        self.assertEqual({e["path"] for e in recall(self.workspace, "Inside")["evidence"]}, {"Inside.md"})


class IsolationTest(WorkspaceCase):
    """Two workspaces on one machine must not see each other's corpus."""

    def other_workspace(self):
        corpus = self.base / "corpus-b"
        corpus.mkdir()
        self.note("Beta Plan", body="Beta delivery is April 2, 2027.", root=corpus)
        workspace = Workspace.create(self.base / "state-b",
                                     sources=[SourceConfig("corpus", corpus)],
                                     embedding=HASHING, retrieval=RetrievalConfig())
        self.assertEqual(index_module.build(workspace)["status"], "built")
        return workspace, corpus

    def test_neither_workspace_returns_the_other_corpus(self):
        self.note("Alpha Plan", body="Alpha delivery is March 1, 2027.")
        self.build()
        other, _ = self.other_workspace()

        first = recall(self.workspace, "delivery date")
        second = recall(other, "delivery date")
        self.assertIn("Alpha", json.dumps(first["evidence"], default=str))
        self.assertNotIn("Beta", json.dumps(first["evidence"], default=str))
        self.assertIn("Beta", json.dumps(second["evidence"], default=str))
        self.assertNotIn("Alpha", json.dumps(second["evidence"], default=str))

    def test_state_files_are_per_workspace(self):
        self.note("Alpha Plan")
        self.build()
        other, _ = self.other_workspace()
        self.assertNotEqual(self.workspace.db_path, other.db_path)
        for path in (other.db_path, other.status_path, other.sidecar_path):
            self.assertFalse(path.is_relative_to(self.workspace.state_dir))

    def test_rebuilding_one_workspace_leaves_the_other_index_untouched(self):
        self.note("Alpha Plan")
        self.build()
        other, other_corpus = self.other_workspace()
        before = other.db_path.read_bytes()
        self.note("Alpha Second", body="more alpha evidence")
        self.build()
        self.assertEqual(other.db_path.read_bytes(), before)

    def test_a_source_filter_that_matches_nothing_reports_no_evidence(self):
        self.note("Alpha Plan")
        self.build()
        result = recall(self.workspace, "delivery date", source_ids=["records"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "no_evidence")
        self.assertEqual(result["evidence"], [])


class RebuildSafetyTest(WorkspaceCase):
    def test_rebuild_preserves_every_source_file(self):
        self.cedar()
        before = tree_digest(self.corpus)
        self.build()
        self.note("Extra", body="extra evidence")
        self.build()
        after = tree_digest(self.corpus)
        self.assertEqual({k: v for k, v in after.items() if k != "Extra.md"}, before)

    def test_index_state_never_lands_in_a_source_root(self):
        self.cedar()
        self.build()
        for source in self.workspace.sources:
            names = {p.name for p in Path(source.root).rglob("*") if p.is_file()}
            self.assertFalse({"index.db", "index.status.json", "sidecar.json"} & names)

    def test_unchanged_rebuild_embeds_nothing(self):
        self.note("Alpha")
        self.build()
        with mock.patch("carry.embedding.HashingEmbedding.embed_document") as embed:
            self.build()
            embed.assert_not_called()

    def test_appending_reuses_unchanged_windows(self):
        path = self.note("Stream", body="a " * 1200)
        self.build()
        path.write_text(path.read_text(encoding="utf-8") + "\n# New section\nfresh evidence",
                        encoding="utf-8")
        result = self.build()
        self.assertEqual(result["embedded_chunks"], 1, result)
        self.assertGreater(result["reused_chunks"], 0, result)

    def test_deletion_removes_the_file_from_the_index(self):
        self.note("Alpha", body="alpha sentinel evidence")
        self.note("Beta", body="beta evidence")
        self.build()
        (self.corpus / "Alpha.md").unlink()
        result = self.build()
        self.assertEqual(result["removed_files"], 1, result)
        evidence = recall(self.workspace, "alpha sentinel")["evidence"]
        self.assertNotIn("sentinel", json.dumps(evidence, default=str))
        self.assertEqual(evidence, [])
        self.assertEqual({e["path"] for e in recall(self.workspace, "beta evidence")["evidence"]}, {"Beta.md"})

    def test_failed_build_keeps_the_published_index_byte_for_byte(self):
        self.note("Alpha", body="alpha evidence")
        self.build()
        published = self.workspace.db_path.read_bytes()
        self.note("Beta", body="beta evidence")
        with mock.patch("carry.embedding.HashingEmbedding.embed_document",
                        side_effect=RuntimeError("credential sk-must-not-be-logged")):
            result = index_module.build(self.workspace)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(self.workspace.db_path.read_bytes(), published)
        status_text = self.workspace.status_path.read_text(encoding="utf-8")
        self.assertNotIn("sk-must-not-be-logged", status_text)
        self.assertEqual(json.loads(status_text)["error_type"], "RuntimeError")

    def test_interrupted_build_keeps_serving_and_reports_stale(self):
        self.note("Alpha", body="alpha evidence")
        self.build()
        self.note("Beta", body="beta evidence")
        with mock.patch("carry.embedding.HashingEmbedding.embed_document",
                        side_effect=RuntimeError("interrupted")):
            index_module.build(self.workspace)
        health = index_module.health(self.workspace)
        self.assertEqual(health["state"], "stale")
        self.assertEqual(health["last_build"]["status"], "failed")
        result = recall(self.workspace, "alpha evidence")
        self.assertEqual(result["status"], "evidence")
        self.assertIn("index_stale", result["diagnostics"]["warnings"])

    def test_a_concurrent_build_is_refused_rather_than_interleaved(self):
        self.note("Alpha")
        self.build()
        with open(self.workspace.lock_path, "a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(index_module.build(self.workspace)["status"], "reindexing")

    def test_no_leftover_build_file_after_success_or_failure(self):
        self.note("Alpha")
        self.build()
        self.assertFalse(Path(str(self.workspace.db_path) + ".building").exists())
        self.note("Beta")
        with mock.patch("carry.embedding.HashingEmbedding.embed_document",
                        side_effect=RuntimeError("boom")):
            index_module.build(self.workspace)
        self.assertFalse(Path(str(self.workspace.db_path) + ".building").exists())

    def test_empty_workspace_answers_no_evidence_not_unavailable(self):
        self.build()
        result = recall(self.workspace, "anything at all")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "no_evidence")


class SidecarIdentityTest(WorkspaceCase):
    def record_ids(self):
        entries = sidecar_map.load(self.workspace.sidecar_path)["entries"]
        return {key.split("\t", 1)[1]: value["record_id"] for key, value in entries.items()}

    def test_imported_files_get_stable_identity_without_being_edited(self):
        path = self.note("Alpha", body="alpha evidence")
        before = path.read_bytes()
        self.build()
        first = self.record_ids()["Alpha.md"]
        self.build()
        self.assertEqual(self.record_ids()["Alpha.md"], first)
        self.assertEqual(path.read_bytes(), before)

    def test_rename_with_identical_content_carries_the_identity_over(self):
        path = self.note("Alpha", body="alpha evidence")
        self.build()
        original = self.record_ids()["Alpha.md"]
        path.rename(self.corpus / "Alpha Renamed.md")
        self.build()
        ids = self.record_ids()
        self.assertNotIn("Alpha.md", ids)
        self.assertEqual(ids["Alpha Renamed.md"], original)

    def test_ambiguous_move_is_flagged_instead_of_guessed(self):
        # Byte-identical files: content alone cannot say which one moved where.
        body = '---\ntype: note\nsummary: "same"\n---\n# Section\nidentical evidence'
        first, second = self.corpus / "One.md", self.corpus / "Two.md"
        first.write_text(body, encoding="utf-8")
        second.write_text(body, encoding="utf-8")
        self.build()
        shutil.move(str(first), self.corpus / "Three.md")
        shutil.move(str(second), self.corpus / "Four.md")
        result = self.build()
        self.assertGreaterEqual(result["needs_reconciliation"], 1, result)
        reconcile = sidecar_map.load(self.workspace.sidecar_path)["reconcile"]
        self.assertTrue(any(entry["reason"] == "ambiguous_move" for entry in reconcile))


class RecordStorageTest(WorkspaceCase):
    def test_written_record_carries_identity_in_frontmatter(self):
        result = store.write_record(self.workspace, "Cedar delivery",
                                    "Cedar pilot delivery is October 15, 2026.",
                                    event_id="evt-1", author="tester", surface="cli")
        path = self.records / result["path"]
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.is_file())
        self.assertIn("carry_record: " + result["record_id"], text)
        self.assertIn("carry_revision: 1", text)
        self.assertIn("carry_state: draft", text)
        self.assertIn("carry_current: true", text)
        self.assertIn("carry_event: evt-1", text)

    def test_replaying_an_event_does_not_write_a_second_record(self):
        first = store.write_record(self.workspace, "Cedar delivery", "October 15, 2026.",
                                   event_id="evt-1")
        second = store.write_record(self.workspace, "Cedar delivery", "October 15, 2026.",
                                    event_id="evt-1")
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["record_id"], first["record_id"])
        self.assertEqual(len(list((self.records / "carry").glob("*.md"))), 1)

    def test_a_replayed_event_reports_the_records_current_state(self):
        record = store.write_record(self.workspace, "Cedar delivery", "October 15, 2026.",
                                    event_id="evt-1")
        store.set_state(self.workspace, record["record_id"], "accepted", 1)
        replayed = store.write_record(self.workspace, "Cedar delivery", "October 15, 2026.",
                                      event_id="evt-1")
        self.assertTrue(replayed["duplicate"])
        self.assertEqual(replayed["state"], "accepted")
        self.assertEqual(replayed["revision"], 1)

    def test_a_distinct_event_with_identical_wording_is_a_distinct_record(self):
        first = store.write_record(self.workspace, "Cedar delivery", "October 15, 2026.",
                                   event_id="evt-1")
        second = store.write_record(self.workspace, "Cedar delivery", "October 15, 2026.",
                                    event_id="evt-2")
        self.assertNotEqual(first["record_id"], second["record_id"])
        self.assertEqual(len(list((self.records / "carry").glob("*.md"))), 2)

    def test_secrets_are_masked_before_persistence(self):
        result = store.write_record(self.workspace, "Token note",
                                    "api_key: sk-abcdefghijklmnopqrst", event_id="evt-secret")
        text = (self.records / result["path"]).read_text(encoding="utf-8")
        self.assertNotIn("sk-abcdefghijklmnopqrst", text)
        self.assertIn("***MASKED***", text)
        self.assertTrue(result["masked"])

    def test_state_change_requires_the_expected_revision(self):
        record = store.write_record(self.workspace, "Cedar delivery", "October 15, 2026.",
                                    event_id="evt-1")
        with self.assertRaises(RevisionConflict):
            store.set_state(self.workspace, record["record_id"], "accepted", 7)
        accepted = store.set_state(self.workspace, record["record_id"], "accepted", 1)
        self.assertEqual(accepted["state"], "accepted")

    def test_correction_keeps_history_and_moves_the_current_pointer(self):
        record = store.write_record(self.workspace, "Cedar delivery", "October 15, 2026.",
                                    event_id="evt-1")
        store.set_state(self.workspace, record["record_id"], "accepted", 1)
        corrected = store.supersede(self.workspace, record["record_id"],
                                    "Cedar pilot delivery is October 22, 2026.", 1,
                                    title="Cedar delivery")
        old_text = next(p.read_text(encoding="utf-8") for p in (self.records / "carry").glob("*.md")
                        if record["record_id"] in p.read_text(encoding="utf-8")
                        and "carry_supersedes" not in p.read_text(encoding="utf-8"))
        self.assertEqual(corrected["revision"], 2)
        self.assertIn("carry_current: false", old_text)
        self.assertIn("carry_superseded_by: " + corrected["record_id"], old_text)
        self.assertIn("carry_state: superseded", old_text)
        self.assertIn("October 15, 2026", old_text)
        new_text = (self.records / corrected["path"]).read_text(encoding="utf-8")
        self.assertIn("October 22, 2026", new_text)
        self.assertIn("carry_supersedes: " + record["record_id"], new_text)

    def test_a_stale_correction_is_refused(self):
        record = store.write_record(self.workspace, "Cedar delivery", "October 15, 2026.",
                                    event_id="evt-1")
        store.supersede(self.workspace, record["record_id"], "October 22, 2026.", 1)
        with self.assertRaises(RevisionConflict):
            store.supersede(self.workspace, record["record_id"], "October 29, 2026.", 1)

    def test_records_are_only_written_to_a_writable_source(self):
        store.write_record(self.workspace, "Cedar delivery", "October 15, 2026.", event_id="e")
        self.assertEqual(list(self.corpus.rglob("*.md")), [])
        self.assertTrue(list((self.records / "carry").glob("*.md")))


if __name__ == "__main__":
    unittest.main()
