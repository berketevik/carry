"""Read-only GitHub sources, using the official CLI's credential store.

Only GET API requests are used. Downloads become immutable, commit-pinned
Markdown snapshots, never checkouts of a user's working repository.
"""
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import replace
from urllib.parse import quote, urlsplit

from .config import SOURCE_ID_RE, SourceConfig, Workspace
from .errors import CarryError
from .persistence import writer_lock

MAX_ARCHIVE = 200_000_000
MAX_CONTENT = 100_000_000
MAX_FILES = 20_000


def executable():
    candidates = [Path(sys.prefix).parent / 'bin/gh',
                  Path('/opt/homebrew/bin/gh'), Path('/usr/local/bin/gh')]
    found = shutil.which('gh')
    if found:
        return found
    for path in candidates:
        if path.is_file():
            return str(path)
    raise CarryError('github_cli_missing')


def environment():
    return dict(os.environ, GH_HOST='github.com', GH_PROMPT_DISABLED='1',
                GH_PAGER='cat', GH_NO_UPDATE_NOTIFIER='1', GH_NO_EXTENSION_UPDATE_NOTIFIER='1')


def repository_name(value):
    if not isinstance(value, str):
        raise CarryError('invalid_github_repository')
    value = value.strip()
    if value.startswith('https://'):
        url = urlsplit(value)
        if url.netloc != 'github.com' or url.query or url.fragment:
            raise CarryError('invalid_github_repository')
        value = url.path.strip('/')
    if value.endswith('.git'):
        value = value[:-4]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}', value):
        raise CarryError('invalid_github_repository')
    if value.split('/')[1] in ('.', '..'):
        raise CarryError('invalid_github_repository')
    return value


def folder_name(value):
    if not isinstance(value, str) or '\\' in value or '\x00' in value:
        raise CarryError('invalid_github_folder')
    if value.startswith('/') or any(p in ('.', '..') for p in value.split('/')):
        raise CarryError('invalid_github_folder')
    return value.strip('/')


