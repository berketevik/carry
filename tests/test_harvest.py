"""carry harvest: thread discovery, provenance, attribution, vault comparison, idempotence, pending."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from unittest.mock import patch

import _support  # noqa: F401
from carry import harvest, jev, vault
from carry.config import Workspace
from carry.errors import CarryError


def claude_line(kind, content, timestamp=None):
    line = {'type': kind, 'message': {'role': kind, 'content': content}}
    if timestamp:
        line['timestamp'] = timestamp
    return json.dumps(line)


class HarvestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / 'home'; self.home.mkdir()
        home = patch.object(Path, 'home', return_value=self.home); home.start(); self.addCleanup(home.stop)
        self.root = self.base / 'Vault'
        plan = vault.plan(self.root, language='English', workspace=self.base / 'ws', attach=False)
        vault.apply(plan, git=False)
        self.ws = Workspace.load(self.base / 'ws')
        self.project = harvest.claude_project_dir(self.root); self.project.mkdir(parents=True)
        short = patch.object(harvest, 'MIN_EXCHANGES', 1); short.start(); self.addCleanup(short.stop)
        # No test ever reaches a real Claude or Codex: comparison by the assistant is faked where tested.
        agent = patch.object(harvest, 'ask_agent', side_effect=CarryError('no_agent_in_tests')); agent.start(); self.addCleanup(agent.stop)

    def jev_on(self):
        """Jev chosen as the checker in search: the only case harvest uses it."""
        from dataclasses import replace
        self.ws = replace(self.ws, retrieval=replace(self.ws.retrieval, reranker='jev')).save()

    def thread(self, name, exchanges, writes=None, age=3600):
        lines = []
        for user, assistant in exchanges:
            lines.append(claude_line('user', user))
            blocks = [{'type': 'text', 'text': assistant}]
            if writes:
                blocks.append({'type': 'tool_use', 'name': 'Write', 'input': {'file_path': str(self.root / writes)}})
            lines.append(claude_line('assistant', blocks))
        path = self.project / (name + '.jsonl')
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        os.utime(path, (time.time() - age, time.time() - age))
        return path

    def items(self, *items):
        return patch.object(harvest, 'extract_window', return_value=list(items))

    def test_thread_with_nothing_to_file_leaves_no_draft(self):
        self.thread('eeee5555', [('Just say ok.', 'ok')])
        with self.items(), patch.object(jev, 'api_key', return_value=None):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        self.assertEqual((report['harvested'], report['digests']), (1, []))
        self.assertEqual(list((self.root / '+').glob('*harvest*')), [])
        with self.items() as extract, patch.object(jev, 'api_key', return_value=None):
            harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        extract.assert_not_called()

    def test_digest_and_raw_are_drafts_with_verbatim_quotes_only(self):
        self.thread('aaaa1111', [('We will ship the pilot on 15 October, decided.', 'Noted: the pilot ships on 15 October.')])
        good = dict(type='decision', statement='The pilot ships on 15 October.', exchange=1, quote='ship the pilot on 15 October')
        invented = dict(type='fact', statement='The budget is 1M.', exchange=1, quote='the budget is one million')
        with self.items(good, invented), patch.object(jev, 'api_key', return_value=None):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        self.assertEqual(report['harvested'], 1)
        digest = (self.root / report['digests'][0]).read_text(encoding='utf-8')
        self.assertTrue(report['digests'][0].startswith('+/'))
        self.assertIn('draft: true', digest)
        self.assertIn('The pilot ships on 15 October.', digest)
        self.assertNotIn('1M', digest)
        self.assertIn('"dropped_without_quote": 1', digest)
        raws = list((self.root / 'sources/carry/harvest').glob('*.md'))
        self.assertEqual(len(raws), 1)
        self.assertIn('type: chat-raw', raws[0].read_text(encoding='utf-8'))

    def test_assistant_suggestion_is_not_filed_as_the_owners_decision(self):
        self.thread('bbbb2222', [('What should we do about ECC?', 'I recommend installing only selected skills.')])
        item = dict(type='decision', statement='Only selected ECC skills will be installed.', exchange=1,
                    quote='installing only selected skills')
        scores = dict(supported=0.9, owner_stated=0.06, durable=0.8, withdrawn=0.02)
        self.jev_on()
        with self.items(item), patch.object(jev, 'api_key', return_value='k'), \
                patch.object(harvest, 'verify', return_value=scores), patch.object(harvest, 'compare', return_value=('new', None)):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        digest = (self.root / report['digests'][0]).read_text(encoding='utf-8')
        self.assertIn("assistant's suggestion or result", digest)

    def test_known_items_point_to_the_note_and_conflicts_are_separated(self):
        self.thread('cccc3333', [('The agent now runs model-b and the vault has 445 notes.', 'Recorded both.')])
        a = dict(type='fact', statement='The agent runs model-b.', exchange=1, quote='The agent now runs model-b')
        b = dict(type='fact', statement='The vault has 445 notes.', exchange=1, quote='the vault has 445 notes')
        verdicts = iter([('conflict', str(self.root / 'notes/Agent.md')), ('known', str(self.root / 'notes/Vault.md'))])
        self.jev_on()
        with self.items(a, b), patch.object(jev, 'api_key', return_value='k'), \
                patch.object(harvest, 'verify', return_value=dict(supported=0.9, owner_stated=0.9, durable=0.9, withdrawn=0.0)), \
                patch.object(harvest, 'compare', side_effect=lambda *x, **kw: next(verdicts)):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        digest = (self.root / report['digests'][0]).read_text(encoding='utf-8')
        self.assertIn('## Conflict candidates (review)', digest)
        self.assertIn('[[Agent]]', digest)
        self.assertIn('## Already recorded', digest)

    def test_an_open_item_already_done_in_the_vault_is_separated(self):
        self.thread('hhhh8888', [('Run the playground tests later.', 'Not run yet.')])
        item = dict(type='open_item', statement='The playground tests still need to run.', exchange=1, quote='Run the playground tests later')
        self.jev_on()
        with self.items(item), patch.object(jev, 'api_key', return_value='k'), \
                patch.object(harvest, 'verify', return_value=dict(supported=0.9, owner_stated=0.9, durable=0.9, withdrawn=0.0)), \
                patch.object(harvest, 'compare', return_value=('resolved', str(self.root / 'log/Tests.md'))):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        digest = (self.root / report['digests'][0]).read_text(encoding='utf-8')
        self.assertIn('## Apparently done', digest)
        self.assertNotIn('## New', digest)

    def test_compare_asks_whether_an_open_item_was_done(self):
        sent = []
        def fake(body, key, timeout=jev.TIMEOUT):
            sent.append(body)
            return dict(answers={k: dict(type='noul', noul=(0.9 if k.endswith(('_done', '_about')) else 0.05)) for k in body['questions']})
        evidence = dict(evidence=[dict(path='log/Tests.md', text='The playground tests ran via the API.', date='2026-09-29')])
        with patch('carry.recall.recall', return_value=evidence), patch.object(jev, 'call', side_effect=fake):
            verdict, match = harvest.compare(dict(type='open_item', statement='Tests pending.', quote='pending'), self.ws, 'k',
                                             since='2026-09-28')
        self.assertEqual((verdict, match), ('resolved', 'log/Tests.md'))
        self.assertIn('p1_done', sent[0]['questions'])

    def test_compare_never_sends_secret_notes_to_jev(self):
        sent = []
        def fake(body, key, timeout=jev.TIMEOUT):
            sent.append(body)
            return dict(answers={k: dict(type='noul', noul=0.05) for k in body['questions']})
        evidence = dict(evidence=[dict(path='notes/Keys.md', text='The vault key is hidden here.', metadata=dict(sensitivity='secret')),
                                  dict(path='notes/Plan.md', text='The plan is public.', metadata={})])
        with patch('carry.recall.recall', return_value=evidence), patch.object(jev, 'call', side_effect=fake):
            harvest.compare(dict(type='fact', statement='The plan changed.', quote='plan'), self.ws, 'k')
        self.assertEqual([p['source'] for p in sent[0]['state']['passages'].values()], ['notes/Plan.md'])
        only_secret = dict(evidence=[evidence['evidence'][0]])
        with patch('carry.recall.recall', return_value=only_secret), patch.object(jev, 'call', side_effect=fake):
            self.assertEqual(harvest.compare(dict(type='fact', statement='Key moved.', quote='key'), self.ws, 'k'), ('new', None))
        self.assertEqual(len(sent), 1)

    def test_digest_is_not_served_as_a_note_where_the_inbox_is_indexed(self):
        from dataclasses import replace
        from carry.index import build
        from carry.recall import recall
        self.ws = self.ws.with_sources([replace(s, exclude=()) for s in self.ws.sources]).save()
        self.thread('abab1212', [('The pilot ships on 15 October, decided.', 'Noted.')])
        item = dict(type='decision', statement='The pilot ships on 15 October.', exchange=1, quote='pilot ships on 15 October')
        with self.items(item), patch.object(jev, 'api_key', return_value=None):
            digest = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)['digests'][0]
        build(self.ws)
        served = lambda **kw: [e['path'] for e in recall(self.ws, 'pilot ships 15 October', **kw).get('evidence', [])]
        self.assertNotIn(digest, served())
        self.assertIn(digest, served(include_drafts=True))
        path = self.root / digest
        path.write_text(path.read_text(encoding='utf-8').replace('draft: true\n', ''), encoding='utf-8')
        build(self.ws)
        self.assertIn(digest, served())

    def closing(self, passages, answers, since='2026-09-28'):
        """compare() on an open item with fixed passages and fixed Jev answers {(passage, question): p}."""
        def fake(body, key, timeout=jev.TIMEOUT):
            return dict(answers={k: dict(type='noul', noul=answers.get(tuple(k.split('_', 1)), 0.05)) for k in body['questions']})
        with patch('carry.recall.recall', return_value=dict(evidence=passages)), patch.object(jev, 'call', side_effect=fake):
            return harvest.compare(dict(type='open_item', statement='Signing is not done.', quote='not signed'), self.ws, 'k', since=since)

    def test_a_conflict_keeps_the_contradicting_passage_in_the_digest(self):
        def fake(body, key, timeout=jev.TIMEOUT):
            return dict(answers={k: dict(type='noul', noul=(0.9 if k in ('p2_contra', 'p2_real') else 0.05)) for k in body['questions']})
        passages = [dict(path='notes/A.md', text='Unrelated.'), dict(path='notes/Mac mini.md', text='The Mac mini has   8 GB.')]
        item = dict(type='fact', statement='The Mac mini has 16 GB.', quote='16 GB')
        with patch('carry.recall.recall', return_value=dict(evidence=passages)), patch.object(jev, 'call', side_effect=fake):
            self.assertEqual(harvest.compare(item, self.ws, 'k'), ('conflict', 'notes/Mac mini.md'))
        self.assertEqual(item['match_text'], 'The Mac mini has 8 GB.')

    def test_a_contradiction_that_is_not_a_real_conflict_stays_new_with_the_note_related(self):
        sent = []
        def fake(body, key, timeout=jev.TIMEOUT):
            sent.append(body)
            return dict(answers={k: dict(type='noul', noul=(0.9 if k == 'p1_contra' else 0.3 if k == 'p1_real' else 0.05)) for k in body['questions']})
        passages = [dict(path='log/2026-09-14 — Pilot.md', text='Output: internal-pilot.zip', date='2026-09-14', metadata=dict(type='output'))]
        item = dict(type='preference', statement="Say 'pilot', not 'internal pilot'.", quote='pilot')
        with patch('carry.recall.recall', return_value=dict(evidence=passages)), patch.object(jev, 'call', side_effect=fake):
            self.assertEqual(harvest.compare(item, self.ws, 'k', since='2026-09-27'), ('new', 'log/2026-09-14 — Pilot.md'))
        self.assertTrue(item['related'])
        self.assertEqual(item['match_text'], 'Output: internal-pilot.zip')
        seen = sent[0]['state']['passages']['p1']
        self.assertEqual((seen['date'], seen['type']), ('2026-09-14', 'output'))
        self.assertNotIn('kind', seen)
        self.assertIn('p1_real', sent[0]['questions'])

    def agent_case(self, answers, passages=None):
        items = [dict(type='fact', statement='The Mac mini has 16 GB.', quote='16 GB', at='2026-09-28'),
                 dict(type='fact', statement='The repo is public.', quote='public', at='2026-09-28'),
                 dict(type='open_item', statement='Sign the app.', quote='sign', at='2026-09-28')]
        passages = passages or [dict(path='notes/Mac mini.md', text='The Mac mini has 8 GB.', date='2026-07-01', metadata=dict(type='thing'))]
        sent = []
        def fake(system, body, which, field):
            sent.append((system, json.loads(body)))
            if isinstance(answers, Exception):
                raise answers
            return answers
        with patch.object(harvest, 'evidence_for', return_value=passages), patch.object(harvest, 'ask_agent', side_effect=fake):
            return items, harvest.agent_compare(items, self.ws, 'claude:sonnet', 'English'), sent

    def test_the_assistant_compares_when_jev_is_off_and_code_checks_its_answers(self):
        answers = [dict(case=1, verdict='conflict', passage='p1', why='The note says 8 GB for the same Mac.'),
                   dict(case=2, verdict='known', passage='p9'),        # no such passage: ignored
                   dict(case=3, verdict='resolved', passage='p1'),     # the assistant never closes an item
                   dict(case='x', verdict='known', passage='p1')]
        items, (verdicts, ran), sent = self.agent_case(answers)
        self.assertTrue(ran)
        self.assertEqual(verdicts, [('conflict', 'notes/Mac mini.md'), ('new', None), ('new', None)])
        self.assertEqual((items[0]['match_text'], items[0]['why']), ('The Mac mini has 8 GB.', 'The note says 8 GB for the same Mac.'))
        system, cases = sent[0]
        self.assertIn('real conflict', system)
        self.assertEqual(cases[0]['passages']['p1']['type'], 'thing')

    def test_an_assistant_that_fails_leaves_items_new_and_unchecked(self):
        items, (verdicts, ran), _ = self.agent_case(CarryError('extraction_unparseable'))
        self.assertFalse(ran)
        self.assertEqual(verdicts, [('new', None)] * 3)

    def test_a_harvest_without_jev_is_compared_by_the_assistant(self):
        self.thread('mmmm2222', [('The Mac mini has 16 GB now.', 'Noted.')])
        item = dict(type='fact', statement='The Mac mini has 16 GB.', exchange=1, quote='Mac mini has 16 GB')
        with self.items(item), patch.object(jev, 'api_key', return_value=None), \
                patch.object(harvest, 'agent_compare', return_value=([('conflict', str(self.root / 'notes/Mac mini.md'))], True)) as agent:
            digest = (self.root / harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)['digests'][0]).read_text(encoding='utf-8')
        agent.assert_called_once()
        self.assertIn('"judge": "agent"', digest)
        self.assertIn('## Conflict candidates', digest)
        self.assertNotIn('neither Jev nor your assistant', digest)

    def test_an_older_or_undated_passage_cannot_close_an_open_item(self):
        shelved = dict(path='notes/Plan.md', text='The signed pilot was shelved.', date='2026-09-18')
        self.assertEqual(self.closing([shelved], {('p1', 'dropped'): 0.9}), ('new', None))
        undated = dict(path='notes/Plan.md', text='Signing done.')
        self.assertEqual(self.closing([undated], {('p1', 'done'): 0.9}), ('new', None))
        self.assertEqual(self.closing([dict(undated, date='2026-09-30')], {('p1', 'done'): 0.9}, since=None), ('new', None))

    def test_dropped_is_not_done_and_a_newer_still_open_passage_keeps_it_open(self):
        dropped = dict(path='log/Drop.md', text='We no longer sign the app.', date='2026-09-29')
        self.assertEqual(self.closing([dropped], {('p1', 'dropped'): 0.9, ('p1', 'about'): 0.9}), ('dropped', 'log/Drop.md'))
        self.assertEqual(self.closing([dropped], {('p1', 'dropped'): 0.9}), ('new', None))  # not about this task
        self.assertEqual(self.closing([dropped], {('p1', 'dropped'): 0.6, ('p1', 'about'): 0.9}), ('new', None))  # unsure
        done = dict(path='log/Done.md', text='Notarized.', date='2026-09-29')
        still = dict(path='log/Later.md', text='Signing is still open.', date='2026-09-30')
        self.assertEqual(self.closing([done, still], {('p1', 'done'): 0.9, ('p1', 'about'): 0.9, ('p2', 'open'): 0.9}), ('new', None))
        older = dict(still, date='2026-09-28')
        self.assertEqual(self.closing([done, older], {('p1', 'done'): 0.9, ('p1', 'about'): 0.9, ('p2', 'open'): 0.9}), ('resolved', 'log/Done.md'))

    def test_withdrawn_sees_a_later_assistant_result_deep_in_a_long_reply(self):
        filler = 'Other work happened here and it is unrelated. ' * 20
        exchanges = [dict(i=1, user='Which build do we ship?', assistant='We have not chosen between the full and light build.', at='2026-09-28'),
                     dict(i=2, user='Clean the repo.', assistant=filler + 'The old full and light build scripts are gone; packaging now uses carry app install.', at='2026-09-28')]
        item = dict(type='open_item', statement='Choose between the full and light build.', exchange=1,
                    quote='not chosen between the full and light build', speaker='assistant', keywords=['build', 'packaging'])
        sent = []
        def fake(body, key, timeout=jev.TIMEOUT):
            sent.append(body)
            return dict(answers={k: dict(type='noul', noul=0.9) for k in body['questions']})
        with patch.object(jev, 'call', side_effect=fake):
            harvest.verify(item, exchanges, 'k')
        later = sent[0]['state']['later_exchanges']
        self.assertEqual([x['exchange'] for x in later], [2])
        self.assertIn('packaging now uses carry app install', later[0]['assistant'])
        self.assertNotIn('unrelated', later[0]['assistant'])
        suffixed = [exchanges[0], dict(exchanges[1], assistant=filler + 'Old packaging scripts were removed. Now carry app install is used.')]
        self.assertIn('Now carry app install is used.', harvest.later_context(item, suffixed)[0]['assistant'])
        self.assertEqual(harvest.classify(item, dict(supported=0.9, owner_stated=0.9, durable=0.9, withdrawn=0.9)), 'review')

    def test_later_context_keeps_the_newest_exchanges_when_long(self):
        exchanges = [dict(i=i, user=f'owner build note {i} ' + 'x' * 380, assistant='', at=None) for i in range(1, 40)]
        item = dict(statement='build', quote='build', exchange=1)
        later = harvest.later_context(item, exchanges, limit=2000)
        self.assertEqual(later[-1]['exchange'], 39)
        self.assertLess(len(later), 38)

    def test_transcript_dates_reach_the_comparison(self):
        path = self.project / 'cccc3333.jsonl'
        path.write_text('\n'.join([claude_line('user', 'Signing is still open.', '2026-09-28T09:00:00Z'),
                                    claude_line('assistant', [{'type': 'text', 'text': 'Yes, not signed yet.'}], '2026-09-28T09:00:05Z')]) + '\n', encoding='utf-8')
        exchanges, _ = harvest.read_thread('claude', path, self.root)
        self.assertEqual(exchanges[0]['at'], harvest._day('2026-09-28T09:00:00Z'))
        os.utime(path, (time.time() - 3600, time.time() - 3600))
        item = dict(type='open_item', statement='Signing is not done.', exchange=1, quote='not signed yet')
        with self.items(item), patch.object(jev, 'api_key', return_value='k'), \
                patch.object(harvest, 'verify', return_value=dict(supported=0.9, owner_stated=0.9, durable=0.9, withdrawn=0.0)), \
                patch.object(harvest, 'compare', return_value=('new', None)) as compare:
            self.jev_on()
            harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        self.assertEqual(compare.call_args.kwargs['since'], exchanges[0]['at'])

    def test_a_second_harvest_the_same_day_keeps_the_first_digest(self):
        exchanges = [('The pilot ships on 15 October, decided.', 'Noted.')]
        self.thread('acac1313', exchanges)
        first_item = dict(type='decision', statement='The pilot ships on 15 October.', exchange=1, quote='pilot ships on 15 October')
        with self.items(first_item), patch.object(jev, 'api_key', return_value=None):
            first = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)['digests'][0]
        decided = (self.root / first).read_text(encoding='utf-8').replace('15 October.\n', '15 October. <!-- carry: accepted 2026-09-28 -->\n', 1)
        (self.root / first).write_text(decided, encoding='utf-8')
        self.thread('acac1313', exchanges + [('Budget is 2k, decided.', 'Noted.')])
        second_item = dict(type='decision', statement='The budget is 2k.', exchange=2, quote='Budget is 2k')
        with self.items(second_item), patch.object(jev, 'api_key', return_value=None):
            second = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)['digests'][0]
        self.assertNotEqual(first, second)
        self.assertTrue(second.endswith(' (2).md'))
        self.assertEqual((self.root / first).read_text(encoding='utf-8'), decided)
        self.assertIn('The budget is 2k.', (self.root / second).read_text(encoding='utf-8'))

    def test_filed_and_active_threads_are_skipped_and_reruns_are_idempotent(self):
        self.thread('dddd4444', [('Save this.', 'Saved to notes.')], writes='notes/Plan.md')
        self.thread('eeee5555', [('Still talking.', 'Yes.')], age=60)
        self.thread('ffff6666', [('The server moved to the Mac mini.', 'OK.')])
        item = dict(type='fact', statement='The server moved to the Mac mini.', exchange=1, quote='server moved to the Mac mini')
        with self.items(item) as extract, patch.object(jev, 'api_key', return_value=None):
            first = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
            second = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        self.assertEqual((first['harvested'], first['skipped_filed'], first['skipped_active']), (1, 1, 1))
        self.assertEqual(second['harvested'], 0)
        self.assertEqual(extract.call_count, 1)

    def test_extraction_failure_leaves_the_thread_pending(self):
        self.thread('gggg7777', [('We chose OrbStack.', 'OK.')])
        with patch.object(harvest, 'extract_window', side_effect=CarryError('extraction_unparseable')), \
                patch.object(jev, 'api_key', return_value=None):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        self.assertEqual(report['pending'], 1)
        state = json.loads((self.ws.state_dir / harvest.STATE_NAME).read_text(encoding='utf-8'))
        self.assertEqual(state['claude:gggg7777']['status'], 'pending')
        self.assertFalse(list(self.root.glob('+/*harvest*')))

    def test_a_denied_write_does_not_count_as_filed(self):
        lines = [claude_line('user', 'Note that the office moves in December.'),
                 claude_line('assistant', [{'type': 'text', 'text': 'Saving.'},
                                           {'type': 'tool_use', 'id': 't1', 'name': 'Write', 'input': {'file_path': str(self.root / 'notes/Office.md')}}]),
                 claude_line('user', [{'type': 'tool_result', 'tool_use_id': 't1', 'is_error': True, 'content': 'permission denied'}]),
                 claude_line('assistant', [{'type': 'text', 'text': 'I could not save it.'}])]
        path = self.project / 'llll2222.jsonl'; path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        _, filed = harvest.read_thread('claude', path, self.root)
        self.assertFalse(filed)

    def test_short_threads_are_skipped(self):
        self.thread('iiii9999', [('Hi', 'Hello')])
        with patch.object(harvest, 'MIN_EXCHANGES', 2), self.items() as extract, patch.object(jev, 'api_key', return_value=None):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        self.assertEqual(report['harvested'], 0)
        extract.assert_not_called()

    def test_one_thread_from_a_hook_skips_the_idle_wait(self):
        path = self.thread('jjjj0000', [('We moved the server to the Mac mini.', 'OK.')], age=5)
        other = self.thread('kkkk1111', [('Another chat.', 'OK.')])
        item = dict(type='fact', statement='The server moved to the Mac mini.', exchange=1, quote='moved the server to the Mac mini')
        with self.items(item), patch.object(jev, 'api_key', return_value=None):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None, thread=str(path))
        self.assertEqual((report['threads'], report['harvested']), (1, 1))
        self.assertIn('jjjj0000'[:8], report['digests'][0])

    def test_hook_spawns_in_the_background_only_for_this_vault(self):
        payload = dict(transcript_path=str(self.project / 'x.jsonl'), cwd=str(self.root), hook_event_name='SessionEnd')
        with patch('subprocess.Popen') as popen:
            self.assertTrue(harvest.spawn_from_hook(self.ws.state_dir, payload, self.root, language='Turkish'))
            self.assertFalse(harvest.spawn_from_hook(self.ws.state_dir, dict(payload, cwd=str(self.base)), self.root))
        args = popen.call_args_list[0][0][0]
        self.assertIn('--thread', args)
        self.assertIn('Turkish', args)
        options = popen.call_args_list[0][1]
        if sys.platform == 'win32':
            self.assertTrue(options['creationflags'] & subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertTrue(options['start_new_session'])
        self.assertEqual(popen.call_count, 1)

    def test_codex_threads_are_found_by_working_directory(self):
        sessions = self.home / '.codex' / 'sessions' / '2026' / '09' / '27'; sessions.mkdir(parents=True)
        (sessions / 'rollout-x.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'abc', 'cwd': str(self.root)}}) + '\n', encoding='utf-8')
        (sessions / 'rollout-y.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'zzz', 'cwd': str(self.base)}}) + '\n', encoding='utf-8')
        (sessions / 'rollout-z.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {
            'id': 'rev', 'cwd': str(self.root), 'parent_thread_id': 'abc', 'thread_source': 'guardian_review'}}) + '\n', encoding='utf-8')
        found = [(c, t) for c, t, _ in harvest.find_threads(self.root)]
        self.assertNotIn(('codex', 'rev'), found)
        self.assertIn(('codex', 'abc'), found)
        self.assertNotIn(('codex', 'zzz'), found)


if __name__ == '__main__':
    unittest.main()


class HarvestJevRuleTest(HarvestTest):
    def test_a_saved_key_is_not_used_unless_jev_is_the_chosen_checker(self):
        self.thread('kkkk1111', [('The agent now runs model-b.', 'Noted.')])
        item = dict(type='fact', statement='The agent runs model-b.', exchange=1, quote='The agent now runs model-b')
        self.assertNotEqual(self.ws.retrieval.reranker, 'jev')
        with self.items(item), patch.object(jev, 'api_key', return_value='k'), \
                patch.object(harvest, 'verify', side_effect=AssertionError('Jev must not be called')), \
                patch.object(harvest, 'compare', side_effect=AssertionError('Jev must not be called')):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        digest = (self.root / report['digests'][0]).read_text(encoding='utf-8')
        self.assertNotIn('"judge": "jev"', digest)  # the assistant compares instead


class ClientLaunchTest(unittest.TestCase):
    """How harvest starts Claude/Codex; the client itself is faked."""
    def run_agent(self, platform, which):
        items = [dict(statement='x')]  # Claude wraps the answer in `result`; Codex prints it as is
        reply = mock.Mock(stdout=json.dumps(dict(result=json.dumps(dict(items=items)), items=items)))
        with patch.object(harvest.sys, 'platform', platform), \
                patch.object(harvest, '_client', side_effect=lambda name: ['/bin/' + name]), \
                patch.object(harvest.subprocess, 'run', return_value=reply) as run:
            harvest.ask_agent('SYSTEM', 'line one\nline & two', which, 'items')
        return run.call_args

    def test_the_chat_goes_through_stdin_on_windows_and_as_an_argument_elsewhere(self):
        for which in ('claude:sonnet', 'codex:gpt-5'):
            args, options = self.run_agent('darwin', which)
            self.assertIn('line one\nline & two', args[0][-1])
            self.assertIsNone(options['input'])
            args, options = self.run_agent('win32', which)
            self.assertFalse(any('line & two' in a for a in args[0]))
            self.assertIn('line one\nline & two', options['input'])

    @unittest.skipUnless(sys.platform == 'win32', 'npm shims are a Windows thing')
    def test_an_npm_shim_is_resolved_to_the_program_it_runs(self):
        from carry import desktop
        with tempfile.TemporaryDirectory() as npm:
            npm = Path(npm)
            program = npm / 'node_modules' / 'tool' / 'bin' / 'tool.exe'
            program.parent.mkdir(parents=True)
            program.write_bytes(b'')
            script = npm / 'node_modules' / 'other' / 'cli.js'
            script.parent.mkdir(parents=True)
            script.write_text('', encoding='utf-8')
            (npm / 'tool.cmd').write_text('@ECHO off\r\n' + r'"%dp0%\node_modules\tool\bin\tool.exe"   %*' + '\r\n',
                                          encoding='utf-8')
            (npm / 'other.cmd').write_text('@ECHO off\r\n' + r'"%_prog%"  "%dp0%\node_modules\other\cli.js" %*' + '\r\n',
                                           encoding='utf-8')
            with patch.object(desktop, 'executable_for', side_effect=lambda name: str(npm / (name + '.cmd'))):
                self.assertEqual(desktop.command_for('tool'), [str(program)])
                self.assertEqual(desktop.command_for('other')[1:], [str(script)])
