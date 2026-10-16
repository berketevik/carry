"""A vault's Carry files live in its `.carry/` folder; `carry state move` brings an existing setup there.

    <vault>/.carry/
      .gitignore     written by Carry: only the shared files below are committed
      settings.json  shared, committed: the same search settings on every device (shared.py)
      vault.json     the template stamp (committed, as before)
      local.json     this device's pointer to its state
      state/         this device's state: workspace.json, the index, locks, status, recap state,
                     GitHub snapshots, meeting files, owner, proposals

The move writes `.gitignore` first and, in a git vault, checks that git really ignores the
device files before anything lands next to the notes. It holds Carry's locks while it renames
the state directory into `.carry/state` (a copy and check when that is another disk), leaves a
symlink at the old path so every MCP config, hook, launchd job and script that names it keeps
working, writes or adopts `settings.json`, and points the wiring Carry owns at the new place.
A vault in iCloud Drive, a cloud-storage folder or Dropbox keeps its state outside: a database
written all the time does not survive file sync.
"""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

from . import shared
from .config import CONFIG_NAME, Workspace
from .errors import CarryError
from .filelock import try_lock, unlock
from .persistence import atomic_text

GITIGNORE = """# Written by Carry. Only the shared files below are committed; everything else in .carry/
# (state/, local.json, journal/) belongs to this device and never leaves it.
*
!.gitignore
!settings.json
!vault.json
!team.json
"""
LOCKS = ('maintenance.lock', 'index.db.lock', 'records.lock')


def cloud_service(path):
    """The file-sync service that holds `path`, or None."""
    home = Path.home()
    try:
        path = Path(path).expanduser().resolve()
    except OSError:
        return None
    places = [(home / 'Library' / 'Mobile Documents', 'iCloud'), (home / 'Dropbox', 'Dropbox')]
    for root, name in places:
        if path == root or root in path.parents:
            return name
    storage = home / 'Library' / 'CloudStorage'
    if storage in path.parents:
        provider = path.relative_to(storage).parts[0]
        for prefix, name in (('OneDrive', 'OneDrive'), ('Dropbox', 'Dropbox'), ('GoogleDrive', 'Google Drive'),
                             ('Box', 'Box')):
            if provider.startswith(prefix):
                return name
        return provider.split('-')[0] or 'cloud storage'
    return None


def _git(root, *args):
    try:
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, encoding='utf-8')
    except OSError:
        return None


def is_git(root):
    done = _git(root, 'rev-parse', '--is-inside-work-tree')
    return bool(done) and done.returncode == 0 and done.stdout.strip() == 'true'


def ignore_check(root):
    """Problems that would let device files reach git, and whether settings.json can be committed."""
    problems = []
    for rel in ('.carry/state/workspace.json', '.carry/local.json'):
        done = _git(root, 'check-ignore', '-q', '--no-index', rel)
        if done is None or done.returncode != 0:
            problems.append('not_ignored:' + rel)
    tracked = _git(root, 'ls-files', '--', '.carry/state', '.carry/local.json')
    if tracked is not None and tracked.stdout.strip():
        problems.append('already_tracked:' + ','.join(tracked.stdout.split()))
    done = _git(root, 'check-ignore', '-q', '--no-index', '.carry/settings.json')
    settings_ignored = bool(done) and done.returncode == 0
    return problems, settings_ignored


def _hold_locks(state_dir, wait):
    handles = []
    deadline = time.monotonic() + wait
    try:
        for name in LOCKS:
            handle = open(Path(state_dir) / name, 'a')
            handles.append(handle)
            while not try_lock(handle):
                if time.monotonic() >= deadline:
                    raise CarryError('state_move_busy: an index build or maintenance run holds ' + name)
                time.sleep(0.1)
    except BaseException:
        _release(handles)
        raise
    return handles


def _release(handles):
    for handle in handles:
        try:
            unlock(handle)
        except OSError:
            pass
        handle.close()


