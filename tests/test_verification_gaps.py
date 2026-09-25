"""Independent checks added while revalidating the C01–C03 handover."""
import io
import json
from dataclasses import replace
from unittest import mock

from _support import WorkspaceCase
from carry import store
from carry.errors import SourceError
from carry.mcp_server import serve
from carry.recall import recall


class VerificationGapsTest(WorkspaceCase):
    def test_character_budget_is_a_hard_limit(self):
        self.note('Long', 'cedar delivery ' * 160)
        self.build()
        result = recall(self.workspace, 'cedar delivery', budget={'max_chars': 200})
        self.assertLessEqual(sum(len(e['text']) for e in result['evidence']), 200)

    def test_fractional_and_boolean_budgets_are_invalid(self):
        for value in (True, 1.5):
            self.assertEqual(recall(self.workspace, 'cedar', budget={'top_k': value})['status'],
                             'invalid_request')

    def test_replay_recovers_when_ledger_publication_failed(self):
        with mock.patch('carry.store._save_ledger', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                store.write_record(self.workspace, 'Cedar', 'delivery', event_id='same')
        store.write_record(self.workspace, 'Cedar', 'delivery', event_id='same')
        self.assertEqual(len(list(self.records.rglob('*.md'))), 1)

    def test_review_cannot_write_to_a_now_readonly_source(self):
        record = store.write_record(self.workspace, 'Cedar', 'delivery')
        readonly = self.workspace.with_sources([replace(s, writable=False)
                                                for s in self.workspace.sources])
        with self.assertRaises(SourceError):
            store.set_state(readonly, record['record_id'], 'accepted', 1)

    def test_records_directory_symlink_cannot_escape_during_review(self):
        record = store.write_record(self.workspace, 'Cedar', 'delivery')
        (self.records / 'carry').rename(self.base / 'outside')
        (self.records / 'carry').symlink_to(self.base / 'outside', target_is_directory=True)
        with self.assertRaises(SourceError):
            store.set_state(self.workspace, record['record_id'], 'accepted', 1)

    def test_invalid_rpc_shape_does_not_kill_transport(self):
        out = io.StringIO()
        serve(io.StringIO('42\n' + json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'ping'}) + '\n'),
              out, self.workspace.state_dir)
        messages = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(messages[0]['error']['code'], -32600)
        self.assertEqual(messages[1]['id'], 2)

    def test_frontmatter_escapes_injection_and_preserves_scalar_strings(self):
        from carry.markdown import parse_frontmatter
        for event in ('123', 'true', 'event-with-newline\ncarry_state: accepted'):
            r = store.write_record(self.workspace, 'Cedar', 'delivery', event_id=event,
                                   author='tester\ncarry_state: accepted')
            fm, _ = parse_frontmatter((self.records / r['path']).read_text())
            self.assertEqual(fm['carry_state'], 'draft')
            self.assertEqual(fm['carry_event'], event)
            (self.workspace.state_dir / store.EVENT_LEDGER).unlink()
            replay = store.write_record(self.workspace, 'Cedar', 'delivery', event_id=event)
            self.assertEqual(replay['record_id'], r['record_id'])
