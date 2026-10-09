"""The built-in embedding model: EmbeddingGemma 300M as 4-bit ONNX, run in process.

Search by meaning without Ollama. The files come from a pinned revision on Hugging Face
and are checked against their SHA-256 when downloaded; only `download()` reaches the
network, never a search. The loaded model holds about 0.75 GB and is released when idle.
"""
import hashlib
import os
import threading
import urllib.request
from pathlib import Path

from .errors import ProviderUnavailable

REPOSITORY = 'onnx-community/embeddinggemma-300m-ONNX'
REVISION = '5090578d9565bb06545b4552f76e6bc2c93e4a66'
FILES = {  # path: (bytes, sha256)
    'tokenizer.json': (20323312, '4dda02faaf32bc91031dc8c88457ac272b00c1016cc679757d1c441b248b9c47'),
    'onnx/model_q4.onnx': (519322, 'ad1dfee81a70f7944b9b9d1cc6e48075b832881cf33fab2f2b248be78f3f0043'),
    'onnx/model_q4.onnx_data': (196725760, '599962c3143b040de2dd05e5975be3e9091dd067cacc6a8f7186e3203bab9e02'),
}
DOWNLOAD_BYTES = sum(size for size, _ in FILES.values())
MAX_TOKENS = 2048
# Every assistant session runs its own MCP process; release the model after this many
# idle seconds (the next search reloads it in about half a second). 0 keeps it loaded.
IDLE_SECONDS = float(os.environ.get('CARRY_EMBEDDING_IDLE_SECONDS', '600'))

_lock = threading.RLock()
_loaded = {}
_timer = None


def model_dir():
    base = os.environ.get('CARRY_MODEL_DIR')
    root = Path(base).expanduser() if base else Path.home() / '.cache' / 'carry' / 'models'
    return root / 'embeddinggemma-300m-q4' / REVISION[:12]


def runtime_available():
    """onnxruntime has no wheel for Intel Macs, so it may be missing from an install."""
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
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


def _load(directory):
    import onnxruntime
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(directory / 'tokenizer.json'))
    tokenizer.enable_truncation(MAX_TOKENS)
    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3
    session = onnxruntime.InferenceSession(str(directory / 'onnx' / 'model_q4.onnx'), options,
                                           providers=['CPUExecutionProvider'])
    return tokenizer, session


def embed(texts, directory=None):
    """One normalised 768-dimension vector per text."""
    directory = directory or model_dir()
    if not installed(directory):
        raise ProviderUnavailable('model_not_installed')
    try:
        import numpy
    except ImportError:
        raise ProviderUnavailable('onnx_runtime_missing')
    with _lock:
        key = str(directory)
        if key not in _loaded:
            _loaded.clear()
            try:
                _loaded[key] = _load(directory)
            except ImportError:
                raise ProviderUnavailable('onnx_runtime_missing')
            except Exception as exc:
                raise ProviderUnavailable('model_load_failed:' + type(exc).__name__)
        tokenizer, session = _loaded[key]
        encoded = tokenizer.encode_batch(list(texts))
        width = max(len(e.ids) for e in encoded)
        ids = numpy.zeros((len(encoded), width), dtype=numpy.int64)
        mask = numpy.zeros_like(ids)
        for row, item in enumerate(encoded):
            ids[row, :len(item.ids)] = item.ids
            mask[row, :len(item.ids)] = 1
        vectors = session.run(['sentence_embedding'], {'input_ids': ids, 'attention_mask': mask})[0]
    _schedule_release()
    return [vector.tolist() for vector in vectors]


def release():
    """Drop the loaded model; returns whether one was loaded."""
    with _lock:
        if not _loaded:
            return False
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
