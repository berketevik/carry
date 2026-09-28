"""Platform helpers of the Windows port: program lookup, stdio, the nightly entry point, key store."""
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from _support import WorkspaceCase
from carry import background, console, desktop, maintenance, nightly
from carry.errors import CarryError
from carry.filelock import try_lock

WINDOWS = unittest.skipUnless(sys.platform == 'win32', 'Windows only')


@WINDOWS
class ProgramLookupTest(unittest.TestCase):
    """Windows looks in the current folder before PATH; a vault is the hook's current folder."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.here, self.bin = Path(self.tmp.name) / 'vault', Path(self.tmp.name) / 'bin'
        self.here.mkdir()
        self.bin.mkdir()
        cwd = os.getcwd()
        os.chdir(self.here)
        self.addCleanup(os.chdir, cwd)

    def test_a_program_in_the_current_folder_is_never_found(self):
        (self.here / 'tool.cmd').write_text('@echo planted', encoding='utf-8')
        with mock.patch.dict(os.environ, {'PATH': str(self.bin)}):
            self.assertIsNone(background.which('tool'))
            (self.bin / 'tool.exe').write_bytes(b'')
            self.assertEqual(os.path.normcase(background.which('tool')), os.path.normcase(self.bin / 'tool.exe'))

    def test_the_client_command_is_absolute_or_refused(self):
        with mock.patch.object(desktop, 'executable_for', return_value='claude'):
            with self.assertRaisesRegex(CarryError, 'client_not_found'):
                desktop.command_for('claude')


@WINDOWS
class MaintenanceHandoverTest(WorkspaceCase):
    def test_a_job_being_handed_to_its_worker_is_running(self):
        maintenance.job_progress(self.workspace, state='running', stage='queued')
        with (self.workspace.state_dir / 'maintenance.start.lock').open('a') as handover:
            self.assertTrue(try_lock(handover))
            self.assertTrue(maintenance.is_running(self.workspace))
            self.assertEqual(maintenance.job_status(self.workspace)['state'], 'running')
            self.assertFalse(maintenance.start(self.workspace)['started'])
        self.assertEqual(maintenance.job_status(self.workspace)['state'], 'interrupted')


class ConsoleTest(unittest.TestCase):
    def test_utf8_keeps_each_stream_error_handler(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='cp1254', errors='backslashreplace')
        with mock.patch.object(console.sys, 'stderr', stream), mock.patch.object(console.sys, 'stdin', None), \
                mock.patch.object(console.sys, 'stdout', None):
            console.use_utf8()
            self.assertEqual((stream.encoding, stream.errors), ('utf-8', 'backslashreplace'))
            stream.write('ş \udcff')
            stream.flush()
        self.assertEqual(raw.getvalue(), 'ş \\udcff'.encode('utf-8'))


class NightlyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.argv = ['--workspace', self.tmp.name, 'harvest']

    def test_a_windowless_run_restarts_under_a_hidden_console(self):
        # pythonw has no console, so each program it starts would open a visible one.
        child = mock.Mock(**{'wait.return_value': 3})
        with mock.patch.object(nightly.sys, 'platform', 'win32'), \
                mock.patch.object(nightly.sys, 'executable', r'C:\py\pythonw.exe'), \
                mock.patch.object(nightly, 'detached', return_value=child) as start, \
                mock.patch.object(nightly, 'main') as main:
            self.assertEqual(nightly.run(self.argv), 3)
        main.assert_not_called()
        args = start.call_args[0][0]
        self.assertEqual(args[0], r'C:\py\python.exe')
        self.assertEqual(args[-3:], self.argv)

    def test_a_crash_is_written_to_the_log(self):
        before = sys.stdout, sys.stderr
        with mock.patch.object(nightly, 'main', side_effect=RuntimeError('boom')), \
                mock.patch.object(nightly.sys, 'executable', '/usr/bin/python3'):
            with self.assertRaises(RuntimeError):
                nightly.run(self.argv)
        log = (Path(self.tmp.name) / 'harvest.log').read_text(encoding='utf-8')
        self.assertIn('RuntimeError: boom', log)
        self.assertEqual((sys.stdout, sys.stderr), before)


@WINDOWS
class KeyStoreTest(unittest.TestCase):
    def test_an_unreadable_credential_is_no_key(self):
        from carry import jev, wincred
        with mock.patch.dict(os.environ, {'TYPESAFE_API_KEY': ''}), \
                mock.patch.object(wincred, 'read', side_effect=UnicodeDecodeError('utf-16-le', b'x', 0, 1, 'odd')):
            self.assertIsNone(jev.api_key())


if __name__ == '__main__':
    unittest.main()
