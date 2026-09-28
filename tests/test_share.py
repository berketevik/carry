"""Sharing an approved digest item into a team repo's inbox: render and checks only, nothing sent."""
import contextlib
import datetime
import io
from pathlib import Path
import tempfile
import unittest

import _support  # noqa: F401
from carry import cli, digest, share, vault, vaultview
from carry.config import SourceConfig, Workspace
from carry.errors import CarryError, WorkspaceError

DIGEST = '''---
type: output
summary: "claude abcd1234: 3 new, 1 conflict"
created: 2026-09-28
draft: true
provenance: chat
sources:
  - "[[2026-09-28 — claude abcd1234]]"
harvest: {"extractor": "claude:sonnet", "judge": "jev", "compared": true}
---

# Harvest claude abcd1234

## New

- **decision:** The pilot ships on 15 October.
  > ship the pilot on 15 October *(exchange 1, owner, 2026-09-27)*
- **open item:** Invite two colleagues to the repository.
  > invite two colleagues *(exchange 2, owner)*
- **fact:** The build takes 32 seconds.
  > build takes 32 seconds *(exchange 3, assistant, 2026-09-27)*

## Conflict candidates (review)

- **fact:** The Mac mini has 16 GB. ↔ [[Mac mini]]
  > the Mac mini has 16 GB *(exchange 4, owner, 2026-09-27)*
'''
DAY = datetime.date(2026, 9, 28)


class ShareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.root = base / 'Vault'
        vault.apply(vault.plan(self.root, language='English', workspace=base / 'ws', attach=True), git=False)
        (base / 'team').mkdir()
        ws = Workspace.load(base / 'ws')
        team = SourceConfig('team', base / 'team', scope='team',
                            github=dict(repository='acme/kb', branch='main', folder='', commit='', synced_at=0))
        ws.with_sources([*ws.sources, team]).save()
        self.ws = Workspace.load(base / 'ws')
        self.sid = vaultview.default_vault(self.ws)
        self.rel = '+/2026-09-28 — harvest claude abcd1234.md'
        (self.root / self.rel).write_text(DIGEST)

    def item(self, statement):
        return next(it for it in digest.items(self.ws, self.sid, self.rel)['items'] if it['statement'] == statement)

    def accept(self, statement, action='accept', text=None):
        found = self.item(statement)
        digest.decide(self.ws, self.sid, self.rel, found['id'], action, text)
        return found['id'] if action != 'fix' else digest.item_id(text)

    def plan(self, item, **kw):
        return share.plan(self.ws, 'team', self.sid, self.rel, item, kw.pop('topic', 'Pilot tarihi'),
                          'Ada', today=DAY, **kw)

    def test_an_accepted_item_becomes_an_inbox_file_without_the_chat_quote(self):
        got = self.plan(self.accept('The pilot ships on 15 October.'))
        self.assertEqual(got['path'], '90_Inbox/2026-09-28 — Pilot tarihi.md')
        self.assertEqual(got['message'], 'docs(inbox): Pilot tarihi')
        self.assertEqual(got['repository'], 'acme/kb')
        self.assertEqual(got['text'], '# 2026-09-28 — Pilot tarihi\n\n'
                         'Kaynak: Carry, Ada sohbetinden onaylanan madde (2026-09-27). Ham sohbet paylaşılmadı.\n\n'
                         '- **decision:** The pilot ships on 15 October.\n')
        self.assertEqual((got['blocked'], got['sent'], got['enabled']), ([], False, False))

    def test_the_quote_is_shared_only_on_request(self):
        got = self.plan(self.accept('The pilot ships on 15 October.'), with_quote=True)
        self.assertIn('  > ship the pilot on 15 October\n', got['text'])

    def test_a_fixed_item_shares_the_fixed_text(self):
        got = self.plan(self.accept('The build takes 32 seconds.', 'fix', 'The build takes 40 seconds.'))
        self.assertIn('- **fact:** The build takes 40 seconds.', got['text'])

    def test_undecided_skipped_and_open_items_are_refused(self):
        with self.assertRaisesRegex(CarryError, 'share_needs_approved_item'):
            self.plan(self.item('The pilot ships on 15 October.')['id'])
        skipped = self.item('The build takes 32 seconds.')['id']
        digest.decide(self.ws, self.sid, self.rel, skipped, 'skip')
        with self.assertRaisesRegex(CarryError, 'share_needs_approved_item'):
            self.plan(skipped)
        with self.assertRaisesRegex(CarryError, 'share_not_for_open_items'):
            self.plan(self.accept('Invite two colleagues to the repository.'))

    def test_an_item_linked_to_a_private_note_is_refused(self):
        (self.root / 'notes' / 'Mac mini.md').write_text('---\ntype: thing\nsensitivity: private\n---\n8 GB\n')
        with self.assertRaisesRegex(CarryError, 'share_linked_note_private'):
            self.plan(self.accept('The Mac mini has 16 GB.'))

    def test_a_secret_blocks_the_share_instead_of_being_masked(self):
        got = self.plan(self.accept('The build takes 32 seconds.', 'fix', 'The deploy token: abc123secret works.'))
        self.assertEqual(got['blocked'], ['assignment'])
        self.assertIn('abc123secret', got['text'])  # shown to the owner as is; never sent

    def test_topic_and_source_checks(self):
        item = self.accept('The pilot ships on 15 October.')
        for topic in ('', 'a/b', '.hidden', 'x' * 101):
            with self.assertRaisesRegex(CarryError, 'invalid_share_topic'):
                self.plan(item, topic=topic)
        with self.assertRaisesRegex(CarryError, 'share_requires_github_source'):
            share.plan(self.ws, self.sid, self.sid, self.rel, item, 'Pilot', 'Ada', today=DAY)

    def test_share_config_needs_a_github_source_and_round_trips(self):
        with self.assertRaisesRegex(WorkspaceError, 'share_requires_github_source'):
            SourceConfig('x', self.root, share=dict(enabled=True)).validate()
        team = self.ws.source('team')
        with self.assertRaisesRegex(WorkspaceError, 'invalid_share_mode'):
            SourceConfig('t', team.root, github=team.github, share=dict(mode='push')).validate()
        again = SourceConfig.from_json(SourceConfig('t', team.root, github=team.github,
                                                    share=dict(enabled=True, mode='pr')).to_json())
        self.assertEqual(again.share, dict(enabled=True, mode='pr'))

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(['--workspace', str(self.ws.state_dir), *argv])
        return code, out.getvalue()

    def test_cli_enables_sharing_and_only_dry_runs(self):
        code, _ = self.run_cli('share', 'enable', '--id', 'team', '--mode', 'pr')
        self.assertEqual(code, 0)
        self.assertEqual(Workspace.load(self.ws.state_dir).source('team').share,
                         dict(enabled=True, folder='90_Inbox', mode='pr'))
        item = self.accept('The pilot ships on 15 October.')
        args = ('share', 'item', '--team', 'team', '--digest', self.rel, '--item', item,
                '--topic', 'Pilot tarihi', '--author', 'Ada')
        code, out = self.run_cli(*args)
        self.assertEqual(code, 1)
        self.assertIn('share_live_not_available', out)
        code, out = self.run_cli(*args, '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('acme/kb (pr, enabled)', out)
        self.assertIn('- **decision:** The pilot ships on 15 October.', out)
        code, _ = self.run_cli('share', 'disable', '--id', 'team')
        self.assertEqual(Workspace.load(self.ws.state_dir).source('team').share, {})


if __name__ == '__main__':
    unittest.main()
