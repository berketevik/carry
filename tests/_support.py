"""Shared test scaffolding: real filesystem and SQLite, deterministic embeddings."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from carry.config import EmbeddingConfig, RetrievalConfig, SourceConfig, Workspace  # noqa: E402
from carry.fixtures import install_fixture  # noqa: E402
from carry import index as index_module  # noqa: E402

HASHING = EmbeddingConfig(provider="hashing", dim=128)


def tree_digest(root):
    """Content digest of every file under `root`, keyed by relative path."""
    root = Path(root)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def symlink_or_skip(test, link, target, **options):
    """Windows grants symlinks only to admins or in Developer Mode; without that, the escape
    a test stages cannot exist on this machine, so the test is skipped rather than failed."""
    try:
        link.symlink_to(target, **options)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:  # ERROR_PRIVILEGE_NOT_HELD
            test.skipTest("this Windows account cannot create symlinks")
        raise


class WorkspaceCase(unittest.TestCase):
    """A temporary workspace whose state directory sits outside every source."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.corpus = self.base / "corpus"
        self.corpus.mkdir()
        self.records = self.base / "records"
        self.records.mkdir()
        self.workspace = Workspace.create(
            self.base / "state",
            sources=[SourceConfig("corpus", self.corpus),
                     SourceConfig("records", self.records, writable=True)],
            embedding=HASHING, retrieval=RetrievalConfig(top_k=6, auto_refresh=False))
        self.addCleanup(self.tmp.cleanup)

    def note(self, name, body="evidence body", summary=None, folder=None, root=None):
        root = Path(root or self.corpus)
        directory = root / folder if folder else root
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (name + ".md")
        front = f'---\ntype: note\nsummary: "{summary or name}"\ncreated: 2026-09-01\n---\n'
        path.write_text(front + "# Section\n" + body, encoding="utf-8")
        return path

    def cedar(self):
        """Install the frozen synthetic corpus under this workspace's corpus source.

        The fixture README stays out: it talks about the corpus and would
        otherwise show up as evidence.
        """
        return install_fixture(self.corpus, exist_ok=True, include_readme=False)

    def build(self, expect="built"):
        result = index_module.build(self.workspace)
        if expect:
            self.assertEqual(result["status"], expect, result)
        return result
