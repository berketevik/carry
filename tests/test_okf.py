"""Open Knowledge Format v0.2: what Carry reads, derives and writes (carry.okf and its callers)."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import _support
from _support import WorkspaceCase
from carry import digest, harvest, markdown, okf, vault
from carry.config import Workspace
from carry.errors import CarryError
from carry.markdown import parse_frontmatter
from carry.recall import recall

NOW = dt.datetime(2026, 10, 16, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=3)))


class FallbackParserTest(unittest.TestCase):
    """Without PyYAML the built-in parser still reads the shapes OKF notes use."""

    def parse(self, text):
        with patch.object(markdown, '_yaml', None):
            return parse_frontmatter('---\n' + text + '\n---\nbody\n')[0]

    def test_flow_mapping_with_plain_keys_and_timestamps(self):
        front = self.parse('generated: { by: human:berke, at: "2026-10-16T14:05:00+03:00" }\n'
                           'verified: { by: carry/0.13.0, at: 2026-10-16T14:05:00+03:00 }')
        self.assertEqual(front['generated'], {'by': 'human:berke', 'at': '2026-10-16T14:05:00+03:00'})
        self.assertEqual(front['verified']['at'], '2026-10-16T14:05:00+03:00')

    def test_block_list_of_mappings_and_of_json_lines(self):
        front = self.parse('sources:\n  - resource: "/sources/a b.md"\n    title: "a b"\n'
                           '  - {"resource": "https://example.com/x", "title": "x, y"}\n'
                           '  - "[[Legacy note]]"\ntags: [a, b]')
        self.assertEqual(front['sources'], [{'resource': '/sources/a b.md', 'title': 'a b'},
                                            {'resource': 'https://example.com/x', 'title': 'x, y'},
                                            '[[Legacy note]]'])
        self.assertEqual(front['tags'], ['a', 'b'])

    def test_nested_block_mapping_and_a_bare_verified_list(self):
        front = self.parse('generated:\n  by: codex/gpt-5\n  at: 2026-10-16T09:00:00Z\n'
                           'verified:\n- {"by": "human:berke", "at": "2026-10-16T10:00:00+03:00"}\nstatus: draft')
        self.assertEqual(front['generated'], {'by': 'codex/gpt-5', 'at': '2026-10-16T09:00:00Z'})
        self.assertEqual(okf.trust(front), 'human-reviewed')
        self.assertTrue(okf.is_draft(front))


class ReadTest(unittest.TestCase):
    def test_description_falls_back_to_summary(self):
        self.assertEqual(okf.description({'description': 'New', 'summary': 'Old'}), 'New')
        self.assertEqual(okf.description({'summary': 'Old'}), 'Old')
        self.assertEqual(okf.description({}), '')

    def test_lifecycle_reads_okf_and_legacy_keys(self):
        self.assertEqual(okf.lifecycle({'status': 'draft'}), ('draft', True))
        self.assertEqual(okf.lifecycle({'draft': True}), ('draft', True))
        self.assertEqual(okf.lifecycle({'status': 'stable', 'draft': True}), ('stable', True))
        self.assertEqual(okf.lifecycle({}), ('stable', True))
        self.assertEqual(okf.lifecycle({'status': 'Deprecated'}), ('deprecated', True))
        # An effort's intensity in `status` (YAML 1.1 reads an unquoted `on` as true) is legacy, not unknown.
        effort = {'type': 'effort', 'status': True}
        self.assertEqual((okf.lifecycle(effort), okf.intensity(effort)), (('stable', True), 'on'))
        self.assertEqual(okf.intensity({'intensity': 'simmering', 'status': 'draft'}), 'simmering')
        self.assertEqual(okf.lifecycle({'status': 'synthesized'}), ('stable', False))

    def test_verified_bare_mapping_is_one_element_list_and_tiers(self):
        self.assertEqual(okf.trust({}), 'unverified')
        self.assertEqual(okf.verified({'verified': {'by': 'human:a', 'at': '2026-10-01'}}), [{'by': 'human:a', 'at': '2026-10-01'}])
        self.assertEqual(okf.trust({'verified': {'by': 'process:nightly', 'at': '2026-10-01'}}), 'machine-confirmed')
        self.assertEqual(okf.trust({'verified': [{'by': 'process:nightly'}, {'by': 'human:a'}]}), 'human-reviewed')
        self.assertEqual(okf.trust({'verified': [{'at': '2026-10-01'}, 'junk']}), 'unverified')

    def test_staleness_from_okf_instants_dates_and_pyyaml_objects(self):
        self.assertTrue(okf.is_stale({'stale_after': '2026-10-16T09:00:00Z'}, NOW))
        self.assertFalse(okf.is_stale({'stale_after': '2026-10-17T00:00:00+03:00'}, NOW))
        self.assertTrue(okf.is_stale({'stale_after': dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)}, NOW))
        # Legacy valid_until D: stale from D's first instant, local time.
        self.assertTrue(okf.is_stale({'valid_until': dt.date(2026, 10, 16)}, NOW.astimezone()))
        self.assertFalse(okf.is_stale({'valid_until': '2026-12-31'}, NOW))
        self.assertFalse(okf.is_stale({'stale_after': 'not a date'}, NOW))

    def test_pyyaml_datetimes_read_as_iso_text(self):
        front, _ = parse_frontmatter('---\ngenerated: { by: human:berke, at: 2026-10-16T14:05:00+03:00 }\n'
                                     'created: 2026-10-16\nstale_after: 2026-11-01T00:00:00Z\n---\nx\n')
        self.assertEqual(okf.generated(front), {'by': 'human:berke', 'at': '2026-10-16T14:05:00+03:00'})
        view = okf.view(front, NOW)
        self.assertEqual(view['stale_after'], '2026-11-01T00:00:00+00:00')
        self.assertFalse(view['stale'])
        json.dumps(okf.plain(front))   # every date and time is JSON text
        self.assertEqual(okf.plain(front)['created'], '2026-10-16')

    def test_source_entries_from_mappings_and_legacy_strings(self):
        front = {'sources': [{'resource': '/sources/a.md', 'title': 'a'}, '[[Note B]]', 'https://x.org/y',
                             'sources/c.md', [['Unquoted']], {'title': 'no resource'}]}
        self.assertEqual(okf.source_refs(front), ['/sources/a.md', '[[Note B]]', 'https://x.org/y', 'sources/c.md', '[[Unquoted]]'])
        self.assertEqual(okf.source_refs({'sources': 'https://one.org'}), ['https://one.org'])
        self.assertEqual(okf.as_wikilink('/sources/carry/harvest/2026-10-16 — claude ab.md'), '[[2026-10-16 — claude ab]]')
        self.assertEqual(okf.as_wikilink('[[Note B]]'), '[[Note B]]')
        self.assertEqual(okf.as_wikilink('https://x.org/y'), 'https://x.org/y')

    def test_reserved_listings(self):
        self.assertTrue(okf.is_reserved_listing('index.md', {'okf_version': '0.2'}))
        self.assertTrue(okf.is_reserved_listing('notes/log.md', {}))
        self.assertFalse(okf.is_reserved_listing('notes/index.md', {'type': 'thing'}))
        self.assertFalse(okf.is_reserved_listing('notes/indexes.md', {}))


class ApproveTest(unittest.TestCase):
    AT = '2026-10-16T10:00:00+03:00'

    def test_status_draft_becomes_stable_with_the_owners_check(self):
        raw = '---\ntype: thing\nstatus: draft\ntags: [x]\n---\n# T\nstatus: draft stays in the body\n'
        self.assertEqual(okf.approve(raw, 'human:berke', self.AT),
                         '---\ntype: thing\nstatus: stable\ntags: [x]\n'
                         'verified: {"by": "human:berke", "at": "2026-10-16T10:00:00+03:00"}\n---\n# T\nstatus: draft stays in the body\n')

    def test_an_existing_verification_is_kept_and_extended(self):
        raw = '---\nstatus: draft\nverified: { by: process:nightly, at: "2026-10-01T00:00:00Z" }\nx: 1\n---\nb\n'
        out = okf.approve(raw, 'human:berke', self.AT)
        front, _ = parse_frontmatter(out)
        self.assertEqual([e['by'] for e in okf.verified(front)], ['process:nightly', 'human:berke'])
        self.assertEqual((front['status'], front['x']), ('stable', 1))

    def test_legacy_draft_next_to_a_legacy_status_only_drops_the_flag(self):
        raw = '---\ntype: effort\nstatus: on\ndraft: true\n---\nb\n'
        out = okf.approve(raw, 'human:berke', self.AT)
        self.assertNotIn('draft:', out)
        self.assertEqual(out.count('status:'), 1)
        self.assertEqual(okf.trust(parse_frontmatter(out)[0]), 'human-reviewed')

    def test_not_a_draft_or_no_frontmatter_changes_nothing(self):
        self.assertIsNone(okf.approve('---\nstatus: stable\n---\nb\n', 'human:berke'))
        self.assertIsNone(okf.approve('no frontmatter\n', 'human:berke'))

    def test_windows_line_endings_are_kept(self):
        out = okf.approve('---\r\nstatus: draft\r\n---\r\nb\r\n', 'human:berke', self.AT)
        self.assertTrue(out.startswith('---\r\nstatus: stable\r\nverified: '))
        self.assertNotIn('\n---\n', out.replace('\r\n', '#'))


class OwnerTest(unittest.TestCase):
    def test_owner_id_is_short_and_never_an_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(okf.owner(tmp), okf.DEFAULT_OWNER)
            self.assertEqual(okf.set_owner(tmp, 'Berke'), 'human:berke')
            self.assertEqual(okf.owner(tmp), 'human:berke')
            self.assertEqual(okf.set_owner(tmp, 'human:ismail'), 'human:ismail')
            for bad in ('berke@example.com', '', 'two words', 'x' * 80):
                with self.assertRaises(CarryError):
                    okf.set_owner(tmp, bad)
            self.assertEqual(okf.owner(tmp), 'human:ismail')


class ConformanceTest(unittest.TestCase):
    def test_counts_what_keeps_a_tree_from_conforming(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'notes').mkdir()
            (root / '.claude').mkdir()
            files = {'index.md': '---\nokf_version: "0.2"\n---\n# Vault\n',
                     'notes/ok.md': '---\ntype: thing\nstatus: stable\n---\nx\n',
                     'notes/legacy.md': '---\ntype: thing\nsummary: s\nsources:\n  - "[[ok]]"\n---\nx\n',
                     'notes/nofront.md': 'plain\n', 'notes/notype.md': '---\ntags: [a]\n---\nx\n',
                     'notes/badstatus.md': '---\ntype: effort\nstatus: on\n---\nx\n',
                     'notes/log.md': '---\ntype: thing\n---\nnote that took the name\n',
                     '.claude/agent.md': 'tool config, not part of the bundle\n'}
            for rel, text in files.items():
                (root / rel).write_text(text, encoding='utf-8')
            c = okf.conformance(root)
            self.assertEqual((c['files'], c['no_frontmatter'], c['no_type'], c['reserved_misuse'],
                              c['invalid_status'], c['legacy_keys']), (7, 1, 1, 1, 1, 1))
            self.assertFalse(c['conformant'])
            for rel in ('notes/nofront.md', 'notes/notype.md', 'notes/log.md'):
                (root / rel).unlink()
            self.assertTrue(okf.conformance(root)['conformant'])


class RecallTest(WorkspaceCase):
    def write(self, name, front, body):
        (self.corpus / f'{name}.md').write_text('---\n' + front + '\n---\n# ' + name + '\n' + body + '\n', encoding='utf-8')

    def paths(self, query, **kw):
        return {e['path']: e for e in recall(self.workspace, query, **kw)['evidence']}

    def test_deprecated_is_history_and_passages_carry_the_okf_view(self):
        self.write('gross-margin-legacy', 'type: Metric\nstatus: deprecated\nsuperseded_by: "[[gross-margin]]"',
                   'Gross margin legacy formula subtracts freight.')
        self.write('gross-margin', 'type: Metric\ndescription: "Gross margin per the finance policy."\n'
                   'verified: { by: human:ahormati, at: "2026-06-25T09:00:00Z" }\nstale_after: "2026-01-01T00:00:00Z"',
                   'Gross margin formula subtracts cost of goods.')
        self.write('draft-note', 'type: thing\nstatus: draft\ngenerated: {"by": "claude-code/x", "at": "2026-10-16T09:00:00+03:00"}',
                   'Gross margin draft thoughts.')
        self.build()
        found = self.paths('gross margin formula')
        self.assertNotIn('gross-margin-legacy.md', found)
        self.assertIn('gross-margin-legacy.md', self.paths('gross margin formula', include_history=True))
        current = found['gross-margin.md']['metadata']
        self.assertEqual((current['status'], current['trust'], current['stale']), ('stable', 'human-reviewed', True))
        self.assertEqual(current['description'], 'Gross margin per the finance policy.')
        # A draft outside the inbox stays searchable, marked as a draft and unverified.
        draft = found['draft-note.md']['metadata']
        self.assertEqual((draft['status'], draft['trust'], draft['generated']['by']), ('draft', 'unverified', 'claude-code/x'))
        result = recall(self.workspace, 'gross margin formula')
        self.assertIn('stale_evidence', result['diagnostics']['warnings'])
        json.dumps(result)

    def test_description_is_its_own_passage_and_legacy_summary_still_is(self):
        self.write('new', 'type: thing\ndescription: "Quokka habitat survey results."', 'Body without the word.')
        self.write('old', 'type: thing\nsummary: "Wombat burrow census."', 'Body again.')
        self.build()
        self.assertEqual(self.paths('quokka habitat')['new.md']['heading'], 'summary')
        self.assertEqual(self.paths('wombat burrow')['old.md']['heading'], 'summary')

    def test_a_pending_digest_waits_in_either_form(self):
        meta = '{"extractor": "claude:sonnet"}'
        self.write('digest-new', f'type: output\nstatus: draft\nharvest: {meta}', 'Platypus decision pending.')
        self.write('digest-old', f'type: output\ndraft: true\nharvest: {meta}', 'Platypus decision legacy.')
        self.write('digest-done', f'type: output\nstatus: stable\nharvest: {meta}', 'Platypus decision accepted.')
        self.build()
        found = self.paths('platypus decision')
        self.assertEqual(set(found), {'digest-done.md'})
        self.assertEqual(set(self.paths('platypus decision', include_drafts=True)),
                         {'digest-new.md', 'digest-old.md', 'digest-done.md'})

    def test_an_okf_listing_is_not_indexed_but_a_note_named_index_is(self):
        self.write('index', 'okf_version: "0.2"', '* [notes](notes/) - echidna listing')
        (self.corpus / 'sub').mkdir()
        (self.corpus / 'sub' / 'index.md').write_text('---\ntype: thing\n---\n# Echidna note\nEchidna facts.\n', encoding='utf-8')
        self.build()
        self.assertEqual(set(self.paths('echidna')), {'sub/index.md'})

    def test_new_chunking_rules_re_split_without_re_embedding(self):
        import sqlite3
        from carry import index
        self.write('a', 'type: thing\ndescription: "Kiwi orchard."', 'Kiwi body.')
        first = self.build()
        con = sqlite3.connect(self.workspace.db_path)
        con.execute("UPDATE meta SET value='old' WHERE key='chunking'")
        con.commit(); con.close()
        self.assertEqual(index.health(self.workspace)['state'], 'stale')
        again = self.build()
        self.assertEqual((again['changed_files'], again['embedded_chunks']), (1, 0))
        self.assertEqual(again['reused_chunks'], first['embedded_chunks'])
        self.assertEqual(index.health(self.workspace)['state'], 'fresh')


class WritersTest(unittest.TestCase):
    """Harvest digests, their raws, the log files accepted items go to, and approval."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        home = patch.object(Path, 'home', return_value=base / 'home'); home.start(); self.addCleanup(home.stop)
        self.root = base / 'Vault'
        vault.apply(vault.plan(self.root, language='English', workspace=base / 'ws', attach=False), git=False)
        self.ws = Workspace.load(base / 'ws')
        okf.set_owner(self.ws.state_dir, 'berke')

    def digest(self):
        item = dict(type='decision', statement='The pilot ships on 15 October.', quote='ship the pilot on 15 October',
                    exchange=1, speaker='owner', at='2026-10-15')
        exchanges = [dict(i=1, user='We ship the pilot on 15 October.', assistant='Noted.', at='2026-10-15')]
        groups = dict(new=[item], conflict=[], review=[], resolved=[], dropped=[], known=[])
        meta = dict(extractor='claude:sonnet', judge='agent', compared=True)
        return harvest.write_outputs(self.root, 'claude', 'abcd1234-ef', exchanges, groups, meta, 'English',
                                     today=dt.date(2026, 10, 16))

    def test_digest_and_raw_are_okf(self):
        raw_rel, digest_rel = self.digest()
        front, _ = parse_frontmatter((self.root / digest_rel).read_text(encoding='utf-8'))
        self.assertEqual((front['type'], front['status']), ('output', 'draft'))
        self.assertTrue(front['generated']['by'].startswith('carry/'))
        self.assertEqual(okf.source_refs(front), ['/' + raw_rel])
        self.assertFalse({'summary', 'draft'} & set(front))
        self.assertIn('1 new', front['description'])
        raw, _ = parse_frontmatter((self.root / raw_rel).read_text(encoding='utf-8'))
        self.assertEqual((raw['type'], raw['source_type']), ('chat-raw', 'harvest'))
        self.assertTrue(raw['generated']['by'].startswith('carry/'))
        with patch.object(markdown, '_yaml', None):
            plain, _ = parse_frontmatter((self.root / digest_rel).read_text(encoding='utf-8'))
        self.assertEqual((plain['status'], okf.source_refs(plain)), ('draft', ['/' + raw_rel]))
        self.assertEqual(digest.parse((self.root / digest_rel).read_text(encoding='utf-8'))['raw'],
                         '[[' + Path(raw_rel).stem + ']]')

    def test_deciding_the_last_item_logs_it_and_records_the_owners_check(self):
        raw_rel, digest_rel = self.digest()
        item = digest.items(self.ws, 'vault', digest_rel)['items'][0]
        result = digest.decide(self.ws, 'vault', digest_rel, item['id'], 'accept')
        self.assertTrue(result['done'])
        front, _ = parse_frontmatter((self.root / digest_rel).read_text(encoding='utf-8'))
        self.assertEqual((front['status'], okf.trust(front)), ('stable', 'human-reviewed'))
        self.assertEqual(okf.verified(front)[0]['by'], 'human:berke')
        log, body = parse_frontmatter((self.root / result['logged']).read_text(encoding='utf-8'))
        self.assertEqual((log['type'], okf.source_refs(log)), ('daily', ['/' + raw_rel]))
        self.assertIn('items approved from chats', log['description'])
        self.assertIn('[[' + Path(raw_rel).stem + ']], exchange 1', body)

    def test_a_legacy_digest_logs_its_raw_by_path_when_one_file_matches(self):
        raw_rel, digest_rel = self.digest()
        path = self.root / digest_rel
        text = path.read_text(encoding='utf-8')
        legacy = text.replace('status: draft\n', 'draft: true\n').replace(
            f'  - {okf.flow(okf.entry(raw_rel))}', f'  - "[[{Path(raw_rel).stem}]]"')
        path.write_text(legacy, encoding='utf-8')
        item = digest.items(self.ws, 'vault', digest_rel)['items'][0]
        result = digest.decide(self.ws, 'vault', digest_rel, item['id'], 'accept')
        log, _ = parse_frontmatter((self.root / result['logged']).read_text(encoding='utf-8'))
        self.assertEqual(okf.source_refs(log), ['/' + raw_rel])
        after = path.read_text(encoding='utf-8')
        self.assertNotIn('draft: true', after)
        self.assertIn('status: stable', after)

    def test_the_new_vault_is_a_conformant_bundle(self):
        c = okf.conformance(self.root)
        self.assertTrue(c['conformant'], c)
        self.assertEqual(c['legacy_keys'], 0, c)
        front, _ = parse_frontmatter((self.root / 'index.md').read_text(encoding='utf-8'))
        self.assertEqual(front, {'okf_version': '0.2'})


if __name__ == '__main__':
    unittest.main()
