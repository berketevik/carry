"""One-step vault bootstrap: a template pack planned, compared and written like client settings.

Writes only into an empty folder or one this module created before. A file that
differs from what Carry last wrote is a conflict, never an overwrite; an upgrade
touches only files whose hash still matches the stamp.
"""
import datetime
import difflib
import hashlib
from importlib import resources
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid

from . import capture as capture_module
from . import lifecycle
from .config import CONFIG_NAME, EmbeddingConfig, SourceConfig, Workspace
from .connections import _json, _object, _read
from .errors import CarryError
from .paths import is_contained, resolve_root
from .persistence import atomic_text, writer_lock

TEMPLATE_VERSION = '3'
GUIDE_BASE = '3.7'
RECALL_TOOL = 'carry_recall'
STAMP = '.carry/vault.json'
SOURCE_ID = 'vault'
FOLDERS = ('+', 'notes', 'sources', 'log', 'workbench')
IGNORED_WHEN_EMPTY = {'.git', '.carry', '.DS_Store'}
# Captured prompts are raw material and Carry never rewrites a record in place.
RECORDS_DIR = 'sources/carry'
# Recall covers knowledge (notes, sources, log), not policy folders, adapters or indexes.
EXCLUDE = ('+', 'workbench', 'x', 'tools', '*_index.md',
           'Home.md', 'LLM-GUIDE.md', 'VAULT-RULES.md', 'SETUP-GUIDE.md', 'CLAUDE.md', 'AGENTS.md')
# Seeds are the owner's to edit: written when absent, never compared or upgraded.
# VAULT-RULES.md is the owner's local layer on top of the managed guide.
SEEDS = ('Home.md', 'VAULT-RULES.md', '.gitignore', 'x/Templates/')
STATE_HEADING = {'turkish': 'Son durum'}


def _is_seed(rel):
    return rel.endswith('/.gitkeep') or any(rel == s or (s.endswith('/') and rel.startswith(s)) for s in SEEDS)


def _published(rel):
    # Dotfiles are stored as dot_* so package data globs pick them up.
    return '/'.join('.' + p[4:] if p.startswith('dot_') else p for p in rel.split('/'))


def _walk(node, prefix=''):
    for child in sorted(node.iterdir(), key=lambda c: c.name):
        rel = prefix + child.name
        if child.is_dir():
            yield from _walk(child, rel + '/')
        elif not child.name.startswith('.') and child.name != '__pycache__':
            yield rel, child.read_text(encoding='utf-8')


def render(language='Turkish', today=None):
    values = {'{{LANGUAGE}}': language, '{{RECALL_TOOL}}': RECALL_TOOL, '{{GUIDE_BASE}}': GUIDE_BASE,
              '{{TEMPLATE_VERSION}}': TEMPLATE_VERSION, '{{CREATED}}': (today or datetime.date.today()).isoformat(),
              '{{STATE_HEADING}}': STATE_HEADING.get(language.lower(), 'Current state')}
    files = {}
    for rel, text in _walk(resources.files('carry') / 'templates' / 'vault'):
        for key, value in values.items():
            text = text.replace(key, value)
        files[_published(rel)] = text
    for folder in FOLDERS:
        files.setdefault(folder + '/.gitkeep', '')
    return files


def _wiring(workspace):
    def args(client):
        return ['-I', '-m', 'carry.mcp_server', '--workspace', str(workspace), '--client', client]
    claude = _json({'mcpServers': {'carry': dict(type='stdio', command=sys.executable, args=args('claude'))}})
    codex = ('[mcp_servers.carry]\ncommand = ' + json.dumps(sys.executable) +
             '\nargs = ' + json.dumps(args('codex')) + '\n')
    # Device-local pointer for `carry context` run inside the vault; gitignored like the wiring.
    local = _json({'workspace': str(workspace)})
    return {'.mcp.json': claude, '.codex/config.toml': codex, '.carry/local.json': local,
            '.claude/settings.local.json': _claude_settings(workspace, capture=False),
            '.codex/hooks.json': _codex_hooks(workspace, capture=False)}


def _session_start(workspace):
    return [{'hooks': [dict(type='command', timeout=10, command=shlex.join(
        [sys.executable, '-I', '-m', 'carry.cli', '--workspace', str(workspace), 'context']))]}]


def _session_end(workspace):
    # Starts the harvest of the closed thread in the background and returns at once.
    return [{'hooks': [dict(type='command', timeout=3, command=shlex.join(
        [sys.executable, '-I', '-m', 'carry.cli', '--workspace', str(workspace), 'harvest', '--from-hook']))]}]


