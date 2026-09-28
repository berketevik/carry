"""C04: native hook contracts, receipts, pause, isolation and crash recovery."""
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest import mock

from _support import SRC, WorkspaceCase, symlink_or_skip, tree_digest
from carry import capture, store
from carry.config import Workspace
from carry.errors import SourceError
from carry.markdown import parse_frontmatter
from carry.mcp_server import call_tool
from carry.status import status


class CaptureTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        for client in capture.CLIENTS:
            self.enable(client)

    def enable(self, client):
        with mock.patch('carry.capture.probe_client', return_value={'state': 'supported', 'version': 'test'}):
            capture.configure(self.workspace, client, 'records', opt_in=True)

    def payload(self, client='codex', event='one', prompt='Cedar pilot delivery is October 15, 2026.'):
        return dict(hook_event_name='UserPromptSubmit', session_id='synthetic-session',
                    prompt_id=event, turn_id=event, prompt=prompt,
                    transcript_path='/unused/transcript', assistant_response='not captured')

    def send(self, client='codex', **kwargs):
        return capture.ingest(self.workspace, client, io.StringIO(json.dumps(self.payload(client, **kwargs))))

    def test_configuration_is_not_claimed_as_capture(self):
        for a in capture.capture_status(self.workspace)['adapters']:
            self.assertEqual(a['state'], 'no_receipt')
            self.assertIsNone(a['receipt'])

    def test_both_native_adapters_create_one_raw_and_one_linked_draft(self):
        for client in capture.CLIENTS:
            r = self.send(client)
            self.assertEqual(r['state'], 'captured', r)
            fm, body = parse_frontmatter((self.records / r['proposal_path']).read_text(encoding='utf-8'))
            self.assertEqual(fm['carry_state'], 'draft')
            self.assertEqual(fm['carry_event'], r['event_id'])
            self.assertIn('records:' + r['event_path'], fm['sources'])
            raw = (self.records / r['event_path']).read_text(encoding='utf-8')
            self.assertNotIn('/unused/transcript', raw)
            self.assertNotIn('not captured', raw)
            self.assertIn('October 15', body)
        self.assertEqual(len(list(self.records.rglob('*.md'))), 4)

    def test_harness_events_are_not_captured_as_prompts(self):
        before = sorted(self.records.rglob('*.md'))
        for prompt in ('<task-notification>\n<task-id>x</task-id>\n</task-notification>',
                       '<agent-message from="helper">\nreport\n</agent-message>'):
            for client in capture.CLIENTS:
                self.assertEqual(self.send(client, prompt=prompt)['state'], 'harness_event')
        self.assertEqual(sorted(self.records.rglob('*.md')), before)
        self.assertEqual(self.send('codex', event='two', prompt='<pasted_content>notes</pasted_content> özetle')['state'],
                         'captured')

    def test_replay_is_exactly_once(self):
        for client in capture.CLIENTS:
            first = self.send(client)
            second = self.send(client)
            self.assertEqual(second['state'], 'duplicate')
            self.assertEqual(first['proposal_id'], second['proposal_id'])
        self.assertEqual(len(list(self.records.rglob('*.md'))), 4)

    def test_distinct_ids_with_identical_text_stay_distinct(self):
        first = self.send(event='one')
        second = self.send(event='two')
        self.assertNotEqual(first['event_id'], second['event_id'])
        self.assertNotEqual(first['proposal_id'], second['proposal_id'])

    def test_sessions_are_part_of_identity(self):
        a = self.payload()
        b = dict(a, session_id='other')
        self.assertNotEqual(capture.normalize('codex', a)['event_id'],
                            capture.normalize('codex', b)['event_id'])

    def test_native_ids_required_without_text_or_timestamp_fallback(self):
        for client, key in [('claude', 'prompt_id'), ('codex', 'turn_id')]:
            p = self.payload(client)
            del p[key]
            r = capture.ingest(self.workspace, client, io.StringIO(json.dumps(p)))
            self.assertEqual(r['state'], 'missing_event_identity')
            self.assertIn('action', r)
        self.assertEqual(list(self.records.rglob('*.md')), [])

    def test_conflicting_replay_is_refused(self):
        self.send()
        before = tree_digest(self.records)
        r = self.send(prompt='different prompt with same native id')
        self.assertEqual(r['state'], 'event_identity_conflict')
        self.assertEqual(tree_digest(self.records), before)

    def test_pause_is_checked_before_stdin_is_read(self):
        capture.set_paused(self.workspace, 'codex', True)
        stream = mock.Mock()
        r = capture.ingest(self.workspace, 'codex', stream)
        self.assertEqual(r['state'], 'paused')
        stream.read.assert_not_called()
        self.assertEqual(list(self.records.rglob('*.md')), [])
        self.assertEqual(self.send('claude')['state'], 'captured')
        capture.set_paused(self.workspace, 'codex', False)
        self.assertEqual(self.send()['state'], 'captured')

    def test_no_opt_in_no_prompt_write(self):
        with self.assertRaises(capture.CaptureError):
            capture.configure(self.workspace, 'claude', 'records', opt_in=False)

    def test_configuration_rechecks_without_unpausing(self):
        capture.set_paused(self.workspace, 'codex', True)
        self.enable('codex')
        self.assertEqual(self.send()['state'], 'paused')

    def test_unconfigured_client_does_not_read_input(self):
        workspace = Workspace.create(self.base / 'unconfigured')
        stream = mock.Mock()
        r = capture.ingest(workspace, 'codex', stream)
        self.assertEqual(r['state'], 'not_configured')
        stream.read.assert_not_called()

    def test_readonly_and_team_destinations_refused(self):
        with self.assertRaises(SourceError):
            capture.configure(self.workspace, 'codex', 'corpus', opt_in=True)
        ws = self.workspace.with_sources([replace(s, scope='team') for s in self.workspace.sources])
        with self.assertRaises(capture.CaptureError):
            capture.configure(ws, 'codex', 'records', opt_in=True)

    def test_masking_and_status_never_leak_payload_or_exception_text(self):
        secret = 'sk-' + 'a' * 24
        r = self.send(prompt='Cedar token ' + secret)
        self.assertEqual(r['state'], 'captured')
        for path in self.base.rglob('*'):
            if path.is_file():
                self.assertNotIn(secret.encode(), path.read_bytes())
        status_text = json.dumps(status(self.workspace))
        self.assertNotIn('Cedar token', status_text)
        with mock.patch('carry.capture._persist', side_effect=OSError(secret)):
            r = self.send(event='two')
        self.assertEqual(r['error_type'], 'OSError')
        self.assertNotIn(secret, json.dumps(r))
        self.assertIsNotNone(r['last_success_at'])

    def test_malformed_oversized_and_unsupported_inputs_have_safe_receipts(self):
        for data, expected in [('not json', 'invalid_payload'), ('[]', 'invalid_payload'),
                               ('x' * (capture.MAX_INPUT_BYTES + 1), 'payload_too_large'),
                               (json.dumps(dict(self.payload(), hook_event_name='Stop')), 'unsupported_event')]:
            r = capture.ingest(self.workspace, 'codex', io.StringIO(data))
            self.assertEqual(r['state'], expected)
        self.assertEqual(list(self.records.rglob('*.md')), [])

    def test_symlink_escape_is_refused_without_external_writes(self):
        outside = self.base / 'outside'
        outside.mkdir()
        symlink_or_skip(self, self.records / 'carry', outside, target_is_directory=True)
        self.assertEqual(self.send()['state'], 'failed')
        self.assertEqual(list(outside.iterdir()), [])

    def test_interruption_after_raw_write_is_repaired_on_replay(self):
        with mock.patch('carry.store._write_record', side_effect=OSError('interrupted')):
            self.assertEqual(self.send()['state'], 'failed')
        self.assertEqual(len(list(self.records.rglob('*.md'))), 1)
        self.assertEqual(self.send()['state'], 'captured')
        self.assertEqual(len(list(self.records.rglob('*.md'))), 2)

    def test_interruption_after_draft_before_ledger_is_repaired(self):
        with mock.patch('carry.store._save_ledger', side_effect=OSError('interrupted')):
            self.assertEqual(self.send()['state'], 'failed')
        r = self.send()
        self.assertEqual(r['state'], 'duplicate')
        self.assertEqual(len(list(self.records.rglob('*.md'))), 2)

    def test_failed_receipt_publication_does_not_duplicate_canonical_files(self):
        with mock.patch('carry.capture._receipt', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.send()
        self.assertEqual(self.send()['state'], 'duplicate')
        self.assertEqual(len(list(self.records.rglob('*.md'))), 2)

    def test_deleted_ledger_rebuilds_from_markdown(self):
        first = self.send()
        (self.workspace.state_dir / store.EVENT_LEDGER).unlink()
        second = self.send()
        self.assertEqual(first['proposal_id'], second['proposal_id'])
        self.assertEqual(len(list(self.records.rglob('*.md'))), 2)

    def test_actual_parallel_hook_processes_keep_exactly_once(self):
        env = dict(os.environ, PYTHONPATH=str(SRC))
        command = [sys.executable, '-m', 'carry.hook', '--workspace', str(self.workspace.state_dir),
                   '--client', 'codex']
        def run(_):
            return subprocess.run(command, input=json.dumps(self.payload()), text=True,
                                  capture_output=True, env=env, timeout=15)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(run, range(6)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr, '')
        self.assertEqual(len(list(self.records.rglob('*.md'))), 2)

    def test_raw_events_not_indexed_draft_retrievable_by_fresh_mcp(self):
        r = self.send('claude')
        store.set_state(self.workspace, r['proposal_id'], 'accepted', 1)
        self.build()
        text, error = call_tool('carry_recall', {'query': 'Cedar pilot delivery'}, self.workspace.state_dir)
        self.assertFalse(error)
        self.assertIn('October 15', text)
        self.assertIn(r['proposal_id'], text)
        self.assertIn('state accepted', text)
        self.assertNotIn('Captured user prompt', text)
        self.assertEqual(status(self.workspace)['index']['indexed_files'], 1)
        self.assertEqual(self.send('claude')['proposal_state'], 'accepted')

    def test_other_workspace_never_sees_receipt_or_configuration(self):
        self.send()
        ws = Workspace.create(self.base / 'other')
        self.assertEqual(capture.capture_status(ws)['state'], 'not_configured')
        self.assertFalse((ws.state_dir / 'capture-codex.json').exists())

    def test_settings_fragment_has_one_silent_hook_and_no_mcp_writer(self):
        for client in capture.CLIENTS:
            fragment = capture.settings_fragment(self.workspace, client)
            hooks = fragment['hooks']['UserPromptSubmit'][0]['hooks']
            self.assertEqual(len(hooks), 1)
            self.assertIn('carry.hook', hooks[0]['command'])
            self.assertNotIn('carry_propose', hooks[0]['command'])

    def test_corrupt_config_fails_closed_and_status_answers(self):
        (self.workspace.state_dir / 'capture.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(self.send()['state'], 'failed')
        self.assertEqual(capture.capture_status(self.workspace)['state'], 'failed')
        self.assertEqual(list(self.records.rglob('*.md')), [])

    def test_failed_hook_is_silent_and_does_not_block_client(self):
        result = subprocess.run([sys.executable, '-m', 'carry.hook', '--workspace',
            str(self.workspace.state_dir), '--client', 'codex'], input='malformed', text=True,
            capture_output=True, env=dict(os.environ, PYTHONPATH=str(SRC)), timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(capture.capture_status(self.workspace)['adapters'][1]['state'], 'invalid_payload')


class CapabilityTest(WorkspaceCase):
    def test_missing_client_actionable(self):
        result = capture.configure(self.workspace, 'codex', 'records', True, '/nonexistent/carry-client')
        self.assertEqual(result['adapters'][0]['state'], 'client_unavailable')
        self.assertTrue(result['adapters'][0]['action'])
        with self.assertRaises(capture.CaptureError):
            capture.settings_fragment(self.workspace, 'codex')

    def test_older_claude_with_no_prompt_id_is_unsupported(self):
        with mock.patch('carry.capture.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '2.1.100')):
            self.assertEqual(capture.probe_client('claude')['state'], 'unsupported_version')

    def test_codex_hook_capability_is_probed(self):
        results = [subprocess.CompletedProcess([], 0, 'codex-cli 0.153.4'),
                   subprocess.CompletedProcess([], 0, 'hooks stable false')]
        with mock.patch('carry.capture.subprocess.run', side_effect=results):
            self.assertEqual(capture.probe_client('codex')['state'], 'hooks_disabled')
