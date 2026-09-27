"""Local app bridge: one bounded JSON request/response, over private stdio pipes.

No listener, telemetry, model downloads or shell execution. A fresh Workspace
is loaded for every command so CLI/hook edits remain visible to the app.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from dataclasses import replace

from . import capture, connections, index, lifecycle, github, maintenance
from .config import EmbeddingConfig, RetrievalConfig, SourceConfig, Workspace
from .errors import CarryError
from .paths import resolve_within
from .persistence import atomic_text, writer_lock
from .status import status

MAX_REQUEST = 1_048_576


def executable_for(client):
    found = shutil.which(client)
    candidates = [Path.home() / '.local/bin' / client, Path('/opt/homebrew/bin') / client,
                  Path('/usr/local/bin') / client, Path.home() / '.npm-global/bin' / client]
    if client == 'codex':
        candidates += [Path('/Applications/Codex.app/Contents/Resources/codex'),
                       Path('/Applications/ChatGPT.app/Contents/Resources/codex')]
    return found or next((str(p) for p in candidates if p.is_file()), client)


class Bridge:
    def __init__(self):
        self.plans = {}
        self.vault_plans = {}
        self.login = None

    def dispatch(self, request):
        if not isinstance(request, dict) or not isinstance(request.get('action'), str):
            raise CarryError('invalid_desktop_request')
        action = request['action']
        if action == 'ping':
            import resource
            return dict(ready=True, python=sys.version.split()[0], pid=os.getpid(),
                        max_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024))
        state = request.get('workspace')
        if not isinstance(state, str) or not state.strip():
            raise CarryError('workspace_required')
        if action == 'initialize':
            # Nothing to download and nothing loaded on this Mac: keyword search that the
            # client's carry-recall subagent queries several times and judges.
            from .models import KEYWORD_ASSISTANT
            ws = Workspace.create(state, embedding=EmbeddingConfig(provider='hashing'),
                                  retrieval=RetrievalConfig(**KEYWORD_ASSISTANT))
            return status(ws)
        ws = Workspace.load(state)
        if action == 'github_account':
            return github.account()
        if action == 'github_repositories':
            return github.repositories(request.get('page', 1))
        if action == 'github_login':
            if self.login is None or self.login.process.poll() is not None:
                self.login = github.Login()
            return self.login.result
        if action == 'github_login_status':
            return self.login.result if self.login else dict(state='idle')
        if action == 'github_login_cancel':
            if self.login:
                self.login.cancel()
            return dict(state='cancelled')
        if action == 'github_connect':
            return maintenance.start(ws, action, source_id=request['source_id'],
                repository=request['repository'], branch=request.get('branch', ''), folder=request.get('folder', ''))
        if action == 'github_sync':
            return maintenance.start(ws, action, source_id=request['source_id'])
        if action == 'model_setup':
            return maintenance.start(ws, action, model=request['model'])
        if action == 'maintenance_status':
            maintenance.automatic(ws)
            return maintenance.job_status(ws)
        if action == 'source_exclusions':
            if not isinstance(request.get('exclude'), list) or not all(isinstance(p, str) for p in request['exclude']):
                raise CarryError('invalid_source_exclusions')
            with writer_lock(ws):
                current = Workspace.load(state)
                current.source(request['source_id'])
                current.with_sources([replace(s, exclude=tuple(request['exclude'])) if s.source_id == request['source_id'] else s for s in current.sources]).save()
            return maintenance.start(Workspace.load(state))
        if action == 'source_files':
            from .index import corpus_snapshot
            src = ws.source(request['source_id'])
            return dict(files=sorted(path for sid, path in corpus_snapshot(ws) if sid == src.source_id))
        if action == 'snapshot':
            maintenance.automatic(ws)
            report = status(ws, probe_provider=request.get('probe') is True)
            report['activity'] = lifecycle.list_proposals(ws, include_reviewed=True)
            report['connections'] = connections.history(ws)
            return report
        if action == 'add_source':
            if type(request.get('writable', False)) is not bool:
                raise CarryError('invalid_source_permission')
            src = SourceConfig(request['source_id'], Path(request['root']).expanduser().resolve(), request.get('writable', False))
            with writer_lock(ws):
                current = Workspace.load(state)
                updated = current.with_sources([*current.sources, src])
                atomic_text(updated.config_path, json.dumps(updated.to_json(), indent=2) + '\n')
            maintenance.start(updated)
            return status(updated)
        if action == 'embedding':
            provider = request.get('provider')
            if provider not in ('hashing', 'ollama'):
                raise CarryError('unsupported_embedding_provider')
            with writer_lock(ws):
                current = Workspace.load(state)
                updated = replace(current, embedding=EmbeddingConfig(provider=provider, model=request.get('model', current.embedding.model)),
                                  retrieval=replace(current.retrieval, reranker='off'))
                atomic_text(updated.config_path, json.dumps(updated.to_json(), indent=2) + '\n')
            maintenance.start(updated)
            return status(updated, probe_provider=True)
        if action == 'index':
            return maintenance.start(ws, 'index')
        if action == 'recall':
            from .recall import recall
            return recall(ws, request['query'])
        if action == 'review':
            return lifecycle.review(ws, request['record_id'])
        if action in ('accept', 'reject'):
            return getattr(lifecycle, action)(ws, request['record_id'], request['revision'], request['review_token'])
        if action == 'source':
            path = resolve_within(ws.source(request['source_id']).root, request['path'])
            if not path.is_file():
                raise CarryError('source_file_missing')
            return dict(path=str(path))
        if action == 'pause':
            if type(request.get('paused')) is not bool:
                raise CarryError('invalid_pause_state')
            return capture.set_paused(ws, request['client'], request['paused'])
        if action == 'connection_preview':
            client = capture._client(request['client'])
            plan = connections.preview(ws, client, request['project'], request.get('source_id'),
                request.get('prompts', False), request.get('proposals', False),
                request.get('executable') or executable_for(client))
            self.plans = {plan['id']: (str(ws.state_dir), plan)}
            return {k: v for k, v in plan.items() if k != 'changes'}
        if action == 'connection_apply':
            saved = self.plans.get(request['id'])
            if not saved or saved[0] != str(ws.state_dir):
                raise CarryError('preview_again_required')
            result = connections.apply(ws, saved[1])
            self.plans.clear()
            return result
        if action == 'vault_preview':
            from . import vault
            language = request.get('language', 'Turkish')
            if not isinstance(language, str) or not language.strip() or type(request.get('capture', False)) is not bool:
                raise CarryError('invalid_vault_request')
            plan = vault.plan(request['target'], language=language.strip(), workspace=ws.state_dir,
                              capture=request.get('capture', False), attach=True)
            self.vault_plans = {plan['id']: (str(ws.state_dir), plan)}
            return dict({k: v for k, v in plan.items() if k != 'changes'},
                        files=[dict(rel=c['rel'], status=c['status']) for c in plan['changes']])
        if action == 'vault_apply':
            from . import vault
            saved = self.vault_plans.get(request['id'])
            if not saved or saved[0] != str(ws.state_dir):
                raise CarryError('preview_again_required')
            result = vault.apply(saved[1], git=request.get('git', True) is not False)
            self.vault_plans.clear()
            maintenance.start(Workspace.load(state))
            return result
        if action == 'connection_rollback':
            return connections.rollback(ws, request['id'])
        if action == 'connection_test':
            # Local transport test, deliberately unbound: cannot stand in for a
            # native client's session, capture receipt or trust approval.
            messages = [dict(jsonrpc='2.0', id=1, method='initialize', params={}),
                        dict(jsonrpc='2.0', method='notifications/initialized'),
                        dict(jsonrpc='2.0', id=2, method='tools/list'),
                        dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='carry_status', arguments={}))]
            proc = subprocess.run([sys.executable, *(['-I'] if sys.flags.isolated else []), '-m', 'carry.mcp_server', '--workspace', str(ws.state_dir)],
                input=''.join(json.dumps(m) + '\n' for m in messages), capture_output=True, text=True, timeout=20)
            replies = [json.loads(line) for line in proc.stdout.splitlines()]
            valid = (proc.returncode == 0 and len(replies) == 3 and
                     replies[0].get('result', {}).get('serverInfo', {}).get('name') == 'carry' and
                     any(t['name'] == 'carry_recall' for t in replies[1].get('result', {}).get('tools', [])) and
                     replies[2].get('result', {}).get('isError') is False)
            return dict(mcp_local_test='passed' if valid else 'failed',
                        native_client='not_verified_by_this_test', capture=capture.capture_status(ws))
        raise CarryError('unknown_desktop_action')


def serve(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    bridge = Bridge()
    while True:
        line = stdin.readline(MAX_REQUEST + 1)
        if not line:
            return 0
        identifier = None
        try:
            if len(line) > MAX_REQUEST:
                raise CarryError('desktop_request_too_large')
            request = json.loads(line)
            identifier = request.get('request_id') if isinstance(request, dict) else None
            result = dict(ok=True, result=bridge.dispatch(request))
        except CarryError as exc:
            result = dict(ok=False, error=str(exc))
        except Exception as exc:
            result = dict(ok=False, error=type(exc).__name__)
        stdout.write(json.dumps(dict(result, request_id=identifier), ensure_ascii=False, default=str) + '\n')
        stdout.flush()
        if len(line) > MAX_REQUEST:
            return 1


if __name__ == '__main__':
    sys.exit(serve())
