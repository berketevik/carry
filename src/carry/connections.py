"""Reviewable project-local setup with compare-before-write and recovery journals.

Never runs a shell or trusts client hooks. Rollback refuses later edits, including
unrelated ones: it must not silently undo work done after installation.
"""
import difflib
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tomllib
import uuid

from . import capture, lifecycle
from .errors import CarryError
from .persistence import atomic_text, writer_lock


def _read(path):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise CarryError('settings_symlink_refused')
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 1_000_000:
        raise CarryError('settings_file_invalid')
    # Bytes, not read_text: newline translation would turn CRLF into LF and break exact rollback.
    return path.read_bytes().decode('utf-8')


def _object(text):
    data = json.loads(text) if text is not None else {}
    if not isinstance(data, dict):
        raise CarryError('settings_object_required')
    return data


def _json(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


def preview(workspace, client, project, source_id=None, prompts=False, proposals=False, executable=None, language=None):
    capture._client(client)
    if type(prompts) is not bool or type(proposals) is not bool:
        raise CarryError('invalid_opt_in')
    capability = capture.probe_client(client, executable)
    # A hook feature gate need not prevent read-only MCP setup.
    if capability['state'] != 'supported' and (prompts or capability['state'] != 'hooks_disabled'):
        raise CarryError(capability['state'])
    project = Path(project).expanduser().resolve()
    if not project.is_dir():
        raise CarryError('project_directory_required')
    if prompts or proposals:
        source = workspace.writable_source(source_id)
        if source.scope != 'personal':
            raise CarryError('personal_destination_required')
    changes = []

    def change(path, after):
        before = _read(path)
        if before != after:
            changes.append(dict(path=str(path), before=before, after=after,
                mode=stat.S_IMODE(path.stat().st_mode) if before is not None else 0o600))

    args = ['-I', '-m', 'carry.mcp_server', '--workspace', str(workspace.state_dir), '--client', client]
    if client == 'claude':
        path = project / '.mcp.json'
        data = _object(_read(path))
        servers = data.setdefault('mcpServers', {})
        desired = dict(type='stdio', command=sys.executable, args=args)
        if 'carry' in servers and servers['carry'] != desired:
            raise CarryError('existing_carry_connection_conflict')
        servers['carry'] = desired
        change(path, _json(data))
        hook_path = project / '.claude' / 'settings.local.json'
    else:
        path = project / '.codex' / 'config.toml'
        old = _read(path) or ''
        data = tomllib.loads(old)
        desired = dict(command=sys.executable, args=args)
        existing = data.get('mcp_servers', {}).get('carry')
        if existing is not None and existing != desired:
            raise CarryError('existing_carry_connection_conflict')
        if existing is None:
            nl = '\r\n' if '\r\n' in old else '\n'  # the file's own line endings
            new = old + nl.join(['', '[mcp_servers.carry]', 'command = ' + json.dumps(sys.executable),
                                 'args = ' + json.dumps(args), ''])
            # Inline tables or exotic conflicting layouts are refused, never rewritten.
            tomllib.loads(new)
            change(path, new)
        hook_path = project / '.codex' / 'hooks.json'
    # The carry-recall subagent judges recall passages whenever Jev is not the judge. A Carry
    # vault already has it; an existing one, customised or not, is left alone.
    from . import vault  # vault imports this module
    rel = '.claude/agents/carry-recall.md' if client == 'claude' else '.codex/agents/carry-recall.toml'
    if _read(project / rel) is None:
        change(project / rel, vault.render(language or _language(project))[rel])
    if prompts:
        data = _object(_read(hook_path))
        handlers = data.setdefault('hooks', {}).setdefault('UserPromptSubmit', [])
        if not isinstance(handlers, list):
            raise CarryError('invalid_hook_settings')
        command = shlex.join([sys.executable, '-I', '-m', 'carry.hook', '--workspace', str(workspace.state_dir), '--client', client])
        handler = {'hooks': [dict(type='command', command=command, timeout=10)]}
        if handler not in handlers:
            if any('carry.hook' in json.dumps(h) for h in handlers):
                raise CarryError('existing_carry_hook_conflict')
            handlers.append(handler)
        change(hook_path, _json(data))
        conf = capture._config(workspace)
        prior = conf['clients'].get(client, {})
        conf['clients'][client] = dict(source_id=source.source_id, opt_in=True,
            paused=prior.get('paused', False), capability=capability,
            executable=str(executable or client), write_owner='hook')
        change(workspace.state_dir / 'capture.json', _json(conf))
    if proposals:
        policy = lifecycle._policy(workspace)
        policy[client] = dict(enabled=True, source_id=source.source_id)
        change(workspace.state_dir / 'proposals.json', _json(policy))
    return dict(id=uuid.uuid4().hex, client=client, project=str(project), capability=capability,
        changes=changes, summary='\n'.join(''.join(difflib.unified_diff(
            (c['before'] or '').splitlines(True), c['after'].splitlines(True),
            fromfile=c['path'] + ' (before)', tofile=c['path'] + ' (after)')) for c in changes),
        trust='Restart the client in this project. Review its project/MCP approval. ' +
              ('Use /hooks to trust the exact hook definition. ' if client == 'codex' and prompts else '') +
              'Submit a labelled synthetic prompt, then refresh Carry to inspect its capture receipt.')


def _language(project):
    """The notes' language from a Carry vault's stamp, else English."""
    from . import vault
    try:
        return json.loads((project / vault.STAMP).read_text(encoding='utf-8')).get('language') or 'English'
    except (OSError, ValueError, AttributeError):
        return 'English'


def _journal_path(workspace, identifier):
    if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise CarryError('invalid_connection_id')
    return workspace.state_dir / 'connections' / (identifier + '.json')


def _write(change, value):
    path = Path(change['path'])
    if value is None:
        if path.exists():
            path.unlink()
    else:
        atomic_text(path, value)
        os.chmod(path, change['mode'])


def apply(workspace, plan):
    """Plan is kept in the local bridge, never accepted from arbitrary UI JSON."""
    with writer_lock(workspace):
        for c in plan['changes']:
            if _read(Path(c['path'])) != c['before']:
                raise CarryError('settings_changed_since_preview')
        journal = dict(plan, state='applying')
        path = _journal_path(workspace, plan['id'])
        atomic_text(path, _json(journal))  # durable undo before the first mutation
        for c in plan['changes']:
            if _read(Path(c['path'])) != c['before']:
                raise CarryError('settings_changed_during_apply')
            _write(c, c['after'])
        journal['state'] = 'applied'
        atomic_text(path, _json(journal))
    return dict(id=plan['id'], state='applied', trust=plan['trust'])


def rollback(workspace, identifier):
    with writer_lock(workspace):
        path = _journal_path(workspace, identifier)
        journal = _object(_read(path))
        if journal.get('state') == 'rolled_back':
            return dict(id=identifier, state='rolled_back')
        if journal.get('state') not in ('applying', 'applied'):
            raise CarryError('connection_journal_invalid')
        for c in journal['changes']:
            if _read(Path(c['path'])) not in (c['before'], c['after']):
                raise CarryError('settings_changed_since_install')
        for c in reversed(journal['changes']):
            current = _read(Path(c['path']))
            if current == c['before']:
                continue
            if current != c['after']:
                raise CarryError('settings_changed_during_rollback')
            _write(c, c['before'])
        journal['state'] = 'rolled_back'
        atomic_text(path, _json(journal))
    return dict(id=identifier, state='rolled_back')


def history(workspace):
    result = []
    for path in sorted((workspace.state_dir / 'connections').glob('*.json')):
        data = _object(_read(path))
        result.append({k: data[k] for k in ('id', 'client', 'project', 'state')})
    return result
