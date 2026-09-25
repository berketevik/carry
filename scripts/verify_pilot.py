"""Installed, isolated runtime verification; synthetic data and content-free report."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('app', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    app = args.app.resolve()
    python = app/'Contents/Resources/python/bin/python3'
    env = dict(os.environ, PATH='/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1', CARRY_AUTO_INDEX='0')
    with tempfile.TemporaryDirectory(prefix='carry-installed-') as temp:
        state = Path(temp)/'state'
        corpus = Path(temp)/'corpus'
        corpus.mkdir(); (corpus/'release.md').write_text('# Release\nThe sample launch is October 22.')
        before = hashlib.sha256((corpus/'release.md').read_bytes()).hexdigest()
        command = [str(python), '-I', '-B', '-m', 'carry.cli', '--workspace', str(state), '--json']
        def cli(*parts):
            process = subprocess.run(command+list(parts),env=env,cwd=temp,capture_output=True,text=True,timeout=120)
            if process.returncode:
                raise RuntimeError('installed_cli_failed:' + parts[0])
            return json.loads(process.stdout)
        cli('init','--source','sample='+str(corpus),'--embedding','hashing')
        built=cli('index'); evidence=cli('recall','sample launch')
        assert evidence['evidence'][0]['path']=='release.md'
        messages=[dict(jsonrpc='2.0',id=1,method='initialize',params={}),
                  dict(jsonrpc='2.0',id=2,method='tools/call',params=dict(name='carry_status',arguments={}))]
        proc=subprocess.run([str(python),'-I','-B','-m','carry.mcp_server','--workspace',str(state)],
            input=''.join(json.dumps(m)+'\n' for m in messages),env=env,cwd=temp,capture_output=True,text=True,timeout=30)
        replies=[json.loads(line) for line in proc.stdout.splitlines()]
        assert proc.returncode==0 and len(replies)==2 and not replies[-1]['result'].get('isError')
        deps=subprocess.run([str(python),'-I','-B','-c','import torch,sentence_transformers; from carry.github import executable; from carry.models import ollama_binary; import json; print(json.dumps(dict(gh=executable(),ollama=ollama_binary())))'],env=env,cwd=temp,capture_output=True,text=True,timeout=120)
        assert deps.returncode==0, 'bundled_dependencies_failed'
        bins=json.loads(deps.stdout)
        assert all(Path(p).is_relative_to(app) for p in bins.values()), 'external_runtime_dependency'
        gh=subprocess.run([bins['gh'],'--version'],env=env,capture_output=True,text=True,timeout=10)
        assert gh.returncode==0
        assert before==hashlib.sha256((corpus/'release.md').read_bytes()).hexdigest()
        # A first app source addition must complete its index without a manual rebuild.
        newstate=Path(temp)/'auto-state'
        request=[dict(action='initialize',workspace=str(newstate)),
                 dict(action='add_source',workspace=str(newstate),source_id='notes',root=str(corpus))]
        bridge=subprocess.run([str(python),'-I','-B','-m','carry.desktop'],input=''.join(json.dumps(r)+'\n' for r in request),env=env,cwd=temp,capture_output=True,text=True,timeout=30)
        assert all(json.loads(line)['ok'] for line in bridge.stdout.splitlines())
        import time
        deadline=time.monotonic()+20
        while not (newstate/'index.db').exists() and time.monotonic()<deadline: time.sleep(.1)
        assert (newstate/'index.db').exists(), 'automatic_initial_index_failed'
        report=dict(installed_cli=True,mcp=True,bundled_reranker_dependencies=True,
                    bundled_github_cli=True,bundled_ollama_runtime=True,automatic_initial_index=True,
                    source_unchanged=True,files=built['files'],
                    scope='relocated app, isolated PATH on development Mac; not a second-machine test')
        args.output.write_text(json.dumps(report,indent=2)); print(json.dumps(report))

if __name__=='__main__': main()
