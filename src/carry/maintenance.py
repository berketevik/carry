"""Workspace-scoped background jobs with a kernel lock and inspectable progress."""
import fcntl
import json
import os
import subprocess
import sys
import time
import threading

from .config import Workspace
from .errors import CarryError
from .persistence import atomic_text


def job_status(workspace):
    try:
        data = json.loads((workspace.state_dir / 'maintenance.json').read_text())
    except (OSError, ValueError):
        return dict(state='idle')
    if data.get('state') == 'running' and not is_running(workspace):
        return dict(data, state='interrupted')
    return data


def is_running(workspace):
    workspace.state_dir.mkdir(parents=True, exist_ok=True)
    with (workspace.state_dir / 'maintenance.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


def job_progress(workspace, **fields):
    path = workspace.state_dir / 'maintenance.json'
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError):
        old = {}
    payload = dict(old, **fields, at=time.time())
    atomic_text(path, json.dumps(payload))
    return payload


def start(workspace, action='maintain', **arguments):
    if action not in ('maintain', 'github_connect', 'github_sync', 'model_setup', 'index'):
        raise CarryError('invalid_maintenance_action')
    workspace.state_dir.mkdir(parents=True, exist_ok=True)
    lock = (workspace.state_dir / 'maintenance.lock').open('a')
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(state='running', started=False)
        job_progress(workspace, state='running', stage='queued', action=action, error=None,
                     source_id=None, completed=None, total=None, started_at=time.time())
        process = subprocess.Popen([sys.executable, *(['-I'] if sys.flags.isolated else []), '-B',
            '-m', 'carry.maintenance', str(workspace.state_dir), str(lock.fileno()),
            action, json.dumps(arguments)], pass_fds=(lock.fileno(),),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        threading.Thread(target=process.wait, daemon=True).start()
        return dict(state='running', started=True, pid=process.pid)
    except Exception:
        job_progress(workspace, state='failed', error='worker_start_failed')
        raise
    finally:
        # The inherited descriptor keeps the same lock held until the child exits.
        lock.close()


def automatic(workspace):
    setting = os.environ.get('CARRY_AUTO_INDEX')
    if setting == '0' or (setting != '1' and not workspace.retrieval.auto_refresh):
        return 'off'
    last = job_status(workspace)
    if last['state'] == 'running':
        return 'running'
    if time.time() - last.get('at', 0) < workspace.retrieval.refresh_seconds:
        return 'retry_pending' if last['state'] in ('failed', 'interrupted') else 'scheduled'
    return start(workspace)['state']


def sync_status(workspace):
    try:
        return json.loads((workspace.state_dir / 'github.status.json').read_text())
    except (OSError, ValueError):
        return {}


def run(workspace, action, arguments):
    from . import github, index
    if action == 'github_connect':
        github.connect(workspace, **arguments)
    elif action == 'github_sync':
        result = github.sync(workspace, arguments['source_id'])
        states = sync_status(workspace)
        states[arguments['source_id']] = dict(result, checked_at=time.time())
        atomic_text(workspace.state_dir / 'github.status.json', json.dumps(states))
    elif action == 'model_setup':
        from .models import setup
        setup(workspace, arguments['model'])
    elif action == 'maintain':
        states = sync_status(workspace)
        for source in workspace.sources:
            if not source.github:
                continue
            previous = states.get(source.source_id, {})
            checked = max(previous.get('checked_at', 0), source.github.get('synced_at', 0))
            if time.time() - checked < workspace.retrieval.github_sync_seconds:
                continue
            try:
                result = github.sync(Workspace.load(workspace.state_dir), source.source_id)
                states[source.source_id] = dict(result, checked_at=time.time())
            except CarryError as exc:
                states[source.source_id] = dict(status='failed', error=str(exc), checked_at=time.time())
            atomic_text(workspace.state_dir / 'github.status.json', json.dumps(states))
    workspace = Workspace.load(workspace.state_dir)
    job_progress(workspace, stage='indexing')
    if action != 'index' and index.health(workspace)['state'] == 'fresh':
        return dict(status='fresh')
    result = index.build(workspace)
    if result['status'] == 'failed' and not index.db_is_usable(workspace.db_path):
        # Initial model failure must not leave a new user without any search.
        from dataclasses import replace
        from .config import EmbeddingConfig
        fallback = replace(workspace, embedding=EmbeddingConfig(provider='hashing'))
        result = dict(index.build(fallback), fallback='lexical_until_model_available')
    return result


def main():
    state, descriptor, action, arguments = sys.argv[1:]
    workspace = Workspace.load(state)
    lock = int(descriptor)
    try:
        result = run(workspace, action, json.loads(arguments))
        failed = result.get('status') in ('failed', 'reindexing')
        job_progress(workspace, state='failed' if failed else 'complete', stage='finished',
                     result=result, error='index_failed' if failed else None)
    except Exception as exc:
        job_progress(workspace, state='failed', stage='failed',
                     error=str(exc) if isinstance(exc, CarryError) else type(exc).__name__)
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()
