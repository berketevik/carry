"""Shared index packs: a folder ships its vectors, a matching workspace skips embedding."""
import io
import tarfile

from _support import HASHING, WorkspaceCase
from carry import github, index
from carry.config import RetrievalConfig, SourceConfig, Workspace
from carry.recall import recall


class PackTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.note('Release', 'The milestone is October 22.')
        self.note('Budget', 'The fourth quarter budget is 40 thousand.')
        self.assertEqual(index.build(self.workspace)['status'], 'built')

    def reader(self, retrieval=None):
        """A second workspace over the same folder, as a teammate would have it."""
        return Workspace.create(self.base / 'reader', sources=[SourceConfig('team', self.corpus)],
                                embedding=HASHING, retrieval=retrieval or RetrievalConfig(auto_refresh=False))

    def test_pack_round_trip_skips_embedding(self):
        written = index.write_pack(self.workspace, 'corpus', self.corpus / '.carry' / 'index.db', commit='abc')
        self.assertEqual((written['commit'], written['chunks'] > 0), ('abc', True))
        reader = self.reader()
        status = index.build(reader)
        self.assertEqual((status['embedded_chunks'], status['packed_chunks']), (0, status['chunks']))
        self.assertIn('October 22', recall(reader, 'milestone')['evidence'][0]['text'])

    def test_pack_from_other_settings_is_ignored(self):
        index.write_pack(self.workspace, 'corpus', self.corpus / '.carry' / 'index.db')
        status = index.build(self.reader(RetrievalConfig(chunk_chars=600, chunk_overlap=80, auto_refresh=False)))
        self.assertEqual(status['packed_chunks'], 0)
        self.assertGreater(status['embedded_chunks'], 0)

    def test_edited_note_embeds_only_what_changed(self):
        index.write_pack(self.workspace, 'corpus', self.corpus / '.carry' / 'index.db')
        (self.corpus / 'Budget.md').write_text('# Budget\nThe budget moved to 55 thousand.', encoding='utf-8')
        status = index.build(self.reader())
        self.assertGreater(status['packed_chunks'], 0)
        self.assertGreater(status['embedded_chunks'], 0)

    def test_stale_source_refuses_to_pack_but_other_sources_may_change(self):
        (self.records / 'note.md').write_text('# Note\nWritten after the build.', encoding='utf-8')
        self.assertGreater(index.write_pack(self.workspace, 'corpus', self.base / 'out.db')['chunks'], 0)
        self.note('Late', 'Added after the build.')
        with self.assertRaisesRegex(ValueError, 'index_not_fresh:corpus'):
            index.write_pack(self.workspace, 'corpus', self.base / 'out.db')

    def test_damaged_pack_is_ignored(self):
        pack = self.corpus / '.carry' / 'index.db'
        pack.parent.mkdir()
        pack.write_bytes(b'not a database')
        self.assertEqual(index.load_pack(self.corpus, 'anything'), {})

    def test_github_archive_keeps_the_pack_and_no_other_dotfile(self):
        index.write_pack(self.workspace, 'corpus', self.base / 'pack.db')
        path = self.base / 'repo.tgz'
        with tarfile.open(path, 'w:gz') as tar:
            for name, data in {'repo/docs/a.md': b'# A\nText', 'repo/docs/.carry/index.db': (self.base / 'pack.db').read_bytes(),
                               'repo/docs/.carry/other.db': b'x', 'repo/.carry/index.db': b'outside folder'}.items():
                item = tarfile.TarInfo(name); item.size = len(data)
                tar.addfile(item, io.BytesIO(data))
        self.assertEqual(github.extract_markdown(path, self.base / 'out', 'docs'), 1)
        self.assertEqual(sorted(str(p.relative_to(self.base / 'out')) for p in (self.base / 'out').rglob('*') if p.is_file()),
                         ['.carry/index.db', 'a.md'])
        self.assertTrue(index.load_pack(self.base / 'out', index.fingerprint(self.workspace, index.build_provider(HASHING))))
