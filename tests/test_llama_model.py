"""The built-in model (llama.cpp): verified download, engine choice, shared fingerprint, real vectors when present."""
import hashlib
import io
import math
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401
from carry import cli, models, llama_model, ollama_setup
from carry.config import EmbeddingConfig, Workspace
from carry.embedding import LlamaEmbedding, OllamaEmbedding, build_provider
from carry.errors import ProviderUnavailable


class LlamaModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def serve(self, files):
        """A file:// mirror of the pinned repository path, and FILES pointing at it."""
        mirror = self.base / 'mirror' / llama_model.REPOSITORY / 'resolve' / llama_model.REVISION
        table = {}
        for path, data in files.items():
            (mirror / path).parent.mkdir(parents=True, exist_ok=True)
            (mirror / path).write_bytes(data)
            table[path] = (len(data), hashlib.sha256(data).hexdigest())
        return (self.base / 'mirror').as_uri(), table

    def test_fingerprint_is_shared_with_ollama_embeddinggemma(self):
        provider = build_provider(EmbeddingConfig(provider='llama', model='embeddinggemma'))
        self.assertIsInstance(provider, LlamaEmbedding)
        self.assertEqual(provider.fingerprint, OllamaEmbedding(model='embeddinggemma').fingerprint)
        with self.assertRaises(ProviderUnavailable):
            LlamaEmbedding(model='nomic-embed-text')

    def test_download_verifies_every_file_and_skips_good_ones(self):
        url, table = self.serve({'model.gguf': b'model', 'extra/part.bin': b'part'})
        target = self.base / 'models'
        with patch.object(llama_model, 'FILES', table):
            llama_model.download(directory=target, base_url=url)
            self.assertTrue(llama_model.installed(target))
            with patch('urllib.request.urlopen', side_effect=AssertionError('no second download')):
                llama_model.download(directory=target, base_url=url)

    def test_checksum_mismatch_leaves_nothing_behind(self):
        url, table = self.serve({'model.gguf': b'{}'})
        table['model.gguf'] = (2, '0' * 64)
        target = self.base / 'models'
        with patch.object(llama_model, 'FILES', table), self.assertRaisesRegex(ProviderUnavailable, 'checksum'):
            llama_model.download(directory=target, base_url=url)
        self.assertEqual([p for p in target.rglob('*') if p.is_file()], [])

    def test_missing_model_is_reported_not_downloaded(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('no download')), \
                self.assertRaisesRegex(ProviderUnavailable, 'model_not_installed'):
            llama_model.embed(['text'], directory=self.base / 'empty')

    def test_engine_prefers_ollama_then_the_built_in_model(self):
        cases = [(True, True, 'embeddinggemma'), (False, True, 'embeddinggemma_builtin'), (False, False, 'embeddinggemma')]
        for installed, runtime, engine in cases:
            with patch.object(ollama_setup, 'status', return_value=dict(installed=installed)), \
                    patch.object(llama_model, 'runtime_available', return_value=runtime):
                self.assertEqual(models.semantic_engine(), engine)

    def test_setup_activates_the_built_in_model_and_search_needs_no_ollama(self):
        ws_dir = self.base / 'ws'
        with redirect_stdout(io.StringIO()):
            cli.main(['--workspace', str(ws_dir), 'init', '--embedding', 'hashing'])
        with patch.object(ollama_setup, 'status', return_value=dict(installed=False)), \
                patch.object(ollama_setup, 'ensure', side_effect=AssertionError('Ollama not needed')), \
                patch.object(llama_model, 'runtime_available', return_value=True), \
                patch.object(llama_model, 'download'), \
                patch.object(LlamaEmbedding, 'probe', return_value=(True, 'ok')), \
                patch('carry.index.build', return_value=dict(status='built')), \
                redirect_stdout(io.StringIO()):
            code = cli.main(['--workspace', str(ws_dir), 'search', '--semantic', 'on'])
        self.assertEqual(code, 0)
        ws = Workspace.load(ws_dir)
        self.assertEqual((ws.embedding.provider, ws.embedding.model, ws.retrieval.reranker), ('llama', 'embeddinggemma', 'off'))

    def test_workspaces_set_up_on_onnx_load_as_the_built_in_model(self):
        config = EmbeddingConfig.from_json(dict(provider='onnx', model='embeddinggemma', prefixes=True))
        self.assertEqual(config.provider, 'llama')
        self.assertIsInstance(build_provider(config), LlamaEmbedding)

    def test_the_old_preset_name_still_sets_up_the_built_in_model(self):
        ws_dir = self.base / 'ws'
        with redirect_stdout(io.StringIO()):
            cli.main(['--workspace', str(ws_dir), 'init', '--embedding', 'hashing'])
        with patch.object(llama_model, 'runtime_available', return_value=True), \
                patch.object(llama_model, 'download'), \
                patch.object(LlamaEmbedding, 'probe', return_value=(True, 'ok')):
            result = models.setup(Workspace.load(ws_dir), 'embeddinggemma_onnx')
        self.assertEqual(result['engine'], 'llama')
        self.assertEqual(Workspace.load(ws_dir).embedding.provider, 'llama')


@unittest.skipUnless(llama_model.runtime_available() and llama_model.installed(), 'built-in model not downloaded here')
class LlamaRealModelTest(unittest.TestCase):
    def test_vectors_rank_the_matching_passage_first(self):
        provider = LlamaEmbedding()
        docs = [provider.embed_document('The fourth quarter marketing budget is 40 thousand euros.'),
                provider.embed_document('Our cat sleeps on the sofa every afternoon.')]
        query = provider.embed_query('How much is the Q4 advertising spend?')
        self.assertEqual(len(query), 768)
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in query)), 1.0, places=3)
        scores = [sum(a * b for a, b in zip(query, d)) for d in docs]
        self.assertGreater(scores[0], scores[1])
        self.assertTrue(llama_model.release())

    def test_a_process_that_exits_with_the_model_loaded_ends_cleanly(self):
        # ggml's Metal teardown aborted such a process (exit 134) and wrote a backtrace to stderr.
        import subprocess
        import sys
        code = 'from carry import llama_model; llama_model.embed(["probe"])'
        done = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120,
                              env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src')))
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, '', ''))


if __name__ == '__main__':
    unittest.main()
