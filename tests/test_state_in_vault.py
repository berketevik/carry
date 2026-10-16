"""The standard layout: a vault's Carry files in `<vault>/.carry/`.

`settings.json` (shared, committed) holds what every device must search the same way; `state/`
(this device's, ignored by git) holds the rest. `carry state move` brings an existing setup there.
"""
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

from _support import HASHING
from carry import cli, harvest, shared, statedir
from carry.config import RetrievalConfig, SourceConfig, Workspace, device_values, discover, open_workspace
from carry.errors import CarryError, WorkspaceError
from carry.paths import walk_markdown

GIT = shutil.which('git')


def digest(*roots):
    out = {}
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                out[str(path)] = 'link:' + os.readlink(path)
            elif path.is_file():
                out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), '-c', 'user.email=t@example.com', '-c', 'user.name=t', *args],
                          capture_output=True, text=True, check=True).stdout


class Layout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / 'home'
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {'HOME': str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('CARRY_WORKSPACE', None)
        for name in ('_rewire_schedule', '_rewire_app'):
            patch = mock.patch.object(statedir, name, return_value=None)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(harvest, 'schedule_status', return_value={})
        patch.start()
        self.addCleanup(patch.stop)
        self.vault = self.base / 'My Vault'
        (self.vault / 'notes').mkdir(parents=True)
        (self.vault / 'notes' / 'a.md').write_text('---\ntype: thing\n---\nbody\n', encoding='utf-8')
        self.old = self.base / 'CarryState'

    def workspace(self, state=None, sources=None):
        return Workspace.create(state or self.old,
                                sources=sources or [SourceConfig('notes', self.vault, exclude=('+', 'x'))],
                                embedding=HASHING, retrieval=RetrievalConfig(top_k=9, auto_refresh=False))


class GuardAndDiscovery(Layout):
    def test_the_state_may_live_only_at_the_vaults_carry_state(self):
        ws = self.workspace(self.vault / '.carry' / 'state')
        self.assertEqual(ws.state_dir, (self.vault / '.carry' / 'state').resolve())
        for elsewhere in (self.vault / 'state', self.vault / '.carry' / 'other', self.vault / 'notes' / '.carry' / 'state'):
            with self.assertRaisesRegex(WorkspaceError, 'state_dir_inside_source_root'):
                self.workspace(elsewhere)

    def test_the_indexer_never_reads_the_carry_folder(self):
        carry = self.vault / '.carry' / 'state' / 'github' / 'team' / 'snap'
        carry.mkdir(parents=True)
        (carry / 'team-note.md').write_text('x', encoding='utf-8')
        (self.vault / '.carry' / 'loose.md').write_text('x', encoding='utf-8')
        self.assertEqual([rel for rel, _ in walk_markdown(self.vault)], ['notes/a.md'])

    def test_discovery_prefers_the_flag_then_the_variable_then_the_vault(self):
        own = self.vault / '.carry' / 'state'
        self.workspace(own)
        (self.base / 'notes-o').mkdir()
        other = self.workspace(self.base / 'Other', sources=[SourceConfig('o', self.base / 'notes-o')])
        inside = self.vault / 'notes'
        self.assertEqual(discover(inside), own.resolve())
        with mock.patch('os.getcwd', return_value=str(inside)):
            self.assertEqual(open_workspace().state_dir, own.resolve())
            with mock.patch.dict(os.environ, {'CARRY_WORKSPACE': str(other.state_dir)}):
                self.assertEqual(open_workspace().state_dir, other.state_dir)
                self.assertEqual(open_workspace(str(own)).state_dir, own.resolve())

    def test_a_pointer_is_used_when_the_vault_has_no_state_of_its_own(self):
        ws = self.workspace()
        (self.vault / '.carry').mkdir()
        (self.vault / '.carry' / 'local.json').write_text(json.dumps({'workspace': str(ws.state_dir)}), encoding='utf-8')
        self.assertEqual(Path(discover(self.vault / 'notes')).resolve(), ws.state_dir)
        self.assertIsNone(discover(self.base / 'home'))


class SharedSettings(Layout):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace(self.vault / '.carry' / 'state')
        self.path = self.vault / '.carry' / 'settings.json'

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_an_in_vault_workspace_writes_its_shared_values(self):
        data = self.read()
        self.assertEqual(data['retrieval']['top_k'], 9)
        self.assertEqual(data['exclude'], ['+', 'x'])
        self.assertEqual(set(data['embedding']), {'model', 'prefixes', 'dim'})
        self.assertNotIn('endpoint', json.dumps(data))
        self.assertNotIn('provider', data['embedding'])

    def test_the_shared_file_wins_over_this_devices_values(self):
        data = self.read()
        data['retrieval']['top_k'] = 11
        data['exclude'] = ['+']
        self.path.write_text(json.dumps(data), encoding='utf-8')
        loaded = Workspace.load(self.ws.state_dir)
        self.assertEqual(loaded.retrieval.top_k, 11)
        self.assertEqual(loaded.source('notes').exclude, ('+',))
        self.assertEqual(device_values(loaded)['retrieval']['top_k'], 9)
        info = statedir.describe(loaded)
        self.assertEqual(info['differ']['retrieval.top_k'], dict(shared=11, device=9))
        self.assertTrue(info['in_vault'])

    def test_a_changed_setting_is_written_back_for_the_other_devices(self):
        replace(self.ws, retrieval=replace(self.ws.retrieval, top_k=13)).save()
        self.assertEqual(self.read()['retrieval']['top_k'], 13)
        self.assertEqual(Workspace.load(self.ws.state_dir).retrieval.top_k, 13)

    def test_unknown_keys_and_bad_values_are_reported_and_skipped(self):
        data = self.read()
        data['retrieval']['top_k'] = 'many'
        data['embedding']['provider'] = 'ollama'
        data['colour'] = 'blue'
        self.path.write_text(json.dumps(data), encoding='utf-8')
        loaded = Workspace.load(self.ws.state_dir)
        self.assertEqual(loaded.retrieval.top_k, 9)
        self.assertEqual(loaded.embedding.provider, 'hashing')
        warnings = statedir.describe(loaded)['warnings']
        self.assertIn('shared_settings_invalid:retrieval.top_k', warnings)
        self.assertIn('shared_settings_unknown_key:embedding.provider', warnings)
        self.assertIn('shared_settings_unknown_key:colour', warnings)

    def test_language_comes_from_the_shared_file_then_the_template_stamp(self):
        stamp = self.vault / '.carry' / 'vault.json'
        stamp.write_text(json.dumps({'language': 'Turkish'}), encoding='utf-8')
        self.assertEqual(harvest.draft_language(self.ws, self.vault), 'Turkish')
        harvest.set_draft_language(self.ws, 'English')
        self.assertEqual(self.read()['language'], 'English')
        self.assertEqual(harvest.draft_language(self.ws, self.vault), 'English')

    def test_a_workspace_outside_any_vault_writes_no_shared_file(self):
        (self.base / 'plain').mkdir()
        ws = self.workspace(self.base / 'elsewhere', sources=[SourceConfig('notes', self.base / 'plain')])
        replace(ws, retrieval=replace(ws.retrieval, top_k=4)).save()
        self.assertFalse((self.base / 'plain' / '.carry').exists())


class CloudPaths(Layout):
    def test_sync_services_are_recognised(self):
        for rel, name in (('Library/Mobile Documents/com~apple~CloudDocs/v', 'iCloud'),
                          ('Library/CloudStorage/OneDrive-Ingage/v', 'OneDrive'),
                          ('Library/CloudStorage/GoogleDrive-me@x/v', 'Google Drive'),
                          ('Dropbox/v', 'Dropbox')):
            self.assertEqual(statedir.cloud_service(self.home / rel), name)
        self.assertIsNone(statedir.cloud_service(self.vault))

    def test_a_cloud_vault_gets_the_shared_file_but_keeps_the_state_outside(self):
        cloud = self.home / 'Library' / 'Mobile Documents' / 'com~apple~CloudDocs' / 'Vault'
        cloud.mkdir(parents=True)
        ws = self.workspace(sources=[SourceConfig('notes', cloud)])
        report = statedir.move(ws, cloud)
        self.assertEqual(report['reason'], 'cloud:iCloud')
        self.assertFalse(report['moved'])
        self.assertTrue((cloud / '.carry' / 'settings.json').is_file())
        self.assertFalse((cloud / '.carry' / 'state').exists())
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())
        self.assertEqual(json.loads((self.old / 'workspace.json').read_text())['home'], 'notes')


