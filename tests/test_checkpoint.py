"""carry session checkpoint: a recap before the client compacts a chat, then only what came after."""
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import _support  # noqa: F401
from carry import cli, harvest, jev, vault, vaultview
from carry.config import Workspace
from carry.errors import CarryError
from carry.markdown import parse_frontmatter


def claude_line(kind, content, **extra):
    return json.dumps({'type': kind, 'message': {'role': kind, 'content': content}, **extra})


def said(i):
    return f'Decision number {i} is final.'


def items_for(window, language, chosen):
    """Fake extraction: one decision per exchange it is shown, neighbours included."""
    return [dict(type='decision', statement=f'Decision {e["i"]}.', exchange=e['i'], quote=f'Decision number {e["i"]} is final')
            for e in window]


class Stdin(io.StringIO):
    def isatty(self):
        return False


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / 'home'; self.home.mkdir()
        home = patch.object(Path, 'home', return_value=self.home); home.start(); self.addCleanup(home.stop)
        self.root = self.base / 'Vault'
        vault.apply(vault.plan(self.root, language='English', workspace=self.base / 'ws', attach=False), git=False)
        self.ws = Workspace.load(self.base / 'ws')
        self.project = harvest.claude_project_dir(self.root); self.project.mkdir(parents=True)
        for p in (patch.object(harvest, 'ask_agent', side_effect=CarryError('no_agent_in_tests')),
                  patch.object(jev, 'api_key', return_value=None)):
            p.start(); self.addCleanup(p.stop)

    def thread(self, name, n, lines=None, age=0):
        lines = lines if lines is not None else []
        for i in range(1, n + 1):
            lines += [claude_line('user', said(i)), claude_line('assistant', [{'type': 'text', 'text': 'Noted.'}])]
        path = self.project / (name + '.jsonl')
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        if age:
            os.utime(path, (time.time() - age, time.time() - age))
        return path

    def run_once(self, path, trigger, seen=None):
        def extract(window, language, chosen):
            if seen is not None:
                seen.append([e['i'] for e in window])
            return items_for(window, language, chosen)
        with patch.object(harvest, 'extract_window', side_effect=extract):
            return harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None, thread=str(path), trigger=trigger)

    def recapped(self, digest):
        text = (self.root / digest).read_text(encoding='utf-8')
        return sorted(int(n) for n in re.findall(r'\*\*decision:\*\* Decision (\d+)\.', text)), parse_frontmatter(text)[0]

    def state(self, name):
        return json.loads((self.ws.state_dir / harvest.STATE_NAME).read_text(encoding='utf-8'))[f'claude:{name}']

    # -- incremental -----------------------------------------------------------

    def test_a_checkpoint_then_the_end_recap_every_exchange_once(self):
        path = self.thread('cafe0001', 3)
        seen = []
        first = self.run_once(path, 'checkpoint', seen)
        self.assertEqual(seen, [[1, 2]])  # the newest exchange may still be answering: left for later
        self.assertEqual(self.state('cafe0001')['status'], 'checkpoint')
        self.assertEqual(self.state('cafe0001')['exchanges_done'], 2)
        self.thread('cafe0001', 5)
        seen.clear()
        second = self.run_once(path, 'end', seen)
        self.assertEqual(seen, [[1, 2, 3, 4, 5]])  # neighbours 1-2 are read, not recapped again
        one, front_one = self.recapped(first['digests'][0])
        two, front_two = self.recapped(second['digests'][0])
        self.assertEqual((one, two), ([1, 2], [3, 4, 5]))
        self.assertEqual((front_one['session']['trigger'], front_two['session']['trigger']), ('checkpoint', 'end'))
        self.assertEqual((front_one['session']['exchanges'], front_two['session']['exchanges']), ([1, 2], [3, 5]))
        self.assertEqual(self.state('cafe0001')['status'], 'done')
        raw = next(self.root.glob('sources/carry/session/*cafe0001*.md')).read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'(?m)^## Exchange (\d+)$', raw), ['1', '2', '3', '4', '5'])  # every quote is in it

    def test_the_held_back_exchange_is_recapped_by_the_end_even_with_no_new_write(self):
        path = self.thread('cafe0002', 3)
        self.run_once(path, 'checkpoint')
        end = self.run_once(path, 'end')  # same file, same mtime
        self.assertEqual(self.recapped(end['digests'][0])[0], [3])

    def test_a_checkpoint_needs_min_exchanges_of_new_finished_exchanges(self):
        path = self.thread('cafe0003', 2)
        with patch.object(harvest, 'MIN_EXCHANGES', 2):
            seen = []
            report = self.run_once(path, 'checkpoint', seen)
            self.assertEqual((seen, report['harvested']), ([], 0))
            self.assertFalse((self.ws.state_dir / harvest.STATE_NAME).exists())
            end = self.run_once(path, 'end')
        self.assertEqual(self.recapped(end['digests'][0])[0], [1, 2])

    def test_the_sweep_after_a_checkpoint_reads_only_what_came_after(self):
        path = self.thread('cafe0004', 4)
        self.run_once(path, 'checkpoint')
        os.utime(path, (time.time() - 3600, time.time() - 3600))
        with patch.object(harvest, 'extract_window', side_effect=items_for):
            sweep = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        self.assertEqual(self.recapped(sweep['digests'][0])[0], [4])
        self.assertEqual(self.recapped(sweep['digests'][0])[1]['session']['trigger'], 'sweep')

    def test_a_thread_that_filed_notes_stays_skipped_after_a_checkpoint(self):
        path = self.thread('cafe0005', 3)
        self.run_once(path, 'checkpoint')
        write = claude_line('assistant', [{'type': 'tool_use', 'id': 't9', 'name': 'Write',
                                           'input': {'file_path': str(self.root / 'notes/Plan.md')}}])
        lines = path.read_text(encoding='utf-8').splitlines() + [write]
        self.thread('cafe0005', 0, lines=lines)
        end = self.run_once(path, 'end')
        self.assertEqual((end['harvested'], end['skipped_filed']), (0, 1))

    def test_a_compaction_summary_is_not_an_exchange(self):
        lines = [claude_line('user', said(1)), claude_line('assistant', [{'type': 'text', 'text': 'Noted.'}]),
                 claude_line('user', said(2)),
                 json.dumps({'type': 'system', 'subtype': 'compact_boundary'}),
                 claude_line('user', 'This session is being continued from a previous conversation...',
                             isCompactSummary=True, isVisibleInTranscriptOnly=True),
                 claude_line('assistant', [{'type': 'text', 'text': 'Done with two.'}])]
        path = self.project / 'cafe0006.jsonl'; path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        exchanges, _ = harvest.read_thread('claude', path, self.root)
        self.assertEqual([e['i'] for e in exchanges], [1, 2])
        self.assertEqual(exchanges[1]['assistant'], 'Done with two.')  # the reply after compaction stays with its exchange

    # -- locking ---------------------------------------------------------------

    def test_a_busy_thread_is_left_to_the_run_that_holds_it(self):
        path = self.thread('cafe0007', 3)
        with harvest.thread_lock(self.ws.state_dir, 'claude:cafe0007') as got, \
                patch.dict(harvest.LOCK_WAIT, end=0.2):
            self.assertTrue(got)
            for trigger in ('checkpoint', 'end'):
                seen = []
                report = self.run_once(path, trigger, seen)
                self.assertEqual((report['busy'], report['harvested'], seen), (1, 0, []))
        self.assertFalse((self.ws.state_dir / harvest.STATE_NAME).exists())
        self.assertEqual(self.run_once(path, 'end')['harvested'], 1)

    def test_the_chat_end_waits_for_a_checkpoint_still_recapping(self):
        path = self.thread('cafe0008', 3)
        lock = harvest.thread_lock(self.ws.state_dir, 'claude:cafe0008')
        self.assertTrue(lock.__enter__())
        timer = threading.Timer(0.3, lambda: lock.__exit__(None, None, None)); timer.start(); self.addCleanup(timer.cancel)
        with patch.dict(harvest.LOCK_WAIT, end=10):
            report = self.run_once(path, 'end')
        self.assertEqual((report['busy'], report['harvested']), (0, 1))

    def test_two_runs_at_once_never_recap_an_exchange_twice(self):
        path = self.thread('cafe0009', 4)
        started, results = threading.Event(), []

        def slow(window, language, chosen):
            started.set(); time.sleep(0.3)
            return items_for(window, language, chosen)

        def go(trigger):
            results.append(harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None, thread=str(path), trigger=trigger))
        with patch.object(harvest, 'extract_window', side_effect=slow), patch.dict(harvest.LOCK_WAIT, end=10):
            first = threading.Thread(target=go, args=('checkpoint',)); first.start()
            started.wait(5)
            second = threading.Thread(target=go, args=('end',)); second.start()
            first.join(10); second.join(10)
        digests = [d for r in results for d in r['digests']]
        numbers = sorted(n for d in digests for n in self.recapped(d)[0])
        self.assertEqual(numbers, [1, 2, 3, 4])

    def test_the_draft_language_setting_keeps_the_threads_state(self):
        self.run_once(self.thread('cafe0010', 2), 'end')
        harvest.set_draft_language(self.ws, 'Turkish')
        state = json.loads((self.ws.state_dir / harvest.STATE_NAME).read_text(encoding='utf-8'))
        self.assertEqual((state['language'], state['claude:cafe0010']['status']), ('Turkish', 'done'))
        self.assertEqual(harvest.draft_language(self.ws), 'Turkish')

    # -- the hook ----------------------------------------------------------------

    def cli(self, *argv, stdin=None):
        with patch.object(sys, 'stdin', stdin if stdin is not None else Stdin('')), patch('sys.stdout', io.StringIO()) as out:
            return cli.main(['--workspace', str(self.ws.state_dir), *argv]), out.getvalue()

    def test_the_precompact_hook_starts_a_checkpoint_in_the_background_and_prints_nothing(self):
        payload = dict(transcript_path=str(self.project / 'x.jsonl'), cwd=str(self.root), hook_event_name='PreCompact',
                       trigger='auto', custom_instructions=None)
        with patch.object(harvest, 'detached') as start:
            code, out = self.cli('session', 'checkpoint', '--vault', str(self.root), stdin=Stdin(json.dumps(payload)))
        self.assertEqual((code, out), (0, ''))
        args = start.call_args[0][0]
        self.assertEqual(args[args.index('session'):args.index('session') + 2], ['session', 'checkpoint'])
        self.assertEqual(args[args.index('--thread') + 1], str(self.project / 'x.jsonl'))

    def test_the_precompact_hook_never_blocks_the_compaction(self):
        with patch.object(harvest, 'spawn_from_hook', side_effect=RuntimeError('boom')):
            self.assertEqual(self.cli('session', 'checkpoint', stdin=Stdin('{not json'))[0], 0)
        with patch('sys.stderr', io.StringIO()):
            self.assertEqual(self.cli('session', 'checkpoint', '--no-such-option', stdin=Stdin('{}'))[0], 0)
            with self.assertRaises(SystemExit):  # other commands keep argparse's usage error
                self.cli('session', 'end', '--no-such-option', stdin=Stdin('{}'))

    def test_by_hand_without_a_thread_it_explains_itself(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        code, out = self.cli('session', 'checkpoint', stdin=Terminal())
        self.assertEqual(code, 1)
        self.assertIn('PreCompact', out)

    def test_a_new_vault_gets_the_precompact_hook_for_both_clients(self):
        for rel in ('.claude/settings.local.json', '.codex/hooks.json'):
            hook = json.loads((self.root / rel).read_text(encoding='utf-8'))['hooks']['PreCompact'][0]['hooks'][0]
            self.assertTrue(hook['command'].endswith(' session checkpoint || true'), hook['command'])
            self.assertEqual(hook['timeout'], vault.CHECKPOINT_TIMEOUT)
        self.assertTrue(vaultview._checkpoint_hook('claude', self.root))
        self.assertTrue(vaultview.assistants(self.ws, self.root)['checkpoint'])


class SessionHooksTest(unittest.TestCase):
    """`carry session hooks` on a vault Carry did not create."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / 'home'; self.home.mkdir()
        home = patch.object(Path, 'home', return_value=self.home); home.start(); self.addCleanup(home.stop)
        self.root = self.base / 'Notes'; (self.root / '.claude').mkdir(parents=True); (self.root / '.codex').mkdir()
        self.state = self.base / 'ws'
        self.local = self.root / '.claude' / 'settings.local.json'
        self.local.write_text(json.dumps({'permissions': {'allow': ['Bash(git status)']}, 'hooks': {
            'SessionEnd': [{'hooks': [{'type': 'command', 'command': 'echo session ended >> log'}]},
                           {'hooks': [{'type': 'command', 'command': '/py -m carry.cli --workspace w harvest --from-hook'}]}],
            'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': 'python3 tools/hook_capture.py'}]}]}}), encoding='utf-8')

    def git(self, *args):
        subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)

    def commands(self, path):
        data = json.loads(path.read_text(encoding='utf-8'))
        return {event: [h['command'] for g in groups for h in g['hooks']] for event, groups in data.get('hooks', {}).items()}, data

    def test_install_adds_the_three_hooks_and_keeps_everything_else(self):
        result = vault.set_session_hooks(self.state, self.root, ('claude',), on=True)
        hooks, data = self.commands(self.local)
        self.assertEqual(data['permissions'], {'allow': ['Bash(git status)']})
        self.assertEqual(hooks['UserPromptSubmit'], ['python3 tools/hook_capture.py'])
        self.assertIn('echo session ended >> log', hooks['SessionEnd'])
        self.assertFalse(any('harvest --from-hook' in c for c in hooks['SessionEnd']))  # the old Carry hook is replaced
        self.assertTrue(any(c.endswith(f' session end --vault {self.root}') for c in hooks['SessionEnd']))
        self.assertTrue(hooks['PreCompact'][0].endswith(f' session checkpoint --vault {self.root} || true'))
        self.assertIn(' session start --from-hook --vault ', hooks['SessionStart'][0])
        self.assertEqual(result['claude']['hooks'], dict(start=True, end=True, checkpoint=True))
        self.assertTrue((self.root / '.claude' / 'settings.local.json.bak-carry').exists())
        again = vault.set_session_hooks(self.state, self.root, ('claude',), on=True)
        self.assertEqual(again['claude']['changed'], [])  # installing twice changes nothing

    def test_remove_takes_out_only_carry_session_hooks(self):
        vault.set_session_hooks(self.state, self.root, ('claude',), on=True)
        result = vault.set_session_hooks(self.state, self.root, ('claude',), on=False)
        hooks, data = self.commands(self.local)
        self.assertEqual(hooks, {'SessionEnd': ['echo session ended >> log'], 'UserPromptSubmit': ['python3 tools/hook_capture.py']})
        self.assertEqual(data['permissions'], {'allow': ['Bash(git status)']})
        self.assertEqual(result['claude']['hooks'], dict(start=False, end=False, checkpoint=False))

    def test_a_committed_codex_hooks_file_is_never_written(self):
        committed = self.root / '.codex' / 'hooks.json'
        committed.write_text('{"hooks": {"UserPromptSubmit": [{"hooks": [{"command": "python3 hook.py"}]}]}}\n', encoding='utf-8')
        self.git('init', '-q'); self.git('add', '.codex/hooks.json')
        before = committed.read_text(encoding='utf-8')
        result = vault.set_session_hooks(self.state, self.root, ('codex',), on=True)
        self.assertEqual(committed.read_text(encoding='utf-8'), before)
        self.assertEqual(result['codex']['path'], str(self.home / '.codex' / 'hooks.json'))
        self.assertIn('committed', result['codex']['note'])
        hooks, _ = self.commands(self.home / '.codex' / 'hooks.json')
        self.assertEqual(sorted(hooks), ['PreCompact', 'SessionEnd', 'SessionStart'])
        vault.set_session_hooks(self.state, self.root, ('codex',), on=False)
        self.assertEqual(committed.read_text(encoding='utf-8'), before)
        self.assertEqual(self.commands(self.home / '.codex' / 'hooks.json')[0], {})

    def test_the_cli_turns_them_on_and_shows_them(self):
        Workspace.create(self.state)
        with patch('sys.stdout', io.StringIO()) as out:
            code = cli.main(['--workspace', str(self.state), 'session', 'hooks', '--install', '--vault', str(self.root), '--client', 'claude'])
        self.assertEqual(code, 0)
        self.assertIn('claude: start on, end on, checkpoint on', out.getvalue())
        with patch('sys.stdout', io.StringIO()) as out:
            cli.main(['--workspace', str(self.state), 'session', 'hooks', '--vault', str(self.root), '--client', 'claude'])
        self.assertIn('checkpoint on', out.getvalue())

    def test_hook_kinds_of_every_generation(self):
        kind = vault.session_hook_kind
        self.assertEqual(kind('/py -I -m carry.cli --workspace w context'), 'start')
        self.assertEqual(kind('/py -I -m carry.cli --workspace w session start --from-hook --vault v'), 'start')
        self.assertEqual(kind('carry --workspace w harvest --from-hook'), 'end')
        self.assertEqual(kind('/py -I -m carry.cli --workspace w session end'), 'end')
        self.assertEqual(kind('/py -I -m carry.cli --workspace w session checkpoint || true'), 'checkpoint')
        self.assertIsNone(kind('/py -I -m carry.hook --workspace w --client claude'))
        self.assertIsNone(kind('echo session ended'))


if __name__ == '__main__':
    unittest.main()
