"""App vault browsing and settings: real files in temporary folders."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from carry import harvest, vaultview
from carry.config import EmbeddingConfig, SourceConfig, Workspace
from carry.desktop import Bridge
from carry.errors import CarryError


class VaultViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.vault = base / 'vault'
        (self.vault / 'notes').mkdir(parents=True)
        (self.vault / '+').mkdir()
        (self.vault / '.obsidian').mkdir()
        (self.vault / 'notes' / 'Alpha.md').write_text('---\ntype: effort\nsummary: "Alpha summary"\ndraft: true\n---\n# Alpha\nSee [[Beta|the beta note]].\n', encoding='utf-8')
        (self.vault / 'notes' / 'Beta.md').write_text('---\ntype: thing\n---\nBeta body, links back to [[Alpha]].\n', encoding='utf-8')
        (self.vault / '+' / 'Capture.md').write_text('inbox item\n', encoding='utf-8')
        (self.vault / '.obsidian' / 'Hidden.md').write_text('never listed\n', encoding='utf-8')
        self.ws = Workspace.create(base / 'state', sources=[SourceConfig('notes', self.vault, exclude=('+',))],
                                   embedding=EmbeddingConfig(provider='hashing'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_browse_lists_excluded_files_marked_and_skips_hidden_folders(self):
        files = {f['path']: f for f in vaultview.browse(self.ws, 'notes')['files']}
        self.assertEqual(set(files), {'notes/Alpha.md', 'notes/Beta.md', '+/Capture.md'})
        self.assertFalse(files['+/Capture.md']['indexed'])
        self.assertTrue(files['notes/Alpha.md']['draft'])
        self.assertEqual(files['notes/Alpha.md']['summary'], 'Alpha summary')

    def test_note_reads_frontmatter_links_and_backlinks(self):
        note = vaultview.note(self.ws, 'notes', 'notes/Alpha.md')
        self.assertEqual(note['frontmatter']['type'], 'effort')
        self.assertEqual(note['links'], ['Beta'])
        self.assertEqual(vaultview.backlinks(self.ws, 'notes', 'notes/Alpha.md')['backlinks'], ['notes/Beta.md'])
        self.assertEqual(vaultview.resolve_link(self.ws, 'notes', 'Beta')['path'], 'notes/Beta.md')

    def test_note_refuses_paths_outside_the_source(self):
        (Path(self.tmp.name) / 'outside.md').write_text('secret', encoding='utf-8')
        with self.assertRaises(CarryError):
            vaultview.note(self.ws, 'notes', '../outside.md')

    def test_overview_counts(self):
        with mock.patch.object(harvest, 'schedule_status', return_value=dict(installed=False)):
            o = vaultview.overview(self.ws, 'notes')
        self.assertEqual((o['total'], o['indexed'], o['inbox'], o['drafts']), (3, 2, 1, 1))

    def test_settings_update_validates_and_persists(self):
        vaultview.update(self.ws, retrieval=dict(top_k=12, reranker_min_score=0.6),
                         sources=dict(notes=dict(exclude=['+', 'x'])))
        again = Workspace.load(self.ws.state_dir)
        self.assertEqual((again.retrieval.top_k, again.retrieval.reranker_min_score), (12, 0.6))
        self.assertEqual(again.source('notes').exclude, ('+', 'x'))
        for bad in (dict(top_k=0), dict(top_k=True), dict(chunk_chars=10), dict(auto_refresh='yes')):
            with self.assertRaises(CarryError):
                vaultview.update(self.ws, retrieval=bad)
        with self.assertRaises(CarryError):
            vaultview.update(self.ws, sources=dict(notes=dict(root='/')))
        self.assertEqual(Workspace.load(self.ws.state_dir).retrieval.top_k, 12)

    def test_remove_source_keeps_files(self):
        vaultview.remove_source(self.ws, 'notes')
        self.assertEqual(Workspace.load(self.ws.state_dir).sources, ())
        self.assertTrue((self.vault / 'notes' / 'Alpha.md').is_file())

    def test_bridge_uses_default_vault_and_reports_settings(self):
        bridge = Bridge()
        call = lambda action, **f: bridge.dispatch(dict(action=action, workspace=str(self.ws.state_dir), **f))
        self.assertEqual(len(call('vault_browse')['files']), 3)
        self.assertEqual(call('vault_note', path='notes/Beta.md')['title'], 'Beta')
        with mock.patch.object(harvest, 'schedule_status', return_value=dict(installed=False)):
            s = call('settings')
        self.assertEqual((s['preset'], s['default_vault']), ('keyword_assistant', 'notes'))


class ScheduleTest(unittest.TestCase):
    def launchctl(self, fail_bootstrap=0):
        """Fake launchctl: remembers what is loaded so `print` answers like the real one.
        The first `fail_bootstrap` bootstraps fail."""
        loaded, failures = set(), [fail_bootstrap]
        def run(args, **kw):
            verb = args[1]
            code = 0
            if verb == 'bootstrap':
                code = 1 if failures[0] > 0 else 0
                failures[0] -= 1
                if not code:
                    loaded.add(harvest.LAUNCH_LABEL)
            elif verb == 'bootout':
                loaded.discard(harvest.LAUNCH_LABEL)
            elif verb == 'print':
                code = 0 if harvest.LAUNCH_LABEL in loaded else 113
            return mock.Mock(returncode=code)
        return run

    def test_status_reads_back_what_install_wrote(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.object(Path, 'home', return_value=Path(home)), \
                mock.patch.object(harvest.subprocess, 'run', side_effect=self.launchctl()):
            status = harvest.install_schedule('/bin/carry & co', '/state', vault='/v a/ult', language='Turkish', hour=6, minute=5)
            self.assertEqual((status['hour'], status['minute'], status['vault'], status['language'], status['loaded']),
                             (6, 5, '/v a/ult', 'Turkish', True))
            with self.assertRaises(CarryError):
                harvest.install_schedule('/bin/carry', '/state', hour=24)
            self.assertEqual(harvest.remove_schedule(), dict(installed=False, loaded=False))
            self.assertFalse(harvest.schedule_status()['installed'])

    def test_failed_load_restores_the_previous_job(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.object(Path, 'home', return_value=Path(home)):
            with mock.patch.object(harvest.subprocess, 'run', side_effect=self.launchctl()):
                harvest.install_schedule('/bin/carry', '/state-a', hour=21, minute=30)
            with mock.patch.object(harvest.subprocess, 'run', side_effect=self.launchctl(fail_bootstrap=1)):
                with self.assertRaisesRegex(CarryError, 'schedule_load_failed'):
                    harvest.install_schedule('/bin/carry', '/state-b', hour=6, minute=0)
                self.assertEqual(harvest.schedule_status()['workspace'], '/state-a')


class LinksAndWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.vault = base / 'vault'
        for rel, text in {'a/Same.md': 'A', 'b/Same.md': 'B', 'b/Other.md': 'see [[Same]] and [[a/Same|the a one]]',
                          'notes/Alpha.md': 'alpha', 'log/Ref.md': 'full path [[notes/Alpha]] and [[Alpha.md]]'}.items():
            (self.vault / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.vault / rel).write_text(text, encoding='utf-8')
        self.ws = Workspace.create(base / 'state', sources=[SourceConfig('notes', self.vault)],
                                   embedding=EmbeddingConfig(provider='hashing'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_paths_win_over_basenames_and_neighbours_over_strangers(self):
        self.assertEqual(vaultview.resolve_link(self.ws, 'notes', 'b/Same')['path'], 'b/Same.md')
        self.assertEqual(vaultview.resolve_link(self.ws, 'notes', 'Same', 'b/Other.md')['path'], 'b/Same.md')
        self.assertEqual(vaultview.resolve_link(self.ws, 'notes', 'Same', 'notes/Alpha.md')['path'], 'a/Same.md')

    def test_backlinks_follow_path_links_and_do_not_mix_same_names(self):
        self.assertEqual(vaultview.backlinks(self.ws, 'notes', 'notes/Alpha.md')['backlinks'], ['log/Ref.md'])
        self.assertEqual(vaultview.backlinks(self.ws, 'notes', 'b/Same.md')['backlinks'], ['b/Other.md'])
        self.assertEqual(vaultview.backlinks(self.ws, 'notes', 'a/Same.md')['backlinks'], ['b/Other.md'])

    def test_new_file_is_pending_until_indexed(self):
        files = {f['path']: f['index_state'] for f in vaultview.browse(self.ws, 'notes')['files']}
        self.assertEqual(set(files.values()), {'pending'})

    def test_threshold_default_does_not_block_other_changes(self):
        self.assertEqual(self.ws.retrieval.reranker_min_score, -4.0)
        vaultview.update(self.ws, retrieval=dict(top_k=10))
        self.assertEqual(Workspace.load(self.ws.state_dir).retrieval.top_k, 10)

    def test_embedding_only_models_are_read_back(self):
        from dataclasses import replace
        ws = replace(self.ws, embedding=EmbeddingConfig(provider='ollama', model='qwen3-embedding:0.6b'))
        self.assertEqual(vaultview.current_preset(ws), 'qwen3-embedding:0.6b')
        ws = replace(ws, retrieval=replace(ws.retrieval, reranker='jev'))
        self.assertEqual(vaultview.current_preset(ws), 'custom')

    def test_harvest_targets_only_this_workspaces_sources(self):
        from carry.desktop import harvest_vault
        self.assertEqual(harvest_vault(self.ws, None), str(self.vault.resolve()))
        with self.assertRaisesRegex(CarryError, 'harvest_vault_not_a_source'):
            harvest_vault(self.ws, self.tmp.name)

    def test_draft_language_setting_wins(self):
        with mock.patch.object(harvest, 'schedule_status', return_value=dict(installed=False)):
            self.assertEqual(harvest.draft_language(self.ws, self.vault), 'English')
            harvest.set_draft_language(self.ws, 'Turkish')
            self.assertEqual(harvest.draft_language(self.ws, self.vault), 'Turkish')
        with self.assertRaises(CarryError):
            harvest.set_draft_language(self.ws, 'Klingon')

    def test_edited_file_is_pending_until_reindexed(self):
        import hashlib, sqlite3
        con = sqlite3.connect(self.ws.db_path)
        con.execute("CREATE TABLE files (source_id TEXT, path TEXT, digest TEXT)")
        digest = hashlib.sha256((self.vault / 'notes/Alpha.md').read_bytes()).hexdigest()
        con.execute("INSERT INTO files VALUES ('notes', 'notes/Alpha.md', ?)", (digest,))
        con.commit(); con.close()
        states = lambda: {f['path']: f['index_state'] for f in vaultview.browse(self.ws, 'notes')['files']}
        self.assertEqual(states()['notes/Alpha.md'], 'indexed')
        (self.vault / 'notes/Alpha.md').write_text('alpha, edited', encoding='utf-8')
        self.assertEqual(states()['notes/Alpha.md'], 'pending')

    def test_hook_uses_the_app_language_over_its_own_argument(self):
        harvest.set_draft_language(self.ws, 'Turkish')
        with mock.patch.object(harvest.subprocess, 'Popen') as popen:
            harvest.spawn_from_hook(self.ws.state_dir, dict(transcript_path='/t.jsonl', cwd=str(self.vault)), self.vault, language='English')
        args = popen.call_args[0][0]
        self.assertEqual(args[args.index('--language') + 1], 'Turkish')

    def test_reused_pid_is_not_a_running_harvest(self):
        from carry.desktop import _is_harvest
        self.assertFalse(_is_harvest(os.getpid(), self.ws.state_dir))
        self.assertFalse(_is_harvest(None, self.ws.state_dir))

    def test_assistant_status_reads_the_notes_folder_wiring(self):
        import json as _json
        with mock.patch('carry.desktop.executable_for', return_value='/nonexistent/claude'), \
                mock.patch.object(Path, 'home', return_value=Path(self.tmp.name)):
            status = vaultview.assistants(self.ws)
            self.assertEqual((status['claude']['connected'], status['chat_end']), (False, False))
            (self.vault / '.mcp.json').write_text(_json.dumps(dict(mcpServers=dict(carry=dict(command='x')))), encoding='utf-8')
            (self.vault / '.claude').mkdir()
            (self.vault / '.claude' / 'settings.local.json').write_text('{"hooks": {"SessionEnd": [{"command": "carry harvest --from-hook"}]}}', encoding='utf-8')
            status = vaultview.assistants(self.ws)
            self.assertTrue(status['claude']['connected'])
            self.assertTrue(status['claude']['chat_end'])
            self.assertFalse(status['claude']['installed'])
            self.assertFalse(status['codex']['connected'])

    def test_first_note_goes_to_notes_and_never_overwrites(self):
        first = vaultview.create_note(self.ws, 'notes', 'Plan: Q4/launch', 'Ship in October.')['path']
        self.assertEqual(first, 'notes/Plan Q4 launch.md')
        self.assertIn('Ship in October.', (self.vault / first).read_text(encoding='utf-8'))
        self.assertEqual(vaultview.create_note(self.ws, 'notes', 'Plan: Q4/launch')['path'], 'notes/Plan Q4 launch (2).md')

    def test_approve_drops_only_the_draft_flag_and_respects_locks(self):
        (self.vault / 'notes/D.md').write_text('---\ntype: thing\ndraft: true\nsummary: s\n---\nbody\ndraft: true stays in body\n', encoding='utf-8')
        self.assertTrue(vaultview.approve_note(self.ws, 'notes', 'notes/D.md')['changed'])
        text = (self.vault / 'notes/D.md').read_text(encoding='utf-8')
        self.assertEqual(text, '---\ntype: thing\nsummary: s\n---\nbody\ndraft: true stays in body\n')
        (self.vault / 'notes/L.md').write_text('---\nlock: true\ndraft: true\n---\nx\n', encoding='utf-8')
        with self.assertRaisesRegex(CarryError, 'note_locked'):
            vaultview.approve_note(self.ws, 'notes', 'notes/L.md')

    def test_bulk_approve_skips_locked_and_raw_and_keeps_going(self):
        files = {'notes/A.md': '---\ndraft: true\n---\na\n', 'notes/B.md': '---\ntype: thing\ndraft: true\n---\nb\n',
                 'notes/L.md': '---\nlock: true\ndraft: true\n---\nl\n', 'notes/R.md': '---\ntype: chat-raw\ndraft: true\n---\nr\n',
                 'notes/N.md': '---\ntype: thing\n---\nn\n'}
        for rel, text in files.items():
            (self.vault / rel).write_text(text, encoding='utf-8')
        result = vaultview.approve_many(self.ws, 'notes', list(files) + ['notes/Missing.md', '../escape.md'])
        self.assertEqual(result['approved'], ['notes/A.md', 'notes/B.md'])
        reasons = {s['path']: s['reason'] for s in result['skipped']}
        self.assertEqual(reasons['notes/L.md'], 'locked')
        self.assertEqual(reasons['notes/R.md'], 'raw')
        self.assertEqual(reasons['notes/N.md'], 'not_draft')
        self.assertIn('notes/Missing.md', reasons)
        self.assertIn('../escape.md', reasons)
        self.assertNotIn('draft', (self.vault / 'notes/A.md').read_text(encoding='utf-8'))
        self.assertIn('draft: true', (self.vault / 'notes/L.md').read_text(encoding='utf-8'))

    def test_raw_material_is_refused_alone_as_in_bulk(self):
        (self.vault / 'notes/R.md').write_text('---\ntype: chat-raw\ndraft: true\n---\nr\n', encoding='utf-8')
        (self.vault / 'notes/C.md').write_text('---\nsource_type: clip\ndraft: true\n---\nc\n', encoding='utf-8')
        for rel in ('notes/R.md', 'notes/C.md'):
            with self.assertRaisesRegex(CarryError, 'note_raw'):
                vaultview.approve_note(self.ws, 'notes', rel)
        files = {f['path']: f for f in vaultview.browse(self.ws, 'notes')['files']}
        self.assertFalse(files['notes/R.md']['approvable'] or files['notes/C.md']['approvable'])

    def test_only_inbox_drafts_wait_for_review(self):
        (self.vault / '+').mkdir(exist_ok=True)
        (self.vault / '+' / 'Capture.md').write_text('inbox item\n', encoding='utf-8')
        (self.vault / '+' / 'Digest.md').write_text('---\ntype: output\ndraft: true\n---\nitems\n', encoding='utf-8')
        (self.vault / 'notes' / 'Agent.md').write_text('---\ntype: output\ndraft: true\n---\nagent note\n', encoding='utf-8')
        (self.vault / '+' / 'Routed.md').write_text('---\ntype: capture\nrouted_into: "[[Plan]]"\ndraft: true\n---\nx\n', encoding='utf-8')
        files = {f['path']: f for f in vaultview.browse(self.ws, 'notes')['files']}
        self.assertTrue(files['+/Digest.md']['review'])
        self.assertTrue(files['notes/Agent.md']['approvable'])
        self.assertFalse(files['notes/Agent.md']['review'])
        self.assertFalse(files['+/Capture.md']['review'])  # not a draft
        self.assertFalse(files['+/Routed.md']['review'])  # already routed
        with mock.patch.object(harvest, 'schedule_status', return_value=dict(installed=False)):
            o = vaultview.overview(self.ws, 'notes')
        self.assertEqual((o['drafts'], o['awaiting_review']), (3, 1))

    def test_guides_and_templates_are_not_counted_as_notes(self):
        (self.vault / 'CLAUDE.md').write_text('guide', encoding='utf-8')
        (self.vault / 'x').mkdir(exist_ok=True)
        (self.vault / 'x' / 'Template.md').write_text('---\ndraft: true\n---\n', encoding='utf-8')
        with mock.patch.object(harvest, 'schedule_status', return_value=dict(installed=False)):
            o = vaultview.overview(self.ws, 'notes')
        self.assertEqual((o['total'], o['system_files'], o['drafts']), (5, 2, 0))

    def test_schedule_of_another_workspace_is_not_changed_silently(self):
        other = dict(installed=True, workspace='/elsewhere', hour=21, minute=30)
        bridge = Bridge()
        with mock.patch.object(harvest, 'schedule_status', return_value=other), \
                mock.patch.object(harvest, 'remove_schedule') as remove:
            with self.assertRaisesRegex(CarryError, 'schedule_other_workspace'):
                bridge.dispatch(dict(action='harvest_schedule', workspace=str(self.ws.state_dir), enabled=False))
            remove.assert_not_called()


if __name__ == '__main__':
    unittest.main()
