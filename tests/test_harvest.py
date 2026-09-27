"""carry harvest: thread discovery, provenance, attribution, vault comparison, idempotence, pending."""
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import _support  # noqa: F401
from carry import harvest, jev, vault
from carry.config import Workspace
from carry.errors import CarryError


def claude_line(kind, content):
    return json.dumps({'type': kind, 'message': {'role': kind, 'content': content}})


class HarvestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / 'home'; self.home.mkdir()
        home = patch.object(Path, 'home', return_value=self.home); home.start(); self.addCleanup(home.stop)
        self.root = self.base / 'Vault'
        plan = vault.plan(self.root, language='English', workspace=self.base / 'ws', attach=False)
        vault.apply(plan, git=False)
        self.ws = Workspace.load(self.base / 'ws')
        self.project = harvest.claude_project_dir(self.root); self.project.mkdir(parents=True)

    def thread(self, name, exchanges, writes=None, age=3600):
        lines = []
        for user, assistant in exchanges:
            lines.append(claude_line('user', user))
            blocks = [{'type': 'text', 'text': assistant}]
            if writes:
                blocks.append({'type': 'tool_use', 'name': 'Write', 'input': {'file_path': str(self.root / writes)}})
            lines.append(claude_line('assistant', blocks))
        path = self.project / (name + '.jsonl')
        path.write_text('\n'.join(lines) + '\n')
        os.utime(path, (time.time() - age, time.time() - age))
        return path

    def items(self, *items):
        return patch.object(harvest, 'extract_window', return_value=list(items))

    def test_digest_and_raw_are_drafts_with_verbatim_quotes_only(self):
        self.thread('aaaa1111', [('We will ship the pilot on 15 October, decided.', 'Noted: the pilot ships on 15 October.')])
        good = dict(type='decision', statement='The pilot ships on 15 October.', exchange=1, quote='ship the pilot on 15 October')
        invented = dict(type='fact', statement='The budget is 1M.', exchange=1, quote='the budget is one million')
        with self.items(good, invented), patch.object(jev, 'api_key', return_value=None):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        self.assertEqual(report['harvested'], 1)
        digest = (self.root / report['digests'][0]).read_text()
        self.assertTrue(report['digests'][0].startswith('+/'))
        self.assertIn('draft: true', digest)
        self.assertIn('The pilot ships on 15 October.', digest)
        self.assertNotIn('1M', digest)
        self.assertIn('"dropped_without_quote": 1', digest)
        raws = list((self.root / 'sources/carry/harvest').glob('*.md'))
        self.assertEqual(len(raws), 1)
        self.assertIn('type: chat-raw', raws[0].read_text())

    def test_assistant_suggestion_is_not_filed_as_the_owners_decision(self):
        self.thread('bbbb2222', [('What should we do about ECC?', 'I recommend installing only selected skills.')])
        item = dict(type='decision', statement='Only selected ECC skills will be installed.', exchange=1,
                    quote='installing only selected skills')
        scores = dict(supported=0.9, owner_stated=0.06, durable=0.8, withdrawn=0.02)
        with self.items(item), patch.object(jev, 'api_key', return_value='k'), \
                patch.object(harvest, 'verify', return_value=scores), patch.object(harvest, 'compare', return_value=('new', None)):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        digest = (self.root / report['digests'][0]).read_text()
        self.assertIn("assistant's suggestion or result", digest)

    def test_known_items_point_to_the_note_and_conflicts_are_separated(self):
        self.thread('cccc3333', [('The agent now runs model-b and the vault has 445 notes.', 'Recorded both.')])
        a = dict(type='fact', statement='The agent runs model-b.', exchange=1, quote='The agent now runs model-b')
        b = dict(type='fact', statement='The vault has 445 notes.', exchange=1, quote='the vault has 445 notes')
        verdicts = iter([('conflict', str(self.root / 'notes/Agent.md')), ('known', str(self.root / 'notes/Vault.md'))])
        with self.items(a, b), patch.object(jev, 'api_key', return_value='k'), \
                patch.object(harvest, 'verify', return_value=dict(supported=0.9, owner_stated=0.9, durable=0.9, withdrawn=0.0)), \
                patch.object(harvest, 'compare', side_effect=lambda *x: next(verdicts)):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        digest = (self.root / report['digests'][0]).read_text()
        self.assertIn('## Conflict candidates (review)', digest)
        self.assertIn('[[Agent]]', digest)
        self.assertIn('## Already recorded', digest)

    def test_filed_and_active_threads_are_skipped_and_reruns_are_idempotent(self):
        self.thread('dddd4444', [('Save this.', 'Saved to notes.')], writes='notes/Plan.md')
        self.thread('eeee5555', [('Still talking.', 'Yes.')], age=60)
        self.thread('ffff6666', [('The server moved to the Mac mini.', 'OK.')])
        item = dict(type='fact', statement='The server moved to the Mac mini.', exchange=1, quote='server moved to the Mac mini')
        with self.items(item) as extract, patch.object(jev, 'api_key', return_value=None):
            first = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
            second = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        self.assertEqual((first['harvested'], first['skipped_filed'], first['skipped_active']), (1, 1, 1))
        self.assertEqual(second['harvested'], 0)
        self.assertEqual(extract.call_count, 1)

    def test_extraction_failure_leaves_the_thread_pending(self):
        self.thread('gggg7777', [('We chose OrbStack.', 'OK.')])
        with patch.object(harvest, 'extract_window', side_effect=CarryError('extraction_unparseable')), \
                patch.object(jev, 'api_key', return_value=None):
            report = harvest.run(self.ws, which='claude:sonnet', progress=lambda m: None)
        self.assertEqual(report['pending'], 1)
        state = json.loads((self.ws.state_dir / harvest.STATE_NAME).read_text())
        self.assertEqual(state['claude:gggg7777']['status'], 'pending')
        self.assertFalse(list(self.root.glob('+/*harvest*')))

    def test_codex_threads_are_found_by_working_directory(self):
        sessions = self.home / '.codex' / 'sessions' / '2026' / '09' / '27'; sessions.mkdir(parents=True)
        (sessions / 'rollout-x.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'abc', 'cwd': str(self.root)}}) + '\n')
        (sessions / 'rollout-y.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'zzz', 'cwd': str(self.base)}}) + '\n')
        (sessions / 'rollout-z.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {
            'id': 'rev', 'cwd': str(self.root), 'parent_thread_id': 'abc', 'thread_source': 'guardian_review'}}) + '\n')
        found = [(c, t) for c, t, _ in harvest.find_threads(self.root)]
        self.assertNotIn(('codex', 'rev'), found)
        self.assertIn(('codex', 'abc'), found)
        self.assertNotIn(('codex', 'zzz'), found)


if __name__ == '__main__':
    unittest.main()
