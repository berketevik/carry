"""carry context: state pack from commits and recent harvest drafts; SessionStart wiring."""
import datetime
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
import unittest.mock

import _support  # noqa: F401
from carry import cli, context, vault


class ContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'Vault'
        vault.apply(vault.plan(self.root, language='English', workspace=self.base / 'ws'), git=False)
        today = datetime.date.today().isoformat()
        (self.root / '+' / f'{today} — harvest claude abcd1234.md').write_text(
            '---\ntype: output\n---\n\n# Harvest\n\n## New\n\n'
            '- **decision:** The pilot ships on 15 October.\n  > quote\n'
            "- **decision · assistant's suggestion or result; not confirmed by the owner:** Use a bigger model.\n  > q\n"
            '- **open item:** Invite two colleagues to the repository.\n  > q\n\n'
            '## Apparently done\n\n- Write the release note → [[Release]]\n\n'
            '## Apparently dropped\n\n- **open item:** Choose the full or light build.\n')

    def test_pack_lists_open_items_and_owner_decisions_only(self):
        text = context.pack(self.root)
        self.assertIn('Invite two colleagues to the repository.', text)
        self.assertIn('The pilot ships on 15 October.', text)
        self.assertNotIn('Use a bigger model.', text)
        self.assertNotIn('release note', text)
        self.assertNotIn('light build', text)
        self.assertIn('1 draft digests (+/)', text)
        self.assertLessEqual(len(context.pack(self.root, max_chars=200)), 202)

    def test_vault_wires_session_start_and_a_gitignored_workspace_pointer(self):
        settings = json.loads((self.root / '.claude' / 'settings.local.json').read_text())
        command = settings['hooks']['SessionStart'][0]['hooks'][0]['command']
        self.assertIn('carry.cli', command)
        self.assertIn(' context', command)
        self.assertEqual(context.workspace_for(self.root), str(self.base / 'ws'))
        self.assertIn('.carry/local.json', (self.root / '.gitignore').read_text())

    def test_both_clients_get_session_start_and_end_hooks(self):
        claude = json.loads((self.root / '.claude' / 'settings.local.json').read_text())['hooks']
        codex = json.loads((self.root / '.codex' / 'hooks.json').read_text())['hooks']
        for hooks in (claude, codex):
            self.assertIn(' context', hooks['SessionStart'][0]['hooks'][0]['command'])
            end = hooks['SessionEnd'][0]['hooks'][0]
            self.assertIn('harvest --from-hook', end['command'])
            self.assertLessEqual(end['timeout'], 3)
        self.assertNotIn('UserPromptSubmit', codex)

    def test_hook_context_is_silent_outside_the_vault(self):
        import sys
        out = io.StringIO()
        payload = io.StringIO(json.dumps(dict(cwd=str(self.base), hook_event_name='SessionStart')))
        with redirect_stdout(out), unittest.mock.patch.object(sys, 'stdin', payload):
            cli.main(['--workspace', str(self.base / 'ws'), 'context', '--from-hook', '--vault', str(self.root)])
        self.assertEqual(out.getvalue(), '')
        out = io.StringIO()
        payload = io.StringIO(json.dumps(dict(cwd=str(self.root), hook_event_name='SessionStart')))
        with redirect_stdout(out), unittest.mock.patch.object(sys, 'stdin', payload):
            cli.main(['--workspace', str(self.base / 'ws'), 'context', '--from-hook', '--vault', str(self.root)])
        self.assertIn('state pack', out.getvalue())

    def test_cli_finds_the_workspace_from_the_vault(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(['context', '--vault', str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn('Carry state pack', out.getvalue())
        self.assertIn('## Index', out.getvalue())


if __name__ == '__main__':
    unittest.main()
