"""Outbound data and untrusted provider response regressions (no API calls)."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from carry import jev


class JevContractTest(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(path='notes/Budget.md', heading='Budget', text='Budget is 120k.')]
        self.response = dict(model='jev-1.13.0', usage=dict(input_tokens=100, output_tokens=20),
            answers=dict(p1_answers=dict(type='noul', noul=0.9),
                         p1_injection=dict(type='noul', noul=0.01),
                         best=dict(type='choice', choice='p1', confidence=0.8,
                                   probabilities=dict(p1=0.9, none=0.1))))

    def run_response(self, response):
        diagnostics = {}
        with patch.object(jev, 'api_key', return_value='synthetic'), patch.object(jev, 'call', return_value=response):
            rows = jev.rerank('budget', self.rows, SimpleNamespace(reranker_min_score=0.5), diagnostics)
        return rows, diagnostics

    def test_every_outbound_text_surface_is_masked(self):
        secret = 'ghp_' + 'a' * 36
        rows = [dict(path=secret, heading=secret, text=secret)]
        _, body = jev.request(secret, rows)
        self.assertNotIn(secret, json.dumps(body))
        self.assertEqual(rows[0]['text'], secret)  # local evidence stays canonical
        self.assertIsInstance(body['state']['passages']['p1']['text'], str)

    def test_malformed_responses_degrade_without_claiming_verification(self):
        cases = [None, [], dict(answers=[])]
        for value in (float('nan'), float('inf'), -0.1, 1.1, True, '0.9', None):
            r = copy.deepcopy(self.response)
            r['answers']['p1_answers']['noul'] = value
            cases.append(r)
        for change in ('missing', 'wrong_type', 'unknown_choice', 'missing_probability', 'bad_sum'):
            r = copy.deepcopy(self.response)
            if change == 'missing': del r['answers']['p1_injection']
            if change == 'wrong_type': r['answers']['best'] = []
            if change == 'unknown_choice': r['answers']['best']['choice'] = 'unknown'
            if change == 'missing_probability': del r['answers']['best']['probabilities']['p1']
            if change == 'bad_sum': r['answers']['best']['probabilities']['p1'] = 0.1
            cases.append(r)
        for response in cases:
            with self.subTest(response=response):
                rows, diagnostics = self.run_response(response)
                self.assertEqual(rows, self.rows)
                self.assertEqual(diagnostics['answerability'], 'not_verified')
                self.assertEqual(diagnostics['warnings'], ['reranker_unavailable:jev_invalid_response'])

    def test_records_model_usage_and_uncertainty_without_content(self):
        _, diagnostics = self.run_response(self.response)
        self.assertEqual(diagnostics['judge_model'], 'jev-1.13.0')
        self.assertEqual(diagnostics['judge_usage']['input_tokens'], 100)
        self.assertEqual(diagnostics['best_confidence'], 0.8)
        self.assertFalse(diagnostics['judge_disagreement'])
        self.assertNotIn('Budget is', json.dumps(diagnostics))

    def test_independent_judges_disagree_without_silently_discarding_evidence(self):
        self.response['answers']['best'].update(choice='none', probabilities=dict(p1=0.1, none=0.9))
        rows, diagnostics = self.run_response(self.response)
        self.assertTrue(rows)
        self.assertTrue(diagnostics['judge_disagreement'])
        self.assertEqual(diagnostics['none_probability'], 0.9)
