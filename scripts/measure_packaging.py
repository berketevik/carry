"""Measure a relocated app's core with no PATH Python or optional dependencies.

This isolates dependencies on the development Mac; it is not a pristine macOS
VM or Gatekeeper test. --ollama probes only the fixed local model, no downloads.
"""
import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile


def distribution(values):
    values = sorted(values)
    return dict(samples=len(values), p50_ms=round(values[len(values)//2] * 1000, 2),
                p95_ms=round(values[math.ceil(len(values)*.95)-1] * 1000, 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ollama', action='store_true')
    args = parser.parse_args()
    report = dict(environment='dependency-isolated process on development Mac; not a pristine OS',
                  model_downloaded=False, real_clean_machine_test=False)
    with tempfile.TemporaryDirectory(prefix='carry-relocation-') as temp:
        base = Path(temp).resolve()
        app = base / 'relocated with spaces' / 'Carry.app'
        shutil.copytree(args.app, app, symlinks=True)
        python = str(app / 'Contents/Resources/python/bin/python3')
        report['app_logical_bytes'] = sum(p.stat().st_size for p in app.rglob('*') if p.is_file() and not p.is_symlink())
        archive = base / 'size-only.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as out:
            for p in app.rglob('*'):
                if p.is_file() and not p.is_symlink():
                    out.write(p, p.relative_to(app.parent))
        report['deflated_payload_bytes'] = archive.stat().st_size
        report['archive_note'] = 'Size estimate only, not a distribution archive; symlinks excluded.'
        env = dict(PATH='/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1')
        command = [python, '-I', '-B', '-u', '-m', 'carry.desktop']
        state = str(base / 'state')

        def run(code):
            return json.loads(subprocess.check_output([python, '-I', '-B', '-c', code], env=env, cwd=base, text=True))

        report['dependencies'] = run('import importlib.util,json,sys,sqlite3; print(json.dumps(dict(python=sys.version.split()[0],sqlite=sqlite3.sqlite_version,optional={m:importlib.util.find_spec(m) is not None for m in ("numpy","yaml","mcp")})))')
        worker = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, cwd=base, env=env)

        def call(action, **fields):
            worker.stdin.write(json.dumps(dict(action=action, workspace=state, **fields)) + '\n')
            worker.stdin.flush()
            reply = json.loads(worker.stdout.readline())
            if not reply['ok']:
                raise RuntimeError(reply['error'])
            return reply['result']

        try:
            call('initialize')
            root = base / 'records'
            root.mkdir()
            call('add_source', source_id='records', root=str(root), writable=True)
            run('from carry.fixtures import install_fixture; import json; install_fixture(' + repr(str(root)) + ', exist_ok=True, include_readme=False); print("true")')
            built = call('index')
            assert built['status'] == 'built', built
            assert call('recall', query='When is Cedar pilot delivery?')['evidence']
            cold = []
            for _ in range(20):
                start = time.perf_counter()
                reply = subprocess.run(command, input=json.dumps(dict(action='snapshot', workspace=state)) + '\n',
                    capture_output=True, text=True, env=env, cwd=base, check=True)
                assert json.loads(reply.stdout)['ok']
                cold.append(time.perf_counter() - start)
            report['fresh_process_snapshot'] = distribution(cold)
            warm = []
            for _ in range(40):
                start = time.perf_counter()
                call('snapshot')
                warm.append(time.perf_counter() - start)
            report['worker_snapshot'] = distribution(warm)
            queries = []
            for _ in range(30):
                start = time.perf_counter()
                call('recall', query='When is Cedar pilot delivery?')
                queries.append(time.perf_counter() - start)
            report['worker_lexical_recall'] = distribution(queries)
            report['worker_peak_rss_bytes'] = call('ping')['max_rss_bytes']
            report['mcp_local_test'] = call('connection_test')['mcp_local_test']
            assert report['mcp_local_test'] == 'passed'
            # An absent local provider must be explicit and leave lexical read available.
            report['absent_provider_probe'] = run('from carry.embedding import OllamaEmbedding; import json; print(json.dumps(OllamaEmbedding(endpoint="http://127.0.0.1:1",timeout=.2).probe()))')
            report['relocated_without_checkout_or_path_python'] = True
            if args.ollama:
                def get(route):
                    with urllib.request.urlopen('http://127.0.0.1:11434/api/' + route, timeout=3) as response:
                        return json.load(response)
                try:
                    tags, resident = get('tags'), get('ps')
                    model = next(m for m in tags['models'] if m['name'].split(':')[0] == 'nomic-embed-text')
                    already_loaded = any(m['name'].split(':')[0] == 'nomic-embed-text' for m in resident['models'])
                    start = time.perf_counter()
                    probe = call('embedding', provider='ollama')
                    probe_ms = round((time.perf_counter()-start)*1000, 2)
                    start = time.perf_counter()
                    indexed = call('index')
                    model_report = dict(model=model['name'], installed_model_bytes=model['size'],
                        already_loaded_before_probe=already_loaded, index_ms=round((time.perf_counter()-start)*1000, 2),
                        first_provider_probe_ms=probe_ms, provider_available=probe['embedding'].get('available'),
                        index_status=indexed['status'], download_time='not measured; already installed')
                    if indexed['status'] == 'built':
                        queries = []
                        for _ in range(30):
                            start = time.perf_counter()
                            call('recall', query='When is Cedar pilot delivery?')
                            queries.append(time.perf_counter()-start)
                        model_report['worker_warm_recall'] = distribution(queries)
                    report['ollama'] = model_report
                    loaded = next((m for m in get('ps')['models'] if m['name'].split(':')[0] == 'nomic-embed-text'), {})
                    model_report['resident_size_bytes'] = loaded.get('size')
                    model_report['resident_vram_bytes'] = loaded.get('size_vram')
                except Exception as exc:
                    report['ollama'] = dict(state='unavailable', error=type(exc).__name__)
        finally:
            worker.stdin.close()
            worker.wait(timeout=20)
        report['worker_exits_on_parent_pipe_close'] = worker.returncode == 0
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
