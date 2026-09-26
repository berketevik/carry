"""Background lifecycle and retrieval quality regressions with no network."""
import json
import os
import time
from dataclasses import replace
from unittest.mock import patch, Mock

from _support import WorkspaceCase
from carry import index, maintenance, models
from carry.config import EmbeddingConfig, Workspace
from carry.embedding import OllamaEmbedding
from carry.recall import recall
from carry.rerank import rerank


class MaintenanceTest(WorkspaceCase):
    def wait(self):
        deadline = time.monotonic() + 10
        while maintenance.is_running(self.workspace) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(maintenance.is_running(self.workspace))
        return maintenance.job_status(self.workspace)

    def test_first_index_and_edit_refresh_in_background(self):
        note = self.note('Release', 'The milestone is October 22.')
        self.addCleanup(self.wait)
        started = maintenance.start(self.workspace)
        self.assertTrue(started['started'])
        self.assertEqual(self.wait()['state'], 'complete')
        self.assertEqual(index.health(self.workspace)['state'], 'fresh')
        note.write_text('The replacement milestone is November 5.')
        maintenance.start(self.workspace)
        self.wait()
        self.assertIn('November 5', recall(self.workspace, 'replacement milestone')['evidence'][0]['text'])
        self.assertEqual(recall(self.workspace, 'October 22')['evidence'], [])

    def test_only_one_worker_and_interruption_visible(self):
        import fcntl
        with (self.workspace.state_dir/'maintenance.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertFalse(maintenance.start(self.workspace)['started'])
            maintenance.job_progress(self.workspace, state='running')
            self.assertEqual(maintenance.job_status(self.workspace)['state'], 'running')
        self.assertEqual(maintenance.job_status(self.workspace)['state'], 'interrupted')

    def test_disabled_refresh_and_failed_job_backoff(self):
        with patch.dict(os.environ, {'CARRY_AUTO_INDEX':'0'}):
            self.assertEqual(maintenance.automatic(self.workspace), 'off')
        maintenance.job_progress(self.workspace, state='failed')
        with patch.dict(os.environ, {'CARRY_AUTO_INDEX':'1'}):
            self.assertEqual(maintenance.automatic(self.workspace), 'retry_pending')

    def test_failed_initial_semantic_build_still_gets_lexical_index(self):
        self.note('Release', 'October milestone')
        ws = replace(self.workspace, embedding=EmbeddingConfig(provider='ollama',endpoint='http://127.0.0.1:1', timeout=0.1)).save()
        result = maintenance.run(ws, 'index', {})
        self.assertEqual(result['fallback'], 'lexical_until_model_available')
        self.assertTrue(index.db_is_usable(ws.db_path))
        result = recall(ws, 'October milestone')
        self.assertTrue(result['evidence'])
        self.assertFalse(result['diagnostics']['semantic'])
        self.assertIn('index_stale', result['diagnostics']['warnings'])

    def test_model_download_failure_does_not_activate_unavailable_model(self):
        before = self.workspace.config_path.read_bytes()
        with patch.object(models, 'ensure_runtime'), patch('urllib.request.urlopen', side_effect=OSError('offline')):
            with self.assertRaisesRegex(Exception, 'model_download_failed'):
                models.setup(self.workspace, 'embeddinggemma')
        self.assertEqual(self.workspace.config_path.read_bytes(), before)

    def test_assistant_ranked_preset_hands_unfiltered_candidates_to_the_client(self):
        real_setup = models.setup
        def fake(workspace, model):
            if model == 'embeddinggemma':
                current = Workspace.load(workspace.state_dir)
                replace(current, embedding=EmbeddingConfig(provider='hashing'),
                        retrieval=replace(current.retrieval, reranker='cross')).save()
                return dict(model=model, available=True)
            return real_setup(workspace, model)
        with patch.object(models, 'setup', side_effect=fake):
            result = real_setup(self.workspace, 'assistant_ranked')
        self.assertEqual(result['ranking'], 'assistant')
        retrieval = Workspace.load(self.workspace.state_dir).retrieval
        self.assertEqual((retrieval.reranker, retrieval.top_k, retrieval.max_chars, retrieval.vector_min_score),
                         ('off', 12, 16000, -1.0))


class RelevanceTest(WorkspaceCase):
    def test_lexical_hash_collisions_do_not_return_unmatched_documents(self):
        self.note('Release', 'The release date is October 22.')
        self.build()
        self.assertEqual(recall(self.workspace, 'quasar zeppelin')['status'], 'no_evidence')

    def test_reranker_sorts_and_rejects_weak_candidates_using_raw_scores(self):
        rows = [dict(text='Unrelated', path='other.md'),dict(text='The date is October 22.',path='date.md')]
        model = Mock(); model.predict.return_value = [-9.0, 3.0]
        diagnostics = {}
        with patch('carry.rerank.encoder', return_value=model):
            kept = rerank('When?', rows, replace(self.workspace.retrieval, reranker='cross'), diagnostics)
        self.assertEqual([r['path'] for r in kept], ['date.md'])
        self.assertEqual(diagnostics['rejected_candidates'], 1)
        self.assertEqual(model.predict.call_args.kwargs['activation_fn'](2.5), 2.5)

    def test_reranker_can_abstain_and_failure_is_reported(self):
        rows = [dict(text='Unrelated')]
        model = Mock(); model.predict.return_value = [-10.0]
        config = replace(self.workspace.retrieval, reranker='cross')
        with patch('carry.rerank.encoder', return_value=model):
            self.assertEqual(rerank('Unknown?',rows,config,{}), [])
        diagnostics = {}
        with patch('carry.rerank.encoder', side_effect=ImportError()):
            self.assertEqual(rerank('Unknown?',rows,config,diagnostics), rows)
        self.assertEqual(diagnostics['reranker'], 'unavailable')
        self.assertTrue(diagnostics['warnings'])

    def test_idle_reranker_is_released_and_reloaded(self):
        from carry import rerank as rerank_module
        model = Mock(); model.predict.return_value = [3.0]
        config = replace(self.workspace.retrieval, reranker='cross')
        with patch.object(rerank_module, '_load', return_value=model) as load, \
                patch.object(rerank_module, 'IDLE_SECONDS', 0):
            rerank_module.release()
            rerank('When?', [dict(text='The date is October 22.')], config, {})
            self.assertEqual(load.call_count, 1)
            self.assertTrue(rerank_module.release())
            self.assertFalse(rerank_module.release())
            rerank('When?', [dict(text='The date is October 22.')], config, {})
            self.assertEqual(load.call_count, 2)
            rerank_module.release()

    def test_embedding_prompt_contract_is_model_specific(self):
        for model, doc_prefix, query_prefix in [
            ('embeddinggemma','title: none | text: ','task: search result | query: '),
            ('nomic-embed-text','search_document: ','search_query: '),
            ('qwen3-embedding:0.6b','','Instruct:')]:
            provider = OllamaEmbedding(model=model)
            with patch.object(provider, '_embed', return_value=[1.0]) as embed:
                provider.embed_document('Text'); self.assertTrue(embed.call_args.args[0].startswith(doc_prefix))
                provider.embed_query('Question'); self.assertTrue(embed.call_args.args[0].startswith(query_prefix))
