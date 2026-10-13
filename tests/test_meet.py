"""Carry Meet: the transcript note, the summary with verified quotes, the digest and the bridge."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import _support  # noqa: F401
from carry import desktop, digest, harvest, meet, vault, vaultview
from carry.config import SourceConfig, Workspace
from carry.errors import CarryError
from carry.markdown import parse_frontmatter

SEGMENTS = [
    dict(start=2.0, speaker='me', text='Merhaba, bugün Carry toplantı kaydını konuşuyoruz.'),
    dict(start=9.5, speaker='others', text='Kayıtlar cihazda yazıya dökülecek ve ses diske yazılmayacak, buna karar verdik.'),
    dict(start=21.0, speaker='me', text='Ayşe cuma gününe kadar dosya yerini onaylayacak.'),
    dict(start=34.0, speaker='others', text='Sistem sesi macOS on dört nokta ikiden itibaren destekleniyor.'),
]


def agent(*answers):
    """A fake assistant: one list of items per call, in order."""
    calls = []
    replies = iter(answers)

    def ask(system, body, which, field):
        calls.append(dict(system=system, body=body, which=which, field=field))
        return next(replies)
    return ask, calls


class MeetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.root = base / 'Vault'
        vault.apply(vault.plan(self.root, language='Turkish', workspace=base / 'ws', attach=True), git=False)
        self.ws = Workspace.load(base / 'ws')
        self.sid = vaultview.default_vault(self.ws)
        # No test reaches a real assistant or the search index.
        stop = patch.object(harvest, 'ask_agent', side_effect=CarryError('no_agent_in_tests')); stop.start(); self.addCleanup(stop.stop)
        compare = patch.object(harvest, 'agent_compare', side_effect=lambda items, *a: ([('new', None)] * len(items), True))
        self.compare = compare.start(); self.addCleanup(compare.stop)
        self.real_reindex = meet.reindex
        reindex = patch.object(meet, 'reindex'); self.reindex = reindex.start(); self.addCleanup(reindex.stop)

    def saved(self, segments=SEGMENTS, title='Carry Meet'):
        return meet.save(self.ws, segments, title=title, started='2026-10-13T14:30:00+03:00', duration=95)

    def test_the_transcript_is_saved_at_once_as_a_raw_meeting_note(self):
        saved = self.saved(title='Carry: kayıt/özet?')
        self.assertEqual(saved['path'], 'sources/carry/meet/2026-10-13 1430 — Carry kayıt özet.md')
        text = (self.root / saved['path']).read_text(encoding='utf-8')
        front, body = parse_frontmatter(text)
        self.assertEqual((front['type'], front['meet_status'], front['duration_minutes'], str(front['created'])),
                         ('meeting', 'pending', 2, '2026-10-13'))
        self.assertIn('- **[2 · 00:09] Karşı taraf:** Kayıtlar cihazda yazıya dökülecek', body)
        self.assertIn('- **[1 · 00:02] Ben:** Merhaba', body)
        # A raw record: never in the review queue, never approved.
        files = {f['path']: f for f in vaultview.browse(self.ws, self.sid)['files']}
        self.assertFalse(files[saved['path']]['review'])
        self.assertFalse(files[saved['path']]['approvable'])
        self.assertEqual(self.saved()['path'], 'sources/carry/meet/2026-10-13 1430 — Carry Meet.md')
        self.assertEqual(self.saved()['path'], 'sources/carry/meet/2026-10-13 1430 — Carry Meet (2).md')

    def test_an_empty_or_malformed_transcript_is_refused(self):
        with self.assertRaisesRegex(CarryError, 'meeting_transcript_empty'):
            meet.save(self.ws, [dict(start=1, speaker='me', text='  '), dict(start=2, speaker='nobody', text='x')])
        with self.assertRaisesRegex(CarryError, 'invalid_meeting_transcript'):
            meet.save(self.ws, 'not a list')

    def test_the_other_side_heard_through_the_microphone_is_left_out(self):
        lines = meet.clean_lines([
            dict(start=9.5, speaker='others', text='Kayıtlar cihazda yazıya dökülecek, ses diske yazılmayacak.'),
            dict(start=10.4, speaker='me', text='kayıtlar cihazda yazıya dökülecek ses diske yazılmayacak'),  # the echo
            dict(start=12.0, speaker='me', text='Evet, tamam.'),  # a short reply stays
            dict(start=14.0, speaker='me', text='Ses diske yazılmayacaksa yedeği nerede tutacağız?'),
            dict(start=40.0, speaker='me', text='Kayıtlar cihazda yazıya dökülecek, bunu tekrar ediyorum.'),  # too late to be an echo
        ])
        self.assertEqual([(x['speaker'], x['start']) for x in lines], [('others', 9.5), ('me', 12.0), ('me', 14.0), ('me', 40.0)])
        mic_only = [dict(start=1, speaker='me', text='kayıtlar cihazda yazıya dökülecek ses diske yazılmayacak')]
        self.assertEqual(len(meet.clean_lines(mic_only)), 1)

    def test_a_team_knowledge_base_never_receives_a_meeting(self):
        team = Path(self.tmp.name) / 'team'; team.mkdir()
        only_team = self.ws.with_sources([SourceConfig('okb', team, False, scope='team')])
        with self.assertRaisesRegex(CarryError, 'meeting_needs_own_notes'):
            meet.meeting_vault(only_team)
        with self.assertRaisesRegex(CarryError, 'meeting_vault_not_own_source'):
            meet.meeting_vault(self.ws.with_sources([*self.ws.sources, SourceConfig('okb', team, False, scope='team')]), team)

    def test_only_items_with_a_verbatim_quote_survive(self):
        rel = self.saved()['path']
        _, _, lines, _ = meet.read(self.root, rel)
        items = [
            dict(type='decision', statement='Kayıtlar cihazda yazıya dökülür, ses diske yazılmaz.', line=2,
                 quote='kayıtlar cihazda yazıya dökülecek ve ses diske yazılmayacak'),
            # Wrong line, right words: found in the line that has them.
            dict(type='open_item', statement='Ayşe cuma gününe kadar dosya yerini onaylar.', line=4,
                 quote='Ayşe cuma gününe kadar dosya yerini onaylayacak'),
            dict(type='fact', statement='Ses buluta gider.', line=2, quote='ses buluta gönderilecek'),       # invented
            dict(type='decision', statement='Kısa alıntı.', line=2, quote='karar verdik'),                   # too short
            dict(type='opinion', statement='Bilinmeyen tür.', line=2, quote='buna karar verdik ve geçtik'),  # bad type
        ]
        kept, dropped = meet.check(items, lines, meet.TYPES)
        self.assertEqual([k['line'] for k in kept], [2, 3])
        self.assertEqual(kept[1]['speaker'], 'Ben')
        self.assertEqual(dropped, 3)

    def test_a_quote_without_the_marks_of_turkish_letters_keeps_the_line_s_own_words(self):
        lines = [dict(n=1, at='00:03', speaker='Ben', text='O zaman yeni siteyi aralıkta yayına alıyoruz, İstanbul ekibi de onaylar.')]
        kept, dropped = meet.check([dict(type='decision', statement='Site aralıkta yayına alınır.', line=1,
                                         quote='yeni siteyi aralikta yayina aliyoruz'),
                                    dict(type='fact', statement='Ekip onaylar.', line=1, quote='istanbul ekibi de onaylar'),
                                    dict(type='fact', statement='Uydurma.', line=1, quote='yeni siteyi ocakta yayina aliyoruz')],
                                   lines, meet.TYPES)
        self.assertEqual(dropped, 1)
        self.assertEqual([k['quote'] for k in kept], ['yeni siteyi aralıkta yayına alıyoruz', 'istanbul ekibi de onaylar'])

    def test_long_transcripts_are_split_within_the_budget(self):
        lines = [dict(n=i + 1, at='00:00', speaker='Ben', text='kelime ' * 100) for i in range(50)]
        parts, cut = meet.parts(lines, limit=2000, most=3)
        self.assertEqual(len(parts), 3)
        self.assertEqual(cut, parts[-1][-1]['n'] + 1)
        self.assertEqual(meet.parts(lines[:2], limit=2000, most=3), ([lines[:2]], None))

    def test_summary_writes_the_minutes_and_a_digest_reviewed_item_by_item(self):
        rel = self.saved()['path']
        ask, calls = agent([
            dict(type='summary', statement='Toplantı kaydının cihazda yazıya dökülmesi konuşuldu.', line=2,
                 quote='Kayıtlar cihazda yazıya dökülecek'),
            dict(type='decision', statement='Ses diske yazılmaz.', line=2, quote='ses diske yazılmayacak, buna karar verdik'),
            dict(type='open_item', statement='Ayşe cuma gününe kadar dosya yerini onaylar.', line=3,
                 quote='Ayşe cuma gününe kadar dosya yerini onaylayacak'),
            dict(type='fact', statement='Ses buluta gider.', line=2, quote='ses buluta gönderilecek'),
        ])
        with patch.object(harvest, 'ask_agent', side_effect=ask):
            report = meet.summarize(self.ws, rel, which='claude:sonnet')
        self.assertEqual((report['points'], report['items'], report['dropped']), (1, 2, 1))
        self.assertEqual(len(calls), 1)
        self.assertIn('[2] 00:09 Karşı taraf: Kayıtlar cihazda', calls[0]['body'])
        text = (self.root / rel).read_text(encoding='utf-8')
        front, body = parse_frontmatter(text)
        self.assertEqual((front['meet_status'], front['meet_digest']), ('done', report['digest']))
        self.assertIn('## Özet\n\n- Toplantı kaydının cihazda yazıya dökülmesi konuşuldu. *(00:09)*', body)
        self.assertIn('### Kararlar\n\n- Ses diske yazılmaz.\n  > ses diske yazılmayacak, buna karar verdik *(segment 2, Karşı taraf, 2026-10-13)*', body)
        self.assertNotIn('buluta', body)
        self.assertIn('- **[4 · 00:34] Karşı taraf:** Sistem sesi', body)  # the transcript stays
        # The digest goes through the existing review: two items, accepted one lands in the day's log.
        self.assertTrue(report['digest'].startswith('+/2026-10-13 — toplantı 1430 — Carry Meet'))
        found = digest.items(self.ws, self.sid, report['digest'])
        self.assertEqual([(it['kind'], it['unit'], it['exchange'], it['speaker']) for it in found['items']],
                         [('karar', 'segment', 2, 'Karşı taraf'), ('açık iş', 'segment', 3, 'Ben')])
        files = {f['path']: f for f in vaultview.browse(self.ws, self.sid)['files']}
        self.assertTrue(files[report['digest']]['review'])
        self.assertFalse(files[report['digest']]['approvable'])  # never approved wholesale
        decided = digest.decide(self.ws, self.sid, report['digest'], found['items'][0]['id'], 'accept')
        self.assertEqual(decided['logged'], 'log/2026-10-13.md')
        log = (self.root / 'log/2026-10-13.md').read_text(encoding='utf-8')
        self.assertIn('## Toplantılardan', log)
        self.assertIn('- **karar:** Ses diske yazılmaz. ([[2026-10-13 1430 — Carry Meet]], segment 2)', log)
        self.assertEqual(meet.listing(self.ws)['meetings'][0]['status'], 'done')

    def test_many_points_are_merged_into_one_short_summary(self):
        rel = self.saved()['path']
        point = lambda i: dict(type='summary', statement=f'Nokta {i}.', line=2, quote='Kayıtlar cihazda yazıya dökülecek')
        ask, calls = agent([point(i) for i in range(10)],
                           [dict(statement='Birleşik nokta.', line=2, quote='Kayıtlar cihazda yazıya dökülecek'),
                            dict(statement='Uydurma.', line=1, quote='hiç söylenmemiş bir cümle burada')])
        with patch.object(harvest, 'ask_agent', side_effect=ask):
            report = meet.summarize(self.ws, rel, which='claude:sonnet')
        self.assertEqual((report['points'], report['items'], report['digest']), (1, 0, None))
        self.assertEqual(len(calls), 2)
        body = (self.root / rel).read_text(encoding='utf-8')
        self.assertIn('- Birleşik nokta. *(00:09)*', body)
        self.assertIn('Bu toplantıdan kaydedilecek bir karar', body)

    def test_the_on_device_model_keeps_each_part_s_first_points_instead_of_merging(self):
        lines = [dict(n=i + 1, at='00:00', speaker='Ben', text=f'bugün konuştuğumuz konu numara {i + 1} burada') for i in range(12)]
        point = lambda n: dict(type='summary', statement=f'Nokta {n}.', line=n, quote=f'konuştuğumuz konu numara {n}')
        ask, calls = agent([point(n) for n in (6, 5, 4, 3, 2, 1)], [point(n) for n in range(7, 13)])
        with patch.object(meet, 'APPLE_PART_CHARS', 400):  # two parts of six lines
            points, _, _, _ = meet.extract(lines, 'Turkish', meet.APPLE, ask=ask)
        self.assertEqual(len(calls), 2)  # no merge call
        self.assertEqual([p['line'] for p in points], [1, 2, 3, 4, 7, 8, 9, 10])

    def test_a_note_keeps_its_language_and_its_transcript_whatever_the_summary_s(self):
        rel = meet.save(self.ws, SEGMENTS, title='Planning', started='2026-10-13T14:30:00+03:00', duration=95, language='English')['path']
        front, _, _, body = meet.read(self.root, rel)
        self.assertEqual(meet.note_language(front, body), 'English')
        ask, calls = agent([], [])
        with patch.object(harvest, 'ask_agent', side_effect=ask):
            meet.summarize(self.ws, rel, which='claude:sonnet')  # the vault drafts in Turkish, the note is English
            self.assertIn('Notes are in English', calls[0]['system'])
            meet.summarize(self.ws, rel, which='claude:sonnet', language='Turkish')
        text = (self.root / rel).read_text(encoding='utf-8')
        self.assertIn('## Özet', text)
        self.assertIn('## Transcript\n\n- **[1 · 00:02] Me:** Merhaba', text)  # never lost to another heading
        self.assertEqual(self.reindex.call_count, 2)  # the changed note is indexed after each summary

    def test_an_old_note_is_known_by_its_headings(self):
        self.assertEqual(meet.note_language({}, '# M\n\n## Transkript\n\n- x'), 'Turkish')
        self.assertEqual(meet.note_language({}, '# M\n\n## Transcript\n\n- x'), 'English')

    def test_reindex_waits_for_a_build_that_is_running(self):
        with patch('carry.maintenance.start', side_effect=[dict(state='running', started=False), dict(state='running', started=True)]) as start, \
                patch.object(meet.time, 'sleep') as sleep:
            self.real_reindex(self.ws)
        self.assertEqual((start.call_count, sleep.call_count), (2, 1))

    def test_a_failed_summary_keeps_the_transcript_and_says_so(self):
        rel = self.saved()['path']
        with self.assertRaisesRegex(CarryError, 'no_agent_in_tests'):
            meet.summarize(self.ws, rel, which='claude:sonnet')
        front, body = parse_frontmatter((self.root / rel).read_text(encoding='utf-8'))
        self.assertEqual(front['meet_status'], 'failed')
        self.assertIn('Özet çıkarılamadı: no_agent_in_tests', body)
        self.assertIn('- **[3 · 00:21] Ben:** Ayşe cuma gününe kadar', body)
        self.assertEqual(meet.listing(self.ws)['meetings'][0]['status'], 'failed')
        self.assertFalse(list((self.root / '+').glob('*toplantı*')))

    def test_a_signed_out_assistant_hands_the_summary_to_apple_s_on_device_model(self):
        rel = self.saved()['path']
        calls = []

        def ask(system, body, which, field):
            calls.append(which)
            if which.startswith('claude'):
                raise CarryError('assistant_failed: Failed to authenticate: OAuth session expired')
            return [dict(type='decision', statement='Ses diske yazılmaz.', line=2, quote='ses diske yazılmayacak, buna karar verdik')]
        with patch.object(harvest, 'assistants', return_value=['claude:sonnet']), \
                patch.object(harvest, 'apple_model', return_value=Path('/Apps/carry-model')), \
                patch.object(harvest, 'ask_agent', side_effect=ask):
            report = meet.summarize(self.ws, rel)
        self.assertEqual(calls, ['claude:sonnet', meet.APPLE])
        self.assertEqual(report['items'], 1)
        self.compare.assert_not_called()  # the on-device window cannot hold the notes to compare with
        self.assertIn(meet.APPLE, (self.root / report['digest']).read_text(encoding='utf-8'))

    def test_when_no_one_can_summarise_every_reason_is_kept(self):
        rel = self.saved()['path']
        reasons = {'claude:sonnet': 'assistant_failed: Failed to authenticate: OAuth session expired',
                   meet.APPLE: 'assistant_failed: apple_model_unavailable: appleIntelligenceNotEnabled'}

        def ask(system, body, which, field):
            raise CarryError(reasons[which])
        with patch.object(harvest, 'assistants', return_value=['claude:sonnet']), \
                patch.object(harvest, 'apple_model', return_value=Path('/Apps/carry-model')), \
                patch.object(harvest, 'ask_agent', side_effect=ask):
            with self.assertRaises(CarryError) as caught:
                meet.summarize(self.ws, rel)
        self.assertEqual(str(caught.exception), 'assistant_failed: claude: Failed to authenticate: OAuth session expired'
                                                ' | apple: apple_model_unavailable: appleIntelligenceNotEnabled')
        with patch.object(harvest, 'assistants', return_value=[]), patch.object(harvest, 'apple_model', return_value=None):
            with self.assertRaisesRegex(CarryError, 'no_extraction_client'):
                meet.summarize(self.ws, rel)

    def test_a_part_over_the_on_device_window_is_asked_again_in_halves(self):
        lines = meet.read(self.root, self.saved()['path'])[2]
        bodies = []

        def ask(system, body, which, field):
            bodies.append(body)
            if body.count('\n') > 1:
                raise CarryError('assistant_failed: context_window_exceeded')
            return []
        meet.extract(lines, 'Turkish', meet.APPLE, ask=ask)
        self.assertEqual([b.count('\n') + 1 for b in bodies], [4, 2, 2])
        with self.assertRaisesRegex(CarryError, 'Failed to authenticate'):
            meet.extract(lines, 'Turkish', 'claude:sonnet', ask=lambda *a: (_ for _ in ()).throw(CarryError('Failed to authenticate')))

    def test_the_bridge_saves_then_summarises_in_the_background(self):
        bridge = desktop.Bridge()
        with patch.object(meet, 'spawn') as spawn, patch.object(desktop.maintenance, 'start'):
            saved = bridge.dispatch(dict(action='meeting_save', workspace=str(self.ws.state_dir), segments=SEGMENTS, language='English',
                                         title='Bridge', started='2026-10-13T09:05:00+03:00', duration=40))
        self.assertEqual(saved['path'], 'sources/carry/meet/2026-10-13 0905 — Bridge.md')
        self.assertIn('meet_language: English', (self.root / saved['path']).read_text(encoding='utf-8'))  # the app's Language
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[1:], (saved['path'], self.root))
        listed = bridge.dispatch(dict(action='meeting_list', workspace=str(self.ws.state_dir)))
        self.assertEqual([(m['title'], m['status'], m['minutes']) for m in listed['meetings']], [('Bridge', 'pending', 1)])
        self.assertEqual(listed['source_id'], self.sid)  # the app opens the note in the folder it was saved to
        with patch.object(meet, 'spawn') as spawn:
            again = bridge.dispatch(dict(action='meeting_summarize', workspace=str(self.ws.state_dir), path=saved['path']))
        self.assertTrue(again['started'])
        with self.assertRaisesRegex(CarryError, 'not_a_meeting'):
            bridge.dispatch(dict(action='meeting_summarize', workspace=str(self.ws.state_dir), path='notes/README.md'
                                 if (self.root / 'notes/README.md').exists() else 'LLM-GUIDE.md'))

    def test_spawn_gives_the_summary_the_assistants_path(self):
        rel = self.saved()['path']
        with patch.object(meet, 'detached') as run, patch.dict('os.environ', {'PATH': '/usr/bin:/bin'}):
            run.return_value.pid = 4321
            meet.spawn(self.ws, rel, self.root)
        args, options = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(args[-6:], ['meet', 'summarize', '--path', rel, '--vault', str(self.root)])
        self.assertIn(str(Path.home() / '.local' / 'bin'), options['env']['PATH'].split(':'))
        self.assertEqual(json.loads((self.ws.state_dir / 'meet.json').read_text())[rel]['pid'], 4321)

    def test_cli_lists_meetings(self):
        from carry import cli
        self.saved()
        out = io.StringIO()
        with patch('sys.stdout', out):
            code = cli.main(['--workspace', str(self.ws.state_dir), 'meet', 'list'])
        self.assertEqual(code, 0)
        self.assertIn('pending  Carry Meet', out.getvalue())


if __name__ == '__main__':
    unittest.main()
