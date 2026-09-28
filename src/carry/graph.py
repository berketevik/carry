"""The vault as a map: notes are dots, gathered into topics by what they are about.

Read-only and offline. Each note joins its nearest neighbours by the search index's own
vectors, and a deterministic Louvain pass turns those neighbourhoods into topics, so notes
about the same thing group together even when they never link, and the same notes give
the same map on every refresh. A topic is named after the words its notes' titles and
summaries share and the rest of the map rarely uses. The lines drawn are the [[links]].
Positions are computed here: topics are laid out as separate discs first, then each
topic's notes inside its disc, so the topics read as clouds.
"""
import math
import os
from pathlib import Path
import random
import re
import unicodedata

from .errors import CarryError
from .index import open_readonly, unpack
from .markdown import parse_frontmatter
from .vaultview import (MAX_NOTE_BYTES, WIKILINK_RE, _files, _local, _managed, _text,
                        excluded, is_system)

MAX_NODES = 1500
COLORS = 8          # categorical slots the app can colour; smaller groups stay neutral
UNLINKED = -1       # notes the index has no vector for yet
NEIGHBOURS = 6      # each note joins its closest notes by vector

try:  # optional acceleration, as in recall
    import numpy as _np
except ImportError:  # pragma: no cover - exercised on installs without numpy
    _np = None


class _Resolver:
    """vaultview._resolve with its lookup tables built once for the whole vault."""

    def __init__(self, paths):
        self.bare = {p.removesuffix('.md'): p for p in paths}
        self.by_name = {}
        for key, p in self.bare.items():
            self.by_name.setdefault(key.rsplit('/', 1)[-1], []).append(p)

    def __call__(self, target, from_rel):
        wanted = target.strip().removesuffix('.md').strip('/')
        if not wanted:
            return None
        if wanted in self.bare:
            return self.bare[wanted]
        if from_rel and '/' in from_rel:
            near = from_rel.rsplit('/', 1)[0] + '/' + wanted
            if near in self.bare:
                return self.bare[near]
        if '/' in wanted:
            ends = [p for k, p in self.bare.items() if k.endswith('/' + wanted)]
        else:
            ends = self.by_name.get(wanted, [])
        return min(ends, key=lambda p: (p.count('/'), p)) if ends else None


def _read(root, src, managed):
    """Every searchable, non-system Markdown file: path, title, type, summary and raw links."""
    notes = []
    for relative, path in _files(root, src):
        rel = relative.replace(os.sep, '/')
        if is_system(rel, managed) or excluded(rel, src.exclude):
            continue
        try:
            if path.stat().st_size > MAX_NOTE_BYTES:
                continue
            raw = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        front, body = parse_frontmatter(raw)
        front = front if isinstance(front, dict) else {}
        heading = next((l[2:].strip() for l in body.splitlines() if l.startswith('# ')), '')
        # The whole file: `up:` and `related:` links in frontmatter are edges too.
        links = [m.group(1) for m in WIKILINK_RE.finditer(raw)]
        summary = _text(front.get('summary'))
        if not summary:     # a vault without summaries still names its topics from the opening lines
            summary = ' '.join(l for l in body.splitlines() if l.strip() and not l.startswith('#'))[:300]
        notes.append(dict(path=rel, title=_text(front.get('title')) or heading or Path(rel).stem,
                          type=_text(front.get('type')), links=links, summary=summary))
    return notes


