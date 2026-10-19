"""A server started with --scope serves only that scope's sources.

A team member's assistant connected to the owner's workspace must not read, list or
learn the location of the owner's personal notes. Every assertion is on the text
`call_tool` returns, because that is what a client sees.
"""
import io
import json
from contextlib import redirect_stderr

from _support import HASHING, WorkspaceCase

from carry import index as index_module
from carry import lifecycle
from carry.config import RetrievalConfig, SourceConfig, Workspace
from carry.mcp_server import available_tools, call_tool, main
from carry.recall import catalog_entries, recall


class ReadScopeTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.team = self.base / "team"
        self.team.mkdir()
        self.workspace = Workspace.create(
            self.base / "state2",
            sources=[SourceConfig("corpus", self.corpus),
                     SourceConfig("team", self.team, scope="team")],
            embedding=HASHING, retrieval=RetrievalConfig(top_k=6, auto_refresh=False))
        self.state = str(self.workspace.state_dir)
        self.note("Pelican diary", body="The pelican savings plan moves to the blue account in May.")
        self.note("Heron budget", body="The heron rollout budget is forty units.", root=self.team)
        index_module.build(self.workspace)

    def call(self, name, arguments=None, scope="team", client=None):
        return call_tool(name, arguments or {}, self.state, client, scope)

    def test_no_visible_source_fails_closed_on_every_read_tool(self):
        lonely = Workspace.create(self.base / "state3", sources=[SourceConfig("corpus", self.corpus)],
                                  embedding=HASHING, retrieval=RetrievalConfig(auto_refresh=False))
        index_module.build(lonely)
        for name, arguments in (("carry_recall", {"query": "pelican savings"}), ("carry_catalog", {})):
            text, is_error = call_tool(name, arguments, str(lonely.state_dir), None, "team")
            self.assertTrue(is_error, name)
            self.assertNotIn("pelican", text.lower())
            self.assertNotIn("Pelican diary", text)
        text, _ = call_tool("carry_status", {}, str(lonely.state_dir), None, "team")
        self.assertEqual(json.loads(text)["workspace"]["sources"], [])
        self.assertNotIn(str(self.corpus), text)
        self.assertEqual(recall(lonely, "pelican", visible=[])["error"], "no_visible_sources")
        self.assertEqual(catalog_entries(lonely, visible=[])["error"], "no_visible_sources")

    def test_recall_never_returns_personal_passages(self):
        text, is_error = self.call("carry_recall", {"query": "pelican savings plan blue account"})
        self.assertFalse(is_error)
        self.assertNotIn("Pelican diary", text)
        self.assertNotIn("blue account", text)
        text, _ = self.call("carry_recall", {"query": "heron rollout budget"})
        self.assertIn("Heron budget", text)
        self.assertIn("TEAM KNOWLEDGE BASE", text)

    def test_a_hidden_source_answers_like_an_unknown_one(self):
        for name, arguments in (("carry_recall", {"query": "pelican"}), ("carry_catalog", {})):
            hidden = self.call(name, dict(arguments, source_ids=["corpus"]))
            unknown = self.call(name, dict(arguments, source_ids=["nope"]))
            self.assertEqual(hidden, unknown, name)
            self.assertTrue(hidden[1])

    def test_unmatched_terms_do_not_reveal_personal_words(self):
        # "pelican" exists only in personal notes: under team scope it must count as unmatched.
        result = recall(self.workspace, "pelican heron", visible=["team"])
        self.assertIn("pelican", result["diagnostics"]["unmatched_terms"])
        self.assertNotIn("heron", result["diagnostics"]["unmatched_terms"])
        self.assertNotIn("pelican", recall(self.workspace, "pelican heron")["diagnostics"]["unmatched_terms"])

    def test_catalog_lists_only_team_files(self):
        text, is_error = self.call("carry_catalog")
        self.assertFalse(is_error)
        self.assertIn("team:", text)
        self.assertNotIn("corpus:", text)
        self.assertNotIn("Pelican", text)

    def test_status_hides_personal_sources_and_counts(self):
        text, _ = self.call("carry_status")
        payload = json.loads(text)
        self.assertEqual([s["source_id"] for s in payload["workspace"]["sources"]], ["team"])
        self.assertNotIn(str(self.corpus), text)
        self.assertNotIn(str(self.workspace.state_dir), text)
        for key in ("review", "records", "capture", "maintenance"):
            self.assertNotIn(key, payload)
        self.assertEqual(set(payload["index"]), {"usable", "state"})
        full = json.loads(self.call("carry_status", scope=None)[0])
        self.assertIn(str(self.corpus), json.dumps(full))

    def test_proposals_are_never_offered_on_a_scoped_server(self):
        (self.workspace.state_dir / "proposals.json").write_text(
            json.dumps({"claude": {"enabled": True, "source_id": "corpus"}}), encoding="utf-8")
        self.assertTrue(lifecycle.client_enabled(self.workspace, "claude"))
        self.assertIn("carry_propose", [t["name"] for t in available_tools(self.state, "claude")])
        names = [t["name"] for t in available_tools(self.state, "claude", "team")]
        self.assertNotIn("carry_propose", names)
        text, is_error = self.call("carry_propose", {"content": "x", "event_id": "e", "source_refs": []},
                                   client="claude")
        self.assertTrue(is_error)

    def test_without_scope_everything_stays_visible(self):
        text, _ = self.call("carry_recall", {"query": "pelican savings plan blue account"}, scope=None)
        self.assertIn("Pelican diary", text)
        text, _ = self.call("carry_catalog", scope=None)
        self.assertIn("corpus:", text)

    def test_invalid_scope_is_rejected_at_startup(self):
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["--workspace", self.state, "--scope", "bogus"]), 1)
        self.assertIn("invalid scope", err.getvalue())
