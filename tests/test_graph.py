"""The note map: notes about the same thing become named topics, links are the lines, the same vault gives the same map."""
import math
from pathlib import Path
import tempfile
import unittest

from carry import graph, index
from carry.config import EmbeddingConfig, SourceConfig, Workspace
from carry.desktop import Bridge
from carry.errors import CarryError


def note(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


# Hashing vectors stand in for Ollama: same-topic notes share vocabulary.
KITCHEN = {'Pasta': 'cooking recipe pasta sauce kitchen oven', 'Soup': 'cooking recipe soup kitchen stove pot',
           'Bread': 'cooking recipe bread kitchen oven dough'}
GARAGE = {'Engine': 'car engine repair garage wrench oil', 'Tyres': 'car tyres repair garage wheel pressure',
          'Brakes': 'car brakes repair garage pads fluid'}


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.vault = base / 'vault'
        for name, words in {**KITCHEN, **GARAGE}.items():
            note(self.vault, f'notes/{name}.md', f'# {name}\n' + ' '.join([words] * 20) + '\n')
        # Links are drawn, never used to group: this one crosses the two topics.
        note(self.vault, 'notes/Soup.md', '---\nrelated: ["[[Engine]]"]\n---\n# Soup\n[[Pasta]] '
             + ' '.join([KITCHEN['Soup']] * 20) + '\n')
        note(self.vault, 'sources/Clip.md', '# Clip\n[[Pasta]] ' + ' '.join([KITCHEN['Pasta']] * 5) + '\n')
        note(self.vault, '+/Capture.md', '[[Pasta]]\n')
        self.ws = Workspace.create(base / 'state', sources=[SourceConfig('notes', self.vault, exclude=('+',))],
                                   embedding=EmbeddingConfig(provider='hashing'))
        index.build(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def cluster_of(self, g, path):
        return next(n['cluster'] for n in g['nodes'] if n['path'] == path)

    def test_notes_folder_by_default_and_excluded_files_left_out(self):
        g = graph.build(self.ws, 'notes')
        paths = {n['path'] for n in g['nodes']}
        self.assertEqual(g['folders'], ['notes'])
        self.assertIn('notes/Pasta.md', paths)
        self.assertNotIn('sources/Clip.md', paths)
        self.assertFalse(any(p.startswith('+/') for p in paths))
        self.assertEqual(g['available_folders'], ['notes', 'sources'])

    def test_notes_about_the_same_thing_form_a_named_topic(self):
        g = graph.build(self.ws, 'notes')
        self.assertFalse(g['semantic'])
        kitchen = {self.cluster_of(g, f'notes/{n}.md') for n in KITCHEN}
        garage = {self.cluster_of(g, f'notes/{n}.md') for n in GARAGE}
        self.assertEqual(len(kitchen), 1)
        self.assertEqual(len(garage), 1)
        self.assertNotEqual(kitchen, garage)       # the Soup -> Engine link does not merge them
        # Names come from what the notes say (here their opening lines).
        labels = {c['id']: c['label'].lower() for c in g['clusters']}
        self.assertTrue(any(w in labels[kitchen.pop()] for w in ('cooking', 'recipe', 'kitchen')))
        self.assertTrue(any(w in labels[garage.pop()] for w in ('car', 'repair', 'garage')))
        self.assertEqual(len(set(labels.values())), len(labels))

    def test_links_are_the_lines(self):
        g = graph.build(self.ws, 'notes')
        at = {n['path']: i for i, n in enumerate(g['nodes'])}
        pair = lambda a, b: sorted([at[f'notes/{a}.md'], at[f'notes/{b}.md']])
        self.assertEqual(sorted(g['edges']), sorted([pair('Soup', 'Engine'), pair('Soup', 'Pasta')]))

    def test_a_note_not_indexed_yet_waits_in_an_uncoloured_group(self):
        note(self.vault, 'notes/Fresh.md', '# Fresh\nWritten after the index was built.\n')
        g = graph.build(self.ws, 'notes')
        fresh = next(c for c in g['clusters'] if c['id'] == self.cluster_of(g, 'notes/Fresh.md'))
        self.assertTrue(fresh['unlinked'])
        self.assertIsNone(fresh['color'])
        self.assertEqual(fresh['label'], '')
        self.assertEqual(g['clusters'][-1], fresh)

    def test_same_vault_same_map(self):
        self.assertEqual(graph.build(self.ws, 'notes'), graph.build(self.ws, 'notes'))

    def test_topic_clouds_do_not_overlap(self):
        clusters = graph.build(self.ws, 'notes')['clusters']
        for i, a in enumerate(clusters):
            for b in clusters[i + 1:]:
                self.assertGreater(math.hypot(a['x'] - b['x'], a['y'] - b['y']), a['r'] + b['r'])

    def test_folders_widen_the_map_and_links_resolve_across_them(self):
        g = graph.build(self.ws, 'notes', ['notes', 'sources'])
        at = {n['path']: i for i, n in enumerate(g['nodes'])}
        self.assertIn(sorted([at['notes/Pasta.md'], at['sources/Clip.md']]), g['edges'])
        with self.assertRaises(CarryError):
            graph.build(self.ws, 'notes', 'notes')

    def test_node_cap_keeps_the_most_linked(self):
        old = graph.MAX_NODES
        graph.MAX_NODES = 3
        try:
            g = graph.build(self.ws, 'notes')
        finally:
            graph.MAX_NODES = old
        self.assertTrue(g['truncated'])
        self.assertEqual({n['path'] for n in g['nodes']}, {'notes/Soup.md', 'notes/Pasta.md', 'notes/Engine.md'})

    def test_needs_an_index(self):
        (self.ws.db_path).unlink()
        with self.assertRaises(CarryError):
            graph.build(self.ws, 'notes')

    def test_desktop_bridge(self):
        result = Bridge().dispatch(dict(action='vault_graph', workspace=str(self.ws.state_dir)))
        self.assertEqual(len(result['nodes']), 6)


if __name__ == '__main__':
    unittest.main()