def _louvain(n, adj, seed=0):
    """Community per node. Weighted undirected graph, adj[i] = {j: w}. Deterministic."""
    community = list(range(n))
    nodes_of = [[i] for i in range(n)]      # original nodes inside each current super-node
    graph = [dict(a) for a in adj]
    rng = random.Random(seed)
    while True:
        m2 = sum(sum(a.values()) for a in graph)
        if m2 == 0:
            break
        size = len(graph)
        degree = [sum(a.values()) for a in graph]
        comm = list(range(size))
        total = degree[:]
        order = list(range(size))
        rng.shuffle(order)
        moved_any, improved = False, True
        while improved:
            improved = False
            for i in order:
                ci = comm[i]
                links = {}
                for j, w in graph[i].items():
                    if j != i:
                        links[comm[j]] = links.get(comm[j], 0.0) + w
                total[ci] -= degree[i]
                best, best_gain = ci, links.get(ci, 0.0) - total[ci] * degree[i] / m2
                for c in sorted(links):
                    gain = links[c] - total[c] * degree[i] / m2
                    if gain > best_gain + 1e-12:
                        best, best_gain = c, gain
                total[best] += degree[i]
                if best != ci:
                    comm[i] = best
                    improved = moved_any = True
        if not moved_any:
            break
        labels = {c: k for k, c in enumerate(sorted(set(comm)))}
        merged = [[] for _ in labels]
        new_graph = [dict() for _ in labels]
        for i in range(size):
            ci = labels[comm[i]]
            merged[ci] += nodes_of[i]
            for j, w in graph[i].items():
                cj = labels[comm[j]]
                new_graph[ci][cj] = new_graph[ci].get(cj, 0.0) + w
        nodes_of, graph = merged, new_graph
    for k, members in enumerate(nodes_of):
        for i in members:
            community[i] = k
    return community


def _force(n, edges, iterations, rng, radius=1.0):
    """Fruchterman-Reingold inside a unit disc; returns positions scaled to `radius`."""
    if n == 1:
        return [(0.0, 0.0)]
    pos = [[rng.uniform(-1, 1), rng.uniform(-1, 1)] for _ in range(n)]
    k = math.sqrt(4.0 / n)
    temp = 0.2
    for _ in range(iterations):
        disp = [[0.0, 0.0] for _ in range(n)]
        for i in range(n):
            xi, yi = pos[i]
            for j in range(i + 1, n):
                dx, dy = xi - pos[j][0], yi - pos[j][1]
                d2 = dx * dx + dy * dy or 1e-6
                f = k * k / d2
                disp[i][0] += dx * f; disp[i][1] += dy * f
                disp[j][0] -= dx * f; disp[j][1] -= dy * f
        for i, j in edges:
            dx, dy = pos[i][0] - pos[j][0], pos[i][1] - pos[j][1]
            d = math.sqrt(dx * dx + dy * dy) or 1e-6
            f = d / k
            disp[i][0] -= dx * f; disp[i][1] -= dy * f
            disp[j][0] += dx * f; disp[j][1] += dy * f
        for i in range(n):
            # A light pull to the centre keeps unlinked members from drifting to the rim.
            disp[i][0] -= pos[i][0] * 0.5; disp[i][1] -= pos[i][1] * 0.5
            dx, dy = disp[i]
            d = math.sqrt(dx * dx + dy * dy) or 1e-6
            step = min(d, temp)
            pos[i][0] += dx / d * step; pos[i][1] += dy / d * step
        temp = max(temp * 0.97, 0.005)
    cx = sum(p[0] for p in pos) / n; cy = sum(p[1] for p in pos) / n
    far = max(math.hypot(p[0] - cx, p[1] - cy) for p in pos) or 1.0
    return [((p[0] - cx) / far * radius, (p[1] - cy) / far * radius) for p in pos]


def _pack(radii, weights, rng):
    """Centres for discs of the given radii: related groups pulled together, no overlaps."""
    n = len(radii)
    golden = math.pi * (3 - math.sqrt(5))
    spread = math.sqrt(sum(r * r for r in radii))
    pos = [[spread * math.sqrt((i + 0.5) / n) * math.cos(i * golden),
            spread * math.sqrt((i + 0.5) / n) * math.sin(i * golden)] for i in range(n)]
    # Room between clouds for the topic name drawn above each one.
    gap = (sum(radii) / n) * 0.8 if radii else 0
    steps = 500
    for step in range(steps):
        # Related groups pull together for the first part; the rest only separates them,
        # so no two clouds overlap when it ends.
        pull = 0.02 * (1 - step / 300) if step < 300 else 0.0
        for (a, b), w in weights.items():
            dx, dy = pos[b][0] - pos[a][0], pos[b][1] - pos[a][1]
            f = pull * min(w, 5) / 5
            pos[a][0] += dx * f; pos[a][1] += dy * f
            pos[b][0] -= dx * f; pos[b][1] -= dy * f
        if pull:
            for i in range(n):
                pos[i][0] -= pos[i][0] * 0.01; pos[i][1] -= pos[i][1] * 0.01
        for i in range(n):
            for j in range(i + 1, n):
                dx, dy = pos[j][0] - pos[i][0], pos[j][1] - pos[i][1]
                d = math.hypot(dx, dy)
                need = radii[i] + radii[j] + gap
                if d < need:
                    if d < 1e-9:
                        a = rng.uniform(0, 2 * math.pi); dx, dy, d = math.cos(a), math.sin(a), 1.0
                    push = (need - d) / 2
                    pos[i][0] -= dx / d * push; pos[i][1] -= dy / d * push
                    pos[j][0] += dx / d * push; pos[j][1] += dy / d * push
    return pos


