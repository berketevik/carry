"""GitHub snapshots: hostile archives, exact revisions, errors and source isolation."""
import io
import json
from pathlib import Path
import tarfile
from unittest.mock import patch
from dataclasses import replace

from _support import WorkspaceCase, tree_digest
from carry import github, index, maintenance
from carry.config import Workspace
from carry.errors import CarryError
from carry.recall import recall


def archive(path, files, special=None):
    with tarfile.open(path, 'w:gz') as tar:
        for name, text in files.items():
            data = text.encode(); item = tarfile.TarInfo(name); item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
        if special:
            tar.addfile(special)


class GithubTest(WorkspaceCase):
    def fake_download(self, repo, sha, path):
        archive(path, {'repo/docs/release.md': '# Release\nDelivery is October 22.',
                       'repo/other.md': 'Not in the selected folder',
                       'repo/.hidden/secret.md': 'Excluded content'})

    def fake_api(self, path):
        return {'sha': 'a' * 40} if '/commits/' in path else {'default_branch': 'main'}

    def test_connect_readonly_folder_and_commit_citation(self):
        before = tree_digest(self.corpus)
        with patch.object(github, 'api', side_effect=self.fake_api), patch.object(github, 'download', side_effect=self.fake_download):
            result = maintenance.run(self.workspace, 'github_connect', dict(source_id='team', repository='acme/docs', folder='docs'))
        self.assertEqual(result['status'], 'built')
        ws = Workspace.load(self.workspace.state_dir)
        self.assertFalse(ws.source('team').writable)
        evidence = recall(ws, 'Delivery', source_ids=['team'])['evidence']
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['url'], 'https://github.com/acme/docs/blob/' + 'a'*40 + '/docs/release.md')
        self.assertEqual(tree_digest(self.corpus), before)

    def test_changed_revision_removes_old_documents_and_preserves_snapshot(self):
        with patch.object(github, 'api', side_effect=self.fake_api), patch.object(github, 'download', side_effect=self.fake_download):
            maintenance.run(self.workspace, 'github_connect', dict(source_id='team', repository='acme/docs', folder='docs'))
        ws = Workspace.load(self.workspace.state_dir); previous = ws.source('team').root
        def next_download(repo, commit, path):
            archive(path, {'repo/docs/new.md': '# Revision\nReplacement milestone is November 8.'})
        with patch.object(github, 'api', return_value={'sha': 'b'*40}), patch.object(github, 'download', side_effect=next_download):
            maintenance.run(ws, 'github_sync', dict(source_id='team'))
        current = Workspace.load(ws.state_dir)
        self.assertTrue((previous / 'release.md').exists())
        self.assertEqual(index.health(current)['state'], 'fresh')
        self.assertEqual(recall(current, 'October 22', source_ids=['team'])['evidence'], [])
        self.assertIn('new.md', [r['path'] for r in recall(current, 'Replacement milestone')['evidence']])

    def test_same_commit_skips_download(self):
        with patch.object(github, 'api', side_effect=self.fake_api), patch.object(github, 'download', side_effect=self.fake_download):
            github.connect(self.workspace, 'team', 'acme/docs')
        ws = Workspace.load(self.workspace.state_dir)
        with patch.object(github, 'api', side_effect=self.fake_api), patch.object(github, 'download') as download:
            result = github.sync(ws, 'team')
        download.assert_not_called(); self.assertEqual(result['status'], 'unchanged')

    def test_failed_download_keeps_source_and_index(self):
        self.note('Local', 'Known answer'); self.build()
        before = self.workspace.config_path.read_bytes(), self.workspace.db_path.read_bytes()
        with patch.object(github, 'api', side_effect=self.fake_api), patch.object(github, 'download', side_effect=CarryError('github_download_failed')):
            with self.assertRaises(CarryError):
                github.connect(self.workspace, 'team', 'acme/docs')
        self.assertEqual(before, (self.workspace.config_path.read_bytes(), self.workspace.db_path.read_bytes()))

    def test_reject_urls_credentials_traversal_and_invalid_folder(self):
        for repo in ('https://secret@github.com/acme/docs', 'https://evil.example/acme/docs', '../docs', 'acme/..', 'https://github.com/acme/docs?token=x'):
            with self.assertRaises(CarryError): github.repository_name(repo)
        for folder in ('../private', '/private', 'docs/../../private', 'docs\\private'):
            with self.assertRaises(CarryError): github.folder_name(folder)
        self.assertEqual(github.repository_name('https://github.com/acme/docs.git'), 'acme/docs')

    def test_archive_traversal_is_rejected(self):
        path = self.base/'bad.tgz'; archive(path, {'repo/../../escape.md':'bad'})
        with self.assertRaises(CarryError): github.extract_markdown(path, self.base/'out')
        self.assertFalse((self.base.parent/'escape.md').exists())

    def test_archive_links_are_not_followed(self):
        item = tarfile.TarInfo('repo/link.md'); item.type = tarfile.SYMTYPE; item.linkname = '/etc/passwd'
        path = self.base/'links.tgz'; archive(path, {'repo/good.md':'Good'}, item)
        github.extract_markdown(path, self.base/'out')
        self.assertEqual([p.name for p in (self.base/'out').iterdir()], ['good.md'])

    def test_archive_size_and_empty_folder_fail(self):
        path = self.base/'large.tgz'; archive(path, {'repo/good.md':'Too long'})
        with patch.object(github, 'MAX_CONTENT', 2), self.assertRaises(CarryError):
            github.extract_markdown(path, self.base/'large')
        with self.assertRaisesRegex(CarryError, 'no_markdown'):
            github.extract_markdown(path, self.base/'empty', 'missing')

    def test_excluded_content_disappears_without_editing_source(self):
        self.note('Allowed', 'Visible'); self.note('Private', 'Invisible sentinel')
        before = tree_digest(self.corpus)
        ws = self.workspace.with_sources([replace(s, exclude=('Private.md',)) if s.source_id == 'corpus' else s for s in self.workspace.sources]).save()
        index.build(ws)
        self.assertEqual(recall(ws, 'Invisible sentinel')['evidence'], [])
        self.assertEqual(tree_digest(self.corpus), before)
