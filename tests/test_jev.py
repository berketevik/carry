"""Jev judge: one request per recall, masked passages, gate, injection filter, never blocking."""
from dataclasses import replace
import os
from unittest.mock import patch

from _support import WorkspaceCase
from carry import jev, models
from carry.config import Workspace
from carry.index import build
from carry.recall import recall


def answers(relevance, best, injection=None):
    total = sum(best.values())
    best = {key: value / total for key, value in best.items()}
    out = {f'p{i + 1}_answers': dict(type='noul', noul=v) for i, v in enumerate(relevance)}
    out.update({f'p{i + 1}_injection': dict(type='noul', noul=(injection or {}).get(i + 1, 0.01))
                for i in range(len(relevance))})
    out['best'] = dict(type='choice', choice=max(best, key=best.get), probabilities=best)
    return dict(answers=out)


class JevTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.note('Budget', 'The Q4 budget was approved at 120k by Ayse.', folder='notes')
        self.note('Budget log', 'We talked about the Q4 budget today.', folder='log')
        self.note('Office', 'The office moves in November.', folder='notes')
        models.setup(self.workspace, 'keyword_jev')
        self.workspace = Workspace.load(self.workspace.state_dir)
        build(self.workspace)
        env = patch.dict(os.environ, {'TYPESAFE_API_KEY': 'test-key'})
        env.start(); self.addCleanup(env.stop)

    def test_preset_needs_no_model(self):
        ws = self.workspace
        self.assertEqual((ws.embedding.provider, ws.retrieval.reranker, ws.retrieval.reranker_min_score),
                         ('hashing', 'jev', 0.5))

    def test_one_request_keeps_answers_ranked_by_choice_and_masks_text(self):
        sent = []
        def fake(body, key, timeout=jev.TIMEOUT):
            sent.append((body, key))
            ids = list(body['state']['passages'])
            paths = [body['state']['passages'][p]['source'] for p in ids]
            rel = [0.95 if p.endswith('Budget.md') else 0.6 if 'log' in p else 0.02 for p in paths]
            best = {pid: (0.8 if paths[i].endswith('Budget.md') else 0.1) for i, pid in enumerate(ids)}
            return answers(rel, dict(best, none=0.0))
        with patch.object(jev, 'call', side_effect=fake):
            result = recall(self.workspace, 'Q4 budget')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], 'test-key')
        paths = [e['path'] for e in result['evidence']]
        self.assertEqual(paths[0], 'notes/Budget.md')
        self.assertNotIn('notes/Office.md', paths)
        self.assertEqual(result['diagnostics']['reranker'], 'jev')
        self.assertIn('best', sent[0][0]['questions'])
        self.assertTrue(all(isinstance(p['text'], str) for p in sent[0][0]['state']['passages'].values()))
        self.assertIn('none', sent[0][0]['questions']['best']['criteria'])

    def test_secrets_are_masked_before_sending(self):
        self.note('Token', 'The deploy token is ghp_abcdefghijklmnopqrstuvwxyz0123456789 for CI.', folder='notes')
        build(self.workspace)
        sent = []
        def fake(body, key, timeout=jev.TIMEOUT):
            sent.append(body)
            n = len(body['state']['passages'])
            return answers([0.9] * n, dict({f'p{i + 1}': 1 / n for i in range(n)}, none=0.0))
        with patch.object(jev, 'call', side_effect=fake):
            recall(self.workspace, 'deploy token')
        texts = ' '.join(p['text'] for p in sent[0]['state']['passages'].values())
        self.assertNotIn('ghp_abcdefghijklmnopqrstuvwxyz0123456789', texts)

    def _send_capture(self):
        sent = []
        def fake(body, key, timeout=jev.TIMEOUT):
            sent.append(body)
            n = len(body['state']['passages'])
            return answers([0.9] * n, dict({f'p{i + 1}': 1 / n for i in range(n)}, none=0.0))
        return sent, fake

    def test_secret_notes_never_leave_and_come_back_marked_unjudged(self):
        path = self.corpus / 'notes' / 'Vault key.md'
        path.write_text('---\ntype: fact\nsummary: "budget vault"\nsensitivity: secret\ncreated: 2026-09-01\n---\n# Q4 budget vault\nThe Q4 budget vault code is 7788.')
        build(self.workspace)
        sent, fake = self._send_capture()
        with patch.object(jev, 'call', side_effect=fake):
            result = recall(self.workspace, 'Q4 budget')
        texts = ' '.join(p['text'] for b in sent for p in b['state']['passages'].values())
        self.assertNotIn('7788', texts)
        held = [e for e in result['evidence'] if e['path'].endswith('Vault key.md')]
        self.assertTrue(held)
        self.assertGreaterEqual(result['diagnostics']['local_only_unjudged'], 1)

    def test_a_source_can_opt_out_of_the_external_judge(self):
        ws = self.workspace.with_sources([replace(s, external_judge=False) if s.source_id == 'corpus' else s
                                          for s in self.workspace.sources]).save()
        sent, fake = self._send_capture()
        with patch.object(jev, 'call', side_effect=fake):
            result = recall(Workspace.load(ws.state_dir), 'Q4 budget')
        self.assertEqual(sent, [])
        self.assertTrue(result['evidence'])
        self.assertEqual(Workspace.load(ws.state_dir).source('corpus').external_judge, False)

    def test_model_is_pinned(self):
        sent, fake = self._send_capture()
        with patch.object(jev, 'call', side_effect=fake):
            recall(self.workspace, 'Q4 budget')
        self.assertEqual(sent[0]['model'], 'jev-1.13.0')

    def test_topic_searches_ask_for_relevance_and_questions_ask_for_answers(self):
        self.assertFalse(jev.is_question('varlık sensörü'))
        self.assertFalse(jev.is_question('Home Assistant'))
        self.assertTrue(jev.is_question('Evdeki varlık sensörü nasıl çalışıyor?'))
        self.assertTrue(jev.is_question('Ajan hangi modeli kullanıyor'))
        self.assertTrue(jev.is_question('Which ad network was caught spoofing events'))
        _, topic = jev.request('Home Assistant', [dict(path='a.md', heading='h', text='t')])
        _, question = jev.request('Which runtime hosts Home Assistant?', [dict(path='a.md', heading='h', text='t')])
        self.assertIn('about the topic', topic['questions']['p1_answers']['instructions'])
        self.assertIn('answers the question', question['questions']['p1_answers']['instructions'])

    def test_injected_passage_is_excluded(self):
        def fake(body, key, timeout=jev.TIMEOUT):
            n = len(body['state']['passages'])
            return answers([0.9] * n, dict({f'p{i + 1}': 1 / n for i in range(n)}, none=0.0), injection={1: 0.95})
        with patch.object(jev, 'call', side_effect=fake):
            result = recall(self.workspace, 'Q4 budget')
        self.assertEqual(result['diagnostics']['injection_excluded'], 1)

    def test_nothing_answering_is_no_evidence(self):
        def fake(body, key, timeout=jev.TIMEOUT):
            n = len(body['state']['passages'])
            return answers([0.03] * n, dict({f'p{i + 1}': 0.0 for i in range(n)}, none=1.0))
        with patch.object(jev, 'call', side_effect=fake):
            result = recall(self.workspace, 'Q4 budget')
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result['diagnostics']['answerability'], 'judged_none')

    def test_failure_or_missing_key_returns_unjudged_candidates_with_a_warning(self):
        with patch.object(jev, 'call', side_effect=jev.JevUnavailable('http_529')):
            result = recall(self.workspace, 'Q4 budget')
        self.assertTrue(result['evidence'])
        self.assertIn('reranker_unavailable:jev_http_529', result['diagnostics']['warnings'])
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': ''}), patch.object(jev, 'api_key', return_value=None):
            result = recall(self.workspace, 'Q4 budget')
        self.assertIn('reranker_unavailable:jev_no_api_key', result['diagnostics']['warnings'])

    def test_query_variants_pool_candidates_judged_against_the_question(self):
        seen = []
        def fake(body, key, timeout=jev.TIMEOUT):
            seen.append((body['state']['question'], sorted(p['source'] for p in body['state']['passages'].values())))
            n = len(body['state']['passages'])
            return answers([0.9] * n, dict({f'p{i + 1}': 1 / n for i in range(n)}, none=0.0))
        with patch.object(jev, 'call', side_effect=fake):
            result = recall(self.workspace, 'Q4 budget', queries=['office November'])
        question, sources = seen[0]
        self.assertEqual(question, 'Q4 budget')
        self.assertIn('notes/Office.md', sources)
        self.assertEqual(result['diagnostics']['query_variants'], 1)


class JevBridgeTest(WorkspaceCase):
    def test_status_and_key_actions_never_echo_the_key(self):
        from carry.desktop import Bridge
        bridge = Bridge()
        call = lambda **f: bridge.dispatch(dict(workspace=str(self.workspace.state_dir), **f))
        with patch.object(jev, 'api_key', return_value=None):
            self.assertEqual(call(action='jev_status')['key_present'], False)
        with patch.object(jev, 'store_key') as store, patch.object(jev, 'api_key', return_value='k'):
            result = call(action='jev_key', key='secret-value')
        store.assert_called_once_with('secret-value')
        self.assertEqual(result, dict(key_present=True))