def _codex_hooks(workspace, capture):
    hooks = {'SessionStart': _session_start(workspace), 'SessionEnd': _session_end(workspace)}
    if capture:
        command = shlex.join([sys.executable, '-I', '-m', 'carry.hook', '--workspace', str(workspace), '--client', 'codex'])
        hooks['UserPromptSubmit'] = [{'hooks': [dict(type='command', command=command, timeout=10)]}]
    return _json({'hooks': hooks})


def _claude_settings(workspace, capture):
    hooks = {'SessionStart': _session_start(workspace), 'SessionEnd': _session_end(workspace)}
    if capture:
        command = shlex.join([sys.executable, '-I', '-m', 'carry.hook', '--workspace', str(workspace), '--client', 'claude'])
        hooks['UserPromptSubmit'] = [{'hooks': [dict(type='command', command=command, timeout=10)]}]
    return _json({'hooks': hooks})


def _hooks(workspace):
    def handlers(client):
        command = shlex.join([sys.executable, '-I', '-m', 'carry.hook', '--workspace', str(workspace),
                              '--client', client])
        return _json({'hooks': {'UserPromptSubmit': [{'hooks': [dict(type='command', command=command, timeout=10)]}]}})
    return {'.claude/settings.local.json': _claude_settings(workspace, capture=True), '.codex/hooks.json': _codex_hooks(workspace, capture=True)}


def _has_user_files(target):
    return any(p.is_file() and p.relative_to(target).parts[0] not in IGNORED_WHEN_EMPTY
               and p.name != '.gitkeep' for p in target.rglob('*'))


def _check_workspace(target, workspace, capture, attach=False):
    if is_contained(target, workspace):
        raise CarryError('state_dir_inside_source_root')
    if (workspace / CONFIG_NAME).exists():
        ws = Workspace.load(workspace)
        sources = [s for s in ws.sources if resolve_root(s.root) == target]
        if not sources:
            if not attach:
                raise CarryError('workspace_missing_vault_source')
            # attach_source() adds it at apply time; refuse now what it would refuse then.
            if any(s.source_id == SOURCE_ID for s in ws.sources):
                raise CarryError('duplicate_source_id')
            if any(is_contained(target, resolve_root(s.root)) or is_contained(resolve_root(s.root), target)
                   for s in ws.sources):
                raise CarryError('nested_source_roots')
            return
        if capture and not sources[0].writable:
            raise CarryError('workspace_vault_source_read_only')


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def plan(target, language='Turkish', workspace=None, today=None, capture=False, attach=False):
    """attach: the workspace exists and gains the vault as a source when the plan is applied."""
    if capture and workspace is None:
        raise CarryError('capture_requires_workspace')
    if attach and workspace is None:
        raise CarryError('attach_requires_workspace')
    target = Path(target).expanduser().resolve()
    if target.exists() and not target.is_dir():
        raise CarryError('vault_target_not_directory')
    stamp_text = _read(target / STAMP)
    stamp = _object(stamp_text) if stamp_text is not None else None
    if stamp is None and target.exists() and _has_user_files(target):
        # Existing notes are never reorganized; bootstrap only fresh or Carry-made folders.
        raise CarryError('vault_target_not_empty')
    files = render(language, today)
    if workspace is not None:
        workspace = Path(workspace).expanduser().resolve()
        _check_workspace(target, workspace, capture, attach)
        files.update(_wiring(workspace))
        if capture:
            files.update(_hooks(workspace))
    known = (stamp or {}).get('files', {})
    changes, conflicts = [], []
    for rel, after in sorted(files.items()):
        before = _read(target / rel)
        if before == after:
            continue
        seed = _is_seed(rel)
        if before is not None and (seed or known.get(rel) != _digest(before)):
            if not seed:
                conflicts.append(rel)
            continue
        changes.append(dict(rel=rel, path=str(target / rel), before=before, after=after,
                            status='create' if before is None else 'update'))
    hashes = dict(known)
    hashes.update({c['rel']: _digest(c['after']) for c in changes if not _is_seed(c['rel'])})
    # No workspace path here: the stamp is committed and travels between machines.
    new_stamp = _json(dict(template_version=TEMPLATE_VERSION, guide_base=GUIDE_BASE, language=language,
                           files=dict(sorted(hashes.items()))))
    if changes and new_stamp != stamp_text:
        changes.append(dict(rel=STAMP, path=str(target / STAMP), before=stamp_text, after=new_stamp,
                            status='create' if stamp_text is None else 'update'))
    return dict(id=uuid.uuid4().hex, target=str(target), template_version=TEMPLATE_VERSION,
                workspace=str(workspace) if workspace else None, capture=capture, attach=attach,
                changes=changes, conflicts=conflicts,
                summary=''.join(''.join(difflib.unified_diff(
                    (c['before'] or '').splitlines(True), c['after'].splitlines(True),
                    fromfile=c['rel'] + ' (before)', tofile=c['rel'] + ' (after)')) for c in changes))


