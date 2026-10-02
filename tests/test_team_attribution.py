"""A passage from a team source is labelled as such, so the assistant can say where it came from."""
from _support import HASHING, WorkspaceCase

from carry import index as index_module
from carry.config import RetrievalConfig, SourceConfig, Workspace
from carry.mcp_server import GROUNDING, _format_recall
from carry.recall import recall


class TeamAttributionTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.team = self.base / "team"
        self.team.mkdir()
        self.workspace = Workspace.create(
            self.base / "state2",
            sources=[SourceConfig("corpus", self.corpus),
                     SourceConfig("team", self.team, scope="team")],
            embedding=HASHING, retrieval=RetrievalConfig(top_k=6, auto_refresh=False))
        self.note("Heron rollout", body="The heron rollout starts on the ninth of March.")
        self.note("Heron budget", body="The heron rollout budget is forty units.", root=self.team)
        index_module.build(self.workspace)

    def test_team_passages_are_marked_and_personal_ones_are_not(self):
        result = recall(self.workspace, "heron rollout")
        scopes = {e["source_id"]: e["scope"] for e in result["evidence"]}
        self.assertEqual(scopes, {"corpus": "personal", "team": "team"})
        text, _ = _format_recall(result)
        for block in text.split("\n\n"):
            if block.startswith("["):
                self.assertEqual("TEAM KNOWLEDGE BASE" in block.split("\n", 1)[0], "(team:" in block.split("\n", 1)[0])
        self.assertIn("say so in the answer", GROUNDING)
