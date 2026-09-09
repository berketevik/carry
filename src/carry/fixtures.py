"""Access to the packaged synthetic corpus.

The fixture ships with the package so a demo or an acceptance run never needs a
network fetch and never touches a user's own notes.
"""
import shutil
from pathlib import Path

FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def fixture_path(name="cedar"):
    path = FIXTURE_ROOT / name
    if not (path / "notes").is_dir():
        raise FileNotFoundError("unknown_fixture:" + name)
    return path


def install_fixture(destination, name="cedar", exist_ok=False, include_readme=True):
    """Copy the frozen corpus into `destination` and return its notes folder.

    The README describes the fixture, so it is only written when it lands outside
    the folder that gets indexed: a document about the corpus is not part of the
    corpus, and indexing it would change what the acceptance queries match.
    """
    destination = Path(destination).expanduser()
    target = destination / "notes"
    if target.exists() and not exist_ok:
        raise FileExistsError("fixture_destination_not_empty")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fixture_path(name) / "notes", target, dirs_exist_ok=exist_ok)
    if include_readme:
        shutil.copy2(fixture_path(name) / "README.md", destination / "README.md")
    return target
