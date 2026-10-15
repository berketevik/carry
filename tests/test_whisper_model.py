"""Whisper after the meeting: verified download, the loop guard, made-up lines, pausing while busy."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np

import _support  # noqa: F401
from carry import whisper_model as wm
from carry.errors import ProviderUnavailable


class Segment:
    def __init__(self, t0, t1, text):
        self.t0, self.t1, self.text = round(t0 * 100), round(t1 * 100), text


class FakeModel:
    """Answers by the slice it is given: `script(start, end, abort)` returns (t0, t1, text) in the slice's own seconds."""
    def __init__(self, script):
        self.script, self.calls = script, []

    def transcribe(self, audio, abort_callback=None):
        self.calls.append(len(audio) / wm.RATE)
        return [Segment(*s) for s in self.script(len(self.calls), len(audio) / wm.RATE, abort_callback)]


LOOP = 'Şimdi ' + 'bunu yapalım ' * 8 + 'dedik.'


class WhisperModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def serve(self, files):
        """A file:// mirror of each pinned repository path, and FILES pointing at it."""
        table = {}
        for path, data in files.items():
            repository, revision = 'org/repo-' + path[:3], 'abc123'
            target = self.base / 'mirror' / repository / 'resolve' / revision / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            table[path] = (repository, revision, len(data), hashlib.sha256(data).hexdigest())
        return (self.base / 'mirror').as_uri(), table

    def test_download_verifies_both_files_from_their_own_repositories(self):
        url, table = self.serve({'model.bin': b'model', 'vad.bin': b'vad'})
        target = self.base / 'models'
        with patch.object(wm, 'FILES', table), patch.object(wm, 'DOWNLOAD_BYTES', 8):
            self.assertFalse(wm.installed(target))
            seen = []
            wm.download(progress=lambda done, total: seen.append((done, total)), directory=target, base_url=url)
            self.assertTrue(wm.installed(target))
            self.assertEqual(seen[-1], (8, 8))
            with patch('urllib.request.urlopen', side_effect=AssertionError('no second download')):
                wm.download(directory=target, base_url=url)

    def test_a_checksum_mismatch_leaves_nothing_behind(self):
        url, table = self.serve({'model.bin': b'model'})
        repository, revision, size, _ = table['model.bin']
        table['model.bin'] = (repository, revision, size, '0' * 64)
        target = self.base / 'models'
        with patch.object(wm, 'FILES', table), self.assertRaisesRegex(ProviderUnavailable, 'checksum'):
            wm.download(directory=target, base_url=url)
        self.assertEqual([p for p in target.rglob('*') if p.is_file()], [])

    def test_the_pinned_files_are_the_ones_measured(self):
        self.assertEqual(wm.FILES[wm.MODEL_FILE][2:], (874188075, '317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1'))
        self.assertEqual(wm.FILES[wm.VAD_FILE][2:], (885098, '2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987'))

    def test_a_wav_must_be_16_khz_mono_16_bit(self):
        good, bad = self.base / 'good.wav', self.base / 'bad.wav'
        for path, rate in ((good, 16000), (bad, 44100)):
            with wave.open(str(path), 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
                w.writeframes(np.array([0, 16384, -16384], dtype=np.int16).tobytes())
        self.assertEqual(wm.read_wav(good).tolist(), [0.0, 0.5, -0.5])
        with self.assertRaisesRegex(ProviderUnavailable, 'audio_format'):
            wm.read_wav(bad)

    def test_a_repeated_phrase_is_found_and_collapsed(self):
        self.assertEqual(wm.longest_run(LOOP), 8)
        self.assertEqual(wm.longest_run('evet evet tamam'), 2)
        self.assertEqual(wm.collapse(LOOP), 'Şimdi bunu yapalım dedik.')
        self.assertEqual(wm.collapse('evet evet tamam'), 'evet evet tamam')

    def test_made_up_lines_are_dropped_but_a_real_sentence_stays(self):
        for text in ('Altyazı M.K.', 'İzlediğiniz için teşekkür ederim.', 'Abone olmayı unutmayın!', 'Thanks for watching!'):
            self.assertTrue(wm.made_up(text), text)
        for text in ('Kampanyada yeni abone olmayı unutmayın diye bir hatırlatma mesajı da gönderelim mi?',
                     'Bütçeyi cuma günü onaylıyoruz.'):
            self.assertFalse(wm.made_up(text), text)

    def test_a_looping_stretch_is_transcribed_again_on_its_own(self):
        def script(call, length, abort):
            if call == 1:  # the whole channel, 100 s: the loop at 40-60 s, one line straddling its window
                return [(5, 20, 'Merhaba.'), (22, 38, 'Planı konuşalım.'), (40, 60, LOOP), (78, 85, 'Tamam.')]
            return [(1, 9, 'Planı konuşalım.'), (12, 30, 'Şimdi bunu yapalım.')]  # the window alone
        model = FakeModel(script)
        out = wm.transcribe(model, np.zeros(100 * wm.RATE, dtype=np.float32))
        # The window is 15 s around the loop, widened to the line it cuts: 22-75 s.
        self.assertEqual(model.calls, [100, 53])
        self.assertEqual([t for _, _, t in out], ['Merhaba.', 'Planı konuşalım.', 'Şimdi bunu yapalım.', 'Tamam.'])
        self.assertEqual([round(a) for a, _, _ in out], [5, 23, 34, 78])

    def test_a_loop_that_comes_back_is_collapsed(self):
        model = FakeModel(lambda call, length, abort: [(0, 20, LOOP)])
        out = wm.transcribe(model, np.zeros(30 * wm.RATE, dtype=np.float32))
        self.assertEqual(out, [(0, 20, 'Şimdi bunu yapalım dedik.')])

    def test_whisper_pauses_while_busy_and_goes_on_from_the_last_line(self):
        busy = iter([False, True, True, False])  # free, then a call starts, then it ends

        def script(call, length, abort):
            if call == 1:
                abort(); stopped = abort()  # the second check is a second later
                self.assertTrue(stopped)
                return [(0, 12, 'İlk satır.')]
            return [(0, 5, 'Kalanı.')]
        model = FakeModel(script)
        waits = []
        with patch.object(wm.time, 'monotonic', side_effect=[0, 2]), \
             patch.object(wm.time, 'sleep', side_effect=waits.append):
            out = wm.transcribe(model, np.zeros(60 * wm.RATE, dtype=np.float32), busy=lambda: next(busy))
        self.assertEqual(model.calls, [60, 48])
        self.assertEqual(out, [(0, 12, 'İlk satır.'), (12, 17, 'Kalanı.')])
        self.assertEqual(len(waits), 1)

    def test_an_app_that_never_lets_go_of_the_microphone_pauses_whisper_only_so_long(self):
        def script(call, length, abort):
            if call == 1:
                self.assertTrue(abort())
                return [(0, 12, 'İlk satır.')]
            self.assertFalse(abort())  # the cap is reached: no more pauses
            return [(0, 5, 'Kalanı.')]
        model = FakeModel(script)
        waits = []
        with patch.object(wm.time, 'sleep', side_effect=waits.append):
            out = wm.transcribe(model, np.zeros(60 * wm.RATE, dtype=np.float32), busy=lambda: True)
        self.assertEqual(out, [(0, 12, 'İlk satır.'), (12, 17, 'Kalanı.')])
        self.assertEqual(sum(waits), wm.MAX_PAUSE)

    def test_a_download_cut_short_leaves_nothing_behind(self):
        import http.client
        _, table = self.serve({'model.bin': b'model'})
        target = self.base / 'models'

        class Cut:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): raise http.client.IncompleteRead(b'mo', 3)
        with patch.object(wm, 'FILES', table), patch('urllib.request.urlopen', return_value=Cut()), \
                self.assertRaisesRegex(ProviderUnavailable, 'model_download_failed:IncompleteRead'):
            wm.download(directory=target, base_url='https://example.invalid')
        self.assertEqual([p for p in target.rglob('*') if p.is_file()], [])

    def test_made_up_lines_leave_the_transcript(self):
        model = FakeModel(lambda call, length, abort: [(0, 4, 'Bütçeyi konuştuk.'), (30, 34, 'Altyazı M.K.')])
        self.assertEqual(wm.transcribe(model, np.zeros(40 * wm.RATE, dtype=np.float32)), [(0, 4, 'Bütçeyi konuştuk.')])


if __name__ == '__main__':
    unittest.main()