def _files(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.stat().st_size
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def _relocate(old, target):
    """Rename on one disk; across disks copy, compare and only then remove the original."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.stat(old).st_dev == os.stat(target.parent).st_dev:
        os.rename(old, target)
        return 'renamed'
    shutil.copytree(old, target, symlinks=True)
    if _files(old) != _files(target):
        shutil.rmtree(target, ignore_errors=True)
        raise CarryError('state_move_copy_mismatch')
    shutil.rmtree(old)
    return 'copied'


# -- wiring that names the old path ----------------------------------------------------------------

def _swap_command(command, old, new):
    """Replace `old` (or a path under it) where it stands as one shell word, quoting the new one."""
    word = re.compile(r"(?<!\S)(?:'" + re.escape(old) + r"((?:/[^'\s]*)?)'|" + re.escape(old) + r"((?:/[^\s'\"]*)?))(?!\S)")

    def put(m):
        suffix = m.group(1) if m.group(1) is not None else (m.group(2) or '')
        return shlex.quote(new + suffix)
    return word.sub(put, command)


def _swap_json(value, old, new):
    if isinstance(value, dict):
        return {k: _swap_json(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [_swap_json(v, old, new) for v in value]
    if isinstance(value, str):
        if value == old or value.startswith(old + '/'):
            return new + value[len(old):]
        if old in value:
            return _swap_command(value, old, new)
    return value


def _swap_toml(text, old, new):
    quoted = re.escape(json.dumps(old)[1:-1])
    return re.sub('"' + quoted + r'((?:/[^"]*)?)"', lambda m: json.dumps(new + m.group(1)), text)


def _tracked(root, rel):
    done = _git(root, 'ls-files', '--error-unmatch', rel)
    return bool(done) and done.returncode == 0


def _wiring_files(vault):
    home = Path.home()
    files = [(vault / '.mcp.json', 'json'), (vault / '.claude' / 'settings.local.json', 'json'),
             (vault / '.claude' / 'settings.json', 'json'), (vault / '.codex' / 'hooks.json', 'json'),
             (vault / '.codex' / 'config.toml', 'toml'),
             (home / '.codex' / 'hooks.json', 'json'), (home / '.codex' / 'config.toml', 'toml'),
             (home / '.claude' / 'settings.json', 'json')]
    # A committed file of the vault is the same on every device: never point it at this device's state.
    return [(p, kind) for p, kind in files if not (vault in p.parents and _tracked(vault, p.relative_to(vault).as_posix()))]


def rewire(vault, old, new, dry_run=False):
    """Point the device wiring that names `old` at `new`; returns the files that change."""
    changed = []
    for path, kind in _wiring_files(Path(vault)):
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue
        if old not in text:
            continue
        if kind == 'toml':
            after = _swap_toml(text, old, new)
            if after == text:
                continue
        else:
            try:
                data = json.loads(text)
            except ValueError:
                continue
            swapped = _swap_json(data, old, new)
            if swapped == data:  # the path only appears inside other words (a permission rule, say)
                continue
            after = json.dumps(swapped, indent=2, ensure_ascii=False) + '\n'
        changed.append(str(path))
        if not dry_run:
            atomic_text(path.with_name(path.name + '.bak-carry'), text)
            atomic_text(path, after)
    return changed


def _rewire_schedule(old, new, dry_run=False):
    """A nightly sweep job that names the old state is installed again with the new one."""
    if sys.platform != 'darwin':
        return None
    from . import harvest
    path = harvest.schedule_path()
    try:
        text = Path(path).read_text(encoding='utf-8')
    except OSError:
        return None
    if old not in text:
        return None
    if dry_run:
        return str(path)
    import plistlib
    data = plistlib.loads(text.encode('utf-8'))
    args = list(data.get('ProgramArguments', []))
    opt = lambda flag: args[args.index(flag) + 1] if flag in args[:-1] else None
    when = data.get('StartCalendarInterval', {})
    harvest.install_schedule(args[0], new, vault=opt('--vault'), language=opt('--language'),
                             hour=when.get('Hour', 21), minute=when.get('Minute', 30))
    return str(path)


def _rewire_app(old, new, dry_run=False):
    """The macOS app opens the workspace it last used."""
    if sys.platform != 'darwin' or not shutil.which('defaults'):
        return None
    from .app_install import BUNDLE_ID
    done = subprocess.run(['defaults', 'read', BUNDLE_ID, 'workspace'], capture_output=True, text=True)
    current = done.stdout.strip() if done.returncode == 0 else ''
    if current not in (old,) and not current.startswith(old + '/'):
        return None
    if not dry_run:
        subprocess.run(['defaults', 'write', BUNDLE_ID, 'workspace', new], capture_output=True)
    return 'app'


# -- GitHub snapshots ---------------------------------------------------------------------------

def _snapshot_time(path):
    try:
        return int(path.name.rsplit('-', 1)[1])
    except (IndexError, ValueError):
        return 0


def prune_snapshots(workspace, dry_run=False):
    """Keep each GitHub source's current snapshot and the one before it; returns what goes."""
    removed = []
    for src in workspace.sources:
        if not src.github:
            continue
        cache = Path(workspace.state_dir) / 'github' / src.source_id
        if not cache.is_dir():
            continue
        try:
            current = Path(src.root).expanduser().resolve()
        except OSError:
            continue
        snapshots = sorted((p for p in cache.iterdir() if p.is_dir() and not p.name.startswith('download-')),
                           key=_snapshot_time, reverse=True)
        others = [p for p in snapshots if p.resolve() != current]
        for old in others[1:]:
            removed.append(f'{src.source_id}/{old.name}')
            if not dry_run:
                shutil.rmtree(old, ignore_errors=True)
                sidecar = old.parent / (old.name + '.commits.json')
                if sidecar.exists():
                    sidecar.unlink()
    return removed


# -- the move -------------------------------------------------------------------------------------

def _rewrite_config(target, old, home_id):
    path = Path(target) / CONFIG_NAME
    data = json.loads(path.read_text(encoding='utf-8'))
    for src in data.get('sources', []):
        root = str(src.get('root', ''))
        if root == old or root.startswith(old + '/'):
            src['root'] = str(target) + root[len(old):]
    if home_id:
        data['home'] = home_id
    atomic_text(path, json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def _set_home(workspace, home_id):
    path = Path(workspace.state_dir) / CONFIG_NAME
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('home') == home_id:
        return False
    data['home'] = home_id
    atomic_text(path, json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    return True


def recorded_language(workspace, vault):
    """The recaps' language as this device or vault recorded it; None rather than a default."""
    from . import harvest
    for path in (Path(workspace.state_dir) / harvest.LANGUAGE_NAME, Path(vault) / shared.CARRY_DIR / 'vault.json'):
        try:
            chosen = json.loads(path.read_text(encoding='utf-8')).get('language')
        except (OSError, ValueError, AttributeError):
            continue
        if chosen in shared.LANGUAGES:
            return chosen
    status = harvest.schedule_status()
    owner = status.get('workspace')
    if status.get('language') in shared.LANGUAGES and owner and \
            Path(owner).expanduser().resolve() == Path(workspace.state_dir).resolve():
        return status['language']
    return None


def move(workspace, vault, dry_run=False, shared_only=False, wait=10.0, out=None, language=None):
    """Bring this workspace into `<vault>/.carry/` (see the module text). Safe to run again.
    `language` sets the recaps' language in a new settings.json; without it the recorded one is used,
    and none is written when nothing recorded it."""
    say = out or (lambda line: None)
    if language is not None and language not in shared.LANGUAGES:
        raise CarryError('invalid_language')
    steps = []

    def step(action, detail=''):
        steps.append(dict(action=action, detail=detail))
        say(('would ' if dry_run else '') + action + (f': {detail}' if detail else ''))

    vault = Path(vault).expanduser().resolve()
    if not vault.is_dir():
        raise CarryError('state_move_vault_missing')
    home = next((s for s in workspace.sources if Path(s.root).expanduser().resolve() == vault), None)
    if home is None and not shared_only:
        raise CarryError('state_move_vault_not_a_source: add the vault as a source first')
    if home is not None and home.scope != 'personal':
        raise CarryError('state_move_team_source: a team repository never holds a member\'s state')
    carry_dir = vault / shared.CARRY_DIR
    target = carry_dir / shared.STATE_NAME
    old = Path(workspace.state_dir).resolve()

    # 1. .gitignore before anything of this device's lands next to the notes.
    gitignore = carry_dir / '.gitignore'
    try:
        current_ignore = gitignore.read_text(encoding='utf-8')
    except OSError:
        current_ignore = None
    if current_ignore != GITIGNORE:
        step('write .carry/.gitignore', 'only settings.json, vault.json and team.json are committed')
        if not dry_run:
            if current_ignore is not None:
                atomic_text(gitignore.with_name('.gitignore.bak-carry'), current_ignore)
            atomic_text(gitignore, GITIGNORE)
    git = is_git(vault)
    settings_ignored = False
    if git and not dry_run:
        problems, settings_ignored = ignore_check(vault)
        if problems:
            raise CarryError('state_move_not_ignored: ' + ', '.join(problems))
        step('git ignores the device files', '.carry/state/, .carry/local.json')
    elif git:
        step('check that git ignores .carry/state/ and .carry/local.json')

    # 2. settings.json: written from this device, or adopted from the vault when another wrote it.
    settings_path = carry_dir / shared.SETTINGS_NAME
    existing, warnings = shared.read(settings_path)
    explicit = language
    language = language or shared.language(workspace, vault) or recorded_language(workspace, vault)
    mine = shared.values(replace_home(workspace, home), language=language)
    adopted = {}
    if existing:
        adopted = shared.differences(dict(retrieval=mine['retrieval'], embedding=mine['embedding'],
                                          exclude=mine.get('exclude')), existing)
        step('adopt .carry/settings.json', 'already in the vault' +
             (': ' + '; '.join(f"{k} {v['device']} -> {v['shared']}" for k, v in adopted.items()) if adopted else ', same values'))
        settings = dict(existing, version=shared.VERSION)
        if explicit and existing.get('language') != explicit:
            settings['language'] = explicit
            step('set the recaps language in .carry/settings.json', explicit)
            if not dry_run:
                shared.write(settings_path, settings)
    else:
        settings = mine
        step('write .carry/settings.json', 'the search settings every device of this vault uses' +
             ('' if language else '; no recaps language is recorded here, so none is written (pass --language)'))
        if not dry_run:
            shared.write(settings_path, settings)
    if settings_ignored:
        step('warning', 'git ignores .carry/settings.json: commit it with `git add -f`, or the other devices never get it')
    if home is not None and in_place(workspace, target) is False and not dry_run:
        _set_home(workspace, home.source_id)
    report = dict(vault=str(vault), state_dir=str(old), moved=False, settings=settings, adopted=adopted,
                  warnings=warnings, gitignore=GITIGNORE, steps=steps, dry_run=dry_run)
    if shared_only:
        return report

    # 3. The state itself.
    cloud = cloud_service(vault)
    if cloud:
        step('keep the state outside the vault', f'{cloud} syncs this folder, and a database written all the time does not survive file sync')
        report['reason'] = 'cloud:' + cloud
        return report
    if old == target.resolve():
        step('state already in the vault', str(target))
        report.update(state_dir=str(target), already=True)
        removed = prune_snapshots(Workspace.load(target), dry_run=dry_run)
        if removed:
            step('prune old GitHub snapshots', f'{len(removed)} removed')
        return report
    if target.exists() and any(target.iterdir()):
        raise CarryError('state_move_target_exists: ' + str(target))
    removed = prune_snapshots(workspace, dry_run=dry_run)
    if removed:
        step('prune old GitHub snapshots', f'{len(removed)} removed, the current and the previous kept')
    step('move the state', f'{old} -> {target}')
    step('leave a symlink', f'{old} -> {target}, so everything that names the old path keeps working')
    if dry_run:
        changed = rewire(vault, str(old), str(target), dry_run=True)
        for path in changed:
            step('point at the new state', path)
        for extra in (_rewire_schedule(str(old), str(target), dry_run=True), _rewire_app(str(old), str(target), dry_run=True)):
            if extra:
                step('point at the new state', extra)
        return report
    handles = _hold_locks(old, wait)
    try:
        if target.exists():
            target.rmdir()  # empty, checked above
        how = _relocate(old, target)
    finally:
        _release(handles)
    os.symlink(target, old, target_is_directory=True)
    _rewrite_config(target, str(old), home.source_id)
    atomic_text(carry_dir / 'local.json', json.dumps(dict(workspace=str(target)), indent=2) + '\n')
    changed = rewire(vault, str(old), str(target))
    for path in changed:
        step('point at the new state', path)
    for extra in (_rewire_schedule(str(old), str(target)), _rewire_app(str(old), str(target))):
        if extra:
            step('point at the new state', extra)
    moved = Workspace.load(target)  # validates the new place
    report.update(state_dir=str(moved.state_dir), moved=True, how=how, rewired=changed)
    return report


def replace_home(workspace, home):
    """The workspace with `home` naming this vault, so shared.values picks its exclusions."""
    from dataclasses import replace
    return replace(workspace, home=home.source_id) if home is not None else workspace


def in_place(workspace, target):
    return Path(workspace.state_dir).resolve() == Path(target).resolve()


def describe(workspace):
    """Where this workspace's files are, for `carry state show` and status."""
    path = shared.settings_path(workspace)
    settings, warnings = shared.read(path)
    vault = shared.in_vault_root(workspace.state_dir)
    try:
        from .config import device_values
        differ = shared.differences(device_values(workspace), settings) if settings else {}
    except Exception:
        differ = {}
    home = shared.home_source(workspace)
    return dict(state_dir=str(workspace.state_dir), in_vault=vault is not None,
                vault=str(vault) if vault else (str(Path(home.root).expanduser()) if home else None),
                settings=str(path) if path and path.is_file() else None, shared=bool(settings),
                differ=differ, warnings=warnings,
                cloud=cloud_service(vault) if vault else None)
