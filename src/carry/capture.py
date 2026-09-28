"""Opt-in user-prompt capture with native event identity and durable receipts.

The hook is the sole draft writer. Raw events are separate Markdown files under
`.events` (not indexed); the linked verbatim draft is a proposal, never a fact.
No transcript, assistant reply, tool output or arbitrary payload field is read.
"""
import datetime as dt
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

from .errors import CarryError
from .markdown import parse_frontmatter
from .mask import mask
from .paths import resolve_within
from .persistence import atomic_text, writer_lock
from . import store

CLIENTS = ('claude', 'codex')
# Clients deliver background-task notices and subagent reports through UserPromptSubmit.
HARNESS_PREFIXES = ('<task-notification>', '<agent-message')
MAX_INPUT_BYTES = 262144
REMEDIES = {
    'not_configured': 'Run carry capture configure with an explicit writable source and prompt opt-in.',
    'opt_in_required': 'Whole-prompt capture requires --opt-in-prompts.',
    'paused': 'Run carry capture resume for this client when ready.',
    'no_receipt': 'Merge the settings fragment, complete client trust, restart and submit a labelled test prompt.',
    'unsupported_version': 'Use Claude Code >=2.1.196 or Codex CLI >=0.153.4; then reconfigure and test.',
    'hooks_disabled': 'Enable hooks in this client, reconfigure and run a connection test.',
    'client_unavailable': 'Install the supported CLI or pass its executable with --client-bin.',
    'missing_event_identity': 'Upgrade the client: Claude must supply prompt_id, Codex must supply turn_id. Text deduplication is not safe.',
    'unsupported_event': 'Connect only the UserPromptSubmit command hook.',
    'harness_event': 'Nothing to do: background-task and subagent reports are not user prompts and are not captured.',
    'invalid_payload': 'Check the hook JSON contract; stdin must contain a supported user-prompt event.',
    'payload_too_large': 'Submit a smaller prompt; this adapter accepts at most 256 KiB of input.',
    'event_identity_conflict': 'The same native event ID arrived with different text; inspect the client before retrying.',
    'failed': 'Check the destination, permissions and available disk space, then replay the same native event.',
}


class CaptureError(CarryError):
    pass


def _client(client):
    if client not in CLIENTS:
        raise CaptureError('unsupported_client')
    return client


def _read_json(path, default):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default
    if not isinstance(data, dict):
        raise CaptureError('invalid_capture_state')
    return data


def _config(workspace):
    data = _read_json(workspace.state_dir / 'capture.json', {'version': 1, 'clients': {}})
    if data.get('version') != 1 or not isinstance(data.get('clients'), dict):
        raise CaptureError('invalid_capture_state')
    return data


def _save_config(workspace, config):
    atomic_text(workspace.state_dir / 'capture.json', json.dumps(config, indent=2) + '\n')


def probe_client(client, executable=None):
    """Version is a capability gate, never evidence that a hook actually ran."""
    _client(client)
    executable = str(executable or client)
    try:
        result = subprocess.run([executable, '--version'], capture_output=True, text=True, encoding='utf-8', timeout=10)
        pattern = r'(\d+)\.(\d+)\.(\d+)'
        match = re.search(pattern, result.stdout)
        if result.returncode or not match:
            return dict(state='client_unavailable')
        version = tuple(int(part) for part in match.groups())
        minimum = (2, 1, 196) if client == 'claude' else (0, 153, 4)
        info = dict(version='.'.join(match.groups()), state='supported')
        if version < minimum:
            return dict(info, state='unsupported_version')
        if client == 'codex':
            features = subprocess.run([executable, 'features', 'list'], capture_output=True,
                                      text=True, encoding='utf-8', timeout=10)
            if features.returncode or not re.search(r'^hooks\s+.*\btrue\s*$', features.stdout, re.M):
                return dict(info, state='hooks_disabled')
        return info
    except (OSError, subprocess.TimeoutExpired):
        return dict(state='client_unavailable')


def configure(workspace, client, source_id, opt_in=False, executable=None):
    _client(client)
    if opt_in is not True:
        raise CaptureError('opt_in_required')
    source = workspace.writable_source(source_id)
    # Prompt capture is personal; a team-labelled destination is never implicit publication.
    if source.scope != 'personal':
        raise CaptureError('capture_requires_personal_source')
    capability = probe_client(client, executable)
    with writer_lock(workspace):
        config = _config(workspace)
        prior = config['clients'].get(client, {})
        config['clients'][client] = dict(source_id=source.source_id, opt_in=True,
            paused=prior.get('paused', False), capability=capability,
            executable=str(executable or client), write_owner='hook')
        _save_config(workspace, config)
    return capture_status(workspace)


def set_paused(workspace, client, paused):
    _client(client)
    with writer_lock(workspace):
        config = _config(workspace)
        if client not in config['clients']:
            raise CaptureError('not_configured')
        config['clients'][client]['paused'] = bool(paused)
        _save_config(workspace, config)
    return capture_status(workspace)


def settings_fragment(workspace, client):
    """A mergeable fragment, never an overwrite of a user's settings file."""
    _client(client)
    conf = _config(workspace)['clients'].get(client)
    if not conf:
        raise CaptureError('not_configured')
    if conf['capability']['state'] != 'supported':
        raise CaptureError(conf['capability']['state'])
    command = shlex.join([sys.executable, '-m', 'carry.hook', '--workspace',
                          str(workspace.state_dir), '--client', client])
    return {'hooks': {'UserPromptSubmit': [{'hooks': [
        {'type': 'command', 'command': command, 'timeout': 10}]}]}}


def _receipt_path(workspace, client):
    return workspace.state_dir / f'capture-{_client(client)}.json'


