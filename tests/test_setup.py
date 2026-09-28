"""carry setup: defaults end to end, scripted answers, iCloud warning, banner font."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import _support  # noqa: F401
from carry import cli, jev, setup_wizard
from carry.config import Workspace


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        key = patch.object(jev, 'api_key', return_value=None); key.start(); self.addCleanup(key.stop)

    def test_defaults_create_workspace_vault_and_index_without_questions(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(['--workspace', str(self.base / 'ws'), 'setup', '--yes', '--vault', str(self.base / 'Vault'),
                             '--no-animation', '--no-semantic', '--no-app'])
        self.assertEqual(code, 0)
        ws = Workspace.load(self.base / 'ws')
        self.assertEqual([s.source_id for s in ws.sources], ['vault'])
        self.assertEqual((ws.embedding.provider, ws.retrieval.reranker, ws.retrieval.top_k), ('hashing', 'off', 8))
        self.assertTrue((self.base / 'Vault' / 'LLM-GUIDE.md').exists())
        self.assertTrue((self.base / 'Vault' / '.claude' / 'agents' / 'carry-recall.md').exists())
        mcp = json.loads((self.base / 'Vault' / '.mcp.json').read_text(encoding='utf-8'))
        self.assertIn(str(self.base / 'ws'), mcp['mcpServers']['carry']['args'])
        self.assertIn('YOUR VAULT IS READY', out.getvalue())

    def test_semantic_search_is_the_default_and_installs_ollama_when_missing(self):
        from carry import models, ollama_setup
        calls = []
        def fake_setup(ws, preset):
            calls.append(preset)
        with redirect_stdout(io.StringIO()), \
                patch.object(ollama_setup, 'status', return_value=dict(installed=False, brew='/opt/homebrew/bin/brew', running=False)), \
                patch.object(ollama_setup, 'install', return_value=(True, 'brew_service')) as install, \
                patch.object(models, 'setup', side_effect=fake_setup):
            code = setup_wizard.run(state_dir=str(self.base / 'ws'), assume_yes=True, vault_path=str(self.base / 'Vault'),
                                    animation=False, app=False)
        self.assertEqual(code, 0)
        install.assert_called_once()
        self.assertEqual(calls, ['semantic_assistant'])

    def test_semantic_falls_back_to_keywords_without_homebrew(self):
        from carry import models, ollama_setup
        calls = []
        with redirect_stdout(io.StringIO()) as out, \
                patch.object(ollama_setup, 'status', return_value=dict(installed=False, brew=None, running=False)), \
                patch.object(models, 'setup', side_effect=lambda ws, preset: calls.append(preset)):
            setup_wizard.run(state_dir=str(self.base / 'ws'), assume_yes=True, vault_path=str(self.base / 'Vault'), animation=False, app=False)
        self.assertEqual(calls, ['keyword_assistant'])

    def test_search_presets(self):
        from carry import models
        self.assertEqual(models.search_preset(True, 'jev'), 'semantic_jev')
        self.assertEqual(models.search_preset(False, 'assistant'), 'keyword_assistant')

    def test_scripted_answers_connect_an_existing_folder_read_only(self):
        notes = self.base / 'notes'; notes.mkdir()
        (notes / 'a.md').write_text('# Plan\nThe launch is on 15 October.', encoding='utf-8')
        answers = io.StringIO('\n'.join([str(self.base / 'ws'), '2', str(notes), 'mine', '', 'h', '1', 'h', 'h', 'h', '']) + '\n')
        with redirect_stdout(io.StringIO()), patch('shutil.which', return_value='/usr/bin/true'):
            code = setup_wizard.run(assume_yes=False, animation=False, stream=answers)
        self.assertEqual(code, 0)
        ws = Workspace.load(self.base / 'ws')
        source = ws.source('mine')
        self.assertFalse(source.writable)
        self.assertEqual(source.root.resolve(), notes)
        self.assertEqual((notes / 'a.md').read_text(encoding='utf-8'), '# Plan\nThe launch is on 15 October.')

    def test_icloud_documents_are_flagged(self):
        home = self.base / 'home'
        (home / 'Library/Mobile Documents/com~apple~CloudDocs/Documents').mkdir(parents=True)
        with patch.object(Path, 'home', return_value=home):
            self.assertTrue(setup_wizard.icloud_synced(home / 'Documents' / 'CarryState'))
            self.assertFalse(setup_wizard.icloud_synced(home / 'CarryState'))

    def test_connect_previews_applies_lists_and_undoes(self):
        ws_dir, proj = self.base / 'ws', self.base / 'proj'
        proj.mkdir()
        with redirect_stdout(io.StringIO()):
            cli.main(['--workspace', str(ws_dir), 'init', '--embedding', 'hashing'])
            with patch('carry.capture.probe_client', return_value=dict(state='supported', version='9')):
                self.assertEqual(cli.main(['--workspace', str(ws_dir), 'connect', 'claude', str(proj), '--dry-run']), 0)
                self.assertFalse((proj / '.mcp.json').exists())
                self.assertEqual(cli.main(['--workspace', str(ws_dir), 'connect', 'claude', str(proj)]), 0)
        self.assertTrue((proj / '.mcp.json').exists())
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(['--workspace', str(ws_dir), '--json', 'connect', 'list'])
        ident = json.loads(out.getvalue())[0]['id']
        with redirect_stdout(io.StringIO()):
            cli.main(['--workspace', str(ws_dir), 'connect', 'undo', ident])
        self.assertFalse((proj / '.mcp.json').exists())

    def test_banner_rows_align(self):
        rows = setup_wizard.render(setup_wizard.BANNER)
        self.assertEqual(len(rows), 5)
        self.assertEqual(len({len(r) for r in rows}), 1)
        self.assertTrue(all(ch.upper() in setup_wizard.FONT for ch in setup_wizard.BANNER))


if __name__ == '__main__':
    unittest.main()


class AppInstallTest(unittest.TestCase):
    def test_missing_compiler_and_foreign_bundle_are_refused(self):
        from carry import app_install
        from carry.errors import CarryError
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(app_install, 'swiftc', return_value=None), patch.object(app_install.sys, 'platform', 'darwin'):
                with self.assertRaisesRegex(CarryError, 'swiftc_missing'):
                    app_install.install(target=Path(tmp) / 'Carry.app')
            other = Path(tmp) / 'Other.app' / 'Contents'; other.mkdir(parents=True)
            import plistlib
            with open(other / 'Info.plist', 'wb') as f:
                plistlib.dump(dict(CFBundleIdentifier='com.example.other'), f)
            with patch.object(app_install, 'swiftc', return_value='/usr/bin/swiftc'), patch.object(app_install.sys, 'platform', 'darwin'):
                with self.assertRaisesRegex(CarryError, 'app_path_taken'):
                    app_install.install(target=other.parent)
            self.assertTrue((other / 'Info.plist').exists())

    def test_build_failure_shows_the_first_errors_and_a_known_fix(self):
        from carry import app_install
        output = ("<unknown>:0: error: redefinition of module 'SwiftBridging'\n"
                  "note: previously defined here\n" + "context line\n" * 40)
        detail = app_install.build_error(output)
        self.assertTrue(detail.startswith("<unknown>:0: error: redefinition of module 'SwiftBridging'"))
        self.assertIn('module.modulemap.bak', detail)
        self.assertEqual(app_install.build_error('plain failure'), 'plain failure')

    def test_app_source_ships_with_the_package(self):
        from importlib import resources
        source = (resources.files('carry') / 'app' / 'CarryApp.swift').read_text(encoding='utf-8')
        self.assertIn('python-path.txt', source)
