"""A team clone connected by `carry setup --team` stays current, and never at the cost of a
member's own changes."""
import json
import os
import subprocess
import time
from datetime import datetime

from _support import HASHING, WorkspaceCase

from carry import index, maintenance, team, vaultview
from carry.config import RetrievalConfig, SourceConfig, Workspace
from carry.recall import recall
from carry.status import status


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class TeamCloneTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.remote = self.base / "remote.git"
        git(self.base, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.author = self.base / "author"
        git(self.base, "clone", "-q", str(self.remote), str(self.author))
        for repo in (self.author,):
            git(repo, "config", "user.email", "t@example.com")
            git(repo, "config", "user.name", "T")
        (self.author / "Heron.md").write_text("# Heron\n\nThe heron budget is forty units.\n", encoding="utf-8")
        git(self.author, "add", "-A")
        git(self.author, "commit", "-q", "-m", "first")
        git(self.author, "push", "-q", "origin", "HEAD:main")
        self.clone = self.base / "member"
        git(self.base, "clone", "-q", str(self.remote), str(self.clone))
        git(self.clone, "config", "user.email", "m@example.com")
        git(self.clone, "config", "user.name", "M")
        self.workspace = Workspace.create(
            self.base / "state2", sources=[SourceConfig("team", self.clone, scope="team")],
            embedding=HASHING, retrieval=RetrievalConfig(auto_refresh=False))
        self.source = self.workspace.source("team")

    def publish(self, text):
        (self.author / "Heron.md").write_text(f"# Heron\n\n{text}\n", encoding="utf-8")
        git(self.author, "commit", "-q", "-am", "update")
        git(self.author, "push", "-q", "origin", "HEAD:main")
        return git(self.author, "rev-parse", "HEAD")

    def test_only_a_team_clone_is_pulled(self):
        self.assertTrue(team.is_clone(self.source))
        personal = SourceConfig("notes", self.clone)
        self.assertFalse(team.is_clone(personal))

    def test_a_clean_clone_moves_to_the_new_commit(self):
        head = self.publish("The heron budget is fifty units.")
        result = team.pull(self.source)
        self.assertEqual(result["status"], "pulled")
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), head)
        self.assertEqual(team.pull(self.source)["status"], "current")

    def test_local_changes_are_never_touched(self):
        self.publish("The heron budget is fifty units.")
        (self.clone / "Heron.md").write_text("# Heron\n\nmember draft\n", encoding="utf-8")
        result = team.pull(self.source)
        self.assertEqual((result["status"], result["reason"], result["behind"]), ("skipped", "local_changes", 1))
        self.assertIn("member draft", (self.clone / "Heron.md").read_text(encoding="utf-8"))

    def test_unpushed_commits_are_never_touched(self):
        self.publish("The heron budget is fifty units.")
        (self.clone / "Note.md").write_text("# Note\n\nmine\n", encoding="utf-8")
        git(self.clone, "add", "-A")
        git(self.clone, "commit", "-q", "-m", "mine")
        before = git(self.clone, "rev-parse", "HEAD")
        result = team.pull(self.source)
        self.assertEqual(result["reason"], "unpushed_commits")
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), before)

    def test_maintenance_pulls_on_the_sync_cadence_and_status_reports(self):
        head = self.publish("The heron budget is fifty units.")
        maintenance.run(self.workspace, "maintain", {})
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), head)
        state = maintenance.sync_status(self.workspace)["team"]
        self.assertEqual((state["kind"], state["status"]), ("team_clone", "pulled"))
        # Within the cadence nothing is pulled again, even when the upstream moved.
        self.publish("The heron budget is sixty units.")
        maintenance.run(self.workspace, "maintain", {})
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), head)
        # A dirty clone is reported, not pulled.
        states = maintenance.sync_status(self.workspace)
        states["team"]["checked_at"] = time.time() - 10_000
        (self.workspace.state_dir / "github.status.json").write_text(json.dumps(states), encoding="utf-8")
        (self.clone / "Heron.md").write_text("# Heron\n\nmember draft\n", encoding="utf-8")
        maintenance.run(self.workspace, "maintain", {})
        flags = status(self.workspace)["degradation"]
        self.assertIn("team_clone_has_local_changes", flags)
        self.assertIn("team_clone_behind", flags)

    def test_commit_dates_come_from_the_clone_log(self):
        (self.author / "Heron.md").write_text("# Heron\n\nThe heron budget is fifty units.\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.author), "commit", "-q", "-am", "update"], check=True,
                       capture_output=True, env=dict(os.environ, GIT_COMMITTER_DATE="2026-09-11T14:29:00+03:00"))
        git(self.author, "push", "-q", "origin", "HEAD:main")
        self.assertEqual(team.pull(self.source)["status"], "pulled")
        index.build(self.workspace)
        committed = datetime.fromisoformat("2026-09-11T14:29:00+03:00")
        item = recall(self.workspace, "heron budget")["evidence"][0]
        self.assertEqual(item["committed_at"], committed.astimezone().isoformat(timespec="seconds"))
        listed = {f["path"]: f for f in vaultview.browse(self.workspace, "team")["files"]}
        self.assertEqual(listed["Heron.md"]["modified"], committed.timestamp())
        # An uncommitted edit is newer than the last commit: the file's own time shows.
        (self.clone / "Heron.md").write_text("# Heron\n\nmember draft\n", encoding="utf-8")
        listed = {f["path"]: f for f in vaultview.browse(self.workspace, "team")["files"]}
        self.assertEqual(listed["Heron.md"]["modified"], (self.clone / "Heron.md").stat().st_mtime)

    def test_a_clone_without_upstream_is_skipped(self):
        git(self.clone, "branch", "-q", "--unset-upstream")
        self.assertEqual(team.pull(self.source), dict(status="skipped", reason="no_upstream"))
