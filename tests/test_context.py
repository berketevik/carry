"""carry context: state pack from commits and recent harvest drafts; SessionStart wiring."""
import datetime
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

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
            '## Apparently done\n\n- Write the release note → [[Release]]\n')

    def test_pack_lists_open_items_and_owner_decisions_only(self):
        text = context.pack(self.root)
        self.assertIn('Invite two colleagues to the repository.', text)
        self.assertIn('The pilot ships on 15 October.', text)
        self.assertNotIn('Use a bigger model.', text)
        self.assertNotIn('release note', text)
        self.assertIn('1 draft digests (+/)', text)
        self.assertLessEqual(len(context.pack(self.root, max_chars=200)), 202)

    def test_vault_wires_session_start_and_a_gitignored_workspace_pointer(self):
        settings = json.loads((self.root / '.claude' / 'settings.local.json').read_text())
        command = settings['hooks']['SessionStart'][0]['hooks'][0]['command']
        self.assertIn('carry.cli', command)
        self.assertIn(' context', command)
        self.assertEqual(context.workspace_for(self.root), str(self.base / 'ws'))
        self.assertIn('.carry/local.json', (self.root / '.gitignore').read_text())

    def test_cli_finds_the_workspace_from_the_vault(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(['context', '--vault', str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn('Carry state pack', out.getvalue())
        self.assertIn('## Index', out.getvalue())


if __name__ == '__main__':
    unittest.main()