@unittest.skipUnless(GIT, 'git is not installed')
class Move(Layout):
    def setUp(self):
        super().setUp()
        git(self.vault, 'init', '-q')
        self.target = self.vault / '.carry' / 'state'
        cache = self.old / 'github' / 'team'
        self.snaps = {}
        for stamp in (10, 20, 50, 100):
            snap = cache / f'{"a" * 40}-{stamp}'
            snap.mkdir(parents=True)
            (snap / 'team.md').write_text('---\ntype: thing\n---\nteam\n', encoding='utf-8')
            (cache / f'{snap.name}.commits.json').write_text('{}', encoding='utf-8')
            self.snaps[stamp] = snap
        self.ws = self.workspace(sources=[
            SourceConfig('notes', self.vault, exclude=('+', 'x')),
            SourceConfig('team', self.snaps[100], scope='team',
                         github=dict(repository='o/r', branch='main', folder='', commit='a' * 40))])
        old = str(self.old)
        (self.vault / '.mcp.json').write_text(json.dumps({'mcpServers': {'carry': {
            'command': '/py', 'args': ['-I', '-m', 'carry.mcp_server', '--workspace', old, '--client', 'claude']}}}),
            encoding='utf-8')
        hooks = {'hooks': {
            'SessionEnd': [{'hooks': [{'type': 'command', 'command': f'/py -I -m carry.cli --workspace {old} session end '
                                                                       f"--vault '{self.vault}'"}]}],
            'PreCompact': [{'hooks': [{'type': 'command', 'command': f'/py -I -m carry.cli --workspace {old} session checkpoint || true'}]}],
            'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': 'python3 hook_capture.py'}]}]}}
        (self.vault / '.claude').mkdir()
        (self.vault / '.claude' / 'settings.local.json').write_text(json.dumps(hooks), encoding='utf-8')
        (self.home / '.codex').mkdir()
        (self.home / '.codex' / 'hooks.json').write_text(json.dumps(hooks), encoding='utf-8')
        (self.home / '.codex' / 'config.toml').write_text(
            '[mcp_servers.carry]\ncommand = "/py"\nargs = ' + json.dumps(['-I', '-m', 'carry.mcp_server', '--workspace', old]) + '\n',
            encoding='utf-8')
        (self.vault / '.gitignore').write_text('.mcp.json\n.claude/settings.local.json\n', encoding='utf-8')

    def test_the_state_moves_in_and_everything_keeps_working(self):
        report = statedir.move(self.ws, self.vault, wait=1)
        new = self.target.resolve()
        self.assertTrue(report['moved'])
        self.assertEqual((self.vault / '.carry' / '.gitignore').read_text(), statedir.GITIGNORE)
        self.assertTrue(self.old.is_symlink())
        self.assertEqual(self.old.resolve(), new)
        loaded = Workspace.load(self.old)
        self.assertEqual(loaded.state_dir, new)
        self.assertTrue(str(loaded.source('team').root).startswith(str(new)))
        # git sees the shared files, never the device ones
        untracked = git(self.vault, 'ls-files', '--others', '--exclude-standard').split('\n')
        self.assertIn('.carry/settings.json', untracked)
        self.assertIn('.carry/.gitignore', untracked)
        self.assertFalse([u for u in untracked if u.startswith('.carry/state') or u == '.carry/local.json'])
        settings = json.loads((self.vault / '.carry' / 'settings.json').read_text())
        self.assertEqual(settings['retrieval']['top_k'], 9)
        self.assertEqual(settings['exclude'], ['+', 'x'])
        # wiring now names the new place, quoted where it is a shell word; other hooks untouched
        mcp = json.loads((self.vault / '.mcp.json').read_text())
        self.assertIn(str(new), mcp['mcpServers']['carry']['args'])
        for hooks_file in (self.vault / '.claude' / 'settings.local.json', self.home / '.codex' / 'hooks.json'):
            data = json.loads(hooks_file.read_text())
            end = data['hooks']['SessionEnd'][0]['hooks'][0]['command']
            self.assertIn(str(new), shlex.split(end))
            self.assertIn(str(self.vault), shlex.split(end))
            checkpoint = data['hooks']['PreCompact'][0]['hooks'][0]['command']
            self.assertTrue(checkpoint.endswith(' || true'))
            self.assertIn(str(new), shlex.split(checkpoint))
            self.assertEqual(data['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'], 'python3 hook_capture.py')
            self.assertTrue(hooks_file.with_name(hooks_file.name + '.bak-carry').is_file())
        self.assertIn(json.dumps(str(new)), (self.home / '.codex' / 'config.toml').read_text())
        self.assertEqual(json.loads((self.vault / '.carry' / 'local.json').read_text())['workspace'], str(new))
        # old GitHub snapshots go: the current one and the one before it stay
        kept = sorted(p.name for p in (new / 'github' / 'team').iterdir() if p.is_dir())
        self.assertEqual(kept, sorted([self.snaps[50].name, self.snaps[100].name]))
        self.assertFalse((new / 'github' / 'team' / f'{self.snaps[10].name}.commits.json').exists())

    def test_a_second_run_changes_nothing(self):
        statedir.move(self.ws, self.vault, wait=1)
        before = digest(self.vault, self.home)
        report = statedir.move(Workspace.load(self.old), self.vault, wait=1)
        self.assertTrue(report.get('already'))
        self.assertEqual(digest(self.vault, self.home), before)

    def test_a_dry_run_changes_nothing_and_shows_the_settings(self):
        before = digest(self.vault, self.old, self.home)
        lines = []
        report = statedir.move(self.ws, self.vault, dry_run=True, out=lines.append)
        self.assertEqual(digest(self.vault, self.old, self.home), before)
        self.assertTrue(any('move the state' in line for line in lines))
        self.assertEqual(report['settings']['retrieval']['top_k'], 9)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(['--workspace', str(self.old), 'state', 'move', '--vault', str(self.vault),
                                       '--dry-run']), cli.EXIT_OK)
        self.assertIn('"top_k": 9', out.getvalue())
        self.assertEqual(digest(self.vault, self.old, self.home), before)

    def test_device_files_already_in_git_stop_the_move(self):
        (self.vault / '.carry').mkdir()
        (self.vault / '.carry' / 'local.json').write_text('{}', encoding='utf-8')
        git(self.vault, 'add', '-f', '.carry/local.json')
        git(self.vault, 'commit', '-q', '-m', 'oops')
        with self.assertRaisesRegex(CarryError, 'state_move_not_ignored'):
            statedir.move(self.ws, self.vault, wait=1)
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())

    def test_settings_from_another_device_are_adopted(self):
        (self.vault / '.carry').mkdir()
        (self.vault / '.carry' / 'settings.json').write_text(json.dumps(
            {'version': 1, 'retrieval': {'top_k': 15}, 'exclude': ['+']}), encoding='utf-8')
        report = statedir.move(self.ws, self.vault, wait=1)
        self.assertEqual(report['adopted']['retrieval.top_k'], dict(shared=15, device=9))
        loaded = Workspace.load(self.target)
        self.assertEqual(loaded.retrieval.top_k, 15)
        self.assertEqual(loaded.source('notes').exclude, ('+',))

    def test_shared_only_writes_the_two_files_and_moves_nothing(self):
        report = statedir.move(self.ws, self.vault, shared_only=True)
        self.assertFalse(report['moved'])
        self.assertTrue((self.vault / '.carry' / 'settings.json').is_file())
        self.assertTrue((self.vault / '.carry' / '.gitignore').is_file())
        self.assertFalse(self.target.exists())
        self.assertFalse(self.old.is_symlink())
        data = json.loads((self.vault / '.carry' / 'settings.json').read_text())
        data['retrieval']['top_k'] = 7
        (self.vault / '.carry' / 'settings.json').write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(Workspace.load(self.old).retrieval.top_k, 7)  # home named in workspace.json

    def test_shared_only_into_a_copy_that_is_not_a_source(self):
        copy = self.base / 'checkout'
        copy.mkdir()
        statedir.move(self.ws, copy, shared_only=True)
        self.assertTrue((copy / '.carry' / 'settings.json').is_file())
        with self.assertRaisesRegex(CarryError, 'state_move_vault_not_a_source'):
            statedir.move(self.ws, copy)

    def test_no_language_is_invented_and_the_flag_sets_one(self):
        statedir.move(self.ws, self.vault, shared_only=True)
        self.assertNotIn('language', json.loads((self.vault / '.carry' / 'settings.json').read_text()))
        statedir.move(Workspace.load(self.old), self.vault, wait=1, language='Turkish')
        self.assertEqual(json.loads((self.vault / '.carry' / 'settings.json').read_text())['language'], 'Turkish')
        from carry import context
        self.assertIn('Carry durum paketi', context.pack(self.vault))

    def test_a_file_that_names_the_path_only_inside_other_words_is_left_alone(self):
        rules = self.base / 'rules'
        rules.mkdir()
        text = '{"permissions":{"allow":["Read(/' + str(self.old) + '/**)","Bash(ls ' + str(self.old) + ')"]}}'
        (rules / '.mcp.json').write_text(text, encoding='utf-8')
        changed = statedir.rewire(rules, str(self.old), str(self.target), dry_run=True)
        self.assertNotIn(str(rules / '.mcp.json'), changed)
        self.assertEqual((rules / '.mcp.json').read_text(encoding='utf-8'), text)

    def test_a_team_repository_never_holds_the_state(self):
        with self.assertRaisesRegex(CarryError, 'state_move_team_source'):
            statedir.move(self.ws, self.snaps[100])


