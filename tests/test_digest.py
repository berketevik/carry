"""Item-by-item review of a harvest digest: real files in a temporary vault."""
import datetime
from pathlib import Path
import tempfile
import unittest

import _support  # noqa: F401
from carry import context, digest, vault, vaultview
from carry.config import Workspace
from carry.errors import CarryError

DIGEST = '''---
type: output
summary: "claude abcd1234: 3 new, 1 conflict, 1 already recorded"
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

## Already recorded

- The repo is public. → [[Carry 0.3.0]]
'''


class DigestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.root = base / 'Vault'
        vault.apply(vault.plan(self.root, language='English', workspace=base / 'ws', attach=True), git=False)
        self.ws = Workspace.load(base / 'ws')
        self.sid = vaultview.default_vault(self.ws)
        self.rel = '+/2026-09-28 — harvest claude abcd1234.md'
        (self.root / self.rel).write_text(DIGEST)

    def ids(self):
        return {it['statement']: it for it in digest.items(self.ws, self.sid, self.rel)['items']}

    def test_items_are_parsed_with_sections_dates_and_folded_ones(self):
        got = digest.items(self.ws, self.sid, self.rel)
        items = {it['statement']: it for it in got['items']}
        self.assertEqual(got['waiting'], 4)
        self.assertEqual(items['The pilot ships on 15 October.']['date'], '2026-09-27')
        self.assertEqual(items['Invite two colleagues to the repository.']['date'], '2026-09-28')  # digest date fallback
        self.assertEqual(items['The Mac mini has 16 GB.']['match'], '[[Mac mini]]')
        self.assertEqual(items['The Mac mini has 16 GB.']['section'], 'conflict')
        self.assertEqual(items['The repo is public.']['decision'], 'folded')

    def test_accept_logs_under_the_chat_date_and_never_edits_the_matched_note(self):
        (self.root / 'notes' / 'Mac mini.md').write_text('---\ntype: thing\n---\n8 GB\n')
        items = self.ids()
        r = digest.decide(self.ws, self.sid, self.rel, items['The Mac mini has 16 GB.']['id'], 'accept')
        self.assertEqual(r['logged'], 'log/2026-09-27.md')
        log = (self.root / 'log' / '2026-09-27.md').read_text()
        self.assertIn('## From chats', log)
        self.assertIn('- **fact:** The Mac mini has 16 GB. ↔ [[Mac mini]] ([[2026-09-28 — claude abcd1234]], exchange 4)', log)
        self.assertIn('  > the Mac mini has 16 GB', log)
        self.assertIn('type: daily', log)
        self.assertEqual((self.root / 'notes' / 'Mac mini.md').read_text(), '---\ntype: thing\n---\n8 GB\n')
        self.assertIn('<!-- carry: accepted ', (self.root / self.rel).read_text())
        with self.assertRaisesRegex(CarryError, 'item_already_decided'):
            digest.decide(self.ws, self.sid, self.rel, items['The Mac mini has 16 GB.']['id'], 'skip')

    def test_second_accept_the_same_day_appends_under_one_heading(self):
        items = self.ids()
        digest.decide(self.ws, self.sid, self.rel, items['The pilot ships on 15 October.']['id'], 'accept')
        digest.decide(self.ws, self.sid, self.rel, items['The build takes 32 seconds.']['id'], 'accept')
        log = (self.root / 'log' / '2026-09-27.md').read_text()
        self.assertEqual(log.count('## From chats'), 1)
        self.assertLess(log.index('15 October'), log.index('32 seconds'))

    def test_fix_rewrites_the_item_and_logs_the_fixed_text(self):
        items = self.ids()
        digest.decide(self.ws, self.sid, self.rel, items['The pilot ships on 15 October.']['id'], 'fix', 'The pilot ships on 22 October.')
        text = (self.root / self.rel).read_text()
        self.assertIn('**decision:** The pilot ships on 22 October. <!-- carry: fixed ', text)
        self.assertIn('22 October', (self.root / 'log' / '2026-09-27.md').read_text())
        with self.assertRaisesRegex(CarryError, 'fix_needs_text'):
            digest.decide(self.ws, self.sid, self.rel, items['The build takes 32 seconds.']['id'], 'fix', '  ')

    def test_folded_items_need_no_decision_and_the_last_decision_ends_the_review(self):
        items = self.ids()
        with self.assertRaisesRegex(CarryError, 'item_not_actionable'):
            digest.decide(self.ws, self.sid, self.rel, items['The repo is public.']['id'], 'accept')
        files = {f['path']: f for f in vaultview.browse(self.ws, self.sid)['files']}
        self.assertTrue(files[self.rel]['review'] and files[self.rel]['digest'])
        results = [digest.decide(self.ws, self.sid, self.rel, it['id'], 'skip') for s, it in items.items() if it['decision'] is None]
        self.assertEqual([r['waiting'] for r in results], [3, 2, 1, 0])
        self.assertTrue(results[-1]['done'])
        self.assertNotIn('draft: true', (self.root / self.rel).read_text())
        files = {f['path']: f for f in vaultview.browse(self.ws, self.sid)['files']}
        self.assertFalse(files[self.rel]['review'])
        self.assertFalse((self.root / 'log' / '2026-09-27.md').exists())

    def test_state_pack_drops_skipped_items_and_hides_markers(self):
        today = datetime.date.today().isoformat()
        rel = f'+/{today} — harvest claude abcd1234.md'
        (self.root / rel).write_text(DIGEST)
        items = {it['statement']: it for it in digest.items(self.ws, self.sid, rel)['items']}
        digest.decide(self.ws, self.sid, rel, items['Invite two colleagues to the repository.']['id'], 'accept')
        pack = context.pack(self.root)
        self.assertIn('Invite two colleagues to the repository.', pack)
        self.assertNotIn('<!--', pack)
        digest.decide(self.ws, self.sid, rel, items['The pilot ships on 15 October.']['id'], 'skip')
        self.assertNotIn('15 October', context.pack(self.root))

    def test_a_plain_note_is_not_a_digest(self):
        (self.root / 'notes' / 'Plain.md').write_text('---\ntype: thing\ndraft: true\n---\n- **fact:** x\n')
        with self.assertRaisesRegex(CarryError, 'not_a_digest'):
            digest.items(self.ws, self.sid, 'notes/Plain.md')


if __name__ == '__main__':
    unittest.main()
