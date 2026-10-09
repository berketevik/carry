"""The built-in model: verified download, engine choice, shared fingerprint, real vectors when present."""
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
from carry import cli, models, ollama_setup, onnx_model
from carry.config import EmbeddingConfig, Workspace
from carry.embedding import OnnxEmbedding, OllamaEmbedding, build_provider
from carry.errors import ProviderUnavailable


class OnnxModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def serve(self, files):
        """A file:// mirror of the pinned repository path, and FILES pointing at it."""
        mirror = self.base / 'mirror' / onnx_model.REPOSITORY / 'resolve' / onnx_model.REVISION
        table = {}
        for path, data in files.items():
            (mirror / path).parent.mkdir(parents=True, exist_ok=True)
            (mirror / path).write_bytes(data)
            table[path] = (len(data), hashlib.sha256(data).hexdigest())
        return (self.base / 'mirror').as_uri(), table

    def test_fingerprint_is_shared_with_ollama_embeddinggemma(self):
        provider = build_provider(EmbeddingConfig(provider='onnx', model='embeddinggemma'))
        self.assertIsInstance(provider, OnnxEmbedding)
        self.assertEqual(provider.fingerprint, OllamaEmbedding(model='embeddinggemma').fingerprint)
        with self.assertRaises(ProviderUnavailable):
            OnnxEmbedding(model='nomic-embed-text')

    def test_download_verifies_every_file_and_skips_good_ones(self):
        url, table = self.serve({'tokenizer.json': b'{}', 'onnx/model.onnx': b'model'})
        target = self.base / 'models'
        with patch.object(onnx_model, 'FILES', table):
            onnx_model.download(directory=target, base_url=url)
            self.assertTrue(onnx_model.installed(target))
            with patch('urllib.request.urlopen', side_effect=AssertionError('no second download')):
                onnx_model.download(directory=target, base_url=url)

    def test_checksum_mismatch_leaves_nothing_behind(self):
        url, table = self.serve({'tokenizer.json': b'{}'})
        table['tokenizer.json'] = (2, '0' * 64)
        target = self.base / 'models'
        with patch.object(onnx_model, 'FILES', table), self.assertRaisesRegex(ProviderUnavailable, 'checksum'):
            onnx_model.download(directory=target, base_url=url)
        self.assertEqual([p for p in target.rglob('*') if p.is_file()], [])

    def test_missing_model_is_reported_not_downloaded(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('no download')), \
                self.assertRaisesRegex(ProviderUnavailable, 'model_not_installed'):
            onnx_model.embed(['text'], directory=self.base / 'empty')

    def test_engine_prefers_ollama_then_the_built_in_model(self):
        cases = [(True, True, 'embeddinggemma'), (False, True, 'embeddinggemma_onnx'), (False, False, 'embeddinggemma')]
        for installed, runtime, engine in cases:
            with patch.object(ollama_setup, 'status', return_value=dict(installed=installed)), \
                    patch.object(onnx_model, 'runtime_available', return_value=runtime):
                self.assertEqual(models.semantic_engine(), engine)

    def test_setup_activates_the_built_in_model_and_search_needs_no_ollama(self):
        ws_dir = self.base / 'ws'
        with redirect_stdout(io.StringIO()):
            cli.main(['--workspace', str(ws_dir), 'init', '--embedding', 'hashing'])
        with patch.object(ollama_setup, 'status', return_value=dict(installed=False)), \
                patch.object(ollama_setup, 'ensure', side_effect=AssertionError('Ollama not needed')), \
                patch.object(onnx_model, 'runtime_available', return_value=True), \
                patch.object(onnx_model, 'download'), \
                patch.object(OnnxEmbedding, 'probe', return_value=(True, 'ok')), \
                patch('carry.index.build', return_value=dict(status='built')), \
                redirect_stdout(io.StringIO()):
            code = cli.main(['--workspace', str(ws_dir), 'search', '--semantic', 'on'])
        self.assertEqual(code, 0)
        ws = Workspace.load(ws_dir)
        self.assertEqual((ws.embedding.provider, ws.embedding.model, ws.retrieval.reranker), ('onnx', 'embeddinggemma', 'off'))


@unittest.skipUnless(onnx_model.runtime_available() and onnx_model.installed(), 'built-in model not downloaded here')
class OnnxRealModelTest(unittest.TestCase):
    def test_vectors_rank_the_matching_passage_first(self):
        provider = OnnxEmbedding()
        docs = [provider.embed_document('The fourth quarter marketing budget is 40 thousand euros.'),
                provider.embed_document('Our cat sleeps on the sofa every afternoon.')]
        query = provider.embed_query('How much is the Q4 advertising spend?')
        self.assertEqual(len(query), 768)
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in query)), 1.0, places=3)
        scores = [sum(a * b for a, b in zip(query, d)) for d in docs]
        self.assertGreater(scores[0], scores[1])
        self.assertTrue(onnx_model.release())


if __name__ == '__main__':
    unittest.main()
