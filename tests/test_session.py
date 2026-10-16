"""carry session start | end | sweep | schedule, and the names before 0.14 (context, harvest)."""
import datetime
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import _support  # noqa: F401
from carry import cli, context, harvest, okf, recall, taskscheduler, vault, vaultview
from carry.markdown import parse_frontmatter

RECAP = ('---\ntype: output\nstatus: draft\n{key}: {{"extractor": "claude:sonnet", "judge": "agent", "compared": true}}\n'
         '---\n\n# {head}\n\n## New\n\n- **open item:** {item}\n  > quote *(exchange 1, owner, {day})*\n')


class Stdin(io.StringIO):
    """A hook's stdin: a pipe, not a terminal."""
    def isatty(self):
        return False


class Terminal(io.StringIO):
    def isatty(self):
        return True


class SessionCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        home = patch.object(Path, 'home', return_value=self.base / 'home'); home.start(); self.addCleanup(home.stop)
        self.root = self.base / 'Vault'
        vault.apply(vault.plan(self.root, language='English', workspace=self.base / 'ws', attach=False), git=False)
        self.ws = str(self.base / 'ws')
        self.day = datetime.date.today().isoformat()

    def cli(self, *argv, stdin=None):
        out = io.StringIO()
        with redirect_stdout(out), patch.object(sys, 'stdin', stdin if stdin is not None else Terminal()):
            code = cli.main(['--workspace', self.ws, *argv])
        return code, out.getvalue()

    def recap(self, name, key, head, item):
        (self.root / '+' / name).write_text(RECAP.format(key=key, head=head, item=item, day=self.day), encoding='utf-8')

    # -- start ---------------------------------------------------------------

    def test_start_prints_the_same_state_pack_as_context(self):
        self.recap(f'{self.day} — session claude aaaa1111.md', 'session', 'Session recap claude aaaa1111', 'Invite two colleagues.')
        new = self.cli('session', 'start', '--vault', str(self.root))
        old = self.cli('context', '--vault', str(self.root))
        self.assertEqual(new, old)
        self.assertEqual(new[0], 0)
        self.assertIn('Invite two colleagues.', new[1])

    # -- end -----------------------------------------------------------------

    def test_end_from_a_hook_starts_the_recap_in_the_background(self):
        payload = Stdin(json.dumps(dict(transcript_path=str(self.base / 't.jsonl'), cwd=str(self.root), hook_event_name='SessionEnd')))
        with patch.object(harvest, 'detached') as start:
            code, _ = self.cli('session', 'end', stdin=payload)
        self.assertEqual(code, 0)
        args = start.call_args[0][0]
        self.assertEqual(args[args.index('--workspace') + 2:args.index('--workspace') + 4], ['session', 'end'])
        self.assertEqual(args[args.index('--thread') + 1], str(self.base / 't.jsonl'))

    def test_end_never_fails_the_client(self):
        with patch.object(harvest, 'spawn_from_hook', side_effect=RuntimeError('boom')):
            self.assertEqual(self.cli('session', 'end', stdin=Stdin('{}'))[0], 0)

    def test_end_by_hand_without_a_thread_explains_itself(self):
        code, out = self.cli('session', 'end')
        self.assertEqual(code, 1)
        self.assertIn('--thread', out)

    def test_end_with_a_thread_recaps_it_now(self):
        report = dict(threads=1, harvested=1, skipped_filed=0, skipped_active=0, pending=0, items=1,
                      digests=[f'+/{self.day} — session claude aaaa1111.md'])
        with patch.object(harvest, 'run', return_value=report) as run:
            code, out = self.cli('session', 'end', '--thread', '/tmp/t.jsonl', '--language', 'Turkish')
        self.assertEqual(code, 0)
        self.assertEqual((run.call_args.kwargs['thread'], run.call_args.kwargs['language']), ('/tmp/t.jsonl', 'Turkish'))
        self.assertIn('threads 1: recapped 1', out)
        self.assertIn(f'draft: +/{self.day} — session claude aaaa1111.md', out)

    # -- sweep ---------------------------------------------------------------

    def test_sweep_recaps_every_idle_thread_with_the_old_options(self):
        report = dict(threads=0, harvested=0, skipped_filed=0, skipped_active=0, pending=0, items=0, digests=[])
        with patch.object(harvest, 'run', return_value=report) as run:
            code, _ = self.cli('session', 'sweep', '--dry-run', '--limit', '3', '--min-idle', '5', '--include-filed')
        self.assertEqual(code, 0)
        kw = run.call_args.kwargs
        self.assertEqual((kw['thread'], kw['dry_run'], kw['limit'], kw['min_idle'], kw['include_filed']), (None, True, 3, 5, True))

    def test_a_pending_thread_fails_the_sweep(self):
        report = dict(threads=1, harvested=0, skipped_filed=0, skipped_active=0, pending=1, items=0, digests=[])
        with patch.object(harvest, 'run', return_value=report):
            self.assertEqual(self.cli('session', 'sweep')[0], 1)

    # -- schedule ------------------------------------------------------------

    def test_schedule_installs_removes_and_shows_the_one_nightly_job(self):
        with patch.object(sys, 'platform', 'darwin'), \
                patch.object(harvest, 'install_schedule') as install, patch.object(harvest, 'remove_schedule') as remove, \
                patch.object(harvest, 'schedule_status', return_value=dict(installed=False, loaded=False)):
            self.assertEqual(self.cli('session', 'schedule', '--install')[0], 0)
            self.assertEqual(self.cli('session', 'schedule', '--remove')[0], 0)
            code, out = self.cli('session', 'schedule')
        install.assert_called_once()
        remove.assert_called_once()
        self.assertEqual(code, 0)
        self.assertIn('not scheduled', out)

    def test_the_nightly_job_runs_session_sweep_under_its_old_label(self):
        plist = harvest.schedule_plist('/bin/carry', '/ws', extra=('--vault', '/v'))
        self.assertIn('<string>local.carry.harvest</string>', plist)
        self.assertIn('<string>/ws</string><string>session</string><string>sweep</string><string>--vault</string>', plist)
        self.assertEqual(harvest.schedule_path().name, 'local.carry.harvest.plist')
        self.assertIn('session sweep', taskscheduler.task_xml('/ws', 21, 30))

    # -- the names before 0.14 -----------------------------------------------

    def test_harvest_keeps_working_as_the_old_name(self):
        payload = Stdin(json.dumps(dict(transcript_path=str(self.base / 't.jsonl'), cwd=str(self.root))))
        with patch.object(harvest, 'detached') as start:
            self.assertEqual(self.cli('harvest', '--from-hook', stdin=payload)[0], 0)
        start.assert_called_once()
        report = dict(threads=0, harvested=0, skipped_filed=0, skipped_active=0, pending=0, items=0, digests=[])
        with patch.object(harvest, 'run', return_value=report) as run:
            self.cli('harvest', '--thread', '/tmp/t.jsonl')
            self.cli('harvest', '--dry-run')
        self.assertEqual([c.kwargs['thread'] for c in run.call_args_list], ['/tmp/t.jsonl', None])
        with patch.object(sys, 'platform', 'darwin'), patch.object(harvest, 'install_schedule') as install, \
                patch.object(harvest, 'remove_schedule') as remove:
            self.cli('harvest', '--install-schedule')
            self.cli('harvest', '--remove-schedule')
        install.assert_called_once()
        remove.assert_called_once()

    # -- recaps on disk ------------------------------------------------------

    def test_old_and_new_recaps_both_wait_and_are_listed(self):
        self.recap(f'{self.day} — session claude aaaa1111.md', 'session', 'Session recap claude aaaa1111', 'New-style open item.')
        self.recap(f'{self.day} — harvest codex bbbb2222.md', 'harvest', 'Harvest codex bbbb2222', 'Old-style open item.')
        for name in (f'{self.day} — session claude aaaa1111.md', f'{self.day} — harvest codex bbbb2222.md'):
            front, _ = parse_frontmatter((self.root / '+' / name).read_text(encoding='utf-8'))
            self.assertTrue(recall.is_pending_digest(front), name)
            self.assertTrue(vaultview.is_digest(front), name)
            self.assertEqual(okf.run_record(front)['extractor'], 'claude:sonnet')
        pack = context.pack(self.root)
        self.assertIn('New-style open item.', pack)
        self.assertIn('Old-style open item.', pack)
        self.assertIn('2 draft digests (+/)', pack)
        self.assertEqual(okf.run_record(dict(type='thing')), {})
        self.assertFalse(recall.is_pending_digest(dict(status='draft', type='thing')))

    def test_a_meeting_digest_carries_the_same_run_record(self):
        from carry import meet
        item = dict(type='decision', statement='Ship on Friday.', quote='ship on Friday', line=1, speaker='Deniz')
        groups = dict(new=[item], conflict=[], known=[])
        rel = meet.write_digest(self.root, 'sources/carry/meet/2026-10-16 1100 — Planning.md', 'Planning', groups,
                                dict(extractor='claude:sonnet', kind='meeting', compared=True), 'English', '2026-10-16')
        front, _ = parse_frontmatter((self.root / rel).read_text(encoding='utf-8'))
        self.assertEqual((front['session']['kind'], 'harvest' in front), ('meeting', False))
        self.assertTrue(recall.is_pending_digest(front))

    def test_chat_end_hooks_of_both_generations_are_recognized(self):
        settings = self.root / '.claude' / 'settings.local.json'
        self.assertTrue(vaultview._chat_end_hook('claude', self.root))  # written by this version: session end
        self.assertIn(' session end', settings.read_text(encoding='utf-8'))
        settings.write_text('{"hooks": {"SessionEnd": [{"hooks": [{"command": "carry --workspace w harvest --from-hook"}]}]}}', encoding='utf-8')
        self.assertTrue(vaultview._chat_end_hook('claude', self.root))
        settings.write_text('{"hooks": {}}', encoding='utf-8')
        self.assertFalse(vaultview._chat_end_hook('claude', self.root))


if __name__ == '__main__':
    unittest.main()
