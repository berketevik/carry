"""Item-by-item review of a harvest digest: real files in a temporary vault."""
import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import _support  # noqa: F401
from carry import context, digest, harvest, vault, vaultview, work
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
        (self.root / self.rel).write_text(DIGEST, encoding='utf-8')

    def ids(self):
        return {it['statement']: it for it in digest.items(self.ws, self.sid, self.rel)['items']}

    def test_a_digest_is_recognised_without_pyyaml(self):
        # The default install has no PyYAML: the `harvest:` line must still read as a mapping,
        # or the digest is shown as a plain draft and can be approved wholesale.
        from carry import markdown
        with patch.object(markdown, '_yaml', None):
            front, _ = markdown.parse_frontmatter(DIGEST)
            self.assertEqual(front['harvest']['extractor'], 'claude:sonnet')
            self.assertTrue(vaultview.is_digest(front))
            self.assertEqual(markdown.parse_frontmatter('---\nnote: {not json}\n---\n')[0]['note'], '{not json}')

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
        (self.root / 'notes' / 'Mac mini.md').write_text('---\ntype: thing\n---\n8 GB\n', encoding='utf-8')
        items = self.ids()
        r = digest.decide(self.ws, self.sid, self.rel, items['The Mac mini has 16 GB.']['id'], 'accept')
        self.assertEqual(r['logged'], 'log/2026-09-27.md')
        log = (self.root / 'log' / '2026-09-27.md').read_text(encoding='utf-8')
        self.assertIn('## From chats', log)
        self.assertIn('- **fact:** The Mac mini has 16 GB. ↔ [[Mac mini]] ([[2026-09-28 — claude abcd1234]], exchange 4)', log)
        self.assertIn('  > the Mac mini has 16 GB', log)
        self.assertIn('type: daily', log)
        self.assertEqual((self.root / 'notes' / 'Mac mini.md').read_text(encoding='utf-8'), '---\ntype: thing\n---\n8 GB\n')
        self.assertIn('<!-- carry: accepted ', (self.root / self.rel).read_text(encoding='utf-8'))
        with self.assertRaisesRegex(CarryError, 'item_already_decided'):
            digest.decide(self.ws, self.sid, self.rel, items['The Mac mini has 16 GB.']['id'], 'skip')

    def test_second_accept_the_same_day_appends_under_one_heading(self):
        items = self.ids()
        digest.decide(self.ws, self.sid, self.rel, items['The pilot ships on 15 October.']['id'], 'accept')
        digest.decide(self.ws, self.sid, self.rel, items['The build takes 32 seconds.']['id'], 'accept')
        log = (self.root / 'log' / '2026-09-27.md').read_text(encoding='utf-8')
        self.assertEqual(log.count('## From chats'), 1)
        self.assertLess(log.index('15 October'), log.index('32 seconds'))

    def test_fix_rewrites_the_item_and_logs_the_fixed_text(self):
        items = self.ids()
        digest.decide(self.ws, self.sid, self.rel, items['The pilot ships on 15 October.']['id'], 'fix', 'The pilot ships on 22 October.')
        text = (self.root / self.rel).read_text(encoding='utf-8')
        self.assertIn('**decision:** The pilot ships on 22 October. <!-- carry: fixed ', text)
        self.assertIn('22 October', (self.root / 'log' / '2026-09-27.md').read_text(encoding='utf-8'))
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
        self.assertNotIn('draft: true', (self.root / self.rel).read_text(encoding='utf-8'))
        files = {f['path']: f for f in vaultview.browse(self.ws, self.sid)['files']}
        self.assertFalse(files[self.rel]['review'])
        self.assertFalse((self.root / 'log' / '2026-09-27.md').exists())

    def test_state_pack_drops_skipped_items_and_hides_markers(self):
        # The pack reads recent digests, so it runs on the digest's own day, not today.
        day = datetime.date(2026, 9, 28)
        items = self.ids()
        digest.decide(self.ws, self.sid, self.rel, items['Invite two colleagues to the repository.']['id'], 'accept')
        pack = context.pack(self.root, today=day)
        self.assertIn('Invite two colleagues to the repository.', pack)
        self.assertNotIn('<!--', pack)
        digest.decide(self.ws, self.sid, self.rel, items['The pilot ships on 15 October.']['id'], 'skip')
        self.assertNotIn('15 October', context.pack(self.root, today=day))

    def test_a_digest_is_never_approved_wholesale(self):
        with self.assertRaisesRegex(CarryError, 'note_digest'):
            vaultview.approve_note(self.ws, self.sid, self.rel)
        result = vaultview.approve_many(self.ws, self.sid, [self.rel])
        self.assertEqual((result['approved'], result['skipped'][0]['reason']), ([], 'digest'))
        files = {f['path']: f for f in vaultview.browse(self.ws, self.sid)['files']}
        self.assertTrue(files[self.rel]['review'])
        self.assertFalse(files[self.rel]['approvable'])
        self.assertIn('draft: true', (self.root / self.rel).read_text(encoding='utf-8'))

    def test_the_reader_does_not_show_decision_markers(self):
        items = self.ids()
        digest.decide(self.ws, self.sid, self.rel, items['The build takes 32 seconds.']['id'], 'skip')
        body = vaultview.note(self.ws, self.sid, self.rel)['body']
        self.assertIn('The build takes 32 seconds.', body)
        self.assertNotIn('<!--', body)

    def test_reject_saves_nothing_and_pass_saves_it_marked_unsure(self):
        items = self.ids()
        r = digest.decide(self.ws, self.sid, self.rel, items['The pilot ships on 15 October.']['id'], 'reject')
        self.assertEqual((r['decision'], r['logged']), ('skipped', None))
        self.assertFalse((self.root / 'log' / '2026-09-27.md').exists())
        r = digest.decide(self.ws, self.sid, self.rel, items['The build takes 32 seconds.']['id'], 'pass')
        self.assertEqual((r['decision'], r['logged']), ('unsure', 'log/2026-09-27.md'))
        log = (self.root / 'log' / '2026-09-27.md').read_text(encoding='utf-8')
        self.assertIn('- **fact · unsure, the owner was not certain:** The build takes 32 seconds. ([[2026-09-28 — claude abcd1234]], exchange 3)', log)
        self.assertNotIn('15 October', log)
        self.assertIn('<!-- carry: unsure ', (self.root / self.rel).read_text(encoding='utf-8'))
        self.assertNotIn('<!--', vaultview.note(self.ws, self.sid, self.rel)['body'])
        with self.assertRaisesRegex(CarryError, 'invalid_decision'):
            digest.decide(self.ws, self.sid, self.rel, items['The Mac mini has 16 GB.']['id'], 'maybe')

    def test_a_passed_open_item_stays_off_the_work_list_and_the_state_pack(self):
        day = datetime.date(2026, 9, 28)
        items = self.ids()
        digest.decide(self.ws, self.sid, self.rel, items['Invite two colleagues to the repository.']['id'], 'pass')
        self.assertNotIn('Invite two colleagues', context.pack(self.root, today=day))
        opens, _ = work._from_digests(self.ws, day)
        self.assertEqual(opens, [])
        self.assertIn('**open item · unsure, the owner was not certain:** Invite two colleagues',
                      (self.root / 'log' / '2026-09-28.md').read_text(encoding='utf-8'))
        # A passed item is decided: the recap still leaves Review once nothing else waits.
        results = [digest.decide(self.ws, self.sid, self.rel, it['id'], 'pass') for it in items.values()
                   if it['decision'] is None and it['statement'] != 'Invite two colleagues to the repository.']
        self.assertTrue(results[-1]['done'])

    def test_undo_puts_the_files_back_as_they_were_newest_first(self):
        items = self.ids()
        start = (self.root / self.rel).read_text(encoding='utf-8')
        first = digest.decide(self.ws, self.sid, self.rel, items['The pilot ships on 15 October.']['id'], 'fix', 'The pilot ships on 22 October.')
        self.assertTrue((self.root / 'log' / '2026-09-27.md').exists())
        second = digest.decide(self.ws, self.sid, self.rel, items['Invite two colleagues to the repository.']['id'], 'reject')
        with self.assertRaisesRegex(CarryError, 'undo_not_available'):
            digest.undo(self.ws, self.sid, self.rel, first['undo'])  # not the newest
        digest.undo(self.ws, self.sid, self.rel, second['undo'])
        result = digest.undo(self.ws, self.sid, self.rel, first['undo'])
        # The fixed statement's first wording, and no daily log the decision had created.
        self.assertEqual((self.root / self.rel).read_text(encoding='utf-8'), start)
        self.assertFalse((self.root / 'log' / '2026-09-27.md').exists())
        self.assertEqual(result['waiting'], 4)
        with self.assertRaisesRegex(CarryError, 'undo_not_available'):
            digest.undo(self.ws, self.sid, self.rel, first['undo'])

    def test_undo_brings_a_finished_recap_back_to_review_and_refuses_after_an_edit(self):
        items = self.ids()
        results = [digest.decide(self.ws, self.sid, self.rel, it['id'], 'accept') for it in items.values() if it['decision'] is None]
        self.assertTrue(results[-1]['done'])
        self.assertTrue(digest.undo(self.ws, self.sid, self.rel, results[-1]['undo'])['draft'])
        files = {f['path']: f for f in vaultview.browse(self.ws, self.sid)['files']}
        self.assertTrue(files[self.rel]['review'])
        log = self.root / 'log' / '2026-09-27.md'
        log.write_text(log.read_text(encoding='utf-8') + '\nA line the owner added.\n', encoding='utf-8')
        with self.assertRaisesRegex(CarryError, 'undo_changed_since'):
            digest.undo(self.ws, self.sid, self.rel, results[-2]['undo'])
        self.assertIn('A line the owner added.', log.read_text(encoding='utf-8'))

    def test_accept_all_is_taken_back_in_one_undo(self):
        start = (self.root / self.rel).read_text(encoding='utf-8')
        result = digest.accept_all(self.ws, self.sid, self.rel)
        self.assertEqual(result['accepted'], 3)
        digest.undo(self.ws, self.sid, self.rel, result['undo'])
        self.assertEqual((self.root / self.rel).read_text(encoding='utf-8'), start)
        self.assertFalse((self.root / 'log').exists() and any((self.root / 'log').glob('2026-09-2*.md')))

    def test_an_unsure_item_is_listed_and_settled_later(self):
        items = self.ids()
        digest.decide(self.ws, self.sid, self.rel, items['The build takes 32 seconds.']['id'], 'pass')
        digest.decide(self.ws, self.sid, self.rel, items['The pilot ships on 15 October.']['id'], 'pass')
        digest.decide(self.ws, self.sid, self.rel, items['Invite two colleagues to the repository.']['id'], 'accept')
        listed = {it['statement']: it for it in digest.unsure(self.ws, self.sid)['items']}
        self.assertEqual(set(listed), {'The build takes 32 seconds.', 'The pilot ships on 15 October.'})
        self.assertEqual(listed['The build takes 32 seconds.']['path'], self.rel)
        log = self.root / 'log' / '2026-09-27.md'
        # Accepted once the owner knows: the log line loses its unsure mark, in place.
        r = digest.settle(self.ws, self.sid, self.rel, listed['The build takes 32 seconds.']['id'], 'accept')
        self.assertEqual((r['decision'], r['logged']), ('accepted', 'log/2026-09-27.md'))
        text = log.read_text(encoding='utf-8')
        self.assertIn('- **fact:** The build takes 32 seconds. ([[2026-09-28 — claude abcd1234]], exchange 3)', text)
        self.assertNotIn('fact · unsure', text)
        # Rejected: the line goes, the rest of the log stays.
        digest.settle(self.ws, self.sid, self.rel, listed['The pilot ships on 15 October.']['id'], 'reject')
        text = log.read_text(encoding='utf-8')
        self.assertNotIn('15 October', text)
        self.assertIn('32 seconds', text)
        self.assertEqual(digest.unsure(self.ws, self.sid)['items'], [])
        decisions = {it['statement']: it['decision'] for it in digest.parse((self.root / self.rel).read_text(encoding='utf-8'))['items']}
        self.assertEqual((decisions['The build takes 32 seconds.'], decisions['The pilot ships on 15 October.']), ('accepted', 'skipped'))
        with self.assertRaisesRegex(CarryError, 'item_not_unsure'):
            digest.settle(self.ws, self.sid, self.rel, listed['The build takes 32 seconds.']['id'], 'reject')

    def test_the_app_core_passes_settles_and_undoes(self):
        # The requests the app sends, through the core it talks to.
        from carry import maintenance
        from carry.desktop import Bridge
        bridge = Bridge()
        call = lambda action, **f: bridge.dispatch(dict(action=action, workspace=str(self.ws.state_dir), path=self.rel, **f))
        item = self.ids()['The build takes 32 seconds.']['id']
        with patch.object(maintenance, 'start') as start:
            passed = call('digest_decide', item=item, decision='pass')
            self.assertEqual([it['statement'] for it in call('digest_unsure')['items']], ['The build takes 32 seconds.'])
            settled = call('digest_settle', item=item, decision='accept')
            self.assertEqual((settled['decision'], call('digest_unsure')['items']), ('accepted', []))
            call('digest_undo', undo=settled['undo'])
            self.assertEqual(len(call('digest_unsure')['items']), 1)
            call('digest_undo', undo=passed['undo'])
            self.assertEqual(call('digest_items')['waiting'], 4)
        self.assertEqual(start.call_count, 4)  # each write re-indexes the daily log

    def test_settling_with_a_fix_rewrites_the_line_and_can_be_taken_back(self):
        items = self.ids()
        digest.decide(self.ws, self.sid, self.rel, items['The build takes 32 seconds.']['id'], 'pass')
        log = self.root / 'log' / '2026-09-27.md'
        before = (log.read_text(encoding='utf-8'), (self.root / self.rel).read_text(encoding='utf-8'))
        r = digest.settle(self.ws, self.sid, self.rel, items['The build takes 32 seconds.']['id'], 'fix', 'The build takes 40 seconds.')
        self.assertIn('- **fact:** The build takes 40 seconds.', log.read_text(encoding='utf-8'))
        self.assertIn('**fact:** The build takes 40 seconds. <!-- carry: fixed ', (self.root / self.rel).read_text(encoding='utf-8'))
        digest.undo(self.ws, self.sid, self.rel, r['undo'])
        self.assertEqual((log.read_text(encoding='utf-8'), (self.root / self.rel).read_text(encoding='utf-8')), before)

    def test_a_conflict_shows_the_line_of_the_note_it_contradicts(self):
        (self.root / 'notes' / 'Mac mini.md').write_text('---\ntype: thing\n---\n# Mac mini\n\nIt runs the local server.\nThe Mac mini has 8 GB of memory.\n', encoding='utf-8')
        conflict = next(it for it in digest.items(self.ws, self.sid, self.rel)['items'] if it['section'] == 'conflict')
        self.assertEqual(conflict['match_path'], 'notes/Mac mini.md')
        self.assertEqual(conflict['match_text'], 'The Mac mini has 8 GB of memory.')
        stored = DIGEST.replace('the Mac mini has 16 GB *(exchange 4, owner, 2026-09-27)*',
                                'the Mac mini has 16 GB *(exchange 4, owner, 2026-09-27)*\n  ≠ Mac mini: 8 GB RAM, bought in July.')
        (self.root / self.rel).write_text(stored, encoding='utf-8')
        conflict = next(it for it in digest.items(self.ws, self.sid, self.rel)['items'] if it['section'] == 'conflict')
        self.assertEqual(conflict['match_text'], 'Mac mini: 8 GB RAM, bought in July.')

    def test_indented_lines_parse_in_any_order_and_old_digests_still_parse(self):
        text = DIGEST.replace('- **fact:** The build takes 32 seconds.\n  > build takes 32 seconds *(exchange 3, assistant, 2026-09-27)*',
                              '- **fact:** The build takes 32 seconds. ↔ [[Build]]\n  ∵ The note records an older build.\n'
                              '  > build takes 32 seconds *(exchange 3, assistant, 2026-09-27)*\n  ≠ The build took 50 seconds in July.')
        items = {it['statement']: it for it in digest.parse(text)['items']}
        build = items['The build takes 32 seconds.']
        self.assertEqual((build['exchange'], build['why'], build['match_text'], build['match']),
                         (3, 'The note records an older build.', 'The build took 50 seconds in July.', '[[Build]]'))
        old = {it['statement']: it for it in digest.parse(DIGEST)['items']}
        self.assertEqual((old['The build takes 32 seconds.']['exchange'], old['The build takes 32 seconds.']['why']), (3, ''))

    def recheck(self, verdict, related=True):
        def fake(cands, ws, which, language):
            if related:
                cands[0].update(related=True, match_text='The Mac mini had 8 GB in July.')
            return [verdict], True
        with patch.object(harvest, 'extractor', return_value='claude:sonnet'), patch.object(harvest, 'agent_compare', side_effect=fake):
            return digest.recheck(self.ws, self.sid, self.rel)

    def test_recheck_moves_a_dismissed_conflict_to_new_with_its_related_note(self):
        before = digest.items(self.ws, self.sid, self.rel)['items']
        result = self.recheck(('new', str(self.root / 'notes' / 'Mac mini.md')))
        self.assertEqual((result['rechecked'], result['moved']['new']), (1, 1))
        text = (self.root / self.rel).read_text(encoding='utf-8')
        self.assertNotIn('## Conflict candidates', text)
        items = {it['statement']: it for it in digest.parse(text)['items']}
        moved = items['The Mac mini has 16 GB.']
        self.assertEqual((moved['section'], moved['match'], moved['match_text'], moved['exchange'], moved['date']),
                         ('new', '[[Mac mini]]', 'The Mac mini had 8 GB in July.', 4, '2026-09-27'))
        self.assertEqual(len(items), len(before))
        self.assertLess(text.index('The Mac mini has 16 GB.'), text.index('## Already recorded'))

    def test_recheck_never_writes_the_fallback_date_as_the_chat_date(self):
        (self.root / self.rel).write_text(DIGEST.replace('the Mac mini has 16 GB *(exchange 4, owner, 2026-09-27)*', 'the Mac mini has 16 GB *(exchange 4, owner)*'), encoding='utf-8')
        self.recheck(('new', str(self.root / 'notes' / 'Mac mini.md')))
        self.assertIn('the Mac mini has 16 GB *(exchange 4, owner)*', (self.root / self.rel).read_text(encoding='utf-8'))

    def test_recheck_folds_a_known_one_and_never_touches_decided_items(self):
        items = self.ids()
        digest.decide(self.ws, self.sid, self.rel, items['The pilot ships on 15 October.']['id'], 'accept')
        decided = [l for l in (self.root / self.rel).read_text(encoding='utf-8').split('\n') if '<!-- carry:' in l]
        self.recheck(('known', str(self.root / 'notes' / 'Mac mini.md')), related=False)
        text = (self.root / self.rel).read_text(encoding='utf-8')
        self.assertIn('- The Mac mini has 16 GB. → [[Mac mini]]', text)
        self.assertEqual([l for l in text.split('\n') if '<!-- carry:' in l], decided)
        self.assertEqual(digest.items(self.ws, self.sid, self.rel)['waiting'], 2)

    def test_recheck_keeps_a_real_conflict_in_place(self):
        def fake(cands, ws, which, language):
            cands[0].update(match_text='8 GB, bought in July.', why='Same Mac, different memory.')
            return [('conflict', str(self.root / 'notes' / 'Mac mini.md'))], True
        with patch.object(harvest, 'extractor', return_value='claude:sonnet'), patch.object(harvest, 'agent_compare', side_effect=fake):
            digest.recheck(self.ws, self.sid, self.rel)
        c = next(it for it in digest.parse((self.root / self.rel).read_text(encoding='utf-8'))['items'] if it['statement'] == 'The Mac mini has 16 GB.')
        self.assertEqual((c['section'], c['match_text'], c['why']), ('conflict', '8 GB, bought in July.', 'Same Mac, different memory.'))

    def test_a_plain_note_is_not_a_digest(self):
        (self.root / 'notes' / 'Plain.md').write_text('---\ntype: thing\ndraft: true\n---\n- **fact:** x\n', encoding='utf-8')
        with self.assertRaisesRegex(CarryError, 'not_a_digest'):
            digest.items(self.ws, self.sid, 'notes/Plain.md')


