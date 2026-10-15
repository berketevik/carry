"""Whisper on this Mac: a meeting's audio transcribed once the meeting ends, by whisper.cpp.

large-v3-turbo as 8-bit GGML with Silero voice detection, from pinned revisions on Hugging
Face, each file checked against its SHA-256 when downloaded; only `download()` reaches the
network. On Apple Silicon the model runs on the GPU (Metal): a 44-minute meeting took 107 s
while a video call ran on the same Mac (measured), and holds about 1 GB while it runs.

The output is guarded. Whisper sometimes loops on one phrase; such a stretch is transcribed
again on its own, which removed every loop measured, else the repeats are collapsed. In
silence it makes up the endings of subtitled videos ("Altyazı M.K."); those short lines are
dropped.
"""
import hashlib
import http.client
import os
from pathlib import Path
import re
import time
import urllib.request
import wave

from .errors import ProviderUnavailable

FILES = {  # path: (repository, revision, bytes, sha256)
    'ggml-large-v3-turbo-q8_0.bin': ('ggerganov/whisper.cpp', '5359861c739e955e79d9a303bcbc70fb988958b1', 874188075,
                                     '317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1'),
    'ggml-silero-v6.2.0.bin': ('ggml-org/whisper-vad', '9ffd54a1e1ee413ddf265af9913beaf518d1639b', 885098,
                               '2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987'),
}
MODEL_FILE = 'ggml-large-v3-turbo-q8_0.bin'
VAD_FILE = 'ggml-silero-v6.2.0.bin'
DOWNLOAD_BYTES = sum(size for _, _, size, _ in FILES.values())
RATE = 16000
MIN_SECONDS = 0.5   # less audio than this is not worth a call
LOOP_RUN = 6        # the same 1-4 word phrase this many times in a row is a loop
LOOP_PAD = 15       # seconds around a loop transcribed again with it
CHECK_SECONDS = 1   # how often a running transcription asks whether to pause
WAIT_SECONDS = 5    # how often a paused one asks whether to go on
MAX_PAUSE = 30 * 60  # an app that keeps the microphone open outside a call pauses no longer than this
MADE_UP_WORDS = 10  # a made-up line is short; a real sentence that says the same words stays
MADE_UP = re.compile(r'altyaz[ıi]|izlediğiniz için|abone olmayı unutmayın|^\W*abone ol\w*\W*$|\bm\.\s?k\.'
                     r'|thanks? (you )?for watching|subtitles? by')


def model_dir():
    base = os.environ.get('CARRY_MODEL_DIR')
    root = Path(base).expanduser() if base else Path.home() / '.cache' / 'carry' / 'models'
    return root / 'whisper-large-v3-turbo-q8' / FILES[MODEL_FILE][1][:12]


def runtime_available():
    """pywhispercpp ships whisper.cpp built for Apple Silicon; elsewhere it is not installed."""
    try:
        import pywhispercpp.model  # noqa: F401
    except ImportError:
        return False
    return True


def installed(directory=None):
    directory = directory or model_dir()
    return all((directory / path).is_file() and (directory / path).stat().st_size == size
               for path, (_, _, size, _) in FILES.items())


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
    for path, (repository, revision, size, expected) in FILES.items():
        target = directory / path
        if target.is_file() and target.stat().st_size == size and _sha256(target) == expected:
            done += size
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + '.part')
        digest = hashlib.sha256()
        reported = 0
        try:
            with urllib.request.urlopen(f'{base_url}/{repository}/resolve/{revision}/{path}', timeout=60) as response, \
                    temp.open('wb') as out:
                while block := response.read(1 << 20):
                    out.write(block)
                    digest.update(block)
                    done += len(block)
                    if progress and done - reported >= 8 << 20:
                        progress(done, DOWNLOAD_BYTES)
                        reported = done
        except (OSError, http.client.HTTPException) as exc:  # IncompleteRead is not an OSError
            temp.unlink(missing_ok=True)
            raise ProviderUnavailable('model_download_failed:' + type(exc).__name__)
        if digest.hexdigest() != expected:
            temp.unlink(missing_ok=True)
            raise ProviderUnavailable('model_checksum_mismatch')
        os.replace(temp, target)
    if progress:
        progress(DOWNLOAD_BYTES, DOWNLOAD_BYTES)
    return directory