def _note_vectors(ws, source_id, paths):
    """One unit vector per note: the mean of its indexed chunks. Notes not indexed yet are absent."""
    try:
        con = open_readonly(ws.db_path)
    except Exception:
        raise CarryError('graph_needs_index')
    wanted, sums, meta = set(paths), {}, {}
    try:
        meta = dict(con.execute('SELECT key, value FROM meta').fetchall())
        for path, dim, blob in con.execute('SELECT path, dim, vec FROM chunks WHERE source_id = ?', (source_id,)):
            if path not in wanted or not blob:
                continue
            v = unpack(blob, dim)
            acc = sums.get(path)
            if acc is None:
                sums[path] = list(v)
            elif len(acc) == len(v):
                for k, x in enumerate(v):
                    acc[k] += x
    except Exception:
        raise CarryError('graph_needs_index')
    finally:
        con.close()
    vectors = {}
    for path, acc in sums.items():
        norm = math.sqrt(sum(x * x for x in acc))
        if norm > 0:
            vectors[path] = [x / norm for x in acc]
    return vectors, meta.get('semantic') == '1'


def _neighbours(vectors, k=NEIGHBOURS):
    """Symmetric k-nearest-neighbour weights {(a, b): cosine} over the given vectors."""
    n = len(vectors)
    if n < 2:
        return {}
    if _np is not None:
        m = _np.array(vectors, dtype='float32')
        sim = m @ m.T
        _np.fill_diagonal(sim, -1.0)
        top = _np.argsort(-sim, axis=1, kind='stable')[:, :min(k, n - 1)]
        pairs = ((i, int(j), float(sim[i, j])) for i in range(n) for j in top[i])
    else:
        def near(i):
            scores = sorted(((-sum(a * b for a, b in zip(vectors[i], vectors[j])), j) for j in range(n) if j != i))[:k]
            return [(i, j, -s) for s, j in scores]
        pairs = (p for i in range(n) for p in near(i))
    weights = {}
    for i, j, w in pairs:
        if w > 0:
            key = (min(i, j), max(i, j))
            weights[key] = max(weights.get(key, 0.0), w)
    return weights


# Words that say nothing about a topic: Turkish and English function words, note-kind
# vocabulary, and the words every vault uses for itself.
STOPWORDS = frozenset("""
ve ile için icin bir bu da de ne ki mi mı mu mü olarak olan olanı gibi daha çok en her hem ya veya ama fakat
ise yok var değil iş işi işler işleri taraf tarafı tarafında tarafından sonra önce kadar göre üzerinde üzerinden içinde arasında nasıl neden hangi tüm bütün şu o
onu bunu bunun şunu kendi yeni iki üç tek ayrı aynı başka artık hâlâ hala sadece yalnızca ilk son eden edilen
etmek yapmak yapılan olan olur oldu olması durum şekilde kez kısa uzun büyük küçük hakkında ilgili dair tarafı
the and for with from that this into over are was were not but its use using used how what why which when who
about via per based also has have had can will than then more most each all any one two new first last only
note notes not notu notlar notları vault summary type draft statement effort thing person topic wiki article
index source daily output memory stub place work dev moc raw chat
""".split())
_WORD = re.compile(r"[0-9a-zçğıöşüâîû][0-9a-zçğıöşüâîû\-]*[0-9a-zçğıöşüâîû]")
_SUFFIX = re.compile(r"[\'’][a-zçğıöşü]+")


