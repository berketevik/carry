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
import time
from dataclasses import replace

from . import capture, connections, lifecycle, github, maintenance
from .background import detached, system_tool, which
from .config import EmbeddingConfig, RetrievalConfig, SourceConfig, Workspace
from .errors import CarryError
from .paths import resolve_within
from .persistence import atomic_text, writer_lock
from .status import status

MAX_REQUEST = 1_048_576


def executable_for(client):
    found = which(client)
    candidates = [Path.home() / '.local/bin' / client, Path('/opt/homebrew/bin') / client,
                  Path('/usr/local/bin') / client, Path.home() / '.npm-global/bin' / client]
    if client == 'codex':
        candidates += [Path('/Applications/Codex.app/Contents/Resources/codex'),
                       Path('/Applications/ChatGPT.app/Contents/Resources/codex')]
    return found or next((str(p) for p in candidates if p.is_file()), client)


HARVEST_JOB = 'harvest-now.json'


def harvest_vault(ws, requested):
    """The vault a harvest from this workspace writes to: one of its own local sources."""
    from . import vaultview
    roots = {str(Path(s.root).expanduser().resolve()) for s in ws.sources if not s.github}
    if requested:
        chosen = str(Path(requested).expanduser().resolve())
        if chosen not in roots:
            raise CarryError('harvest_vault_not_a_source')
        return chosen
    sid = vaultview.default_vault(ws)
    if not sid:
        raise CarryError('no_sources')
    return str(Path(ws.source(sid).root).expanduser().resolve())


