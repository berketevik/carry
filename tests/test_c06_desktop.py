"""C06 local app boundaries; real files, transactions, pipes and review tokens."""
import io
import json
import os
import subprocess
import sys
import tomllib
from unittest.mock import patch

from _support import WorkspaceCase, SRC, tree_digest
from carry import capture, connections, lifecycle
from carry.config import Workspace
from carry.desktop import Bridge, serve, MAX_REQUEST
from carry.errors import CarryError


class DesktopTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.bridge = Bridge()
        def finish_jobs():
            import time
            from carry.maintenance import is_running
            deadline = time.monotonic() + 10
            while is_running(self.workspace) and time.monotonic() < deadline:
                time.sleep(0.02)
        self.addCleanup(finish_jobs)

    def call(self, action, **fields):
        return self.bridge.dispatch(dict(action=action, workspace=str(self.workspace.state_dir), **fields))

    def test_setup_defaults_to_offline_without_touching_sources(self):
        state = self.base / 'new-state'
        result = self.bridge.dispatch(dict(action='initialize', workspace=str(state)))
        self.assertEqual(result['embedding']['name'], 'hashing')
        retrieval = Workspace.load(state).retrieval
        self.assertEqual((retrieval.reranker, retrieval.top_k, retrieval.vector_min_score), ('off', 8, -1.0))
        self.assertEqual(result['capture']['state'], 'not_configured')
        self.assertEqual(result['workspace']['sources'], [])
        with self.assertRaisesRegex(CarryError, 'already_initialized'):
            self.bridge.dispatch(dict(action='initialize', workspace=str(state)))

    def test_add_readonly_source_and_reopen_fresh_config(self):
        root = self.base / 'extra'
        root.mkdir()
        (root / 'note.md').write_text('Cedar synthetic note', encoding='utf-8')
        before = tree_digest(root)
        self.call('add_source', source_id='extra', root=str(root))
        result = self.call('snapshot')
        self.assertEqual(len(result['workspace']['sources']), 3)
        self.assertFalse(result['workspace']['sources'][-1]['writable'])
        self.call('index')
        self.assertEqual(before, tree_digest(root))

    def test_vault_preview_writes_nothing_and_apply_creates_and_attaches_it(self):
        target = self.base / 'my-vault'
        before = self.workspace.config_path.read_bytes()
        preview = self.call('vault_preview', target=str(target), language='Turkish')
        self.assertNotIn('changes', preview)
        self.assertIn(dict(rel='LLM-GUIDE.md', status='create'), preview['files'])
        self.assertIn('.mcp.json', {f['rel'] for f in preview['files']})
        self.assertFalse(target.exists())
        self.assertEqual(before, self.workspace.config_path.read_bytes())
        result = self.call('vault_apply', id=preview['id'], git=False)
        self.assertEqual(result['state'], 'applied')
        self.assertTrue((target / 'LLM-GUIDE.md').exists())
        sources = {s['source_id']: s for s in self.call('snapshot')['workspace']['sources']}
        self.assertIn('vault', sources)
        with self.assertRaisesRegex(CarryError, 'preview_again_required'):
            self.call('vault_apply', id=preview['id'])

    def test_vault_preview_refuses_a_folder_with_notes_and_bad_fields(self):
        target = self.base / 'notes-here'
        target.mkdir()
        (target / 'mine.md').write_text('# mine', encoding='utf-8')
        with self.assertRaisesRegex(CarryError, 'vault_target_not_empty'):
            self.call('vault_preview', target=str(target))
        with self.assertRaisesRegex(CarryError, 'invalid_vault_request'):
            self.call('vault_preview', target=str(self.base / 'x'), capture='yes')

    def test_add_source_refuses_overlap_and_invalid_write_type(self):
        before = self.workspace.config_path.read_bytes()
        for fields in [dict(source_id='overlap', root=str(self.corpus)),
                       dict(source_id='bad', root=str(self.corpus), writable='false')]:
            with self.assertRaises(CarryError):
                self.call('add_source', **fields)
        self.assertEqual(before, self.workspace.config_path.read_bytes())

    def test_review_accept_and_external_edit_conflict(self):
        old = lifecycle.propose(self.workspace, 'Cedar delivery October 15.', 'a')
        review = self.call('review', record_id=old['record_id'])
        self.call('accept', record_id=old['record_id'], revision=review['revision'], review_token=review['review_token'])
        draft = lifecycle.propose(self.workspace, 'Cedar delivery October 22.', 'b',
            target_id=old['record_id'], expected_revision=1)
        review = self.call('review', record_id=draft['record_id'])
        self.assertIn('-Cedar delivery October 15.', review['diff'])
        self.assertIn('+Cedar delivery October 22.', review['diff'])
        target_path = self.records / old['path']
        target_path.write_text(target_path.read_text(encoding='utf-8') + '\nAn external edit.', encoding='utf-8')
        with self.assertRaises(CarryError):
            self.call('accept', record_id=draft['record_id'], revision=review['revision'], review_token=review['review_token'])
        self.assertEqual(self.call('review', record_id=draft['record_id'])['state'], 'draft')

    def test_reject_preserves_source_and_history(self):
        draft = lifecycle.propose(self.workspace, 'Cedar proposal.', 'reject')
        review = self.call('review', record_id=draft['record_id'])
        self.call('reject', record_id=draft['record_id'], revision=review['revision'], review_token=review['review_token'])
        self.assertEqual(self.call('snapshot')['activity'][0]['state'], 'rejected')
        self.assertTrue((self.records / draft['path']).exists())
        self.assertTrue(list((self.records / 'carry/.history').rglob('*.md')))

    def test_source_open_is_contained_and_resolves_actual_file(self):
        path = self.note('source')
        self.assertEqual(self.call('source', source_id='corpus', path=path.name)['path'], str(path.resolve()))
        for relative in ('../state/workspace.json', str(path)):
            with self.assertRaises(CarryError):
                self.call('source', source_id='corpus', path=relative)
        (self.corpus / 'escape.md').symlink_to(self.workspace.config_path)
        with self.assertRaises(CarryError):
            self.call('source', source_id='corpus', path='escape.md')

    def test_bad_messages_do_not_kill_worker_or_leak_content(self):
        stdin = io.StringIO('[]\nnot json\n' + json.dumps(dict(action='ping', request_id=7)) + '\n')
        stdout = io.StringIO()
        serve(stdin, stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertFalse(replies[0]['ok'])
        self.assertEqual(replies[1]['error'], 'JSONDecodeError')
        self.assertEqual(replies[2]['request_id'], 7)
        self.assertTrue(replies[2]['result']['ready'])

    def test_oversized_request_closes_pipe_after_one_safe_error(self):
        stdout = io.StringIO()
        code = serve(io.StringIO('x' * (MAX_REQUEST + 2)), stdout)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout.getvalue())['error'], 'desktop_request_too_large')

    def test_real_worker_handles_repeated_requests_and_exits_on_eof(self):
        payload = ''.join(json.dumps(dict(action=action, workspace=str(self.workspace.state_dir))) + '\n'
                          for action in ('ping', 'snapshot', 'ping'))
        proc = subprocess.run([sys.executable, '-m', 'carry.desktop'], input=payload,
            capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(SRC)), timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertTrue(all(r['ok'] for r in replies))
        self.assertEqual(replies[0]['result']['pid'], replies[2]['result']['pid'])

    def test_unknown_action_cannot_write(self):
        with self.assertRaisesRegex(CarryError, 'unknown_desktop_action'):
            self.call('exec', command='anything')


class ConnectionTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.project = self.base / 'project with spaces'
        self.project.mkdir()
        self.probe = patch('carry.capture.probe_client', return_value=dict(state='supported', version='99.0.0'))
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def plan(self, client='claude', **kwargs):
        return connections.preview(self.workspace, client, self.project, 'records', **kwargs)

    def test_claude_merge_preview_apply_and_exact_rollback(self):
        mcp = self.project / '.mcp.json'
        original = '{"mcpServers":{"other":{"command":"other"}},"other":true}\n'
        mcp.write_text(original, encoding='utf-8')
        hook = self.project / '.claude/settings.local.json'
        hook.parent.mkdir()
        hook.write_text('{"permissions":{"allow":[]},"hooks":{"UserPromptSubmit":[{"hooks":[]}]}}', encoding='utf-8')
        before = tree_digest(self.project)
        plan = self.plan(prompts=True, proposals=True)
        self.assertEqual(before, tree_digest(self.project))
        self.assertIn('UserPromptSubmit', plan['summary'])
        connections.apply(self.workspace, plan)
        data = json.loads(mcp.read_text(encoding='utf-8'))
        self.assertIn('other', data['mcpServers'])
        self.assertTrue(data['other'])
        self.assertEqual(len(json.loads(hook.read_text(encoding='utf-8'))['hooks']['UserPromptSubmit']), 2)
        self.assertEqual(capture.capture_status(self.workspace)['state'], 'no_receipt')
        self.assertTrue(lifecycle.client_enabled(self.workspace, 'claude'))
        connections.rollback(self.workspace, plan['id'])
        self.assertEqual(before, tree_digest(self.project))
        self.assertEqual(capture.capture_status(self.workspace)['state'], 'not_configured')
        self.assertFalse(lifecycle.client_enabled(self.workspace, 'claude'))

    def test_codex_preserves_comments_and_toml_settings(self):
        path = self.project / '.codex/config.toml'
        path.parent.mkdir()
        old = '# keep my comment\nmodel = "example"\n[mcp_servers.other]\ncommand = "other"\n'
        path.write_text(old, encoding='utf-8')
        plan = self.plan('codex', prompts=True)
        self.assertIn('/hooks', plan['trust'])
        connections.apply(self.workspace, plan)
        self.assertTrue(path.read_text(encoding='utf-8').startswith(old))
        data = tomllib.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(data['model'], 'example')
        self.assertIn('-I', data['mcp_servers']['carry']['args'])
        connections.rollback(self.workspace, plan['id'])
        self.assertEqual(path.read_text(encoding='utf-8'), old)

    def test_codex_block_follows_the_line_endings_of_a_crlf_config(self):
        path = self.project / '.codex/config.toml'
        path.parent.mkdir()
        old = b'# windows file\r\nmodel = "example"\r\n'
        path.write_bytes(old)
        plan = self.plan('codex')
        connections.apply(self.workspace, plan)
        written = path.read_bytes()
        self.assertTrue(written.startswith(old))
        self.assertNotIn(b'\n', written.replace(b'\r\n', b''))
        connections.rollback(self.workspace, plan['id'])
        self.assertEqual(path.read_bytes(), old)

    def test_settings_changed_after_preview_refuses_all_writes(self):
        plan = self.plan(prompts=True)
        (self.project / '.mcp.json').write_text('{"external":true}', encoding='utf-8')
        before = tree_digest(self.project)
        with self.assertRaisesRegex(CarryError, 'changed_since_preview'):
            connections.apply(self.workspace, plan)
        self.assertEqual(before, tree_digest(self.project))

    def test_rollback_refuses_later_unrelated_edit_without_overwriting(self):
        plan = self.plan(prompts=True)
        connections.apply(self.workspace, plan)
        path = self.project / '.mcp.json'
        path.write_text(path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        before = tree_digest(self.project)
        with self.assertRaisesRegex(CarryError, 'changed_since_install'):
            connections.rollback(self.workspace, plan['id'])
        self.assertEqual(before, tree_digest(self.project))

    def test_interrupted_install_can_be_rolled_back(self):
        plan = self.plan(prompts=True, proposals=True)
        original = connections._write
        calls = []
        def interrupt(change, value):
            calls.append(change)
            if len(calls) == 2:
                raise OSError('synthetic interruption')
            original(change, value)
        with patch('carry.connections._write', side_effect=interrupt), self.assertRaises(OSError):
            connections.apply(self.workspace, plan)
        self.assertEqual(connections.history(self.workspace)[0]['state'], 'applying')
        connections.rollback(self.workspace, plan['id'])
        self.assertEqual(tree_digest(self.project), {})

    def test_existing_carry_and_symlinks_are_not_overwritten(self):
        path = self.project / '.mcp.json'
        path.write_text('{"mcpServers":{"carry":{"command":"my-carry"}}}', encoding='utf-8')
        with self.assertRaisesRegex(CarryError, 'existing_carry_connection_conflict'):
            self.plan()
        path.unlink()
        path.symlink_to(self.workspace.config_path)
        with self.assertRaisesRegex(CarryError, 'settings_symlink_refused'):
            self.plan()

    def test_mcp_only_requires_no_prompt_consent_and_never_configures_capture(self):
        plan = self.plan()
        connections.apply(self.workspace, plan)
        self.assertFalse((self.project / '.claude').exists())
        self.assertFalse(lifecycle.client_enabled(self.workspace, 'claude'))
        self.assertEqual(capture.capture_status(self.workspace)['state'], 'not_configured')

    def test_unsupported_client_does_not_write(self):
        with patch('carry.capture.probe_client', return_value=dict(state='unsupported_version')):
            with self.assertRaisesRegex(CarryError, 'unsupported_version'):
                self.plan(prompts=True)
        self.assertEqual(tree_digest(self.project), {})

    def test_apply_token_is_bound_to_workspace_and_worker(self):
        bridge = Bridge()
        plan = bridge.dispatch(dict(action='connection_preview', workspace=str(self.workspace.state_dir),
                                    client='claude', project=str(self.project)))
        second = Workspace.create(self.base / 'other')
        with self.assertRaisesRegex(CarryError, 'preview_again_required'):
            bridge.dispatch(dict(action='connection_apply', workspace=str(second.state_dir), id=plan['id']))
        with self.assertRaisesRegex(CarryError, 'preview_again_required'):
            Bridge().dispatch(dict(action='connection_apply', workspace=str(self.workspace.state_dir), id=plan['id']))
