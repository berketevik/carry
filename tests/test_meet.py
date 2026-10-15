"""Carry Meet: the transcript note, the summary with verified quotes, the digest and the bridge."""
import io
import json
from pathlib import Path
import tempfile
import time
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

    def test_a_signed_out_assistant_hands_the_summary_to_the_next_connected_one(self):
        rel = self.saved()['path']
        calls = []

        def ask(system, body, which, field):
            calls.append(which)
            if which.startswith('claude'):
                raise CarryError('assistant_failed: Failed to authenticate: OAuth session expired')
            return [dict(type='decision', statement='Ses diske yazılmaz.', line=2, quote='ses diske yazılmayacak, buna karar verdik')]
        with patch.object(harvest, 'assistants', return_value=['claude:sonnet', 'codex:gpt-6-astra']), \
                patch.object(harvest, 'apple_model', return_value=Path('/Apps/carry-model')), \
                patch.object(harvest, 'ask_agent', side_effect=ask):
            report = meet.summarize(self.ws, rel)
        self.assertEqual(calls, ['claude:sonnet', 'codex:gpt-6-astra'])  # Apple's model only when named by hand
        self.assertEqual(report['items'], 1)
        front, body = parse_frontmatter((self.root / rel).read_text(encoding='utf-8'))
        self.assertEqual(front['meet_extractor'], 'codex:gpt-6-astra')
        self.assertIn('*Özeti Codex (gpt-6-astra) çıkardı.*', body)

    def test_apple_s_on_device_model_summarises_only_when_named(self):
        rel = self.saved()['path']
        ask, calls = agent([dict(type='decision', statement='Ses diske yazılmaz.', line=2, quote='ses diske yazılmayacak, buna karar verdik')])
        with patch.object(harvest, 'ask_agent', side_effect=ask):
            report = meet.summarize(self.ws, rel, which=meet.APPLE)
        self.assertEqual([c['which'] for c in calls], [meet.APPLE])
        self.compare.assert_not_called()  # the on-device window cannot hold the notes to compare with
        self.assertIn(meet.APPLE, (self.root / report['digest']).read_text(encoding='utf-8'))

    def test_when_no_one_can_summarise_every_reason_is_kept(self):
        rel = self.saved()['path']
        reasons = {'claude:sonnet': 'assistant_failed: Failed to authenticate: OAuth session expired',
                   'codex:gpt-6-astra': 'assistant_failed: usage limit reached'}

        def ask(system, body, which, field):
            raise CarryError(reasons[which])
        with patch.object(harvest, 'assistants', return_value=['claude:sonnet', 'codex:gpt-6-astra']), \
                patch.object(harvest, 'ask_agent', side_effect=ask):
            with self.assertRaises(CarryError) as caught:
                meet.summarize(self.ws, rel)
        self.assertEqual(str(caught.exception), 'assistant_failed: claude: Failed to authenticate: OAuth session expired'
                                                ' | codex: usage limit reached')

    def test_without_a_connected_assistant_the_transcript_stays_raw(self):
        rel = self.saved()['path']
        unplugged = dict(claude=dict(connected=False), codex=dict(connected=False))
        with patch.object(harvest, 'assistants', return_value=['claude:sonnet', 'codex:gpt-6-astra']), \
                patch.object(vaultview, 'assistants', return_value=unplugged), \
                patch.object(harvest, 'ask_agent', side_effect=AssertionError('nothing is asked')):
            report = meet.summarize(self.ws, rel)
            self.assertTrue(report['raw'])
            front, body = parse_frontmatter((self.root / rel).read_text(encoding='utf-8'))
            self.assertEqual(front['meet_status'], 'raw')
            self.assertIn('Ayarlar › Asistanlar', body)
            self.assertIn('- **[3 · 00:21] Ben:** Ayşe cuma gününe kadar', body)
            self.assertEqual(meet.listing(self.ws)['meetings'][0]['status'], 'raw')
            self.reindex.assert_called()
            # Asked again from the Meetings page, it says why instead of starting nothing.
            with self.assertRaisesRegex(CarryError, 'no_connected_assistant'):
                desktop.Bridge().dispatch(dict(action='meeting_summarize', workspace=str(self.ws.state_dir), path=rel))

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
        env = {'PATH': '/usr/bin:/bin', 'CLAUDECODE': '1', 'CLAUDE_CODE_ENTRYPOINT': 'cli', 'LANG': 'tr_TR.UTF-8'}
        with patch.object(meet, 'detached') as run, patch.dict('os.environ', env, clear=True):
            run.return_value.pid = 4321
            meet.spawn(self.ws, rel, self.root, audio='7f3e-11')
        args, options = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(args[-8:], ['meet', 'summarize', '--path', rel, '--vault', str(self.root), '--audio', '7f3e-11'])
        if Path(meet.TASKPOLICY).exists():
            self.assertEqual(args[:3], [meet.TASKPOLICY, '-c', 'utility'])
        self.assertIn(str(Path.home() / '.local' / 'bin'), options['env']['PATH'].split(':'))
        # Started from a Claude Code session, the app's job must not look like one.
        self.assertEqual(sorted(options['env']), ['LANG', 'PATH'])
        job = json.loads((self.ws.state_dir / 'meet.json').read_text())[rel]
        self.assertEqual((job['pid'], job['audio']), (4321, '7f3e-11'))

    # -- Whisper after the meeting

    def audio(self, name='5d1c2a7e-0b9f-4e3a-9d10-2f6b8c1e4a01', sides=('me', 'others'), age=0):
        """A meeting's audio folder as the app writes it: one 16 kHz mono 16-bit WAV a side."""
        import os, wave
        folder = self.ws.state_dir / 'meet-audio' / name
        folder.mkdir(parents=True)
        for side in sides:
            with wave.open(str(folder / f'{side}.wav'), 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0' * 1600)
        if age:
            for f in [folder, *folder.iterdir()]:
                os.utime(f, (time.time() - age, time.time() - age))
        return folder

    def whisper(self, said):
        """whisper_model with a fake engine: `said[side]` is what it hears on that side."""
        from carry import whisper_model
        heard = iter(said[side] for side in meet.SPEAKERS if side in said)
        return [patch.object(meet, 'whisper_ready', return_value=True),
                patch.object(whisper_model, 'load', return_value=object()),
                patch.object(whisper_model, 'transcribe', side_effect=lambda model, samples, busy=None: next(heard))]

    def test_whisper_s_transcript_replaces_apple_s_and_the_audio_is_deleted(self):
        rel = meet.save(self.ws, SEGMENTS, title='Whisper', started='2026-10-15T10:00:00+03:00', duration=95, mic_only=True)['path']
        folder = self.audio()
        said = dict(me=[(1.5, 6.0, 'Merhaba, bugün Carry toplantı kaydını konuşuyoruz.'),
                        (11.0, 16.0, 'Kayıtlar cihazda yazıya dökülecek ve ses silinecek.')],  # the speaker heard through the mic
                    others=[(9.0, 15.0, 'Kayıtlar cihazda yazıya dökülecek ve ses silinecek, buna karar verdik.')])
        patches = self.whisper(said)
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        ask, _ = agent([dict(type='decision', statement='Ses silinir.', line=2, quote='ses silinecek, buna karar verdik')], [])
        with patch.object(harvest, 'ask_agent', side_effect=ask):
            report = meet.summarize(self.ws, rel, which='claude:sonnet', audio=folder.name)
        self.assertFalse(folder.exists())
        front, body = parse_frontmatter((self.root / rel).read_text(encoding='utf-8'))
        self.assertEqual((front['meet_status'], front['meet_transcript'], front['meet_mic_only']), ('done', 'whisper', True))
        self.assertIn('- **[1 · 00:01] Ben:** Merhaba, bugün Carry', body)
        self.assertIn('- **[2 · 00:09] Karşı taraf:** Kayıtlar cihazda yazıya dökülecek ve ses silinecek, buna karar verdik.', body)
        self.assertNotIn('[3 ·', body)  # the echo left the transcript
        self.assertNotIn('Ayşe', body)  # Apple's transcript is gone
        self.assertIn('Sistem sesi alınamadı', body)
        self.assertEqual(report['items'], 1)

    def test_when_whisper_fails_apple_s_transcript_stays_and_the_audio_is_deleted(self):
        from carry import whisper_model
        from carry.errors import ProviderUnavailable
        rel = self.saved()['path']
        before = (self.root / rel).read_text(encoding='utf-8')
        folder = self.audio()
        with patch.object(whisper_model, 'load', side_effect=ProviderUnavailable('whisper_load_failed:RuntimeError')), \
                patch('sys.stderr', io.StringIO()) as log:
            self.assertFalse(meet.transcribe(self.ws, rel, self.root, folder.name))
        self.assertFalse(folder.exists())
        self.assertEqual((self.root / rel).read_text(encoding='utf-8'), before)
        self.assertIn('whisper_load_failed', log.getvalue())

    def test_audio_is_taken_only_from_the_app_s_folder_and_only_when_whisper_can_use_it(self):
        folder = self.audio()
        outside = Path(self.tmp.name) / 'elsewhere' / folder.name
        outside.mkdir(parents=True)
        self.assertIsNone(meet.audio_folder(self.ws, str(outside)))
        self.assertIsNone(meet.audio_folder(self.ws, '../../Vault'))
        with patch.object(meet, 'whisper_ready', return_value=True):
            self.assertEqual(meet.take_audio(self.ws, str(folder)), folder.name)
        self.assertTrue(folder.exists() and outside.exists())
        with patch.object(meet, 'whisper_ready', return_value=False):
            self.assertIsNone(meet.take_audio(self.ws, str(folder)))
        self.assertFalse(folder.exists())  # no model: Apple's transcript stays, the audio goes at once
        self.assertTrue(outside.exists())

    def test_a_path_given_by_hand_is_never_transcribed_or_deleted(self):
        rel = self.saved()['path']
        precious = Path(self.tmp.name) / 'precious'
        precious.mkdir()
        (precious / 'me.wav').write_bytes(b'not a meeting')
        with patch.object(meet, 'transcribe') as transcribe, patch.object(harvest, 'ask_agent', return_value=[]):
            meet.summarize(self.ws, rel, which='claude:sonnet', audio=str(precious))
        transcribe.assert_not_called()
        self.assertTrue((precious / 'me.wav').exists())

    def test_audio_left_behind_is_deleted_after_a_day(self):
        old = self.audio('0ld', age=25 * 3600)
        recent = self.audio('rec3nt', age=3600)
        meet.take_audio(self.ws, None)
        self.assertEqual((old.exists(), recent.exists()), (False, True))

    def test_whisper_pauses_while_the_app_says_a_call_has_the_microphone(self):
        import os
        flag = self.ws.state_dir / meet.BUSY_NAME
        self.assertFalse(meet.busy(self.ws))
        flag.touch()
        self.assertTrue(meet.busy(self.ws))
        os.utime(flag, (time.time() - 30, time.time() - 30))  # the app quit or the call ended
        self.assertFalse(meet.busy(self.ws))

    def test_the_bridge_hands_the_audio_to_the_background_job(self):
        bridge = desktop.Bridge()
        folder = self.audio()
        with patch.object(meet, 'spawn') as spawn, patch.object(desktop.maintenance, 'start'), \
                patch.object(meet, 'whisper_ready', return_value=True):
            saved = bridge.dispatch(dict(action='meeting_save', workspace=str(self.ws.state_dir), segments=SEGMENTS,
                                         title='Ses', started='2026-10-15T09:05:00+03:00', duration=40,
                                         audio=str(folder), mic_only=True))
        self.assertEqual(spawn.call_args.kwargs['audio'], folder.name)
        self.assertIn('meet_mic_only: true', (self.root / saved['path']).read_text(encoding='utf-8'))
        empty = self.audio('3mpty')
        with patch.object(meet, 'whisper_ready', return_value=False), self.assertRaisesRegex(CarryError, 'meeting_transcript_empty'):
            bridge.dispatch(dict(action='meeting_save', workspace=str(self.ws.state_dir), segments=[], audio=str(empty)))
        self.assertFalse(empty.exists())

    def test_a_meeting_the_live_recognizer_heard_nothing_of_is_left_to_whisper(self):
        bridge = desktop.Bridge()
        folder = self.audio('h3ard-n0thing')
        with patch.object(meet, 'spawn') as spawn, patch.object(desktop.maintenance, 'start'), \
                patch.object(meet, 'whisper_ready', return_value=True):
            saved = bridge.dispatch(dict(action='meeting_save', workspace=str(self.ws.state_dir), segments=[], title='Yüz yüze',
                                         started='2026-10-15T09:05:00+03:00', duration=40, audio=str(folder), mic_only=True))
        self.assertEqual(spawn.call_args.kwargs['audio'], folder.name)
        self.assertIn('transkripti bu Mac\'te Whisper yazıyor', (self.root / saved['path']).read_text(encoding='utf-8'))
        for p in self.whisper(dict(me=[(1.0, 5.0, 'Bütçeyi cuma günü kapatıyoruz, Ayşe onaylayacak.')], others=[])):
            p.start(); self.addCleanup(p.stop)
        self.assertTrue(meet.transcribe(self.ws, saved['path'], self.root, folder.name))
        front, body = parse_frontmatter((self.root / saved['path']).read_text(encoding='utf-8'))
        self.assertEqual(front['meet_transcript'], 'whisper')
        self.assertIn('- **[1 · 00:01] Ben:** Bütçeyi cuma günü kapatıyoruz', body)
        self.assertNotIn('Whisper yazıyor', body)

    def test_a_meeting_whisper_heard_nothing_of_either_says_it_failed(self):
        rel = meet.save(self.ws, [], title='Sessiz', started='2026-10-15T09:05:00+03:00', duration=40, whisper=True)['path']
        with self.assertRaisesRegex(CarryError, 'meeting_transcript_empty'):
            meet.summarize(self.ws, rel, which='claude:sonnet')
        front, body = parse_frontmatter((self.root / rel).read_text(encoding='utf-8'))
        self.assertEqual(front['meet_status'], 'failed')
        self.assertEqual(meet.listing(self.ws)['meetings'][0]['status'], 'failed')

    def test_a_short_echo_of_the_other_side_is_left_out(self):
        lines = meet.clean_lines([
            dict(start=26.0, speaker='others', text='Aslında sesim geliyor. Aynen geliyor. Ama senin sesin gelmiyor.'),
            dict(start=27.0, speaker='me', text='Aslında sesim geliyor.'),
            dict(start=28.0, speaker='me', text='Aynen geliyor.'),
            dict(start=29.0, speaker='me', text='Evet.'),             # one word: always kept
            dict(start=31.0, speaker='me', text='Bence geliyor.'),    # a word the other side did not say
            dict(start=45.0, speaker='me', text='Aynen geliyor.'),    # long after: kept
        ])
        self.assertEqual([(x['start'], x['text']) for x in lines if x['speaker'] == 'me'],
                         [(29.0, 'Evet.'), (31.0, 'Bence geliyor.'), (45.0, 'Aynen geliyor.')])

    def test_the_whisper_model_downloads_once_in_its_own_job(self):
        from carry import whisper_model
        bridge = desktop.Bridge()
        with patch.object(whisper_model, 'runtime_available', return_value=True), \
                patch.object(whisper_model, 'installed', return_value=False), patch.object(meet, 'detached') as run:
            run.return_value.pid = 999999  # no such process: the job looks interrupted
            started = bridge.dispatch(dict(action='whisper_download', workspace=str(self.ws.state_dir)))
            self.assertEqual(run.call_args.args[0][-2:], ['meet', 'download-whisper'])
            status = bridge.dispatch(dict(action='whisper_status', workspace=str(self.ws.state_dir)))
        self.assertTrue(started['started'])
        self.assertEqual((status['installed'], status['bytes'], status['download']['state']), (False, 874188075 + 885098, 'interrupted'))

        def fetch(progress=None):
            progress(100, 200)
            self.assertEqual(meet.whisper_download_job(self.ws)['completed'], 100)  # this process is alive
            progress(200, 200)
        with patch.object(whisper_model, 'download', side_effect=fetch), patch.object(whisper_model, 'DOWNLOAD_BYTES', 200):
            meet.download_whisper(self.ws)
        self.assertEqual(meet.whisper_download_job(self.ws)['state'], 'done')
        with patch.object(whisper_model, 'runtime_available', return_value=True), \
                patch.object(whisper_model, 'installed', return_value=True), patch.object(meet, 'detached') as run:
            self.assertFalse(meet.start_whisper_download(self.ws)['started'])
        run.assert_not_called()

    def test_a_failed_summary_keeps_who_wrote_the_transcript(self):
        rel = meet.save(self.ws, SEGMENTS, title='Kalır', started='2026-10-15T11:00:00+03:00', duration=95, mic_only=True)['path']
        meet.write_transcript(self.root, rel, meet.clean_lines(SEGMENTS[:2]), 'whisper')
        with self.assertRaises(CarryError):
            meet.summarize(self.ws, rel, which='claude:sonnet')
        front, body = parse_frontmatter((self.root / rel).read_text(encoding='utf-8'))
        self.assertEqual((front['meet_status'], front['meet_transcript'], front['meet_mic_only']), ('failed', 'whisper', True))
        self.assertIn('Sistem sesi alınamadı', body)
        self.assertEqual(len(meet.read(self.root, rel)[2]), 2)

    def test_a_renamed_transcript_heading_keeps_the_transcript(self):
        rel = self.saved()['path']
        path = self.root / rel
        path.write_text(path.read_text(encoding='utf-8').replace('## Transkript\n', '## Transkript (düzeltildi) \n'), encoding='utf-8')
        with self.assertRaises(CarryError):
            meet.summarize(self.ws, rel, which='claude:sonnet')
        body = path.read_text(encoding='utf-8')
        self.assertIn('## Transkript (düzeltildi)', body)
        self.assertEqual(len(meet.read(self.root, rel)[2]), 4)

    def test_lines_without_their_heading_are_never_rewritten_away(self):
        rel = self.saved()['path']
        path = self.root / rel
        path.write_text(path.read_text(encoding='utf-8').replace('## Transkript\n', '## Konuşma\n'), encoding='utf-8')
        before = path.read_text(encoding='utf-8')
        with self.assertRaises(CarryError):
            meet.summarize(self.ws, rel, which='claude:sonnet')
        self.assertEqual(path.read_text(encoding='utf-8'), before)
        with self.assertRaisesRegex(CarryError, 'meeting_transcript_heading_missing'):
            meet.mark_raw(self.root, rel, 'Turkish')

    def test_a_rewrite_keeps_the_owner_s_frontmatter(self):
        rel = self.saved()['path']
        path = self.root / rel
        text = path.read_text(encoding='utf-8')
        path.write_text(text.replace('source_type: meet\n', 'source_type: meet\ntags:\n  - carry\nrelated: "[[Carry]]"\n'), encoding='utf-8')
        meet.mark_raw(self.root, rel, 'Turkish')
        front, body = parse_frontmatter(path.read_text(encoding='utf-8'))
        self.assertEqual((front['meet_status'], front['tags'], front['related']), ('raw', ['carry'], '[[Carry]]'))
        self.assertEqual((front['type'], front['source_type'], front['duration_minutes']), ('meeting', 'meet', 2))
        self.assertEqual(path.read_text(encoding='utf-8').count('source_type:'), 1)
        self.assertEqual(len(meet.read(self.root, rel)[2]), 4)

    def test_a_note_with_windows_line_endings_keeps_its_frontmatter(self):
        rel = self.saved()['path']
        path = self.root / rel
        path.write_bytes(path.read_text(encoding='utf-8').replace('\n', '\r\n').encode('utf-8'))
        meet.mark_raw(self.root, rel, 'Turkish')
        front, _ = parse_frontmatter(path.read_text(encoding='utf-8'))
        self.assertEqual((front['meet_status'], front['duration_minutes'], str(front['created'])), ('raw', 2, '2026-10-13'))
        self.assertIn('Carry Meet', front['description'])
        self.assertTrue(front['generated']['by'].startswith('carry/'))

    def test_a_locked_note_is_never_rewritten(self):
        rel = self.saved()['path']
        path = self.root / rel
        path.write_text(path.read_text(encoding='utf-8').replace('source_type: meet\n', 'source_type: meet\nlock: true\n'), encoding='utf-8')
        before = path.read_text(encoding='utf-8')
        with self.assertRaisesRegex(CarryError, 'meeting_note_locked'):
            meet.summarize(self.ws, rel, which='claude:sonnet')
        with self.assertRaisesRegex(CarryError, 'meeting_note_locked'):
            meet.write_transcript(self.root, rel, meet.clean_lines(SEGMENTS[:1]), 'whisper')
        self.assertEqual(path.read_text(encoding='utf-8'), before)

    def test_an_edit_made_while_the_summary_runs_is_kept_in_the_transcript(self):
        rel = self.saved()['path']
        path = self.root / rel

        def ask(system, body, which, field):
            path.write_text(path.read_text(encoding='utf-8').replace('Ayşe cuma', 'Ayşe Yılmaz cuma'), encoding='utf-8')
            return []
        with patch.object(harvest, 'ask_agent', side_effect=ask):
            meet.summarize(self.ws, rel, which='claude:sonnet')
        self.assertIn('Ayşe Yılmaz cuma', path.read_text(encoding='utf-8'))

    def test_a_side_whisper_mostly_missed_keeps_apple_s_lines(self):
        rel = self.saved()['path']
        folder = self.audio()
        said = dict(me=[(1.5, 6.0, 'Merhaba, bugün Carry toplantı kaydını konuşuyoruz.'),
                        (21.0, 26.0, 'Ayşe cuma gününe kadar dosya yerini onaylayacak.')],
                    others=[])  # its voice detection dropped the other side
        for p in self.whisper(said):
            p.start(); self.addCleanup(p.stop)
        self.assertTrue(meet.transcribe(self.ws, rel, self.root, folder.name))
        self.assertFalse(folder.exists())
        front, body = parse_frontmatter((self.root / rel).read_text(encoding='utf-8'))
        self.assertEqual(front['meet_transcript'], 'whisper+apple')
        self.assertIn('Karşı taraf:** Kayıtlar cihazda yazıya dökülecek ve ses diske yazılmayacak', body)
        self.assertIn('Karşı taraf:** Sistem sesi macOS on dört', body)
        self.assertEqual(len(meet.read(self.root, rel)[2]), 4)

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
