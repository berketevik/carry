"""C05 acceptance: review preconditions, linked synthesis, history and clients."""
import hashlib
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest import mock

from _support import SRC, WorkspaceCase, tree_digest
from carry import capture, index, lifecycle, store
from carry.errors import CarryError, RevisionConflict, SourceError
from carry.markdown import parse_frontmatter
from carry.mcp_server import available_tools, call_tool
from carry.recall import recall
from carry.status import status

OCT15 = 'Cedar pilot delivery is October 15, 2026.'
OCT22 = 'Cedar pilot delivery is October 22, 2026.'


class LifecycleTest(WorkspaceCase):
    def propose(self, content=OCT15, event='one', **kwargs):
        return lifecycle.propose(self.workspace, content, event, title='Cedar delivery', **kwargs)

    def accept(self, proposal):
        review = lifecycle.review(self.workspace, proposal['record_id'])
        return lifecycle.accept(self.workspace, proposal['record_id'], review['revision'], review['review_token'])

    def correction(self, original, event='two', content=OCT22):
        return self.propose(content, event, target_id=original['record_id'],
                            expected_revision=original['revision'])

    def test_pending_draft_never_changes_accepted_answer(self):
        old = self.accept(self.propose())
        pending = self.correction(old)
        self.build()
        answer = recall(self.workspace, 'Cedar pilot delivery')
        text = json.dumps(answer['evidence'])
        self.assertIn('October 15', text)
        self.assertNotIn('October 22', text)
        self.assertEqual(answer['diagnostics']['pending_proposals'], 1)
        explicit = recall(self.workspace, 'Cedar pilot delivery', include_drafts=True)
        self.assertIn(pending['record_id'], json.dumps(explicit['evidence']))

    def test_diff_token_acceptance_and_history(self):
        old = self.accept(self.propose())
        old_path = self.records / old['path']
        old_bytes = old_path.read_bytes()
        pending = self.correction(old)
        review = lifecycle.review(self.workspace, pending['record_id'])
        self.assertIn('-' + OCT15, review['diff'])
        self.assertIn('+' + OCT22, review['diff'])
        accepted = self.accept(pending)
        self.assertEqual(accepted['revision'], 2)
        self.assertEqual(accepted['index']['status'], 'built')
        # A writable target gains exactly one marker line; nothing else changes.
        self.assertEqual(accepted['target_marker'], 'marked')
        self.assertEqual(old_path.read_bytes(),
                         lifecycle.with_marker(old_bytes.decode('utf-8'), accepted['record_id']).encode('utf-8'))
        answer = recall(self.workspace, 'Cedar pilot delivery')
        self.assertIn('October 22', json.dumps(answer['evidence']))
        self.assertNotIn('October 15', json.dumps(answer['evidence']))
        history = recall(self.workspace, 'Cedar pilot delivery', include_history=True)
        old_hit = next(e for e in history['evidence'] if e['record_id'] == old['record_id'])
        self.assertEqual(old_hit['state'], 'superseded')
        self.assertFalse(old_hit['current'])
        self.assertEqual(old_hit['superseded_by'], accepted['record_id'])

    def test_imported_target_is_replaced_without_editing_source(self):
        imported = self.note('Cedar imported', OCT15)
        self.note('Other note', 'Unrelated maple information')
        self.build()
        target = next(f for f in lifecycle.catalog(self.workspace).values() if f['path'] == imported.name)
        before = tree_digest(self.corpus)
        accepted = self.accept(self.correction(target))
        self.assertEqual(tree_digest(self.corpus), before)
        result = recall(self.workspace, 'Cedar pilot delivery')
        self.assertEqual(result['evidence'][0]['record_id'], accepted['record_id'])
        self.assertNotIn('October 15', json.dumps(result['evidence']))
        self.workspace.db_path.unlink()
        self.build()
        self.assertNotIn('October 15', json.dumps(recall(self.workspace, 'Cedar pilot delivery')['evidence']))

    def test_writable_imported_target_is_marked_and_stays_history(self):
        imported = self.note('Cedar living note', OCT15, root=self.records)
        self.build()
        target = next(f for f in lifecycle.catalog(self.workspace).values() if f['path'] == imported.name)
        before = imported.read_text(encoding='utf-8')
        accepted = self.accept(self.correction(target))
        self.assertEqual(accepted['target_marker'], 'marked')
        after = imported.read_text(encoding='utf-8')
        self.assertEqual(after, lifecycle.with_marker(before, accepted['record_id']))
        self.assertEqual(parse_frontmatter(after)[0]['carry_superseded_by'], accepted['record_id'])
        answer = recall(self.workspace, 'Cedar pilot delivery')
        self.assertNotIn('October 15', json.dumps(answer['evidence']))
        self.assertNotIn('correction_target_conflict', answer['diagnostics'].get('warnings', []))
        history = recall(self.workspace, 'Cedar pilot delivery', include_history=True)
        old_hit = next(e for e in history['evidence'] if e['record_id'] == target['record_id'])
        self.assertEqual(old_hit['state'], 'superseded')
        # Any edit beyond the marker is a real change: both claims come back with a conflict.
        imported.write_text(after.replace('October 15', 'October 17'), encoding='utf-8')
        self.build()
        conflicted = recall(self.workspace, 'Cedar pilot delivery')
        self.assertIn('correction_target_conflict', conflicted['diagnostics']['warnings'])
        self.assertIn('October 17', json.dumps(conflicted['evidence']))

    def test_marker_round_trip_with_and_without_frontmatter(self):
        for raw in ('---\ntype: note\n---\n# A\nbody\n', '# No frontmatter\nbody\n', '---\n---\nbody\n'):
            marked = lifecycle.with_marker(raw, 'rec_0123456789abcdef')
            self.assertEqual(parse_frontmatter(marked)[0]['carry_superseded_by'], 'rec_0123456789abcdef')
            entry = dict(raw=marked)
            target = dict(digest=hashlib.sha256(raw.encode('utf-8')).hexdigest())
            self.assertTrue(lifecycle._marked_digest_matches(entry, target, 'rec_0123456789abcdef'))
            self.assertFalse(lifecycle._marked_digest_matches(entry, target, 'rec_ffffffffffffffff'))

    def test_pending_target_content_edit_conflicts_even_without_revision_change(self):
        old = self.accept(self.propose())
        correction = self.correction(old)
        review = lifecycle.review(self.workspace, correction['record_id'])
        path = self.records / old['path']
        path.write_text(path.read_text().replace('October 15', 'October 17'))
        with self.assertRaises(RevisionConflict):
            lifecycle.accept(self.workspace, correction['record_id'], review['revision'], review['review_token'])
        self.assertEqual(lifecycle.review(self.workspace, correction['record_id'])['conflict'], 'target_content_changed')
        self.assertEqual(lifecycle.review(self.workspace, correction['record_id'])['state'], 'draft')

    def test_review_token_detects_manual_proposal_edit(self):
        pending = self.propose()
        review = lifecycle.review(self.workspace, pending['record_id'])
        path = self.records / pending['path']
        path.write_text(path.read_text() + '\nUnreviewed addition\n')
        with self.assertRaises(RevisionConflict):
            lifecycle.accept(self.workspace, pending['record_id'], 1, review['review_token'])

    def test_two_simultaneous_corrections_have_one_winner(self):
        old = self.accept(self.propose())
        a, b = self.correction(old, 'a'), self.correction(old, 'b', 'Cedar pilot delivery is October 29, 2026.')
        reviews = [lifecycle.review(self.workspace, p['record_id']) for p in (a, b)]
        def run(review):
            try:
                lifecycle.accept(self.workspace, review['record_id'], review['revision'], review['review_token'])
                return 'accepted'
            except RevisionConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, reviews))
        self.assertCountEqual(results, ['accepted', 'conflict'])

    def test_exact_accept_retry_is_idempotent(self):
        proposal = self.propose()
        review = lifecycle.review(self.workspace, proposal['record_id'])
        self.accept(proposal)
        second = lifecycle.accept(self.workspace, proposal['record_id'], 1, review['review_token'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(len(list((self.records / 'carry').glob('*.md'))), 1)

    def test_index_failure_never_serves_superseded_answer_as_current(self):
        old = self.accept(self.propose())
        proposal = self.correction(old)
        published = self.workspace.db_path.read_bytes()
        with mock.patch('carry.embedding.HashingEmbedding.embed_document', side_effect=OSError('offline')):
            result = self.accept(proposal)
        self.assertEqual(result['status'], 'accepted_index_pending')
        self.assertEqual(self.workspace.db_path.read_bytes(), published)
        answer = recall(self.workspace, 'Cedar pilot delivery')
        self.assertNotIn('October 15', json.dumps(answer['evidence']))
        self.assertIn('index_stale', answer['diagnostics']['warnings'])
        self.build()
        self.assertIn('October 22', json.dumps(recall(self.workspace, 'Cedar pilot delivery')['evidence']))

    def test_interrupted_accept_before_commit_keeps_draft_and_previous_answer(self):
        old = self.accept(self.propose())
        pending = self.correction(old)
        real = lifecycle.atomic_text
        def fail(path, text):
            if path.resolve() == (self.records / pending['path']).resolve():
                raise OSError('interrupted')
            return real(path, text)
        with mock.patch('carry.lifecycle.atomic_text', side_effect=fail):
            with self.assertRaises(OSError):
                self.accept(pending)
        self.assertEqual(lifecycle.review(self.workspace, pending['record_id'])['state'], 'draft')
        self.assertIn('October 15', json.dumps(recall(self.workspace, 'Cedar pilot delivery')['evidence']))
        self.accept(pending)

    def test_rejected_proposal_is_retained_but_not_recalled(self):
        pending = self.propose()
        r = lifecycle.review(self.workspace, pending['record_id'])
        lifecycle.reject(self.workspace, pending['record_id'], 1, r['review_token'])
        self.build()
        self.assertTrue((self.records / pending['path']).exists())
        self.assertEqual(lifecycle.list_proposals(self.workspace), [])
        self.assertEqual(recall(self.workspace, 'Cedar', include_drafts=True, include_history=True)['evidence'], [])
        self.assertEqual(status(self.workspace)['review']['rejected'], 1)

    def test_accepted_target_edited_later_is_visible_as_a_conflict(self):
        imported = self.note('Imported', OCT15)
        self.build()
        target = next(iter(lifecycle.catalog(self.workspace).values()))
        self.accept(self.correction(target))
        imported.write_text(imported.read_text().replace('October 15', 'October 18'))
        self.build()
        result = recall(self.workspace, 'Cedar pilot delivery')
        self.assertIn('correction_target_conflict', result['diagnostics']['warnings'])
        self.assertIn('October 18', json.dumps(result['evidence']))
        self.assertEqual(status(self.workspace)['review']['target_conflicts'], 1)

    def test_deleted_target_cannot_be_accepted(self):
        old = self.accept(self.propose())
        pending = self.correction(old)
        (self.records / old['path']).unlink()
        with self.assertRaises(SourceError):
            self.accept(pending)

    def test_correction_chains_keep_all_historical_dates(self):
        a = self.accept(self.propose())
        b = self.accept(self.correction(a))
        c = self.accept(self.correction(b, 'three', 'Cedar pilot delivery is October 29, 2026.'))
        current = recall(self.workspace, 'Cedar pilot delivery')
        self.assertEqual({e['record_id'] for e in current['evidence']}, {c['record_id']})
        history = json.dumps(recall(self.workspace, 'Cedar pilot delivery', include_history=True)['evidence'])
        for date in ('October 15', 'October 22', 'October 29'):
            self.assertIn(date, history)

    def test_replay_keeps_identity_and_new_event_keeps_distinction(self):
        a = self.propose()
        self.accept(a)
        b = self.propose()
        self.assertTrue(b['duplicate'])
        self.assertEqual(b['state'], 'accepted')
        self.assertNotEqual(self.propose(event='different')['record_id'], a['record_id'])

    def test_drafts_do_not_starve_accepted_evidence_before_ranking(self):
        accepted = self.accept(self.propose())
        for n in range(30):
            self.propose('Cedar pilot delivery date ' * 50, event=f'draft-{n}')
        self.build()
        result = recall(self.workspace, 'Cedar pilot delivery date', budget={'top_k': 1})
        self.assertEqual(result['evidence'][0]['record_id'], accepted['record_id'])

    def test_legacy_state_setter_cannot_bypass_review_or_reactivate_history(self):
        pending = self.propose()
        with self.assertRaises(CarryError):
            store.set_state(self.workspace, pending['record_id'], 'accepted', 1)
        old = self.accept(pending)
        self.accept(self.correction(old))
        with self.assertRaises(RevisionConflict):
            store.supersede(self.workspace, old['record_id'], 'fork', 1)

    def test_import_rename_preserves_target_identity(self):
        imported = self.note('Original', OCT15)
        self.build()
        original = next(iter(lifecycle.catalog(self.workspace).values()))
        pending = self.correction(original)
        imported.rename(self.corpus / 'Renamed.md')
        self.accept(pending)
        self.assertNotIn('October 15', json.dumps(recall(self.workspace, 'Cedar delivery')['evidence']))

    def test_source_refs_must_be_contained_and_exist(self):
        for refs in (['corpus:../outside.md'], ['corpus:/outside.md'], ['absent:x'], ['corpus:missing.md']):
            with self.assertRaises(SourceError):
                self.propose(source_refs=refs)
        self.assertEqual(list(self.records.rglob('*.md')), [])

    def test_readonly_destination_and_stale_revision_are_refused(self):
        with self.assertRaises(SourceError):
            self.propose(source_id='corpus')
        original = self.accept(self.propose())
        with self.assertRaises(RevisionConflict):
            self.propose(event='correction', target_id=original['record_id'], expected_revision=9)

    def test_other_workspace_has_no_proposals_or_policy(self):
        from carry.config import Workspace
        self.propose()
        other = Workspace.create(self.base / 'other')
        self.assertEqual(lifecycle.list_proposals(other), [])
        self.assertFalse(lifecycle.client_enabled(other, 'codex'))

    def test_target_outside_requested_source_is_never_leaked(self):
        self.note('Imported', OCT15)
        self.build()
        target = next(iter(lifecycle.catalog(self.workspace).values()))
        self.accept(self.correction(target))
        result = recall(self.workspace, 'Cedar delivery', source_ids=['corpus'])
        self.assertEqual(result['evidence'], [])

    def test_secret_masking_applies_to_synthesis_and_archived_draft(self):
        secret = 'sk-' + 'z' * 24
        self.propose('Cedar decision ' + secret)
        for path in self.records.rglob('*.md'):
            self.assertNotIn(secret, path.read_text())

    def test_same_proposal_refinement_never_decreases_revision_on_accept(self):
        original = self.accept(self.propose())
        pending = self.correction(original)
        # Use the same native event identifier, with explicit draft revisions.
        for n in range(1, 4):
            pending = self.propose(OCT22 + ' reviewed ' + str(n), 'two',
                target_id=original['record_id'], expected_revision=1,
                expected_proposal_revision=pending['revision'])
        self.assertGreaterEqual(self.accept(pending)['revision'], pending['revision'])

    def test_missing_optional_policy_is_safe_but_corrupt_policy_fails_closed(self):
        self.assertFalse(lifecycle.client_enabled(self.workspace, 'claude'))
        (self.workspace.state_dir / 'proposals.json').write_text('{broken')
        text, error = call_tool('carry_propose', dict(content=OCT15, event_id='a', source_refs=[]),
                                self.workspace.state_dir, 'claude')
        self.assertTrue(error)
        self.assertEqual(list(self.records.rglob('*.md')), [])

    def test_edit_during_review_commit_is_detected(self):
        pending = self.propose()
        real = lifecycle._archive
        def edit(source, item):
            real(source, item)
            item['absolute'].write_text(item['raw'] + '\nexternal edit\n')
        with mock.patch('carry.lifecycle._archive', side_effect=edit):
            with self.assertRaises(RevisionConflict):
                self.accept(pending)

    def test_event_replay_reports_derived_superseded_state(self):
        old = store.write_record(self.workspace, 'Cedar delivery', OCT15, event_id='legacy', state='accepted')
        self.accept(self.correction(old))
        replay = store.write_record(self.workspace, 'Cedar delivery', OCT15, event_id='legacy')
        self.assertEqual(replay['state'], 'superseded')
        self.assertTrue(replay['superseded_by'])


class ClientLifecycleTest(WorkspaceCase):
    def enable(self, client):
        with mock.patch('carry.capture.probe_client', return_value={'state': 'supported'}):
            capture.configure(self.workspace, client, 'records', True)
        lifecycle.configure_client(self.workspace, client, 'records', True)

    def captured(self, client):
        return capture.ingest(self.workspace, client, io.StringIO(json.dumps(dict(
            session_id='synthetic', prompt_id='one', turn_id='one', hook_event_name='UserPromptSubmit',
            prompt='Please remember our decision: ' + OCT15))))

    def test_client_synthesis_refines_one_linked_capture_draft(self):
        for client in ('claude', 'codex'):
            self.enable(client)
            receipt = self.captured(client)
            raw_before = (self.records / receipt['event_path']).read_bytes()
            arguments = dict(content=OCT15, title='Cedar decision', source_refs=[],
                             event_id=receipt['event_id'], expected_proposal_revision=1)
            text, error = call_tool('carry_propose', arguments, self.workspace.state_dir, client)
            self.assertFalse(error, text)
            proposal = json.loads(text)
            self.assertEqual(proposal['record_id'], receipt['proposal_id'])
            self.assertEqual(proposal['revision'], 2)
            self.assertEqual((self.records / receipt['event_path']).read_bytes(), raw_before)
            fm, body = parse_frontmatter((self.records / proposal['path']).read_text())
            self.assertIn('records:' + receipt['event_path'], fm['sources'])
            self.assertNotIn('Please remember', body)
            self.assertEqual(fm['carry_state'], 'draft')
            self.assertTrue(list((self.records / 'carry' / '.history').glob('*.md')))
            again, error = call_tool('carry_propose', arguments, self.workspace.state_dir, client)
            self.assertTrue(json.loads(again)['duplicate'])
            self.assertEqual(self.captured(client)['state'], 'duplicate')

    def test_mcp_writes_are_opt_in_and_bound_to_server_client(self):
        args = dict(content=OCT15, source_refs=[], event_id='one')
        self.assertTrue(call_tool('carry_propose', args, self.workspace.state_dir, 'claude')[1])
        self.assertEqual(len(available_tools(self.workspace.state_dir, 'claude')), 2)
        self.enable('claude')
        self.assertEqual(len(available_tools(self.workspace.state_dir, 'claude')), 3)
        self.assertTrue(call_tool('carry_propose', dict(args, client='claude'), self.workspace.state_dir, 'codex')[1])
        self.assertTrue(call_tool('carry_propose', args, self.workspace.state_dir, None)[1])
        self.assertNotIn('carry_accept', [t['name'] for t in available_tools(self.workspace.state_dir, 'claude')])
        lifecycle.configure_client(self.workspace, 'claude', 'records', False)
        self.assertTrue(call_tool('carry_propose', args, self.workspace.state_dir, 'claude')[1])

    def test_mcp_cannot_claim_other_clients_captured_event_or_write_while_paused(self):
        self.enable('claude')
        self.enable('codex')
        receipt = self.captured('claude')
        args = dict(content=OCT15, source_refs=[], event_id=receipt['event_id'], expected_proposal_revision=1)
        self.assertTrue(call_tool('carry_propose', args, self.workspace.state_dir, 'codex')[1])
        capture.set_paused(self.workspace, 'claude', True)
        self.assertTrue(call_tool('carry_propose', args, self.workspace.state_dir, 'claude')[1])

    def test_two_fresh_stdio_clients_observe_reviewed_correction(self):
        for client in ('claude', 'codex'):
            self.enable(client)
        def rpc(client, name, arguments):
            messages = [dict(jsonrpc='2.0', id=1, method='initialize', params={'protocolVersion': '2025-06-18'}),
                        dict(jsonrpc='2.0', id=2, method='tools/call', params={'name': name, 'arguments': arguments})]
            p = subprocess.run([sys.executable, '-m', 'carry.mcp_server', '--workspace', str(self.workspace.state_dir),
                '--client', client], input='\n'.join(json.dumps(m) for m in messages)+'\n', text=True,
                capture_output=True, env=dict(os.environ, PYTHONPATH=str(SRC)), timeout=20)
            self.assertEqual(p.returncode, 0, p.stderr)
            r = json.loads(p.stdout.splitlines()[-1])['result']
            self.assertFalse(r['isError'], r)
            return r['content'][0]['text']
        a = json.loads(rpc('claude', 'carry_propose', dict(content=OCT15, source_refs=[], event_id='a')))
        r = lifecycle.review(self.workspace, a['record_id'])
        lifecycle.accept(self.workspace, a['record_id'], r['revision'], r['review_token'])
        self.assertIn('October 15', rpc('codex', 'carry_recall', dict(query='Cedar delivery')))
        b = json.loads(rpc('codex', 'carry_propose', dict(content=OCT22, source_refs=[], event_id='b',
            target_id=a['record_id'], expected_revision=1)))
        self.build()
        self.assertNotIn('October 22', rpc('claude', 'carry_recall', dict(query='Cedar delivery')))
        r = lifecycle.review(self.workspace, b['record_id'])
        lifecycle.accept(self.workspace, b['record_id'], r['revision'], r['review_token'])
        for client in ('claude', 'codex'):
            text = rpc(client, 'carry_recall', dict(query='Cedar delivery'))
            self.assertIn('October 22', text)
            self.assertNotIn('October 15', text)
            self.assertIn('rev 2 state accepted', text)
            self.assertIn('October 15', rpc(client, 'carry_recall', dict(query='Cedar delivery', include_history=True)))

    def test_non_boolean_draft_flag_is_rejected(self):
        text, error = call_tool('carry_recall', dict(query='Cedar', include_drafts='false'),
                                self.workspace.state_dir)
        self.assertTrue(error)

    def test_cli_review_accept_and_correct_requires_separate_acceptance(self):
        env = dict(os.environ, PYTHONPATH=str(SRC))
        def cli(*args, success=True):
            p = subprocess.run([sys.executable, '-m', 'carry.cli', '--workspace',
                str(self.workspace.state_dir), '--json', *args], capture_output=True, text=True,
                env=env, timeout=20)
            self.assertEqual(p.returncode, 0 if success else 1, p.stdout + p.stderr)
            return json.loads(p.stdout)
        first = cli('proposal', 'add', '--event', 'cli-a', '--content', OCT15)
        reviewed = cli('proposal', 'show', '--id', first['record_id'])
        cli('proposal', 'accept', '--id', first['record_id'], '--expect-revision', '1',
            '--review-token', 'outdated', success=False)
        cli('proposal', 'accept', '--id', first['record_id'], '--expect-revision', '1',
            '--review-token', reviewed['review_token'])
        correction = cli('record', 'correct', '--id', first['record_id'], '--expect-revision', '1',
                         '--event', 'cli-b', '--content', OCT22)
        self.assertEqual(correction['state'], 'draft')
        self.assertIn('October 15', json.dumps(cli('recall', 'Cedar delivery')['evidence']))
