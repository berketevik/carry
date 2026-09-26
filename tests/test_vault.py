"""Vault bootstrap: fresh install, idempotent re-run, conflicts, upgrades, rollback, scrubbed template."""
import io
import json
import re
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import _support  # noqa: F401  (puts src on sys.path)
from carry import cli, vault
from carry.config import SourceConfig, Workspace, EmbeddingConfig
from carry.errors import CarryError


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.target = self.base / 'vault'

    def install(self, **kwargs):
        plan = vault.plan(self.target, **kwargs)
        return plan, vault.apply(plan, git=False)

    def test_fresh_install_writes_the_pack_and_a_rerun_is_a_no_op(self):
        plan, result = self.install()
        rels = {c['rel'] for c in plan['changes']}
        for rel in ('LLM-GUIDE.md', 'CLAUDE.md', 'AGENTS.md', 'Home.md', 'SETUP-GUIDE.md', '.gitignore',
                    'x/Templates/Effort.md', 'notes/.gitkeep', '+/.gitkeep', vault.STAMP):
            self.assertIn(rel, rels)
        self.assertTrue(all(c['status'] == 'create' for c in plan['changes']))
        self.assertTrue((self.target / 'workbench').is_dir())
        again = vault.plan(self.target)
        self.assertEqual((again['changes'], again['conflicts']), ([], []))

    def test_placeholders_are_filled_but_obsidian_ones_survive(self):
        files = vault.render('English')
        self.assertIn('**English**', files['LLM-GUIDE.md'])
        self.assertIn('carry_recall', files['CLAUDE.md'])
        self.assertIn('{{title}}', files['x/Templates/Note.md'])
        for rel, text in files.items():
            self.assertIsNone(re.search(r'\{\{[A-Z_]+\}\}', text), rel)

    def test_template_carries_no_personal_content(self):
        for rel, text in vault.render().items():
            low = text.lower()
            # test_c01_package scans the template sources for the vault markers; these are the rest.
            for marker in ('tevik', 'mac mini', '@mbp'):
                self.assertNotIn(marker, low, f'{marker} in {rel}')

    def test_an_edited_file_is_a_conflict_and_is_never_overwritten(self):
        self.install()
        edited = self.target / 'CLAUDE.md'
        edited.write_text('my own rules\n', encoding='utf-8')
        plan = vault.plan(self.target)
        self.assertEqual(plan['conflicts'], ['CLAUDE.md'])
        self.assertEqual(vault.apply(plan, git=False)['conflicts'], ['CLAUDE.md'])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['vault', 'init', str(self.target), '--no-git']), cli.EXIT_FAILED)
        self.assertEqual(edited.read_text(encoding='utf-8'), 'my own rules\n')

    def test_upgrade_applies_to_untouched_files_and_skips_edited_ones(self):
        self.install()
        (self.target / 'SETUP-GUIDE.md').write_text('edited\n', encoding='utf-8')
        original = vault.render

        def newer(*args, **kwargs):
            files = original(*args, **kwargs)
            files['LLM-GUIDE.md'] += '\nnew rule\n'
            files['SETUP-GUIDE.md'] += '\nnew line\n'
            return files
        with patch.object(vault, 'render', newer):
            plan = vault.plan(self.target)
            self.assertEqual([(c['rel'], c['status']) for c in plan['changes']],
                             [('LLM-GUIDE.md', 'update'), (vault.STAMP, 'update')])
            self.assertEqual(plan['conflicts'], ['SETUP-GUIDE.md'])
            result = vault.apply(plan, git=False)
            self.assertEqual(result['conflicts'], ['SETUP-GUIDE.md'])
            self.assertTrue((self.target / 'LLM-GUIDE.md').read_text(encoding='utf-8').endswith('new rule\n'))
            self.assertEqual((self.target / 'SETUP-GUIDE.md').read_text(encoding='utf-8'), 'edited\n')
            again = vault.plan(self.target)
        self.assertEqual((again['changes'], again['conflicts']), ([], ['SETUP-GUIDE.md']))

    def test_seed_files_are_the_owners_and_never_conflict(self):
        self.install()
        (self.target / 'Home.md').write_text('my dashboard\n', encoding='utf-8')
        (self.target / 'x' / 'Templates' / 'Note.md').write_text('my template\n', encoding='utf-8')
        plan = vault.plan(self.target)
        self.assertEqual((plan['changes'], plan['conflicts']), ([], []))
        stamp = json.loads((self.target / vault.STAMP).read_text(encoding='utf-8'))
        self.assertNotIn('Home.md', stamp['files'])
        self.assertNotIn('workspace', stamp)

    def test_state_heading_follows_the_language(self):
        self.assertIn('## Son durum', vault.render('Turkish')['x/Templates/Effort.md'])
        self.assertIn('## Current state', vault.render('English')['x/Templates/Effort.md'])

    def test_a_folder_with_existing_notes_is_refused(self):
        self.target.mkdir()
        (self.target / 'old note.md').write_text('mine\n', encoding='utf-8')
        with self.assertRaisesRegex(CarryError, 'vault_target_not_empty'):
            vault.plan(self.target)

    def test_an_empty_git_repository_is_accepted(self):
        (self.target / '.git').mkdir(parents=True)
        self.assertTrue(vault.plan(self.target)['changes'])

    def test_rollback_removes_carry_files_keeps_user_files_and_allows_reinstall(self):
        plan, result = self.install()
        note = self.target / 'notes' / 'Mine.md'
        note.write_text('keep\n', encoding='utf-8')
        vault.rollback(self.target, result['id'])
        self.assertFalse((self.target / 'LLM-GUIDE.md').exists())
        self.assertFalse((self.target / vault.STAMP).exists())
        self.assertEqual(note.read_text(encoding='utf-8'), 'keep\n')
        note.unlink()
        self.assertTrue(vault.plan(self.target)['changes'])

    def test_rollback_refuses_after_a_later_edit(self):
        plan, result = self.install()
        (self.target / 'CLAUDE.md').write_text('edited\n', encoding='utf-8')
        with self.assertRaisesRegex(CarryError, 'vault_changed_since_install'):
            vault.rollback(self.target, result['id'])
        self.assertTrue((self.target / 'LLM-GUIDE.md').exists())

    def test_rollback_keeps_an_edited_seed(self):
        plan, result = self.install()
        (self.target / 'Home.md').write_text('my dashboard\n', encoding='utf-8')
        vault.rollback(self.target, result['id'])
        self.assertFalse((self.target / 'LLM-GUIDE.md').exists())
        self.assertEqual((self.target / 'Home.md').read_text(encoding='utf-8'), 'my dashboard\n')

    def test_workspace_is_created_with_the_vault_as_source_and_clients_are_wired(self):
        state = self.base / 'state'
        plan, result = self.install(workspace=state)
        self.assertTrue(result['created_workspace'])
        ws = Workspace.load(state)
        self.assertEqual([s.root.resolve() for s in ws.sources], [self.target])
        mcp = json.loads((self.target / '.mcp.json').read_text(encoding='utf-8'))
        self.assertIn(str(state), mcp['mcpServers']['carry']['args'])
        self.assertIn('--client', (self.target / '.codex' / 'config.toml').read_text(encoding='utf-8'))
        ignored = (self.target / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('.mcp.json', ignored)

    def test_capture_wires_hooks_and_lands_prompts_in_the_inbox(self):
        from carry import capture
        state = self.base / 'state'
        with patch.object(capture, 'probe_client', return_value=dict(state='supported', version='9.9.9')):
            plan, result = self.install(workspace=state, capture=True)
        self.assertEqual(result['capture'], dict(claude='no_receipt', codex='no_receipt'))
        # The guide's correction rule needs carry_propose; acceptance stays with the owner.
        self.assertEqual(result['proposals'], dict(claude='enabled', codex='enabled'))
        hooks = json.loads((self.target / '.codex' / 'hooks.json').read_text(encoding='utf-8'))
        self.assertIn('carry.hook', hooks['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'])
        self.assertTrue((self.target / '.claude' / 'settings.local.json').exists())
        self.assertIn('.codex/hooks.json', (self.target / '.gitignore').read_text(encoding='utf-8'))
        ws = Workspace.load(state)
        self.assertTrue(ws.sources[0].writable)
        payload = json.dumps(dict(hook_event_name='UserPromptSubmit', session_id='s1', turn_id='t1',
                                  prompt='kararı not et'))
        receipt = capture.ingest(ws, 'codex', io.StringIO(payload))
        self.assertEqual(receipt['state'], 'captured')
        self.assertTrue(receipt['event_path'].startswith('sources/carry/.events/'))
        self.assertTrue((self.target / receipt['event_path']).exists())

    def test_recall_scope_is_knowledge_only(self):
        from carry.index import corpus_snapshot
        state = self.base / 'state'
        self.install(workspace=state)
        self.assertTrue((self.target / 'VAULT-RULES.md').exists())
        for rel in ('notes/Karar.md', 'log/2026-09-25.md', 'workbench/Taslak.md', '+/Kıvılcım.md',
                    'notes/_index.md'):
            (self.target / rel).write_text('# x\n', encoding='utf-8')
        indexed = sorted(path for _, path in corpus_snapshot(Workspace.load(state)))
        self.assertEqual(indexed, ['log/2026-09-25.md', 'notes/Karar.md'])

    def test_capture_needs_a_workspace_and_a_writable_vault_source(self):
        with self.assertRaisesRegex(CarryError, 'capture_requires_workspace'):
            vault.plan(self.target, capture=True)
        self.target.mkdir()
        Workspace.create(self.base / 'state', sources=[SourceConfig(source_id='vault', root=self.target)],
                         embedding=EmbeddingConfig(provider='hashing'))
        with self.assertRaisesRegex(CarryError, 'workspace_vault_source_read_only'):
            vault.plan(self.target, workspace=self.base / 'state', capture=True)

    def test_workspace_inside_the_vault_is_refused(self):
        with self.assertRaisesRegex(CarryError, 'state_dir_inside_source_root'):
            vault.plan(self.target, workspace=self.target / '.state')

    def test_existing_workspace_without_the_vault_source_is_refused(self):
        other = self.base / 'other'
        other.mkdir()
        self.target.mkdir()
        Workspace.create(self.base / 'state', sources=[SourceConfig(source_id='other', root=other)],
                         embedding=EmbeddingConfig(provider='hashing'))
        with self.assertRaisesRegex(CarryError, 'workspace_missing_vault_source'):
            vault.plan(self.target, workspace=self.base / 'state')

    def test_attach_adds_the_vault_to_an_existing_workspace_on_apply(self):
        other = self.base / 'other'
        other.mkdir()
        state = self.base / 'state'
        Workspace.create(state, sources=[SourceConfig(source_id='other', root=other)],
                         embedding=EmbeddingConfig(provider='hashing'))
        plan = vault.plan(self.target, workspace=state, attach=True)
        # Preview writes nothing, the workspace included.
        self.assertEqual([s.source_id for s in Workspace.load(state).sources], ['other'])
        self.assertFalse(self.target.exists())
        result = vault.apply(plan, git=False)
        self.assertFalse(result['created_workspace'])
        ws = Workspace.load(state)
        added = ws.source('vault')
        self.assertEqual(added.root.resolve(), self.target)
        self.assertFalse(added.writable)
        self.assertEqual(tuple(added.exclude), vault.EXCLUDE)
        mcp = json.loads((self.target / '.mcp.json').read_text(encoding='utf-8'))
        self.assertIn(str(state), mcp['mcpServers']['carry']['args'])
        again = vault.plan(self.target, workspace=state, attach=True)
        self.assertEqual((again['changes'], again['conflicts']), ([], []))

    def test_attach_refuses_a_taken_source_id_or_an_overlapping_root(self):
        state = self.base / 'state'
        taken = self.base / 'taken'
        taken.mkdir()
        Workspace.create(state, sources=[SourceConfig(source_id='vault', root=taken)],
                         embedding=EmbeddingConfig(provider='hashing'))
        with self.assertRaisesRegex(CarryError, 'duplicate_source_id'):
            vault.plan(self.target, workspace=state, attach=True)
        state2 = self.base / 'state2'
        Workspace.create(state2, sources=[SourceConfig(source_id='notes', root=taken)],
                         embedding=EmbeddingConfig(provider='hashing'))
        with self.assertRaisesRegex(CarryError, 'nested_source_roots'):
            vault.plan(taken / 'inner', workspace=state2, attach=True)
        with self.assertRaisesRegex(CarryError, 'attach_requires_workspace'):
            vault.plan(self.target, attach=True)

    def test_rerun_without_changes_writes_no_journal(self):
        self.install()
        journals = self.target / '.carry' / 'journal'
        before = sorted(journals.iterdir())
        self.assertEqual(vault.apply(vault.plan(self.target), git=False)['state'], 'unchanged')
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(['vault', 'init', str(self.target), '--no-git']), cli.EXIT_OK)
        self.assertIn('nothing to change', out.getvalue())
        self.assertEqual(sorted(journals.iterdir()), before)

    def test_cli_dry_run_writes_nothing(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(['vault', 'init', str(self.target), '--dry-run'])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn('create\tLLM-GUIDE.md', out.getvalue())
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
