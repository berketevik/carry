import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from carry import cli, update
from carry.errors import CarryError


def git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


class Dist:
    def __init__(self, direct):
        self.direct = direct

    def read_text(self, name):
        return json.dumps(self.direct) if self.direct is not None else None


class InstallationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def detect(self, direct, prefix=None):
        with mock.patch.object(update.metadata, 'distribution', return_value=Dist(direct)), \
                mock.patch.object(update.sys, 'prefix', str(prefix or self.base / 'venv')):
            return update.installation()

    def test_an_editable_checkout_is_a_source_install(self):
        (self.base / 'carry' / '.git').mkdir(parents=True)
        kind, path = self.detect(dict(url=(self.base / 'carry').as_uri(), dir_info=dict(editable=True)))
        self.assertEqual((kind, path), ('source', self.base / 'carry'))

    def test_a_uv_tool_env_is_recognised_by_its_receipt(self):
        (self.base / 'tool').mkdir()
        (self.base / 'tool' / 'uv-receipt.toml').write_text('[tool]\n')
        url = 'git+https://github.com/berketevik/carry'
        self.assertEqual(self.detect(dict(url=url, vcs_info=dict(vcs='git', commit_id='abc')), self.base / 'tool'),
                         ('uv_tool', url))

    def test_anything_else_is_left_alone(self):
        self.assertEqual(self.detect(None), ('other', None))
        with mock.patch.object(update, 'installation', return_value=('other', None)):
            with self.assertRaises(CarryError):
                update.update()


class SourceUpdateTest(unittest.TestCase):
    """A real origin and checkout; only the reinstall command is faked."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.origin, self.checkout, self.other = base / 'origin.git', base / 'carry', base / 'other'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(self.origin)], check=True)
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(self.other)], check=True, capture_output=True)
        git(self.other, 'config', 'user.email', 't@example.com')
        git(self.other, 'config', 'user.name', 'T')
        self.commit('pyproject.toml', '[project]\nname = "carry"\n')
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(self.checkout)], check=True)
        patcher = mock.patch.object(update, 'installation', return_value=('source', self.checkout))
        patcher.start()
        self.addCleanup(patcher.stop)

    def commit(self, name, text):
        (self.other / name).write_text(text)
        git(self.other, 'add', name)
        git(self.other, 'commit', '-q', '-m', name)
        git(self.other, 'push', '-q', 'origin', 'HEAD:main')

    def run_update(self):
        real = subprocess.run
        calls = []

        def fake(command, *args, **kwargs):
            if command[0] == 'git':
                return real(command, *args, **kwargs)
            calls.append(command)
            return mock.Mock(returncode=0, stdout='', stderr='')
        with mock.patch.object(update.subprocess, 'run', side_effect=fake):
            return update.update(), calls

    def test_check_reports_how_far_behind_the_checkout_is(self):
        self.commit('a.py', 'x = 1\n')
        info = update.check()
        self.assertEqual((info['kind'], info['behind'], info['available']), ('source', 1, True))

    def test_a_code_change_is_pulled_without_a_reinstall(self):
        self.commit('a.py', 'x = 1\n')
        result, calls = self.run_update()
        self.assertTrue(result['updated'])
        self.assertFalse(result['reinstalled'])
        self.assertEqual(calls, [])
        self.assertTrue((self.checkout / 'a.py').is_file())

    def test_a_pyproject_change_reinstalls_the_checkout_with_its_extras(self):
        self.commit('pyproject.toml', '[project]\nname = "carry"\ndependencies = ["numpy"]\n')
        with mock.patch.object(update, 'installed_extras', return_value=['embed', 'yaml']):
            result, calls = self.run_update()
        self.assertTrue(result['reinstalled'])
        self.assertEqual(result['extras'], ['embed', 'yaml'])
        self.assertEqual(len(calls), 1)
        self.assertIn(f'{self.checkout}[embed,yaml]', calls[0])

    def test_without_extras_the_plain_checkout_is_reinstalled(self):
        self.commit('pyproject.toml', '[project]\nname = "carry"\ndependencies = ["numpy"]\n')
        with mock.patch.object(update, 'installed_extras', return_value=[]):
            result, calls = self.run_update()
        self.assertIn(str(self.checkout), calls[0])

    def test_nothing_new_changes_nothing(self):
        result, calls = self.run_update()
        self.assertFalse(result['updated'])
        self.assertEqual(calls, [])

    def test_a_local_commit_that_diverges_is_refused_not_merged(self):
        git(self.checkout, 'config', 'user.email', 't@example.com')
        git(self.checkout, 'config', 'user.name', 'T')
        (self.checkout / 'local.py').write_text('y = 2\n')
        git(self.checkout, 'add', 'local.py')
        git(self.checkout, 'commit', '-q', '-m', 'local')
        self.commit('a.py', 'x = 1\n')
        with self.assertRaises(CarryError):
            self.run_update()


class ExtrasTest(unittest.TestCase):
    def test_an_extra_counts_only_when_all_its_packages_are_installed(self):
        dist = mock.Mock(requires=['numpy<3,>=1.26; extra == "embed"', "PyYAML<7,>=6; extra == 'yaml'",
                                   'sentence-transformers<6,>=5; extra == "rerank"', 'torch<3,>=2; extra == "rerank"'],
                         metadata=mock.Mock(get_all=lambda key: ['embed', 'yaml', 'rerank', 'empty']))
        installed = {'numpy', 'PyYAML', 'torch'}

        def version(name):
            if name not in installed:
                raise update.metadata.PackageNotFoundError(name)
            return '1.0'
        with mock.patch.object(update.metadata, 'distribution', return_value=dist), \
                mock.patch.object(update.metadata, 'version', side_effect=version):
            self.assertEqual(update.installed_extras(), ['embed', 'yaml'])


class UvToolUpdateTest(unittest.TestCase):
    def test_uv_tool_upgrade_runs_and_the_new_version_is_read_back(self):
        replies = [mock.Mock(returncode=0, stdout='Updated carry v0.7.1 -> v0.8.0', stderr=''),
                   mock.Mock(returncode=0, stdout='0.8.0\n', stderr='')]
        with mock.patch.object(update, 'installation', return_value=('uv_tool', None)), \
                mock.patch.object(update, 'which', return_value='/bin/uv'), \
                mock.patch.object(update.metadata, 'version', return_value='0.7.1'), \
                mock.patch.object(update.subprocess, 'run', side_effect=replies) as run:
            result = update.update()
        self.assertEqual(run.call_args_list[0][0][0], ['/bin/uv', 'tool', 'upgrade', 'carry'])
        self.assertEqual((result['before'], result['after'], result['updated']), ('0.7.1', '0.8.0', True))


class CliTest(unittest.TestCase):
    def test_update_needs_no_workspace(self):
        with mock.patch.object(update, 'update', return_value=dict(kind='source', before='a', after='a', updated=False)), \
                mock.patch('builtins.print') as out:
            self.assertEqual(cli.main(['update']), 0)
        out.assert_called_with('carry is up to date')


if __name__ == '__main__':
    unittest.main()
