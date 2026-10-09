"""Embedding providers behind one interface.

Three providers ship today. `ollama` and `llama` run the same local model
(EmbeddingGemma) on the same engine, llama.cpp, through the Ollama app or inside Carry; `hashing` is a
deterministic offline provider used by tests and by keyword-only setups.
The hashing provider is lexical, not semantic, and every status payload that
exposes it says so, because pretending otherwise would hide a quality cliff.
"""
import hashlib
import json
import math
import re
import urllib.error
import urllib.request

from .errors import ProviderUnavailable

TOKEN_RE = re.compile(r"\w{2,}", re.UNICODE)


class EmbeddingProvider:
    name = "abstract"
    semantic = False

    @property
    def fingerprint(self):
        """Anything that changes vector meaning belongs in this string."""
        raise NotImplementedError

    def embed_document(self, text):
        raise NotImplementedError

    def embed_query(self, text):
        return self.embed_document(text)

    def probe(self):
        """Return (available: bool, detail: str) without raising."""
        return True, "ok"


class HashingEmbedding(EmbeddingProvider):
    """Hashed bag of tokens, L2 normalised. Offline, deterministic, lexical."""
    name = "hashing"
    semantic = False

    def __init__(self, dim=256):
        if dim < 16:
            raise ProviderUnavailable("hashing_dim_too_small")
        self.dim = dim

    @property
    def fingerprint(self):
        return f"hashing:{self.dim}"

    def embed_document(self, text):
        vector = [0.0] * self.dim
        for token in TOKEN_RE.findall((text or "").lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            slot = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 else -1.0
            vector[slot] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if not norm:
            # An empty or stopword-only chunk still needs a valid unit vector.
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]


class OllamaEmbedding(EmbeddingProvider):
    name = "ollama"
    semantic = True

    def __init__(self, model="nomic-embed-text", endpoint="http://localhost:11434",
                 prefixes=True, revision="unspecified", timeout=15.0):
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.prefixes = prefixes
        self.revision = revision
        self.timeout = timeout

    @property
    def fingerprint(self):
        return f"ollama:{self.model}:{self.revision}:{int(self.prefixes)}"

    def _post(self, path, payload):
        request = urllib.request.Request(
            self.endpoint + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ProviderUnavailable("ollama_unreachable:" + type(exc).__name__)

    def _embed(self, text):
        out = self._post("/api/embeddings", {"model": self.model, "prompt": text})
        vector = out.get("embedding")
        if not vector or not all(math.isfinite(x) for x in vector) or not any(vector):
            raise ProviderUnavailable("invalid_embedding")
        return vector

    def embed_document(self, text):
        prefix = ''
        if self.prefixes:
            if self.model.startswith('embeddinggemma'):
                prefix = 'title: none | text: '
            elif self.model.startswith('nomic-embed-text'):
                prefix = 'search_document: '
        return self._embed(prefix + text)

    def embed_query(self, text):
        prefix = ''
        if self.prefixes:
            if self.model.startswith('embeddinggemma'):
                prefix = 'task: search result | query: '
            elif self.model.startswith('qwen3-embedding'):
                prefix = 'Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery: '
            elif self.model.startswith('nomic-embed-text'):
                prefix = 'search_query: '
        return self._embed(prefix + text)

    def probe(self):
        try:
            self._embed("probe")
            return True, "ok"
        except ProviderUnavailable as exc:
            return False, str(exc)


class LlamaEmbedding(EmbeddingProvider):
    """EmbeddingGemma run inside Carry by llama.cpp (llama_model): search by meaning without Ollama."""
    name = "llama"
    semantic = True

    def __init__(self, model="embeddinggemma", prefixes=True):
        if model != "embeddinggemma":
            raise ProviderUnavailable("unsupported_llama_model:" + str(model))
        self.model = model
        self.prefixes = prefixes

    @property
    def fingerprint(self):
        # The same model, prompts and engine as Ollama's embeddinggemma: on Apple Silicon the
        # vectors agree with Ollama's to cosine 1.000 for passages and queries, so both share a
        # fingerprint and an index or a team pack carries over when Ollama is added or removed.
        return OllamaEmbedding(model=self.model, prefixes=self.prefixes).fingerprint

    def _embed(self, text):
        from . import llama_model
        vector = llama_model.embed([text])[0]
        if not all(math.isfinite(x) for x in vector) or not any(vector):
            raise ProviderUnavailable("invalid_embedding")
        return vector

    def embed_document(self, text):
        return self._embed(("title: none | text: " if self.prefixes else "") + text)

    def embed_query(self, text):
        return self._embed(("task: search result | query: " if self.prefixes else "") + text)

    def probe(self):
        try:
            self._embed("probe")
            return True, "ok"
        except ProviderUnavailable as exc:
            return False, str(exc)


def build_provider(config):
    """Instantiate the configured provider. Construction never reaches the
    network, so an unavailable model surfaces as a diagnostic, not an import
    error."""
    if config.provider == "hashing":
        return HashingEmbedding(dim=config.dim)
    if config.provider == "ollama":
        return OllamaEmbedding(model=config.model, endpoint=config.endpoint,
                               prefixes=config.prefixes, revision=config.revision,
                               timeout=config.timeout)
    if config.provider == "llama":
        return LlamaEmbedding(model=config.model, prefixes=config.prefixes)
    raise ProviderUnavailable("unknown_provider:" + str(config.provider))
