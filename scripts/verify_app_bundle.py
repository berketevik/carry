"""Installed-bundle setup/hook/MCP contract check with synthetic vendor payloads.

Run with Carry.app/Contents/Resources/python/bin/python3 -I -B. Real installed
client versions are probed, but these payloads are synthetic, not native events.
Personal settings are never edited; every project and source is temporary.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from carry import capture, connections, lifecycle
from carry.config import Workspace, SourceConfig, EmbeddingConfig
from carry.desktop import Bridge, executable_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = dict(native_event_emission=False, synthetic_vendor_payloads=True, clients={})
    with tempfile.TemporaryDirectory(prefix='carry-bundle-contract-') as temp:
        base = Path(temp).resolve()
        records = base / 'records'
        records.mkdir()
        ws = Workspace.create(base / 'state', sources=[SourceConfig('records', records, True)],
                              embedding=EmbeddingConfig(provider='hashing'))
        for client in ('claude', 'codex'):
            project = base / (client + ' project with spaces')
            project.mkdir()
            plan = connections.preview(ws, client, project, 'records', prompts=True, proposals=True,
                                       executable=executable_for(client))
            connections.apply(ws, plan)
            hook_path = project / ('.claude/settings.local.json' if client == 'claude' else '.codex/hooks.json')
            hook = json.loads(hook_path.read_text())['hooks']['UserPromptSubmit'][0]['hooks'][0]['command']
            payload = dict(hook_event_name='UserPromptSubmit', session_id='bundle-synthetic',
                prompt='Cedar bundle test decision.', **{('prompt_id' if client == 'claude' else 'turn_id'): 'one'})
            for _ in range(2):
                run = subprocess.run(shlex.split(hook), input=json.dumps(payload), text=True,
                    capture_output=True, cwd=project, env={'PATH': '/usr/bin:/bin'}, timeout=15)
                assert run.returncode == 0 and run.stdout == '', run.stderr
            receipt = next(a['receipt'] for a in capture.capture_status(ws)['adapters'] if a['client'] == client)
            assert receipt['state'] == 'duplicate'
            tools = subprocess.run([sys.executable, '-I', '-B', '-m', 'carry.mcp_server', '--workspace', str(ws.state_dir), '--client', client],
                input=json.dumps(dict(jsonrpc='2.0', id=1, method='tools/list'))+'\n', text=True,
                capture_output=True, cwd=project, env={'PATH': '/usr/bin:/bin'}, timeout=15)
            assert any(t['name'] == 'carry_propose' for t in json.loads(tools.stdout)['result']['tools'])
            bridge = Bridge()
            review = bridge.dispatch(dict(action='review', workspace=str(ws.state_dir), record_id=receipt['proposal_id']))
            accepted = bridge.dispatch(dict(action='accept', workspace=str(ws.state_dir), record_id=receipt['proposal_id'],
                                            revision=review['revision'], review_token=review['review_token']))
            assert accepted['status'] == 'accepted'
            capture.set_paused(ws, client, True)
            before = sorted(str(p) for p in records.rglob('*.md'))
            payload['prompt_id' if client == 'claude' else 'turn_id'] = 'paused-event'
            subprocess.run(shlex.split(hook), input=json.dumps(payload), text=True, capture_output=True,
                           cwd=project, env={'PATH': '/usr/bin:/bin'}, timeout=15, check=True)
            assert before == sorted(str(p) for p in records.rglob('*.md'))
            # Undo requires exact snapshots; undo the deliberate pause first.
            capture.set_paused(ws, client, False)
            # Formatting can differ after another writer; rollback correctly
            # refuses any changed bytes, then an explicit fresh setup is needed.
            rollback_state = 'not_attempted_after_state_edits'
            report['clients'][client] = dict(capability=plan['capability'], generated_command_silent=True,
                replay_one_event_one_draft=True, bundled_mcp_proposals=True, app_bridge_accept=True,
                paused_no_new_markdown=True, rollback=rollback_state)
        assert len(lifecycle.list_proposals(ws, include_reviewed=True)) == 2
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