def harvest_job(ws, proc=None):
    """State of the last harvest started from the app: running while its process lives."""
    try:
        job = json.loads((ws.state_dir / HARVEST_JOB).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(running=False)
    if proc is not None and proc.pid == job.get('pid'):
        code = proc.poll()
        state = dict(job, running=code is None, exit_code=code)
    else:
        state = dict(job, running=_is_harvest(job.get('pid'), ws.state_dir))
    if not state['running']:
        state.update(_harvest_result(ws, job.get('log_offset')))
    return state


def _harvest_result(ws, offset):
    """What the finished run wrote to the log: how many chats it read and which drafts it made."""
    import re
    try:
        with open(ws.state_dir / 'harvest.log', 'rb') as f:
            f.seek(int(offset or 0))
            text = f.read(200_000).decode('utf-8', errors='ignore')
    except (OSError, ValueError, TypeError):
        return {}
    drafts = re.findall(r'(?m)draft: (.+\.md)\s*$', text)
    threads = sum(int(n) for n in re.findall(r'threads (\d+):', text))
    return dict(drafts=drafts, threads=threads)


def _is_harvest(pid, state_dir):
    """True while `pid` is still a harvest of this workspace (a reused pid is some other command)."""
    try:
        command = _command_line(int(pid))
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False
    return ' harvest' in command and str(state_dir) in command


def _command_line(pid):
    if sys.platform != 'win32':
        return subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, timeout=5).stdout
    # No ps on Windows, and wmic is being removed: ask CIM, in UTF-8 so non-ASCII paths survive.
    query = ('[Console]::OutputEncoding = [Text.Encoding]::UTF8; '
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine")
    return subprocess.run([system_tool(r'WindowsPowerShell\v1.0\powershell.exe'), '-NoProfile', '-NonInteractive', '-Command', query], capture_output=True,
                          text=True, encoding='utf-8', timeout=15, creationflags=subprocess.CREATE_NO_WINDOW).stdout


def carry_executable():
    beside = Path(sys.executable).with_name('carry')
    return str(beside) if beside.is_file() else (which('carry') or 'carry')


def _max_rss_bytes():
    if sys.platform != 'win32':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage',
                'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]

    kernel32 = ctypes.WinDLL('kernel32')
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    counters = Counters(cb=ctypes.sizeof(Counters))
    if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return counters.PeakWorkingSetSize


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
            return dict(ready=True, python=sys.version.split()[0], pid=os.getpid(), max_rss_bytes=_max_rss_bytes())
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
        if action == 'jev_status':
            from . import jev
            return dict(key_present=bool(jev.api_key()), reranker=ws.retrieval.reranker)
        if action == 'jev_key':
            from . import jev
            try:
                jev.store_key(request.get('key'))
            except (ValueError, OSError, subprocess.SubprocessError):
                raise CarryError('typesafe_key_not_saved')
            return dict(key_present=bool(jev.api_key()))
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
        if action in ('vault_browse', 'vault_note', 'vault_resolve', 'vault_backlinks', 'vault_overview', 'vault_graph', 'note_create', 'note_approve', 'note_approve_many',
                      'digest_items', 'digest_decide'):
            from . import vaultview
            sid = request.get('source_id') or vaultview.default_vault(ws)
            if not sid:
                raise CarryError('no_sources')
            if action == 'vault_browse':
                return vaultview.browse(ws, sid)
            if action == 'vault_note':
                return vaultview.note(ws, sid, request['path'])
            if action == 'vault_backlinks':
                return vaultview.backlinks(ws, sid, request['path'])
            if action == 'vault_graph':
                from . import graph
                return graph.build(ws, sid, request.get('folders'))
            if action == 'note_create':
                created = vaultview.create_note(ws, sid, request.get('title', ''), request.get('body', ''))
                maintenance.start(ws)
                return created
            if action == 'note_approve':
                return vaultview.approve_note(ws, sid, request['path'])
            if action == 'digest_items':
                from . import digest
                return digest.items(ws, sid, request['path'])
            if action == 'digest_decide':
                from . import digest
                result = digest.decide(ws, sid, request['path'], request.get('item', ''), request.get('decision', ''), request.get('text'))
                maintenance.start(ws)
                return result
            if action == 'note_approve_many':
                return vaultview.approve_many(ws, sid, request.get('paths'))
            if action == 'vault_resolve':
                return vaultview.resolve_link(ws, sid, request['target'], request.get('from'))
            return vaultview.overview(ws, sid)
        if action == 'settings':
            from . import vaultview
            return vaultview.settings(ws)
        if action == 'assistants':
            from . import vaultview
            return vaultview.assistants(ws)
        if action == 'settings_update':
            from . import vaultview
            updated = vaultview.update(ws, retrieval=request.get('retrieval'), sources=request.get('sources'))
            if request.get('sources'):
                maintenance.start(updated)
            return vaultview.settings(updated)
        if action == 'source_remove':
            from . import vaultview
            updated = vaultview.remove_source(ws, request['source_id'])
            maintenance.start(updated)
            return vaultview.settings(updated)
        if action == 'harvest_schedule':
            from . import harvest, vaultview
            current = vaultview.schedule_for(ws)
            if current.get('installed') and not current.get('this_workspace') and request.get('replace') is not True:
                # One user-wide launchd job: never change another workspace's schedule silently.
                raise CarryError('schedule_other_workspace')
            if request.get('enabled') is False:
                harvest.remove_schedule()
                return vaultview.schedule_for(ws)
            vault = harvest_vault(ws, request.get('vault'))
            harvest.set_draft_language(ws, harvest.draft_language(ws, vault))  # pin the language the job used so far
            harvest.install_schedule(carry_executable(), ws.state_dir, vault=vault, language=None,
                                     hour=request.get('hour', 21), minute=request.get('minute', 30))
            return vaultview.schedule_for(ws)
        if action == 'harvest_language':
            from . import harvest, vaultview
            if request.get('language') not in ('Turkish', 'English'):
                raise CarryError('invalid_harvest_language')
            current = vaultview.schedule_for(ws)
            if current.get('this_workspace') and current.get('language'):
                # An older nightly job carries --language, which would override the setting:
                # re-create it without the argument before the setting changes.
                harvest.install_schedule(carry_executable(), ws.state_dir, vault=current.get('vault'), language=None,
                                         hour=current.get('hour', 21), minute=current.get('minute', 30))
            harvest.set_draft_language(ws, request['language'])
            return vaultview.settings(ws)
        if action == 'harvest_now':
            from . import harvest
            if harvest_job(ws).get('running'):
                return dict(started=False, **harvest_job(ws))
            vault = harvest_vault(ws, None)
            args = [carry_executable(), '--workspace', str(ws.state_dir), 'harvest', '--vault', vault]
            log_path = ws.state_dir / 'harvest.log'
            offset = log_path.stat().st_size if log_path.exists() else 0
            with open(log_path, 'a') as log:
                proc = detached(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            self.harvest_proc = proc
            atomic_text(ws.state_dir / HARVEST_JOB, json.dumps(dict(pid=proc.pid, started_at=time.time(), vault=vault, log_offset=offset)) + '\n')
            return dict(started=True, **harvest_job(ws, proc))
        if action == 'harvest_status':
            return harvest_job(ws, getattr(self, 'harvest_proc', None))
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
                input=''.join(json.dumps(m) + '\n' for m in messages), capture_output=True, text=True, encoding='utf-8', timeout=20)
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
