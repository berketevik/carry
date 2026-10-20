"""A team repository's .carry/team.json: shared search settings and a minimum Carry version.

The file comes from a repository the member does not control, so nothing in it may break
a search; and it is never applied silently, since a workspace may hold the member's own
notes too."""
import io
import json
import tarfile
from contextlib import redirect_stdout

from _support import HASHING, WorkspaceCase

from carry import github, team
from carry import index as index_module
from carry.cli import main
from carry.config import RetrievalConfig, SourceConfig, Workspace
from carry.errors import CarryError
from carry.mcp_server import call_tool
from carry.status import status


class TeamSettingsTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.team = self.base / "team"
        (self.team / ".carry").mkdir(parents=True)
        self.note("Heron budget", body="The heron rollout budget is forty units.", root=self.team)
        self.workspace = Workspace.create(
            self.base / "state2",
            sources=[SourceConfig("corpus", self.corpus), SourceConfig("team", self.team, scope="team")],
            embedding=HASHING, retrieval=RetrievalConfig(top_k=6, max_chars=7000, auto_refresh=False))

    def write(self, payload, root=None):
        path = (root or self.team) / ".carry" / "team.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")

    def reload(self):
        return Workspace.load(self.workspace.state_dir)

    def test_no_file_means_no_report(self):
        self.assertIsNone(team.settings_report(self.workspace))
        self.assertIsNone(status(self.workspace)["team_settings"])

    def test_drift_is_reported_and_applied_only_on_request(self):
        self.write({"min_carry_version": "0.1", "retrieval": {"top_k": 12, "max_chars": 16000}})
        report = team.settings_report(self.workspace, version="0.11.0")
        self.assertEqual(report["differ"], {"max_chars": {"team": 16000, "local": 7000},
                                            "top_k": {"team": 12, "local": 6}})
        self.assertIn("team_settings_differ", status(self.workspace)["degradation"])
        self.assertEqual(self.reload().retrieval.top_k, 6)  # status never applies
        result = team.apply_settings(self.workspace)
        self.assertEqual(result["applied"], {"max_chars": 16000, "top_k": 12})
        workspace = self.reload()
        self.assertEqual((workspace.retrieval.top_k, workspace.retrieval.max_chars), (12, 16000))
        self.assertEqual(workspace.retrieval.chunk_chars, self.workspace.retrieval.chunk_chars)
        self.assertEqual(team.settings_report(workspace)["differ"], {})
        self.assertNotIn("team_settings_differ", status(workspace)["degradation"])

    def test_unknown_keys_and_bad_values_are_reported_not_applied(self):
        self.write({"retrieval": {"top_k": 12, "chunk_chars": 500, "max_chars": -3, "vector_min_score": True},
                    "owner": "x"})
        settings, warnings = team.load_settings(self.workspace.source("team"))
        self.assertEqual(settings["retrieval"], {"top_k": 12})
        self.assertEqual(sorted(warnings), sorted([
            "team_settings_unknown_key:owner", "team_settings_unknown_key:retrieval.chunk_chars",
            "team_settings_invalid:retrieval.max_chars", "team_settings_invalid:retrieval.vector_min_score"]))
        team.apply_settings(self.workspace)
        self.assertEqual(self.reload().retrieval.chunk_chars, self.workspace.retrieval.chunk_chars)

    def test_mail_domains_and_trello_key_for_signing_in(self):
        key = "0123456789abcdef0123456789abcdef"
        self.write({"email_domains": ["Ornek.com.tr", "ornek.dev"], "trello_api_key": key})
        settings, warnings = team.load_settings(self.workspace.source("team"))
        self.assertEqual(warnings, [])
        self.assertEqual((settings["email_domains"], settings["trello_api_key"]), (["ornek.com.tr", "ornek.dev"], key))
        self.assertEqual(team.sign_in(self.workspace), dict(email_domains=["ornek.com.tr", "ornek.dev"], trello_api_key=key))
        self.assertNotIn("team_settings_invalid", status(self.workspace)["degradation"])
        self.write({"email_domains": "ornek.com.tr", "trello_api_key": "not a key"})
        settings, warnings = team.load_settings(self.workspace.source("team"))
        self.assertEqual(sorted(warnings), ["team_settings_invalid:email_domains", "team_settings_invalid:trello_api_key"])
        self.assertEqual(team.sign_in(self.workspace), dict(email_domains=[], trello_api_key=None))
        (self.team / ".carry" / "team.json").unlink()
        self.assertEqual(team.sign_in(self.workspace), dict(email_domains=[], trello_api_key=None))

    def test_a_broken_file_never_breaks_search_or_status(self):
        self.write("{not json")
        index_module.build(self.workspace)
        text, is_error = call_tool("carry_recall", {"query": "heron rollout budget"}, str(self.workspace.state_dir))
        self.assertFalse(is_error)
        self.assertIn("Heron budget", text)
        payload = status(self.workspace)
        self.assertIn("team_settings_invalid", payload["degradation"])
        self.assertEqual(payload["team_settings"]["warnings"], ["team_settings_unreadable"])
        self.write("x" * (team.MAX_SETTINGS + 1))
        self.assertEqual(team.load_settings(self.workspace.source("team")), (None, ["team_settings_too_large"]))

    def test_minimum_version(self):
        self.write({"min_carry_version": "0.11"})
        self.assertTrue(team.settings_report(self.workspace, version="0.10.4")["too_old"])
        self.assertFalse(team.settings_report(self.workspace, version="0.11.0")["too_old"])
        self.assertFalse(team.settings_report(self.workspace, version="0.0.0")["too_old"])  # not installed: unknown
        self.write({"min_carry_version": "latest"})
        self.assertIn("team_settings_invalid:min_carry_version", team.settings_report(self.workspace)["warnings"])

    def test_conflicting_team_sources_apply_nothing(self):
        other = self.base / "team2"
        other.mkdir()
        self.write({"retrieval": {"top_k": 12}})
        self.write({"retrieval": {"top_k": 10}}, root=other)
        workspace = self.workspace.with_sources([*self.workspace.sources, SourceConfig("team-2", other, scope="team")]).save()
        report = team.settings_report(workspace)
        self.assertTrue(report["conflict"])
        self.assertIsNone(report["apply"])
        with self.assertRaises(CarryError):
            team.apply_settings(workspace)
        self.assertEqual(self.reload().retrieval.top_k, 6)
        self.assertIn("team_settings_conflict", status(workspace)["degradation"])

    def test_a_personal_source_is_never_read_for_team_settings(self):
        self.write({"retrieval": {"top_k": 12}}, root=self.corpus)
        (self.team / ".carry" / "team.json").unlink(missing_ok=True)
        self.assertIsNone(team.settings_report(self.workspace))

    def test_cli(self):
        self.write({"retrieval": {"top_k": 12}})
        state = str(self.workspace.state_dir)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["--workspace", state, "team"]), 0)
        self.assertIn("top_k: team 12, this workspace 6", out.getvalue())
        self.assertIn("carry team --apply", out.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--workspace", state, "team", "--apply"]), 0)
        self.assertEqual(self.reload().retrieval.top_k, 12)

    def test_a_github_archive_keeps_team_json(self):
        path = self.base / "archive.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for name, text in {"repo/Note.md": "# Note\n", "repo/.carry/team.json": '{"retrieval": {"top_k": 12}}',
                               "repo/.carry/other.json": "{}"}.items():
                data = text.encode()
                item = tarfile.TarInfo(name)
                item.size = len(data)
                tar.addfile(item, io.BytesIO(data))
        github.extract_markdown(path, self.base / "out")
        self.assertTrue((self.base / "out" / ".carry" / "team.json").is_file())
        self.assertFalse((self.base / "out" / ".carry" / "other.json").exists())
