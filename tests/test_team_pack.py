"""A team pack built with another model or chunking is skipped by the index without a word;
status names it, so members do not silently embed different vectors."""
from dataclasses import replace

from _support import HASHING, WorkspaceCase

from carry import index as index_module
from carry import team
from carry.config import RetrievalConfig, SourceConfig, Workspace
from carry.status import status


class TeamPackTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.team = self.base / "team"
        self.team.mkdir()
        self.note("Heron budget", body="The heron rollout budget is forty units.", root=self.team)
        self.workspace = Workspace.create(
            self.base / "state2", sources=[SourceConfig("team", self.team, scope="team")],
            embedding=HASHING, retrieval=RetrievalConfig(auto_refresh=False))
        index_module.build(self.workspace)
        index_module.write_pack(self.workspace, "team", self.team / ".carry" / "index.db")

    def test_a_matching_pack_is_usable(self):
        self.assertEqual(team.pack_report(self.workspace), {"team": "usable"})
        self.assertNotIn("team_pack_incompatible", status(self.workspace)["degradation"])

    def test_another_chunking_is_reported(self):
        other = replace(self.workspace, retrieval=replace(self.workspace.retrieval, chunk_chars=600, chunk_overlap=50)).save()
        self.assertEqual(team.pack_report(other), {"team": "incompatible"})
        self.assertIn("team_pack_incompatible", status(other)["degradation"])
        # The index indeed embeds everything itself in that case.
        self.assertEqual(index_module.build(other)["packed_chunks"], 0)

    def test_a_scoped_status_shows_only_visible_packs(self):
        payload = status(self.workspace, visible=["team"])
        self.assertEqual(payload["team_packs"], {"team": "usable"})
        self.assertEqual(status(self.workspace, visible=[])["team_packs"], {})

    def test_no_pack_no_report(self):
        (self.team / ".carry" / "index.db").unlink()
        self.assertEqual(team.pack_report(self.workspace), {})
