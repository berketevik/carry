"""C01: the package stands alone, with no personal paths or private content."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

from _support import SRC

ROOT = SRC.parent
SKIP_DIRS = {".git", ".venv", "__pycache__", "build", "dist", "workspaces", ".pytest_cache"}

# Extraction is only real if nothing here points back at the vault it came from.
FORBIDDEN = (
    re.compile(r"Obsidian[ _-]?Personal[ _-]?Vault", re.I),
    re.compile(r"/Users/[A-Za-z0-9._-]+"),
    re.compile(r"vault[_-]recall|vault[_-]capture", re.I),
    re.compile(r"\bhompi\b|\bhermes\b", re.I),
    re.compile(r"berke", re.I),
    re.compile(r"\+/captures"),
)


# The author's credit is deliberate; it is not a trace of the vault the code came from.
AUTHOR_CREDIT = "Berke Tevik"

SELF = Path(__file__).resolve()


def project_files():
    # This file holds the patterns themselves, so scanning it would always fail.
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.resolve() == SELF:
            continue
        if path.suffix.lower() in (".md", ".py", ".toml", ".json", ".cfg", ".txt", ".yml"):
            yield path


class PackageTest(unittest.TestCase):
    def test_imports_and_exposes_version(self):
        import carry
        self.assertRegex(carry.__version__, r"^\d+\.\d+\.\d+")
        self.assertTrue(hasattr(carry, "Workspace"))

    def test_entry_points_run_as_modules(self):
        for module in ("carry.cli", "carry.mcp_server"):
            result = subprocess.run([sys.executable, "-c", f"import {module}"],
                                    env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_personal_paths_or_private_content(self):
        offences = []
        for path in project_files():
            text = path.read_text(encoding="utf-8", errors="replace").replace(AUTHOR_CREDIT, "")
            for pattern in FORBIDDEN:
                for match in pattern.finditer(text):
                    line = text[:match.start()].count("\n") + 1
                    offences.append(f"{path.relative_to(ROOT)}:{line}: {pattern.pattern}")
        self.assertEqual(offences, [], "personal references found:\n" + "\n".join(offences))

    def test_no_root_derived_from_package_location(self):
        """The extracted core must not rediscover a corpus above its own folder."""
        for path in (SRC / "carry").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r'__file__[^\n]*"\.\."[^\n]*"\.\."',
                                f"{path.name} derives a root from its own location")

    def test_core_imports_without_optional_dependencies(self):
        """A clean machine with no numpy, PyYAML or MCP SDK still gets a usable core."""
        code = ("import sys\n"
                "sys.modules['numpy'] = None\n"
                "import carry, carry.recall, carry.index, carry.mcp_server, carry.cli\n"
                "print('ok')\n")
        result = subprocess.run([sys.executable, "-c", code],
                                env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


class FixtureTest(unittest.TestCase):
    def setUp(self):
        from carry.fixtures import fixture_path
        self.notes = fixture_path("cedar") / "notes"

    def test_corpus_is_present_and_frozen(self):
        names = sorted(p.name for p in self.notes.glob("*.md"))
        self.assertIn("Cedar Pilot Plan.md", names)
        self.assertIn("Maple Handover.md", names)
        self.assertGreaterEqual(len(names), 8)

    def test_delivery_date_stated_once(self):
        occurrences = sum(p.read_text(encoding="utf-8").count("October 15, 2026")
                          for p in self.notes.glob("*.md"))
        self.assertEqual(occurrences, 1)

    def test_no_budget_or_monetary_amount(self):
        pattern = re.compile(r"budget|dollar|\busd\b|[$€₺]", re.I)
        for path in self.notes.glob("*.md"):
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), path.name)

    def test_decoy_project_has_a_different_date(self):
        maple = (self.notes / "Maple Handover.md").read_text(encoding="utf-8")
        self.assertIn("November 3, 2026", maple)
        self.assertNotIn("October 15", maple)


if __name__ == "__main__":
    unittest.main()
