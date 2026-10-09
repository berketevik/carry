"""The built-in embedding model: EmbeddingGemma 300M as 8-bit GGUF, run in process by llama.cpp.

Search by meaning without Ollama, on the same engine Ollama uses: on Apple Silicon the
model runs on the Mac's GPU (Metal). The file comes from a pinned revision on Hugging
Face and is checked against its SHA-256 when downloaded; only `download()` reaches the
network, never a search. The loaded model holds about 1 GB and is released when idle.
"""
import hashlib
import os
import threading
import urllib.request
from pathlib import Path

from .errors import ProviderUnavailable

REPOSITORY = 'ggml-org/embeddinggemma-300M-GGUF'
REVISION = '0f741b5a6585bd53aeb15cd1372c56f2a0f65e12'
FILES = {  # path: (bytes, sha256)
    'embeddinggemma-300M-Q8_0.gguf': (333590944, 'b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63'),
}
MODEL_FILE = 'embeddinggemma-300M-Q8_0.gguf'
DOWNLOAD_BYTES = sum(size for size, _ in FILES.values())
# Pooled embeddings need the whole passage in one batch, so batch and context are equal.
MAX_TOKENS = 2048
# Every assistant session runs its own MCP process; release the model after this many
# idle seconds (the next search reloads it in a fraction of a second). 0 keeps it loaded.
IDLE_SECONDS = float(os.environ.get('CARRY_EMBEDDING_IDLE_SECONDS', '600'))

_lock = threading.RLock()
_loaded = {}
_timer = None
_log_callback = None


def model_dir():
    base = os.environ.get('CARRY_MODEL_DIR')
    root = Path(base).expanduser() if base else Path.home() / '.cache' / 'carry' / 'models'
    return root / 'embeddinggemma-300m-q8-gguf' / REVISION[:12]


def runtime_available():
    """llama-cpp-python is built from source at install and is not installed on Windows."""
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        return False
    return True


def installed(directory=None):
    directory = directory or model_dir()
    return all((directory / path).is_file() and (directory / path).stat().st_size == size
               for path, (size, _) in FILES.items())


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as data:
        while block := data.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def download(progress=None, directory=None, base_url='https://huggingface.co'):
    """Fetch the missing or damaged files; `progress(done, total)` gets bytes."""
    directory = directory or model_dir()
    done = 0
    for path, (size, expected) in FILES.items():
        target = directory / path
        if target.is_file() and target.stat().st_size == size and _sha256(target) == expected:
            done += size
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + '.part')
        digest = hashlib.sha256()
        reported = 0
        try:
            url = f'{base_url}/{REPOSITORY}/resolve/{REVISION}/{path}'
            with urllib.request.urlopen(url, timeout=60) as response, temp.open('wb') as out:
                while block := response.read(1 << 20):
                    out.write(block)
                    digest.update(block)
                    done += len(block)
                    if progress and done - reported >= 8 << 20:
                        progress(done, DOWNLOAD_BYTES)
                        reported = done
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise ProviderUnavailable('model_download_failed:' + type(exc).__name__)
        if digest.hexdigest() != expected:
            temp.unlink(missing_ok=True)
            raise ProviderUnavailable('model_checksum_mismatch')
        os.replace(temp, target)
    if progress:
        progress(DOWNLOAD_BYTES, DOWNLOAD_BYTES)
    return directory


def _silence(llama_cpp):
    """llama.cpp logs to stderr from C; Carry's hooks and MCP server keep their output clean."""
    global _log_callback
    if _log_callback is None:
        _log_callback = llama_cpp.llama_log_callback(lambda level, text, data: None)
        llama_cpp.llama_log_set(_log_callback, None)


def _load(directory):
    import atexit
    import llama_cpp
    _silence(llama_cpp)
    # A model still loaded when the process ends aborts it: ggml's Metal device is torn down
    # by a C++ destructor while the model's GPU buffers are alive (GGML_ASSERT, exit 134).
    # Free it first; atexit runs before those destructors and ignores a second registration.
    atexit.unregister(release)
    atexit.register(release)
    # n_gpu_layers=-1 puts every layer on the GPU where llama.cpp was built with one (Metal on
    # Apple Silicon) and is ignored on a CPU-only build.
    return llama_cpp.Llama(model_path=str(directory / MODEL_FILE), embedding=True, n_gpu_layers=-1,
                           n_ctx=MAX_TOKENS, n_batch=MAX_TOKENS, n_ubatch=MAX_TOKENS, verbose=False)


def embed(texts, directory=None):
    """One normalised 768-dimension vector per text; longer texts are cut at MAX_TOKENS."""
    directory = directory or model_dir()
    if not installed(directory):
        raise ProviderUnavailable('model_not_installed')
    with _lock:
        key = str(directory)
        if key not in _loaded:
            _loaded.clear()
            try:
                _loaded[key] = _load(directory)
            except ImportError:
                raise ProviderUnavailable('llama_runtime_missing')
            except Exception as exc:
                raise ProviderUnavailable('model_load_failed:' + type(exc).__name__)
        model = _loaded[key]
        try:
            vectors = model.embed(list(texts), normalize=True, truncate=True)
        except Exception as exc:
            raise ProviderUnavailable('embedding_failed:' + type(exc).__name__)
    _schedule_release()
    return [list(vector) for vector in vectors]


def release():
    """Drop the loaded model; returns whether one was loaded."""
    with _lock:
        if not _loaded:
            return False
        for model in _loaded.values():
            model.close()
        _loaded.clear()
    import gc
    gc.collect()
    return True


def _schedule_release():
    global _timer
    if IDLE_SECONDS <= 0:
        return
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(IDLE_SECONDS, release)
        _timer.daemon = True
        _timer.start()