def load(language='tr', directory=None):
    """The model with the settings measured on 56 meetings: beam search of 5, voice detection,
    no carried-over text between windows (it fed the loops), four threads."""
    directory = directory or model_dir()
    if not installed(directory):
        raise ProviderUnavailable('whisper_not_installed')
    try:
        from pywhispercpp.model import Model
    except ImportError:
        raise ProviderUnavailable('whisper_runtime_missing')
    try:
        return Model(str(directory / MODEL_FILE), params_sampling_strategy=1, redirect_whispercpp_logs_to=os.devnull,
                     n_threads=4, language=language, vad=True, vad_model_path=str(directory / VAD_FILE),
                     n_max_text_ctx=0, beam_search={'beam_size': 5, 'patience': -1.0},
                     print_progress=False, print_realtime=False)
    except Exception as exc:
        raise ProviderUnavailable('whisper_load_failed:' + type(exc).__name__)


def read_wav(path):
    """The app's recording of one side: 16 kHz mono 16-bit, as float32 samples."""
    import numpy as np
    try:
        with wave.open(str(path), 'rb') as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (RATE, 1, 2):
                raise ProviderUnavailable('whisper_audio_format')
            data = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        raise ProviderUnavailable('whisper_audio_format')
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def _words(text):
    # 'I' and 'İ' lower to ı and i in Turkish, not to i and i + a dot.
    return re.findall(r'\w+', text.replace('I', 'ı').replace('İ', 'i').lower())


def longest_run(text):
    """How many times in a row the most repeated 1-4 word phrase comes."""
    w, best = _words(text), 1
    for n in (1, 2, 3, 4):
        for i in range(len(w)):
            k = 1
            while w[i + k * n:i + (k + 1) * n] == w[i:i + n]:
                k += 1
            best = max(best, k)
    return best


def collapse(text):
    """The text with each loop said once."""
    tokens = text.split()
    norm = [' '.join(_words(t)) for t in tokens]
    out, i = [], 0
    while i < len(tokens):
        for n in (1, 2, 3, 4):
            k = 1
            while norm[i + k * n:i + (k + 1) * n] == norm[i:i + n]:
                k += 1
            if k >= LOOP_RUN:
                out += tokens[i:i + n]
                i += k * n
                break
        else:
            out.append(tokens[i])
            i += 1
    return ' '.join(out)


def made_up(text):
    return len(_words(text)) <= MADE_UP_WORDS and bool(
        MADE_UP.search(text.lower()) or MADE_UP.search(text.replace('I', 'ı').replace('İ', 'i').lower()))


def _run(model, audio, start, end, abort=None):
    """(start, end, text) in seconds of the recording, for the samples from `start` to `end`."""
    piece = audio[int(start * RATE):int(end * RATE)]
    if len(piece) < MIN_SECONDS * RATE:
        return []
    out = []
    for s in model.transcribe(piece, abort_callback=abort):
        text = ' '.join(s.text.split())
        if text:
            out.append((start + s.t0 / 100, start + s.t1 / 100, text))
    return out


def guard(model, audio, segments):
    """Each looping line transcribed again with LOOP_PAD seconds around it, widened to the lines
    it cuts; when the loop comes back, the repeats are collapsed."""
    total = len(audio) / RATE
    for t0, t1, text in [s for s in segments if longest_run(s[2]) >= LOOP_RUN]:
        inside = [s for s in segments if s[1] > t0 - LOOP_PAD and s[0] < t1 + LOOP_PAD]
        if (t0, t1, text) not in inside:
            continue  # already replaced with a window around an earlier loop
        start = max(0.0, min([t0 - LOOP_PAD] + [s[0] for s in inside]))
        end = min(total, max([t1 + LOOP_PAD] + [s[1] for s in inside]))
        fresh = _run(model, audio, start, end)
        if fresh and max(longest_run(s[2]) for s in fresh) < LOOP_RUN:
            segments = [s for s in segments if s not in inside] + fresh
        else:
            segments = [(a, b, collapse(x)) if (a, b, x) == (t0, t1, text) else (a, b, x) for a, b, x in segments]
    return sorted(segments)


def transcribe(model, audio, busy=None):
    """One side of a meeting as (start, end, text). While `busy()` is true (a call has the
    microphone) whisper stops within a check and goes on from its last line when it is free."""
    total = len(audio) / RATE
    segments, at, paused = [], 0.0, [0.0]
    if busy:
        held, busy = busy, lambda: paused[0] < MAX_PAUSE and bool(held())
    while total - at >= MIN_SECONDS:
        stopped, last = [False], [None]

        def abort():
            if stopped[0]:
                return True
            now = time.monotonic()
            if last[0] is not None and now - last[0] < CHECK_SECONDS:
                return False
            last[0] = now
            stopped[0] = bool(busy())
            return stopped[0]
        part = _run(model, audio, at, total, abort if busy else None)
        segments += part
        if not stopped[0]:
            break
        at = part[-1][1] if part else at
        while busy():
            time.sleep(WAIT_SECONDS)
            paused[0] += WAIT_SECONDS
    return [s for s in guard(model, audio, segments) if not made_up(s[2])]