if __name__ == '__main__':
    unittest.main()


class ReviewHelpTest(unittest.TestCase):
    """Review says where a draft came from and accepts the plain items in one go."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.root = base / 'Vault'
        vault.apply(vault.plan(self.root, language='English', workspace=base / 'ws', attach=True), git=False)
        self.ws = Workspace.load(base / 'ws')
        self.sid = vaultview.default_vault(self.ws)
        self.rel = '+/2026-09-28 — session claude abcd1234.md'
        text = DIGEST.replace('  - "[[2026-09-28 — claude abcd1234]]"',
                              '  - {"resource": "/sources/carry/session/2026-09-28 — claude abcd1234.md", "title": "2026-09-28 — claude abcd1234"}')
        (self.root / self.rel).write_text(text, encoding='utf-8')
        raw = self.root / 'sources' / 'carry' / 'session' / '2026-09-28 — claude abcd1234.md'
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text('---\ntype: raw\n---\n\n## Exchange 1\n\n**Owner:** When does the pilot ship?\nAnd who joins?\n\n'
                       '**Assistant:** On 15 October.\n', encoding='utf-8')

    def test_items_name_the_chat_they_came_from(self):
        chat = digest.items(self.ws, self.sid, self.rel)['chat']
        self.assertEqual(chat, dict(kind='chat', client='claude', day='2026-09-28', topic='', first='When does the pilot ship? And who joins?'))

    def test_a_recap_is_listed_and_shown_by_its_chat_name(self):
        text = (self.root / self.rel).read_text(encoding='utf-8').replace('type: output\n', 'type: output\ntitle: "pilot tarihi"\n', 1)
        (self.root / self.rel).write_text(text, encoding='utf-8')
        files = {f['path']: f for f in vaultview.browse(self.ws, self.sid)['files']}
        self.assertEqual(files[self.rel]['topic'], 'pilot tarihi')
        self.assertEqual(vaultview.note(self.ws, self.sid, self.rel)['topic'], 'pilot tarihi')
        self.assertEqual(digest.items(self.ws, self.sid, self.rel)['chat']['topic'], 'pilot tarihi')
        self.assertNotIn('topic', files['LLM-GUIDE.md'])  # only recaps carry one

    def test_an_item_from_the_assistant_says_what_the_owner_had_asked(self):
        # Review asks "you asked this, the assistant answered that": the owner's message of the cited exchange.
        raw = self.root / 'sources' / 'carry' / 'session' / '2026-09-28 — claude abcd1234.md'
        raw.write_text(raw.read_text(encoding='utf-8') + '\n## Exchange 3\n\n**Owner:** How long does the build take now?\n\n'
                       '**Assistant:** The build takes 32 seconds on the Mac mini.\n', encoding='utf-8')
        items = {it['statement']: it for it in digest.items(self.ws, self.sid, self.rel)['items']}
        self.assertEqual(items['The build takes 32 seconds.']['asked'], 'How long does the build take now?')
        self.assertNotIn('asked', items['The pilot ships on 15 October.'])  # the owner's own words need no question
        self.assertNotIn('asked', items['The Mac mini has 16 GB.'])

    def test_accept_all_takes_every_plain_item_and_leaves_the_conflict(self):
        result = digest.accept_all(self.ws, self.sid, self.rel)
        self.assertEqual((result['accepted'], result['waiting'], result['done']), (3, 1, False))
        items = {it['statement']: it['decision'] for it in digest.items(self.ws, self.sid, self.rel)['items']}
        self.assertEqual(items['The Mac mini has 16 GB.'], None)
        self.assertEqual(items['Invite two colleagues to the repository.'], 'accepted')
        self.assertIn('Invite two colleagues', (self.root / 'log' / '2026-09-28.md').read_text(encoding='utf-8'))

    def test_accept_all_leaves_an_item_marked_to_review_for_the_owner(self):
        # harvest puts an item it scores as probably withdrawn under To review: one click must not accept it.
        doubtful = DIGEST.replace('## Already recorded', '## To review\n\n- **open item:** Move the demo to Friday.\n'
                                  '  > move the demo to Friday *(exchange 5, owner)*\n\n## Already recorded')
        (self.root / self.rel).write_text(doubtful, encoding='utf-8')
        result = digest.accept_all(self.ws, self.sid, self.rel)
        self.assertEqual((result['accepted'], result['waiting']), (3, 2))
        items = {it['statement']: it['decision'] for it in digest.items(self.ws, self.sid, self.rel)['items']}
        self.assertIsNone(items['Move the demo to Friday.'])

    def test_a_statement_that_waits_twice_is_decided_copy_by_copy(self):
        twice = DIGEST.replace('## Conflict candidates (review)', '- **open item:** Invite two colleagues to the repository.\n'
                               '  > invite two colleagues *(exchange 5, owner)*\n\n## Conflict candidates (review)')
        (self.root / self.rel).write_text(twice, encoding='utf-8')
        # Both copies share one id: one click leaves them, and each decision takes the copy that still waits.
        result = digest.accept_all(self.ws, self.sid, self.rel)
        self.assertEqual((result['accepted'], result['waiting']), (2, 3))
        item = digest.item_id('Invite two colleagues to the repository.')
        digest.decide(self.ws, self.sid, self.rel, item, 'accept')
        digest.decide(self.ws, self.sid, self.rel, item, 'skip')
        decided = [it['decision'] for it in digest.items(self.ws, self.sid, self.rel)['items'] if it['id'] == item]
        self.assertEqual(decided, ['accepted', 'skipped'])
        with self.assertRaises(CarryError) as caught:
            digest.decide(self.ws, self.sid, self.rel, item, 'accept')
        self.assertEqual(str(caught.exception), 'item_already_decided')