class VaultInit(Layout):
    def test_a_new_vault_gets_its_state_in_carry_state(self):
        target = self.base / 'New Vault'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['vault', 'init', str(target), '--no-git']), cli.EXIT_OK)
        state = target / '.carry' / 'state'
        self.assertTrue((state / 'workspace.json').is_file())
        self.assertEqual((target / '.carry' / '.gitignore').read_text(), statedir.GITIGNORE)
        settings = json.loads((target / '.carry' / 'settings.json').read_text())
        self.assertEqual(settings['language'], 'Turkish')
        mcp = json.loads((target / '.mcp.json').read_text())
        self.assertIn(str(state.resolve()), mcp['mcpServers']['carry']['args'])
        self.assertEqual(Workspace.load(state).source('vault').root, target.resolve())

    def test_no_workspace_keeps_the_template_only(self):
        target = self.base / 'Plain'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['vault', 'init', str(target), '--no-git', '--no-workspace']), cli.EXIT_OK)
        self.assertFalse((target / '.carry' / 'state').exists())
        self.assertFalse((target / '.mcp.json').exists())


class Wizard(Layout):
    def test_setup_puts_the_state_in_the_new_vault_and_links_the_old_default(self):
        from carry import jev, setup_wizard
        target = self.base / 'Vault'
        with redirect_stdout(io.StringIO()), mock.patch.object(jev, 'api_key', return_value=None):
            code = setup_wizard.run(assume_yes=True, vault_path=str(target), animation=False, app=False,
                                    semantic_default=False)
        self.assertEqual(code, 0)
        state = (target / '.carry' / 'state').resolve()
        self.assertTrue((state / 'workspace.json').is_file())
        self.assertTrue((self.home / 'CarryState').is_symlink())
        self.assertEqual((self.home / 'CarryState').resolve(), state)
        mcp = json.loads((target / '.mcp.json').read_text())
        self.assertIn(str(state), mcp['mcpServers']['carry']['args'])
        self.assertTrue((target / '.carry' / 'settings.json').is_file())
        if GIT:
            ignored = subprocess.run(['git', '-C', str(target), 'check-ignore', '-q', '.carry/state/index.db'])
            self.assertEqual(ignored.returncode, 0)


class Snapshots(Layout):
    def test_prune_keeps_the_current_and_the_previous_snapshot(self):
        cache = self.old / 'github' / 'team'
        snaps = []
        for stamp in (5, 7, 9):
            snap = cache / f'{"b" * 40}-{stamp}'
            snap.mkdir(parents=True)
            snaps.append(snap)
        (cache / 'download-xyz').mkdir()
        ws = self.workspace(sources=[SourceConfig('notes', self.vault),
                                     SourceConfig('team', snaps[0], scope='team',
                                                  github=dict(repository='o/r', branch='main', folder=''))])
        removed = statedir.prune_snapshots(ws)
        # current is the oldest here (5); the newest other (9) stays as the previous one
        self.assertEqual(removed, [f'team/{snaps[1].name}'])
        self.assertTrue(snaps[0].is_dir() and snaps[2].is_dir() and (cache / 'download-xyz').is_dir())


if __name__ == '__main__':
    unittest.main()