def _journal(target, identifier):
    if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise CarryError('invalid_vault_journal_id')
    return Path(target) / '.carry' / 'journal' / (identifier + '.json')


def _write(path, value):
    path = Path(path)
    if value is None:
        if path.exists():
            path.unlink()
    else:
        atomic_text(path, value)


def _configure_capture(workspace):
    """Capability is probed per client; an unsupported client is reported, not fatal."""
    states = {}
    for client in capture_module.CLIENTS:
        try:
            status = capture_module.configure(workspace, client, 'vault', opt_in=True)
            states[client] = next(a['state'] for a in status['adapters'] if a['client'] == client)
        except CarryError as exc:
            states[client] = str(exc)
    return states


def _configure_proposals(workspace):
    """The guide's correction rule needs carry_propose; acceptance stays with the owner."""
    states = {}
    for client in ('claude', 'codex'):  # the generic 'agent' client stays off until the owner enables it
        try:
            states[client] = 'enabled' if lifecycle.configure_client(workspace, client, 'vault', True)['enabled'] else 'disabled'
        except CarryError as exc:
            states[client] = str(exc)
    return states


def _source(target, plan):
    return SourceConfig(source_id=SOURCE_ID, root=target, writable=bool(plan.get('capture')),
                        records_dir=RECORDS_DIR, exclude=EXCLUDE)


def attach_source(workspace, target, plan):
    """Adds the vault to an existing workspace; a source already rooted there is kept as it is."""
    with writer_lock(Workspace.load(workspace)):
        current = Workspace.load(workspace)
        if any(resolve_root(s.root) == Path(target) for s in current.sources):
            return False
        current.with_sources([*current.sources, _source(Path(target), plan)]).save()
    return True


def apply(plan, git=True):
    """Writes the safe changes; conflicting files are left as they are and reported."""
    target = Path(plan['target'])
    if not plan['changes'] and not plan.get('capture'):
        return dict(id=None, state='unchanged', target=str(target), files=0, conflicts=plan['conflicts'],
                    initialised_git=False, created_workspace=False, capture={}, proposals={})
    for c in plan['changes']:
        if _read(Path(c['path'])) != c['before']:
            raise CarryError('vault_changed_since_preview')
    path = _journal(target, plan['id'])
    journal = dict(plan, state='applying')
    atomic_text(path, _json(journal))  # durable undo before the first mutation
    for c in plan['changes']:
        _write(c['path'], c['after'])
    created_workspace = False
    if plan['workspace'] and not (Path(plan['workspace']) / CONFIG_NAME).exists():
        # Offline default, like the app; `carry model` switches to semantic embeddings.
        Workspace.create(plan['workspace'], sources=[_source(target, plan)], embedding=EmbeddingConfig(provider='hashing'))
        created_workspace = True
    elif plan.get('attach'):
        attach_source(plan['workspace'], target, plan)
    capture = _configure_capture(Workspace.load(plan['workspace'])) if plan.get('capture') else {}
    proposals = _configure_proposals(Workspace.load(plan['workspace'])) if plan.get('capture') else {}
    initialised_git = False
    if git and not (target / '.git').exists() and shutil.which('git'):
        subprocess.run(['git', 'init', '-q', str(target)], check=True, capture_output=True)
        initialised_git = True
    journal.update(state='applied', initialised_git=initialised_git, created_workspace=created_workspace,
                   capture=capture, proposals=proposals)
    atomic_text(path, _json(journal))
    return dict(id=plan['id'], state='applied', target=str(target), files=len(plan['changes']),
                conflicts=plan['conflicts'], initialised_git=initialised_git,
                created_workspace=created_workspace, capture=capture, proposals=proposals)


def rollback(target, identifier):
    """Undo one apply. Refuses when a managed file was edited afterwards; edited seeds stay."""
    path = _journal(target, identifier)
    journal = _object(_read(path))
    if journal.get('state') == 'rolled_back':
        return dict(id=identifier, state='rolled_back')
    if journal.get('state') not in ('applying', 'applied'):
        raise CarryError('vault_journal_invalid')
    for c in journal['changes']:
        if not _is_seed(c['rel']) and _read(Path(c['path'])) not in (c['before'], c['after']):
            raise CarryError('vault_changed_since_install:' + c['rel'])
    for c in reversed(journal['changes']):
        if _read(Path(c['path'])) == c['after']:
            _write(c['path'], c['before'])
    journal['state'] = 'rolled_back'
    atomic_text(path, _json(journal))
    # Created folders, the git repository and the workspace are left for the user to remove.
    return dict(id=identifier, state='rolled_back')