def _fold(word):
    """Accent-free key, so `koçtaş` and `koctas` count as one word."""
    return unicodedata.normalize('NFKD', word.replace('ı', 'i')).encode('ascii', 'ignore').decode()


def _terms(text):
    """(key, surface) unigrams and bigrams of a title or summary."""
    text = _SUFFIX.sub('', text.replace('İ', 'i').lower())     # Ankara'nın -> ankara
    words = [w for w in _WORD.findall(text) if len(w) >= 3 and not w.isdigit()]
    out, prev = [], None
    for w in words:
        if w in STOPWORDS:
            prev = None
            continue
        out.append((_fold(w), w))
        if prev:
            out.append((prev[0] + ' ' + _fold(w), prev[1] + ' ' + w))
        prev = (_fold(w), w)
    return out


def _keyword_labels(ordered, groups, notes):
    """Topic names from what the group's notes say: the words and word pairs of their titles
    and summaries that are common inside the group and rare elsewhere."""
    docs, surface = [], {}
    for nd in notes:
        found = _terms(nd['title'] + ' ' + nd['title'] + ' ' + (nd.get('summary') or ''))
        for key, word in found:
            surface.setdefault(key, {}).setdefault(word, 0)
            surface[key][word] += 1
        docs.append({key for key, _ in found})
    n = len(docs)
    df = {}
    for d in docs:
        for key in d:
            df[key] = df.get(key, 0) + 1
    taken, labels = set(), {}
    for key in ordered:
        if key == UNLINKED:
            continue
        members = groups[key]
        counts = {}
        for i in members:
            for term in docs[i]:
                counts[term] = counts.get(term, 0) + 1
        floor = max(2, math.ceil(len(members) * 0.2)) if len(members) > 2 else 1
        # A word in more than a tenth of the whole map (the owner's name, a stream's boilerplate)
        # names no single topic.
        common = max(3, n * 0.1)
        rare = lambda t: all(df.get(p, 0) <= common for p in t.split(' '))
        score = {t: c / len(members) * math.log(n / df[t]) for t, c in counts.items() if c >= floor and rare(t)}
        if not score:   # every telling word is vault-wide: better a common name than none
            score = {t: c / len(members) * math.log(n / df[t]) for t, c in counts.items() if c >= floor}
        # A pair that scores at least as well as its words replaces them ("mac mini").
        for t in [t for t in score if ' ' in t]:
            parts = t.split(' ')
            if all(score[t] >= score.get(p, 0) * 0.8 for p in parts):
                for p in parts:
                    score.pop(p, None)
            else:
                score.pop(t)
        ranked = sorted(score, key=lambda t: (-score[t], t))
        picked = []
        for t in ranked:
            if not picked and t in taken:
                continue
            if any(t in p or p in t for p in picked):
                continue
            if picked and score[t] < score[picked[0]] * 0.5:
                break       # a weak extra word blurs the name more than it helps
            picked.append(t)
            if len(picked) == 3:
                break
        if picked:
            taken.add(picked[0])
        words = [max(surface[t].items(), key=lambda kv: (kv[1], kv[0]))[0] for t in picked]
        labels[key] = ' · '.join(w[:1].upper() + w[1:] for w in words)
    return labels


