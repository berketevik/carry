"""GitHub snapshots: hostile archives, exact revisions, errors and source isolation."""
import io
import json
import tarfile
from unittest.mock import patch
from dataclasses import replace

from datetime import datetime

from _support import WorkspaceCase, tree_digest
from carry import commits, github, index, maintenance, team, vaultview
from carry.config import Workspace
from carry.errors import CarryError
from carry.mcp_server import _format_recall
from carry.recall import recall

REAL_GRAPHQL = github.graphql
RELEASE_COMMIT = '2026-09-11T11:29:00Z'


def local(iso):
    return datetime.fromisoformat(iso).astimezone().isoformat(timespec='seconds')


def archive(path, files, special=None):
    with tarfile.open(path, 'w:gz') as tar:
        for name, text in files.items():
            data = text.encode(); item = tarfile.TarInfo(name); item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
        if special:
            tar.addfile(special)


class GithubTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        # Tests never reach GitHub: commit dates come from this table.
        self.committed = {'docs/release.md': RELEASE_COMMIT}
        self.asked = []
        stub = patch.object(github, 'graphql', side_effect=self.fake_graphql)
        stub.start()
        self.addCleanup(stub.stop)

    def fake_graphql(self, query, variables):
        paths = [variables[f'p{i}'] for i in range(len(variables) - 3)]
        self.asked.append(paths)
        nodes = lambda p: [{'committedDate': self.committed[p]}] if p in self.committed else []
        return {'repository': {'object': {f'f{i}': {'nodes': nodes(p)} for i, p in enumerate(paths)}}}

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

    def test_a_sync_asked_for_by_hand_downloads_the_same_commit_again(self):
        # The snapshot an older Carry made at this commit: without the team's settings.
        def old(repo, sha, path):
            archive(path, {'repo/docs/release.md': '# Release\nDelivery is October 22.'})
        with patch.object(github, 'api', side_effect=self.fake_api), patch.object(github, 'download', side_effect=old):
            github.connect(self.workspace, 'team', 'acme/docs')
        ws = Workspace.load(self.workspace.state_dir)
        self.assertIsNone(team.sign_in(ws)['trello_api_key'])
        key = 'f' * 32
        def full(repo, sha, path):
            archive(path, {'repo/docs/release.md': '# Release\nDelivery is October 22.',
                           'repo/.carry/team.json': json.dumps(dict(trello_api_key=key))})
        self.asked.clear()
        with patch.object(github, 'api', side_effect=self.fake_api), patch.object(github, 'download', side_effect=full):
            maintenance.run(ws, 'github_sync', dict(source_id='team'))
        current = Workspace.load(ws.state_dir)
        self.assertEqual(team.sign_in(current)['trello_api_key'], key)
        self.assertEqual(index.health(current)['state'], 'fresh')
        self.assertEqual(self.asked, [])  # the same bytes keep their commit dates

    def test_failed_download_keeps_source_and_index(self):
        self.note('Local', 'Known answer'); self.build()
        before = self.workspace.config_path.read_bytes(), self.workspace.db_path.read_bytes()
        with patch.object(github, 'api', side_effect=self.fake_api), patch.object(github, 'download', side_effect=CarryError('github_download_failed')):
            with self.assertRaises(CarryError):
                github.connect(self.workspace, 'team', 'acme/docs')
        self.assertEqual(before, (self.workspace.config_path.read_bytes(), self.workspace.db_path.read_bytes()))

    def connect(self):
        with patch.object(github, 'api', side_effect=self.fake_api), patch.object(github, 'download', side_effect=self.fake_download):
            result = maintenance.run(self.workspace, 'github_connect', dict(source_id='team', repository='acme/docs', folder='docs'))
        self.assertEqual(result['status'], 'built')
        return Workspace.load(self.workspace.state_dir)

    def test_last_commit_reaches_evidence_the_assistant_and_the_notes_page(self):
        ws = self.connect()
        self.assertEqual(self.asked, [['docs/release.md']])
        item = recall(ws, 'Delivery', source_ids=['team'])['evidence'][0]
        self.assertEqual(item['committed_at'], local(RELEASE_COMMIT))
        # The note records no date of its own, so it belongs to the day of its last commit.
        self.assertEqual(item['date'], local(RELEASE_COMMIT)[:10])
        text, _ = _format_recall(recall(ws, 'Delivery', source_ids=['team']))
        self.assertIn('Last commit to this file: ' + local(RELEASE_COMMIT), text)
        expected = datetime.fromisoformat(RELEASE_COMMIT).timestamp()
        listed = {f['path']: f for f in vaultview.browse(ws, 'team')['files']}
        self.assertEqual(listed['release.md']['modified'], expected)
        self.assertEqual(vaultview.note(ws, 'team', 'release.md')['modified'], expected)

    def test_own_notes_have_no_commit_date(self):
        self.note('Local', 'Known answer'); self.build()
        item = recall(self.workspace, 'Known answer')['evidence'][0]
        self.assertIsNone(item['committed_at'])
        self.assertEqual(item['date'], '2026-09-01')

    def test_dates_github_could_not_give_are_asked_again_on_the_next_sync(self):
        with patch.object(github, 'graphql', side_effect=CarryError('github_access_failed')):
            ws = self.connect()
        self.assertFalse(commits.sidecar(ws.source('team').root).exists())
        self.assertIsNone(recall(ws, 'Delivery', source_ids=['team'])['evidence'][0]['committed_at'])
        with patch.object(github, 'api', side_effect=self.fake_api), patch.object(github, 'download') as download:
            result = github.sync(ws, 'team')
        download.assert_not_called()
        self.assertEqual((result['status'], result['commit_dates']), ('unchanged', 1))
        self.assertEqual(recall(ws, 'Delivery', source_ids=['team'])['evidence'][0]['committed_at'], local(RELEASE_COMMIT))

    def test_a_new_revision_asks_only_for_files_that_changed(self):
        def first(repo, sha, path):
            archive(path, {'repo/docs/release.md': '# Release\nDelivery is October 22.',
                           'repo/docs/keep.md': '# Keep\nThe archive stays.'})
        self.committed['docs/keep.md'] = '2026-08-01T09:00:00Z'
        with patch.object(github, 'api', side_effect=self.fake_api), patch.object(github, 'download', side_effect=first):
            github.connect(self.workspace, 'team', 'acme/docs', folder='docs')
        ws = Workspace.load(self.workspace.state_dir)

        def second(repo, sha, path):
            archive(path, {'repo/docs/release.md': '# Release\nDelivery moved to November 3.',
                           'repo/docs/keep.md': '# Keep\nThe archive stays.',
                           'repo/docs/new.md': '# New\nA fresh page.'})
        self.committed.update({'docs/release.md': '2026-10-02T08:00:00Z', 'docs/new.md': '2026-10-02T08:00:00Z',
                               'docs/keep.md': '2026-10-02T08:00:00Z'})  # would show if keep.md were asked again
        self.asked.clear()
        with patch.object(github, 'api', return_value={'sha': 'b' * 40}), patch.object(github, 'download', side_effect=second):
            result = github.sync(ws, 'team')
        self.assertEqual(result['commit_dates'], 3)
        self.assertEqual(self.asked, [['docs/new.md', 'docs/release.md']])
        dates = commits.for_source(Workspace.load(ws.state_dir).source('team'))
        self.assertEqual(dates['keep.md'], local('2026-08-01T09:00:00Z'))
        self.assertEqual(dates['release.md'], local('2026-10-02T08:00:00Z'))

    def test_graphql_sends_only_queries_and_keeps_paths_out_of_the_query_text(self):
        with self.assertRaisesRegex(CarryError, 'invalid_github_query'):
            REAL_GRAPHQL('mutation { deleteRepository }', {})
        seen = {}
        def capture(query, variables):
            seen.update(query=query, variables=variables)
            return {'repository': {'object': {'f0': {'nodes': [{'committedDate': RELEASE_COMMIT}]}, 'f1': {'nodes': []}}}}
        with patch.object(github, 'graphql', side_effect=capture):
            found = github.last_commits('acme/docs', 'a' * 40, ['docs/release.md', 'docs/"} mutation {.md'])
        self.assertEqual(found, [RELEASE_COMMIT, None])
        self.assertTrue(seen['query'].startswith('query('))
        self.assertNotIn('release.md', seen['query'])
        self.assertNotIn('mutation', seen['query'])
        self.assertEqual(seen['variables']['p1'], 'docs/"} mutation {.md')
        with self.assertRaisesRegex(CarryError, 'invalid_commit'):
            github.last_commits('acme/docs', 'main', ['docs/release.md'])

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