def _receipt(workspace, client, state, **fields):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds')
    try:
        previous = _read_json(_receipt_path(workspace, client), {})
    except (ValueError, OSError, CaptureError):
        previous = {}
    result = dict(client=client, state=state, at=now,
                  last_success_at=now if state in ('captured', 'duplicate')
                  else previous.get('last_success_at'), **fields)
    if state in REMEDIES:
        result['action'] = REMEDIES[state]
    atomic_text(_receipt_path(workspace, client), json.dumps(result, indent=2) + '\n')
    return result


def capture_status(workspace):
    try:
        config = _config(workspace)
    except (ValueError, OSError, CaptureError):
        return dict(state='failed', adapters=[], action='Repair capture.json; capture is disabled until valid.')
    adapters = []
    for client, conf in config['clients'].items():
        if client not in CLIENTS:
            continue
        try:
            receipt = _read_json(_receipt_path(workspace, client), {})
        except (ValueError, OSError, CaptureError):
            receipt = dict(state='failed', action=REMEDIES['failed'])
        capability = conf.get('capability', {}).get('state', 'client_unavailable')
        state = ('paused' if conf.get('paused') else capability if capability != 'supported'
                 else receipt.get('state', 'no_receipt'))
        adapters.append(dict(client=client, state=state, capability=conf.get('capability'),
            source_id=conf.get('source_id'), prompt_opt_in=conf.get('opt_in') is True,
            write_owner='hook', receipt=receipt or None,
            action=REMEDIES.get(state), capture_scope='user_prompts_only'))
    states = {a['state'] for a in adapters}
    overall = ('not_configured' if not adapters else next(iter(states)) if len(states) == 1 else 'mixed')
    return dict(state=overall, adapters=adapters)


def normalize(client, payload):
    _client(client)
    if not isinstance(payload, dict):
        raise CaptureError('invalid_payload')
    if payload.get('hook_event_name') != 'UserPromptSubmit':
        raise CaptureError('unsupported_event')
    session = payload.get('session_id')
    native_id = payload.get('prompt_id' if client == 'claude' else 'turn_id')
    if any(not isinstance(s, str) or not s.strip() or len(s) > 512 for s in (session, native_id)):
        raise CaptureError('missing_event_identity')
    prompt = payload.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip():
        raise CaptureError('invalid_payload')
    if prompt.lstrip().startswith(HARNESS_PREFIXES):
        raise CaptureError('harness_event')
    event_id = 'evt_' + hashlib.sha256(json.dumps([client, session, native_id]).encode()).hexdigest()
    content, categories = mask(prompt)
    return dict(event_id=event_id, content=content, masked=categories,
                content_digest=hashlib.sha256(content.encode()).hexdigest())


def _persist(workspace, client, source, event):
    root = Path(source.root).expanduser().resolve()
    relative = str(Path(source.records_dir) / '.events' / (event['event_id'] + '.md'))
    path = resolve_within(root, relative)
    duplicate = path.exists()
    if duplicate:
        fm, _ = parse_frontmatter(path.read_text(encoding='utf-8'))
        if fm.get('content_digest') != event['content_digest']:
            raise CaptureError('event_identity_conflict')
    else:
        fm = dict(type='capture_event', event_id=event['event_id'], client=client,
                  content_digest=event['content_digest'], capture_scope='user_prompts_only',
                  created=dt.datetime.now(dt.timezone.utc).isoformat(), masked=event['masked'])
        atomic_text(path, store._frontmatter_block(fm) + '\n\n# Captured user prompt\n\n' + event['content'] + '\n')
    # Runs under the shared writer lock. A crash between these two canonical
    # writes is repaired by replay; the store recovers even without its ledger.
    proposal = store._write_record(workspace, 'Captured prompt — review required',
        event['content'], event_id=event['event_id'], source_id=source.source_id,
        source_refs=[source.source_id + ':' + relative], surface=client, author='user', state='draft',
        summary='Verbatim captured prompt; unreviewed proposal, not an accepted decision.')
    if proposal['state'] == 'missing':
        raise CaptureError('failed')
    return _receipt(workspace, client, 'duplicate' if duplicate and proposal['duplicate'] else 'captured',
        event_id=event['event_id'], source_id=source.source_id, event_path=relative,
        proposal_id=proposal['record_id'], proposal_path=proposal['path'],
        proposal_state=proposal['state'], proposal_revision=proposal['revision'],
        masked=event['masked'], write_owner='hook')


def ingest(workspace, client, stream):
    """Read only after the per-client pause/consent gate, and never log payloads."""
    _client(client)
    with writer_lock(workspace):
        try:
            conf = _config(workspace)['clients'].get(client)
            if not conf:
                return _receipt(workspace, client, 'not_configured')
            if conf.get('paused'):
                return _receipt(workspace, client, 'paused')
            if conf.get('opt_in') is not True:
                return _receipt(workspace, client, 'opt_in_required')
            capability = conf.get('capability', {}).get('state', 'client_unavailable')
            if capability != 'supported':
                return _receipt(workspace, client, capability)
            source = workspace.writable_source(conf['source_id'])
            if source.scope != 'personal':
                raise CaptureError('capture_requires_personal_source')
            raw = stream.read(MAX_INPUT_BYTES + 1)
            size = len(raw) if isinstance(raw, bytes) else len(raw.encode('utf-8'))
            if size > MAX_INPUT_BYTES:
                raise CaptureError('payload_too_large')
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                raise CaptureError('invalid_payload')
            return _persist(workspace, client, source, normalize(client, payload))
        except Exception as exc:
            code = str(exc) if isinstance(exc, CaptureError) and str(exc) in REMEDIES else 'failed'
            return _receipt(workspace, client, code, error_type=type(exc).__name__)