def build(ws, source_id, folders=None):
    """The map of one local source. `folders` limits it to those top-level folders
    (None: `notes/` when the vault has one, else everything searchable)."""
    src, root = _local(ws, source_id)
    notes = _read(root, src, _managed(root))
    tops = sorted({n['path'].split('/', 1)[0] if '/' in n['path'] else '' for n in notes})
    if folders is None:
        folders = ['notes'] if 'notes' in tops else tops
    if not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
        raise CarryError('invalid_graph_folders')
    wanted = set(folders)
    resolve = _Resolver([n['path'] for n in notes])     # links resolve against the whole vault
    notes = [n for n in notes if (n['path'].split('/', 1)[0] if '/' in n['path'] else '') in wanted]
    index = {n['path']: i for i, n in enumerate(notes)}

    weights = {}
    for i, n in enumerate(notes):
        for target in n['links']:
            j = index.get(resolve(target, n['path']))
            if j is not None and j != i:
                key = (min(i, j), max(i, j))
                weights[key] = weights.get(key, 0) + 1
    degree = [0] * len(notes)
    for a, b in weights:
        degree[a] += 1; degree[b] += 1

    truncated = False
    if len(notes) > MAX_NODES:
        keep = sorted(sorted(range(len(notes)), key=lambda i: (-degree[i], notes[i]['path']))[:MAX_NODES])
        remap = {old: new for new, old in enumerate(keep)}
        notes = [notes[i] for i in keep]
        index = {n['path']: i for i, n in enumerate(notes)}
        weights = {(remap[a], remap[b]): w for (a, b), w in weights.items() if a in remap and b in remap}
        degree = [0] * len(notes)
        for a, b in weights:
            degree[a] += 1; degree[b] += 1
        truncated = True

    n = len(notes)
    vectors, semantic = _note_vectors(ws, src.source_id, [nd['path'] for nd in notes])
    have = [i for i in range(n) if notes[i]['path'] in vectors]
    near = _neighbours([vectors[notes[i]['path']] for i in have])
    grouping = {(have[a], have[b]): w for (a, b), w in near.items()}
    joined = [0] * n
    adj = [dict() for _ in range(n)]
    for (a, b), w in grouping.items():
        adj[a][b] = adj[b][a] = w
        joined[a] += 1; joined[b] += 1
    community = _louvain(n, adj)
    groups = {}
    for i in range(n):
        groups.setdefault(UNLINKED if joined[i] == 0 else community[i], []).append(i)
    # Largest topic first; ties by the first member's path so the order never flickers.
    ordered = sorted((k for k in groups if k != UNLINKED), key=lambda k: (-len(groups[k]), notes[groups[k][0]]['path']))
    if UNLINKED in groups:
        ordered.append(UNLINKED)
    cluster_of = {}
    for cid, key in enumerate(ordered):
        for i in groups[key]:
            cluster_of[i] = cid

    labels = _keyword_labels(ordered, groups, notes)
    rng = random.Random(0)
    radii = [math.sqrt(len(groups[key])) for key in ordered]
    between = {}
    for (a, b), w in grouping.items():
        ca, cb = cluster_of[a], cluster_of[b]
        if ca != cb:
            key = (min(ca, cb), max(ca, cb))
            between[key] = between.get(key, 0) + w
    centres = _pack(radii, between, rng)
    xy = [None] * n
    clusters = []
    for cid, key in enumerate(ordered):
        members = groups[key]
        local = {i: k for k, i in enumerate(members)}
        inner = [(local[a], local[b]) for (a, b) in grouping if a in local and b in local]
        iterations = max(30, min(200, int(2_000_000 / max(1, len(members)) ** 2)))
        placed = _force(len(members), inner, iterations, rng, radius=radii[cid])
        for i, (x, y) in zip(members, placed):
            xy[i] = (centres[cid][0] + x, centres[cid][1] + y)
        linked = key != UNLINKED
        clusters.append(dict(id=cid, size=len(members), unlinked=not linked,
                             label=labels.get(key, ''),
                             color=cid if linked and cid < COLORS else None,
                             x=round(centres[cid][0], 3), y=round(centres[cid][1], 3), r=round(radii[cid], 3)))
    return dict(
        source_id=src.source_id, folders=sorted(wanted), available_folders=tops, truncated=truncated,
        semantic=semantic,
        nodes=[dict(path=nd['path'], title=nd['title'], type=nd['type'], cluster=cluster_of[i], degree=degree[i],
                    x=round(xy[i][0], 3), y=round(xy[i][1], 3)) for i, nd in enumerate(notes)],
        edges=[[a, b] for (a, b) in sorted(weights)],
        clusters=clusters)
