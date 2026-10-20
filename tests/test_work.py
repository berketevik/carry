"""İşlerin: the home page's one list of the signed-in person's work, from Trello, the team notes and chats.

Each piece of work is one row; its sources are badges. A chat's open item joins only once the owner
accepted it and stays until they mark it done; done is kept in the state, never in the notes."""
import json
from unittest.mock import patch

from _support import HASHING, WorkspaceCase

from carry import context, digest, profile, work
from carry.config import RetrievalConfig, SourceConfig, Workspace

CARDS = [
    dict(name='Rapor teslimi', board_id='b1', board='Müşteri A', list='Doing', due='2026-10-22T09:00:00.000Z',
         url='https://trello.com/c/AbC123', touched='2026-10-18T10:00:00.000Z', number=213),
    dict(name='Fikir', board_id='b1', board='Müşteri A', list='PARK', due=None,
         url='https://trello.com/c/ZzZ999', touched='2026-10-19T10:00:00.000Z', number=90),
    dict(name='Başka pano aynı numara', board_id='b2', board='Müşteri B', list='Doing', due=None,
         url='https://trello.com/c/QqQ111', touched='2026-10-01T10:00:00.000Z', number=90),
]


def recap(day, items, name='claude 0a1b2c3d'):
    lines = ['---', 'type: output', f'created: {day}', 'status: draft',
             'session: {"extractor": "claude:sonnet", "trigger": "end"}',
             'sources:', f'  - {{"resource": "/sources/carry/session/{day} — {name}.md", "title": "{day} — {name}"}}',
             '---', '', '# Session recap', '', '## New', '']
    for tag, text, decision in items:
        mark = f' <!-- carry: {decision} {day} -->' if decision else ''
        lines += [f'- **{tag}:** {text}{mark}', f'  > {text[:20]} *(exchange 1, owner, {day})*']
    return '\n'.join(lines) + '\n'


class WorkTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.vault = self.base / 'vault'
        (self.vault / '+').mkdir(parents=True)
        (self.vault / '.carry').mkdir()
        (self.vault / '.carry' / 'vault.json').write_text('{"language": "Turkish"}', encoding='utf-8')
        self.team = self.base / 'team'
        (self.team / '90_Inbox').mkdir(parents=True)
        self.okb('Marka rehberi', 'İsmail', 'active')
        self.okb('Ölçüm planı', 'İsmail, Sude', 'to-review', body='Kart: https://trello.com/c/AbC123')
        self.okb('Bütçe', 'ismail', 'open')
        self.okb('Ece işi', 'Ece', 'to-review')
        self.okb('İsmail için KSF devir notu', 'Deniz', 'to-review', folder='90_Inbox')
        self.okb('Deniz devir listesi', 'Deniz', 'to-review', folder='90_Inbox')
        self.workspace = Workspace.create(
            self.base / 'state2', sources=[SourceConfig('notes', self.vault, writable=True), SourceConfig('team', self.team, scope='team')],
            embedding=HASHING, retrieval=RetrievalConfig(top_k=6, auto_refresh=False))
        self.state = self.workspace.state_dir
        profile.save(self.workspace, 'ismail@ornek.com.tr', 'İsmail')

    def okb(self, title, owner, status, folder='', body=''):
        root = self.team / folder if folder else self.team
        (root / f'{title}.md').write_text(f'---\ntype: note\nowner: {owner}\nstatus: {status}\n---\n# {title}\n{body}\n', encoding='utf-8')

    def digest(self, day, items, name='claude 0a1b2c3d'):
        path = self.vault / '+' / f'{day} — session {name}.md'
        path.write_text(recap(day, items, name), encoding='utf-8')
        return f'+/{path.name}'

    def build(self, cards=CARDS):
        with patch.object(work.trello, 'view', return_value=dict(connected=True, cards=cards)):
            return work.build(self.workspace)

    def titles(self, result):
        return [row['title'] for row in result['items']]

    def test_the_team_notes_waiting_for_me_and_handed_to_me(self):
        rows = {row['title']: row for row in self.build(cards=[])['items']}
        self.assertEqual(set(rows), {'Ölçüm planı', 'Bütçe', 'İsmail için KSF devir notu'})
        self.assertEqual(rows['Bütçe']['badges'][0], dict(kind='okb', state='review', source_id='team', path='Bütçe.md'))
        self.assertEqual(rows['İsmail için KSF devir notu']['badges'][0]['state'], 'handed')

    def test_the_rest_of_my_notes_are_counted_not_listed(self):
        result = self.build(cards=[])
        self.assertEqual([n['title'] for n in result['owned']], ['Marka rehberi'])

    def test_only_accepted_open_items_from_chats_join_and_stay_past_two_weeks(self):
        self.digest('2026-09-01', [('open item', 'Eski ama kabul edilmiş iş', 'accepted')], name='claude 11111111')
        self.digest('2026-10-19', [('open item', 'Kabul edilen iş', 'accepted'), ('açık iş', 'Düzeltilen iş', 'fixed'),
                                   ('open item', 'Bekleyen iş', None), ('open item', 'Atlanan iş', 'skipped'),
                                   ('decision', 'Kabul edilen karar', 'accepted')])
        titles = self.titles(self.build(cards=[]))
        for title in ('Eski ama kabul edilmiş iş', 'Kabul edilen iş', 'Düzeltilen iş'):
            self.assertIn(title, titles)
        for title in ('Bekleyen iş', 'Atlanan iş', 'Kabul edilen karar'):
            self.assertNotIn(title, titles)

    def test_accepted_decisions_show_apart_and_never_as_work(self):
        self.digest('2026-10-19', [('decision', 'Kabul edilen karar', 'accepted'), ('decision', 'Bekleyen karar', None)])
        self.assertEqual([d['text'] for d in self.build(cards=[])['decisions']], ['Kabul edilen karar'])

    def test_done_is_kept_in_the_state_and_the_next_chat_is_not_told_either(self):
        rel = self.digest('2026-10-19', [('open item', 'Kabul edilen iş', 'accepted')])
        before = (self.vault / rel).read_text(encoding='utf-8')
        row = next(r for r in self.build(cards=[])['items'] if r['title'] == 'Kabul edilen iş')
        self.assertEqual(row['done'], [dict(digest=rel, item=digest.item_id('Kabul edilen iş'))])
        self.assertEqual(row['open'], dict(kind='note', source_id='notes', path=rel))
        work.mark_done(self.workspace, row['done'])
        self.assertNotIn('Kabul edilen iş', self.titles(self.build(cards=[])))
        self.assertEqual((self.vault / rel).read_text(encoding='utf-8'), before)
        self.assertTrue((self.state / work.DONE).is_file())
        pack = context.pack(self.vault, workspace=self.workspace, today=__import__('datetime').date(2026, 10, 20))
        self.assertNotIn('Kabul edilen iş', pack)

    def test_a_card_link_or_a_unique_number_puts_the_note_or_chat_on_the_card(self):
        self.digest('2026-10-19', [('open item', 'Kart #213 için marka maili gidecek', 'accepted'),
                                   ('open item', 'Card #90 belirsiz', 'accepted')])
        rows = self.build()['items']
        card = next(r for r in rows if r['title'] == 'Rapor teslimi')
        self.assertEqual([b['kind'] for b in card['badges']], ['trello', 'okb', 'chat'])
        self.assertNotIn('Ölçüm planı', [r['title'] for r in rows])
        self.assertNotIn('Kart #213 için marka maili gidecek', [r['title'] for r in rows])
        # #90 is on two boards: no guess, the chat item keeps its own row.
        self.assertIn('Card #90 belirsiz', [r['title'] for r in rows])

    def test_dated_work_first_then_the_latest_activity(self):
        self.digest('2026-10-20', [('open item', 'Bugünkü iş', 'accepted')])
        titles = self.titles(self.build())
        self.assertEqual(titles[0], 'Rapor teslimi')
        self.assertLess(titles.index('Bugünkü iş'), titles.index('Fikir'))

    def test_without_a_profile_only_trello_and_chats(self):
        profile.logout(self.workspace)
        self.digest('2026-10-19', [('open item', 'Kabul edilen iş', 'accepted')])
        with patch.object(profile.trello, 'disconnect'):
            result = self.build(cards=[])
        self.assertFalse(result['signed_in'])
        self.assertEqual(self.titles(result), ['Kabul edilen iş'])

    def test_the_app_reads_work_and_marks_it_done_through_the_bridge(self):
        from carry.desktop import Bridge
        rel = self.digest('2026-10-19', [('open item', 'Kabul edilen iş', 'accepted')])
        call = lambda action, **fields: Bridge().dispatch(dict(action=action, workspace=str(self.state), **fields))
        with patch.object(work.trello, 'view', return_value=dict(connected=False)):
            self.assertIn('Kabul edilen iş', self.titles(call('work')))
            result = call('work_done', done=[dict(digest=rel, item=digest.item_id('Kabul edilen iş'))])
        self.assertNotIn('Kabul edilen iş', self.titles(result))
        self.assertEqual(json.loads((self.state / work.DONE).read_text(encoding='utf-8'))['done'][0]['digest'], rel)