def api(path, timeout=45):
    if not path.startswith('/') or path.startswith('//'):
        raise CarryError('invalid_github_api_path')
    try:
        result = subprocess.run([executable(), 'api', '--hostname', 'github.com', '--method', 'GET', path],
                                env=environment(), capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise CarryError('github_unreachable')
    if result.returncode:
        # Never return stderr: it can contain credentials or a signed URL.
        raise CarryError('github_access_failed')
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise CarryError('github_invalid_response')


def account():
    try:
        user = api('/user')
        return dict(state='connected', login=user['login'])
    except CarryError as exc:
        return dict(state='unavailable', error=str(exc))


def repositories(page=1):
    if type(page) is not int or not 1 <= page <= 1000:
        raise CarryError('invalid_github_page')
    data = api(f'/user/repos?per_page=100&page={page}&sort=updated&affiliation=owner,collaborator,organization_member')
    return dict(repositories=[dict(name=r['full_name'], private=r['private'],
                                  branch=r['default_branch']) for r in data],
                next_page=page + 1 if len(data) == 100 else None)


class Login:
    """Device code stays in memory; no credential is read into Carry."""
    def __init__(self):
        env = environment()
        env['GH_BROWSER'] = '/usr/bin/true'  # the native UI opens the fixed device URL
        self.result = dict(state='starting', verification_uri='https://github.com/login/device')
        self.process = subprocess.Popen([executable(), 'auth', 'login', '--hostname', 'github.com',
            '--git-protocol', 'https', '--web'], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self._read, daemon=True).start()
        self.timer = threading.Timer(900, self.cancel)
        self.timer.daemon = True
        self.timer.start()

    def _read(self):
        for line in self.process.stderr:
            match = re.search(r'\b([A-Z0-9]{4}-[A-Z0-9]{4})\b', line)
            if match:
                self.result = dict(state='waiting_for_browser', user_code=match.group(1),
                                   verification_uri='https://github.com/login/device')
        code = self.process.wait()
        self.result = dict(state='connected' if code == 0 else 'failed')
        self.process.stderr.close()

    def cancel(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.result = dict(state='cancelled')


def download(repo, commit, destination):
    command = [executable(), 'api', '--hostname', 'github.com', '--method', 'GET',
               f'/repos/{repo}/tarball/{commit}']
    with open(destination, 'wb') as out, tempfile.TemporaryFile() as error:
        proc = subprocess.Popen(command, stdout=out, stderr=error, env=environment())
        deadline = time.monotonic() + 180
        try:
            while proc.poll() is None:
                if time.monotonic() > deadline or destination.stat().st_size > MAX_ARCHIVE:
                    raise CarryError('github_archive_limit')
                time.sleep(0.1)
            if proc.returncode:
                raise CarryError('github_download_failed')
            if destination.stat().st_size > MAX_ARCHIVE:
                raise CarryError('github_archive_limit')
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def extract_markdown(archive, destination, folder=''):
    """No extractall: reject traversal and never materialize links or devices."""
    count = total = entries = 0
    names = set()
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r|gz') as tar:
        for item in tar:
            entries += 1
            if entries > 200_000:
                raise CarryError('github_archive_limit')
            path = PurePosixPath(item.name)
            if path.is_absolute() or '..' in path.parts or '\\' in item.name:
                raise CarryError('github_unsafe_archive')
            relative = PurePosixPath(*path.parts[1:])  # GitHub archive root prefix
            if not item.isfile() or relative.suffix.lower() != '.md':
                continue
            if any(p.startswith('.') or p in ('node_modules', '__pycache__') for p in relative.parts):
                continue
            if folder:
                try:
                    relative = relative.relative_to(folder)
                except ValueError:
                    continue
            if not relative.parts or item.size > 2_000_000:
                continue
            count += 1
            total += item.size
            if count > MAX_FILES or total > MAX_CONTENT:
                raise CarryError('github_content_limit')
            if relative in names:
                raise CarryError('github_duplicate_archive_path')
            names.add(relative)
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(item) as inp, target.open('xb') as out:
                shutil.copyfileobj(inp, out)
    if not count:
        raise CarryError('github_no_markdown_in_folder')
    return count


def connect(workspace, source_id, repository, branch='', folder='', progress=None):
    repo = repository_name(repository)
    folder = folder_name(folder)
    if not SOURCE_ID_RE.fullmatch(source_id or ''):
        raise CarryError('invalid_source_id')
    if any(s.source_id == source_id for s in workspace.sources):
        raise CarryError('duplicate_source_id')
    metadata = api(f'/repos/{repo}')
    branch = branch.strip() or metadata['default_branch']
    if not branch or len(branch) > 255 or any(ord(c) < 32 for c in branch):
        raise CarryError('invalid_github_branch')
    config = dict(repository=repo, branch=branch, folder=folder, commit='', synced_at=0)
    return _sync(workspace, source_id, config, adding=True, progress=progress)


def sync(workspace, source_id, progress=None):
    source = workspace.source(source_id)
    if not source.github:
        raise CarryError('source_is_not_github')
    return _sync(workspace, source_id, source.github, progress=progress)


def _sync(workspace, source_id, config, adding=False, progress=None):
    from .maintenance import job_progress
    progress = progress or (lambda **kw: job_progress(workspace, **kw))
    repo = repository_name(config['repository'])
    progress(stage='checking_github', source_id=source_id)
    commit = api(f'/repos/{repo}/commits/{quote(config["branch"], safe="")}')['sha']
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise CarryError('github_invalid_commit')
    if config.get('commit') == commit:
        updated_config = dict(config, synced_at=time.time())
        with writer_lock(workspace):
            current = Workspace.load(workspace.state_dir)
            old = current.source(source_id)
            if old.github != config:
                raise CarryError('github_source_changed_retry')
            current.with_sources([replace(s, github=updated_config) if s.source_id == source_id else s
                                  for s in current.sources]).save()
        return dict(status='unchanged', source_id=source_id, commit=commit)
    cache = workspace.state_dir / 'github' / source_id
    if not cache.resolve().is_relative_to(workspace.state_dir.resolve()):
        raise CarryError('github_cache_outside_workspace')
    cache.mkdir(parents=True, exist_ok=True)
    # Each download gets an independent immutable root, even after a prior failure.
    with tempfile.TemporaryDirectory(prefix='download-', dir=cache) as temp:
        archive = Path(temp) / 'source.tar.gz'
        content = Path(temp) / 'content'
        progress(stage='downloading_github', source_id=source_id)
        download(repo, commit, archive)
        count = extract_markdown(archive, content, config['folder'])
        destination = cache / (commit + '-' + str(time.time_ns()))
        os.replace(content, destination)
    updated_config = dict(config, commit=commit, synced_at=time.time())
    with writer_lock(workspace):
        current = Workspace.load(workspace.state_dir)
        if adding:
            source = SourceConfig(source_id, destination, scope='team', github=updated_config)
            updated = current.with_sources([*current.sources, source])
        else:
            old = current.source(source_id)
            if old.github != config:
                raise CarryError('github_source_changed_retry')
            updated = current.with_sources([replace(s, root=destination, github=updated_config)
                                            if s.source_id == source_id else s for s in current.sources])
        updated.save()
    return dict(status='synced', source_id=source_id, commit=commit, files=count)


def citation_url(source, path):
    info = source.github
    if not info or not info.get('commit'):
        return None
    relative = '/'.join(filter(None, (info.get('folder'), path)))
    return f'https://github.com/{info["repository"]}/blob/{info["commit"]}/{quote(relative, safe="/")}'
