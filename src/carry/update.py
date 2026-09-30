"""`carry update`: bring this installation up to date the way it was installed.

uv tool (the README install): `uv tool upgrade carry`. An editable source checkout
(`uv pip install -e .`): a fast-forward `git pull`, then a reinstall when pyproject.toml
changed, since new dependencies or entry points need one. Any other install is reported
with nothing changed. Running MCP servers keep the old code until their client restarts them.
"""
import json
from importlib import metadata
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .background import which
from .errors import CarryError


def installation():
    """(kind, detail): ('source', checkout), ('uv_tool', git url or None) or ('other', None)."""
    try:
        direct = json.loads(metadata.distribution('carry').read_text('direct_url.json') or '{}')
    except (metadata.PackageNotFoundError, ValueError):
        direct = {}
    url = direct.get('url', '')
    if direct.get('dir_info', {}).get('editable') and url.startswith('file://'):
        path = Path(url2pathname(urlsplit(url).path))
        if (path / '.git').exists():
            return 'source', path
    if (Path(sys.prefix) / 'uv-receipt.toml').is_file():
        return 'uv_tool', (url if 'vcs_info' in direct else None)
    return 'other', None


def _git(checkout, *args):
    proc = subprocess.run(['git', '-C', str(checkout), *args], capture_output=True, text=True,
                          encoding='utf-8', timeout=120)
    if proc.returncode:
        raise CarryError('update_git_failed: ' + (proc.stderr or proc.stdout).strip()[-300:])
    return proc.stdout.strip()


def check():
    kind, detail = installation()
    current = metadata.version('carry')
    if kind == 'source':
        _git(detail, 'fetch', '-q')
        behind = int(_git(detail, 'rev-list', '--count', 'HEAD..@{u}'))
        return dict(kind=kind, path=str(detail), version=current, commit=_git(detail, 'rev-parse', '--short', 'HEAD'),
                    behind=behind, available=behind > 0)
    if kind == 'uv_tool' and detail:
        vcs = json.loads(metadata.distribution('carry').read_text('direct_url.json'))['vcs_info']
        installed = vcs.get('commit_id', '')
        # Compare with the branch or tag it was installed from, not the default branch.
        remote = subprocess.run(['git', 'ls-remote', detail.removeprefix('git+'), vcs.get('requested_revision') or 'HEAD'], capture_output=True,
                                text=True, encoding='utf-8', timeout=60).stdout.split()
        latest = remote[0] if remote else ''
        return dict(kind=kind, version=current, commit=installed[:7], available=bool(latest) and latest != installed)
    return dict(kind=kind, version=current, available=None)


def update():
    kind, detail = installation()
    before = metadata.version('carry')
    if kind == 'uv_tool':
        uv = which('uv')
        if not uv:
            raise CarryError('uv_missing')
        proc = subprocess.run([uv, 'tool', 'upgrade', 'carry'], capture_output=True, text=True, encoding='utf-8')
        if proc.returncode:
            raise CarryError('update_failed: ' + (proc.stderr or proc.stdout).strip()[-300:])
        # This process still has the old metadata loaded; ask the upgraded interpreter.
        after = subprocess.run([sys.executable, '-c', "from importlib.metadata import version; print(version('carry'))"],
                               capture_output=True, text=True, encoding='utf-8').stdout.strip() or before
        return dict(kind=kind, before=before, after=after, updated=after != before)
    if kind != 'source':
        raise CarryError('update_unsupported_install')
    old = _git(detail, 'rev-parse', 'HEAD')
    _git(detail, 'pull', '--ff-only', '-q')
    new = _git(detail, 'rev-parse', 'HEAD')
    reinstalled = False
    if new != old and _git(detail, 'diff', '--name-only', old, new, '--', 'pyproject.toml'):
        uv = which('uv')
        command = ([uv, 'pip', 'install', '--python', sys.executable, '-e', str(detail)] if uv
                   else [sys.executable, '-m', 'pip', 'install', '-e', str(detail)])
        proc = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        if proc.returncode:
            raise CarryError('update_reinstall_failed: ' + (proc.stderr or proc.stdout).strip()[-300:])
        reinstalled = True
    return dict(kind=kind, path=str(detail), before=old[:7], after=new[:7], updated=new != old, reinstalled=reinstalled)
