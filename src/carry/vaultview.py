"""Read-only vault browsing and editable settings for the local app.

Browsing lists every Markdown file under a source root, including the ones its
exclusions keep out of the index, so the owner sees the whole vault and which
parts Carry searches. Nothing here writes a note. Settings changes are limited to
a fixed set of fields, validated by Workspace before they are saved.
"""
import datetime
import fnmatch
import os
from dataclasses import replace
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote

from . import okf
from .config import RetrievalConfig, Workspace
from .errors import CarryError
from .markdown import parse_frontmatter
from .paths import resolve_within, walk_markdown
from .persistence import writer_lock

MAX_FILES = 20_000
HEAD_BYTES = 4096
MAX_NOTE_BYTES = 2_000_000
MARK = re.compile(r' ?<!-- carry: (?:accepted|fixed|skipped) \d{4}-\d{2}-\d{2} -->')
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
# A plain Markdown link, as GitHub knowledge bases write them: [text](folder/Note%20name.md).
MDLINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\((?:<([^>\n]+)>|([^)\s]+))(?:\s+\"[^\"]*\")?\)")
INBOX = '+'


def link_targets(text, from_rel):
    """What a note links to: [[wikilink]] targets as written, and Markdown links to other
    notes as paths from the root (resolved against the linking note's folder, without `.md`)."""
    targets = [m.group(1).strip() for m in WIKILINK_RE.finditer(text)]
    folder = from_rel.rsplit('/', 1)[0] if '/' in from_rel else ''
    for m in MDLINK_RE.finditer(text):
        target = unquote((m.group(1) or m.group(2)).split('#', 1)[0])
        if not target.lower().endswith('.md') or ':' in target.split('/', 1)[0]:
            continue    # web pages, mail, images and other files
        joined = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(folder, target))
        if joined != '..' and not joined.startswith('../'):
            targets.append(joined[:-3])
    return targets


class Resolver:
    """Obsidian-style resolution: exact path, then next to the linking note, then a basename
    (the shortest path wins when several notes share it). None when nothing matches.
    The lookup tables are built once, so resolving every link of a vault stays cheap."""

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
            near = posixpath.normpath(from_rel.rsplit('/', 1)[0] + '/' + wanted)
            if near in self.bare:
                return self.bare[near]
        if '/' in wanted:
            ends = [p for k, p in self.bare.items() if k.endswith('/' + wanted)]
        else:
            ends = self.by_name.get(wanted, [])
        return min(ends, key=lambda p: (p.count('/'), p)) if ends else None


def excluded(relative, patterns):
    return any(fnmatch.fnmatchcase(relative, p) or relative.startswith(p.rstrip('/') + '/') for p in patterns)


def _meta(path):
    try:
        with open(path, 'rb') as f:
            data = f.read(HEAD_BYTES)
            # OKF `sources` entries can make frontmatter longer than the first read.
            if data.startswith(b'---') and b'\n---' not in data[3:]:
                data += f.read(MAX_NOTE_BYTES)
        head = data.decode('utf-8', errors='ignore')
    except OSError:
        return {}
    front, body = parse_frontmatter(head)
    front = dict(front) if isinstance(front, dict) else {}
    # The first heading is the title people read (file names stay as they were, in any language).
    heading = next((l[2:].strip() for l in body.splitlines() if l.startswith('# ')), '')
    front.setdefault('_heading', heading)
    return front


def _text(value):
    if value is None or isinstance(value, (dict, list)):
        return ''
    return str(value)


def _local(ws, source_id):
    src = ws.source(source_id)
    root = Path(src.root).expanduser().resolve()
    if not root.is_dir():
        raise CarryError('source_root_missing')
    return src, root


def _editable(ws, source_id):
    """A GitHub source is a pinned mirror replaced on every sync: the app reads it, never writes to it."""
    src, root = _local(ws, source_id)
    if src.github:
        raise CarryError('source_read_only')
    return src, root


def default_vault(ws):
    """The source the app shows first: a bootstrapped vault, else the first local
    read-only source, else the first source."""
    local = [s for s in ws.sources if not s.github]
    for s in local:
        if (Path(s.root).expanduser() / '.carry' / 'vault.json').is_file():
            return s.source_id
    for s in local:
        if not s.writable:
            return s.source_id
    return ws.sources[0].source_id if ws.sources else None


def _indexed_paths(ws, source_id):
    """Paths the published index actually holds for this source (empty if there is no index)."""
    import sqlite3
    from .index import open_readonly
    if not os.path.isfile(ws.db_path):
        return {}
    try:
        con = open_readonly(ws.db_path)
        try:
            return {p: d for p, d in con.execute("SELECT path, digest FROM files WHERE source_id=?", (source_id,))}
        finally:
            con.close()
    except sqlite3.Error:
        return {}


def _index_state(rel, src, indexed, path=None):
    """excluded, indexed, or pending (new, or edited since the index was built)."""
    if excluded(rel, src.exclude):
        return 'excluded'
    if rel not in indexed:
        return 'pending'
    if path is not None:
        import hashlib
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != indexed[rel]:
                return 'pending'
        except OSError:
            return 'pending'
    return 'indexed'


# Files a vault ships with (assistant guides, dashboards, templates, generated indexes):
# listed apart from the owner's own notes so counts mean "your notes".
SYSTEM_NAMES = {'AGENTS.md', 'AGENTS.override.md', 'CLAUDE.md', 'claude.md', 'LLM-GUIDE.md', 'VAULT-RULES.md',
                'SETUP-GUIDE.md', 'Home.md'}
SYSTEM_DIRS = ('x/', 'tools/', '.carry/')


def _managed(root):
    import json as _json
    try:
        return set(_json.loads((root / '.carry' / 'vault.json').read_text(encoding='utf-8')).get('files', {}))
    except (OSError, ValueError, AttributeError):
        return set()


def is_system(rel, managed=()):
    name = rel.rsplit('/', 1)[-1]
    return (rel in managed or ('/' not in rel and name in SYSTEM_NAMES) or rel.startswith(SYSTEM_DIRS)
            or name.endswith('_index.md') or name.startswith('MSG_FROM_'))


def _files(root, src):
    # No size cutoff here: large notes are listed, only their preview is refused.
    return walk_markdown(root, extensions=tuple(src.extensions), max_bytes=float('inf'))


def _modified(rel, path, history, local):
    """A repository file changed when it was last committed; the file's own time is when it
    was unpacked or pulled here. An uncommitted edit in a team clone is newer than both."""
    committed = history.get(rel)
    if committed and rel not in local:
        from .commits import epoch
        return epoch(committed), committed
    return path.stat().st_mtime, committed


def _history(src):
    from .commits import changed_locally, for_source
    return for_source(src), changed_locally(src)


def browse(ws, source_id):
    src, root = _local(ws, source_id)
    indexed = _indexed_paths(ws, src.source_id)
    managed = _managed(root)
    history, local = _history(src)
    files, truncated = [], False
    for relative, path in _files(root, src):
        if len(files) >= MAX_FILES:
            truncated = True
            break
        try:
            st = path.stat()
        except OSError:
            continue
        front = _meta(path)
        rel = relative.replace(os.sep, '/')
        modified, committed = _modified(rel, path, history, local)
        files.append(dict(
            path=rel, name=Path(rel).stem, title=_text(front.get('title')) or front.get('_heading') or Path(rel).stem,
            folder=rel.rsplit('/', 1)[0] if '/' in rel else '',
            modified=modified, committed=committed, size=st.st_size, indexed=not excluded(rel, src.exclude),
            index_state=_index_state(rel, src, indexed, path),
            type=_text(front.get('type')), summary=okf.description(front),
            status=_text(front.get('status')), lifecycle=okf.lifecycle(front)[0], intensity=okf.intensity(front),
            trust=okf.trust(front), stale=okf.is_stale(front), draft=okf.is_draft(front),
            locked=front.get('lock') is True, system=is_system(rel, managed),
            approvable=not is_system(rel, managed) and cannot_approve(front) is None,
            review=not is_system(rel, managed) and awaits_review(rel, front),
            digest=is_digest(front)))
    return dict(source_id=src.source_id, root=str(root), files=files, truncated=truncated)


def note(ws, source_id, relative):
    src, root = _local(ws, source_id)
    path = resolve_within(root, relative)
    if not path.is_file():
        raise CarryError('source_file_missing')
    if path.stat().st_size > MAX_NOTE_BYTES:
        raise CarryError('note_too_large')
    raw = path.read_text(encoding='utf-8', errors='replace')
    front, body = parse_frontmatter(raw)
    rel = path.relative_to(root).as_posix()
    modified, committed = _modified(rel, path, *_history(src))
    links = sorted(set(link_targets(body, rel)))
    heading = next((l[2:].strip() for l in body.splitlines() if l.startswith('# ')), '')
    return dict(source_id=src.source_id, path=rel, absolute=str(path), title=_text(front.get('title')) or heading or path.stem,
                frontmatter={k: okf.plain(v) if isinstance(v, (str, int, float, bool, list, dict)) or v is None
                             or hasattr(v, 'isoformat') else str(v) for k, v in (front or {}).items()},
                okf=okf.view(front),
                body=MARK.sub('', body) if is_digest(front) else body, modified=modified, committed=committed,
                indexed=not excluded(rel, src.exclude),
                index_state=_index_state(rel, src, _indexed_paths(ws, src.source_id), path), links=links)


def _resolve(target, from_rel, paths):
    return Resolver(paths)(target, from_rel)


def _all_paths(root, src):
    return [r.replace(os.sep, '/') for r, _ in _files(root, src)]


def backlinks(ws, source_id, relative, limit=200):
    """Notes whose [[links]] or Markdown links resolve to this note."""
    from urllib.parse import quote
    src, root = _local(ws, source_id)
    target = resolve_within(root, relative).relative_to(root).as_posix()
    paths = _all_paths(root, src)
    resolve = Resolver(paths)
    stem, found, skipped = Path(target).stem, [], 0
    # A Markdown link spells the name URL-encoded: `Kişisel%20Vault%20Ayrımı.md`.
    needles = {stem, stem.replace(' ', '%20'), quote(stem)}
    for rel in paths:
        if rel == target:
            continue
        try:
            if (root / rel).stat().st_size > MAX_NOTE_BYTES:
                skipped += 1
                continue
            text = (root / rel).read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if not any(n in text for n in needles):
            continue
        if any(resolve(t, rel) == target for t in link_targets(text, rel)):
            found.append(rel)
            if len(found) >= limit:
                break
    return dict(path=target, backlinks=sorted(found), truncated=len(found) >= limit, skipped_large=skipped)


def create_note(ws, source_id, title, body=''):
    """A note the owner writes in the app: notes/<title>.md when the folder has notes/, else the top level."""
    import datetime as _dt
    import re as _re
    src, root = _editable(ws, source_id)
    if not isinstance(title, str) or not isinstance(body, str):
        raise CarryError('invalid_note')
    name = _re.sub(r'[\\/:*?"<>|\n\r\t]+', ' ', title)
    name = ' '.join(name.split()).strip('.') or 'Untitled'
    folder = root / 'notes' if (root / 'notes').is_dir() else root
    path, n = folder / f'{name}.md', 2
    while path.exists():
        path, n = folder / f'{name} ({n}).md', n + 1
    # The owner wrote it: an OKF concept (type required) generated by the owner.
    text = (f"---\ntype: thing\ncreated: {_dt.date.today().isoformat()}\n"
            f"generated: {okf.flow(okf.event(okf.owner(ws.state_dir)))}\n---\n\n# {title.strip() or name}\n\n{body.strip()}\n")
    with open(path, 'x', encoding='utf-8') as f:
        f.write(text)
    return dict(path=path.relative_to(root).as_posix())


RAW_TYPES = ('chat-raw', 'source', 'meeting')


def is_digest(front):
    return isinstance(front.get('harvest'), dict) and 'extractor' in front['harvest']


def _blocked(front):
    if front.get('lock') is True:
        return 'locked'
    if str(front.get('type', '')).lower() in RAW_TYPES or front.get('source_type') == 'clip':
        return 'raw'
    if not okf.is_draft(front):
        return 'not_draft'
    return None


def cannot_approve(front):
    """Why a note cannot be approved as a whole, or None. One rule for single and bulk approval and
    the app. A chat digest is decided item by item (digest.decide), never approved wholesale: that
    would clear it from review without saving any item."""
    return _blocked(front) or ('digest' if is_digest(front) else None)


def awaits_review(rel, front):
    """What waits for the owner: drafts in the inbox (chat digests, captures). Agent notes elsewhere
    keep `status: draft` as an unchecked mark and stay searchable; they are not a queue. A capture
    already routed (`routed_into:` set) is done even if its draft mark stayed."""
    return rel.startswith(INBOX + '/') and _blocked(front) is None and not front.get('routed_into')


def approve_note(ws, source_id, relative):
    """The owner checked a draft: it becomes `status: stable` with the owner's check in `verified`.
    Locked notes and raw material are refused."""
    src, root = _editable(ws, source_id)
    path = resolve_within(root, relative)
    if not path.is_file():
        raise CarryError('source_file_missing')
    raw = path.read_text(encoding='utf-8')
    front, _ = parse_frontmatter(raw)
    reason = cannot_approve(front)
    if reason == 'not_draft':
        return dict(path=relative, changed=False)
    if reason:
        raise CarryError(f'note_{reason}')
    return clear_draft(path, relative, raw, actor=okf.owner(ws.state_dir))


def clear_draft(path, relative, raw=None, actor=okf.DEFAULT_OWNER):
    """OKF approval, line by line: `status: draft` (or a legacy `draft: true`) becomes `status:
    stable` and `verified` gains the owner's check; every other line stays as written."""
    from .persistence import atomic_text
    raw = path.read_text(encoding='utf-8') if raw is None else raw
    approved = okf.approve(raw, actor)
    if approved is None:
        raise CarryError('draft_flag_not_simple')
    atomic_text(path, approved)
    return dict(path=relative, changed=True)


def approve_many(ws, source_id, paths):
    """Approve several drafts at once. Locked notes and raw material (chat logs, clips) are
    skipped with a reason; one failure never stops the rest."""
    src, root = _editable(ws, source_id)
    approved, skipped = [], []
    for rel in paths if isinstance(paths, list) else []:
        try:
            path = resolve_within(root, rel)
            reason = cannot_approve(_meta(path))
            if reason:
                skipped.append(dict(path=rel, reason=reason))
            elif approve_note(ws, source_id, rel).get('changed'):
                approved.append(rel)
        except CarryError as exc:
            skipped.append(dict(path=rel, reason=str(exc)))
        except OSError:
            skipped.append(dict(path=rel, reason='unreadable'))
    return dict(approved=approved, skipped=skipped)


def resolve_link(ws, source_id, target, from_path=None):
    """Path of the note an Obsidian-style [[target]] names."""
    src, root = _local(ws, source_id)
    found = _resolve(target, from_path, _all_paths(root, src))
    if not found:
        raise CarryError('note_not_found')
    return dict(path=found)


def assistants(ws, root=None):
    """Is each assistant installed on this Mac, and does the notes folder let it use Carry?"""
    import json as _json
    from .desktop import executable_for
    root = Path(root) if root else _vault_root(ws)
    out = {}
    for client in ('claude', 'codex'):
        found = executable_for(client)
        installed = os.path.isabs(found) and os.path.isfile(found)
        connected = False
        if root is not None:
            try:
                if client == 'claude':
                    connected = 'carry' in _json.loads((root / '.mcp.json').read_text(encoding='utf-8')).get('mcpServers', {})
                else:
                    connected = '[mcp_servers.carry]' in (root / '.codex' / 'config.toml').read_text(encoding='utf-8')
            except (OSError, ValueError, AttributeError):
                connected = False
        out[client] = dict(installed=installed, connected=connected, chat_end=_chat_end_hook(client, root))
    return dict(root=str(root) if root else None, chat_end=any(v['chat_end'] for v in out.values()), **out)


def _chat_end_hook(client, root):
    """Does this assistant run Carry's harvest when a chat in the notes folder ends?"""
    if client == 'claude':
        places = [root / '.claude' / 'settings.json', root / '.claude' / 'settings.local.json'] if root else []
    else:
        places = ([root / '.codex' / 'hooks.json'] if root else []) + [Path.home() / '.codex' / 'hooks.json']
    for place in places:
        try:
            text = place.read_text(encoding='utf-8')
        except OSError:
            continue
        if 'harvest' in text and '--from-hook' in text:
            return True
    return False


def schedule_for(ws):
    """The user-wide nightly job, marked with whether it belongs to this workspace."""
    from . import harvest
    status = harvest.schedule_status()
    if status.get('installed'):
        owner = status.get('workspace')
        status['this_workspace'] = bool(owner) and Path(owner).expanduser().resolve() == Path(ws.state_dir).resolve()
    return status


def overview(ws, source_id):
    from . import harvest
    listing = browse(ws, source_id)
    files = [f for f in listing['files'] if not f['system']]
    system_files = len(listing['files']) - len(files)
    folders = {}
    for f in files:
        top = f['path'].split('/', 1)[0] if '/' in f['path'] else '(root)'
        folders[top] = folders.get(top, 0) + 1
    week = datetime.datetime.now().timestamp() - 7 * 86400
    inbox = [f for f in files if f['path'].startswith(INBOX + '/')]
    recent = sorted((f for f in files if not f['path'].endswith('_index.md')), key=lambda f: f['modified'], reverse=True)[:12]
    root = Path(listing['root'])
    schedule = schedule_for(ws)
    chats = recent_chats(root)
    log = ws.state_dir / 'harvest.log'
    try:
        tail = [line for line in log.read_text(encoding='utf-8', errors='ignore').splitlines() if line.strip()][-8:]
    except OSError:
        tail = []
    return dict(source_id=listing['source_id'], root=listing['root'], total=len(files), system_files=system_files,
                indexed=sum(f['indexed'] for f in files), drafts=sum(f['draft'] for f in files),
                awaiting_review=sum(f['review'] for f in files),
                inbox=len(inbox), changed_this_week=sum(f['modified'] >= week for f in files),
                folders=[dict(name=k, count=v) for k, v in sorted(folders.items(), key=lambda kv: -kv[1])],
                recent=recent, chats=chats, harvest_log=tail, schedule=schedule,
                draft_language=harvest.draft_language(ws, root), truncated=listing['truncated'],
                assistants=assistants(ws, root))


def recent_chats(root):
    """Open items, decisions and commits the next chat starts from (same rules as `carry context`)."""
    import subprocess
    from . import context
    opens, decisions, seen = [], [], set()
    digests = context._digests(root)
    for day, path in digests:
        try:
            items = context._items(path)
        except OSError:
            continue
        for kind, statement, assistant in items:
            if statement.lower() in seen:
                continue
            seen.add(statement.lower())
            entry = dict(day=day.isoformat(), text=statement, digest=path.relative_to(root).as_posix())
            if kind in context.OPEN_TAGS:
                opens.append(entry)
            elif kind in context.DECISION_TAGS and not assistant:
                decisions.append(entry)
    try:
        log = subprocess.run(['git', '-C', str(root), 'log', '-10', '--format=%ad\t%s', '--date=short'],
                             capture_output=True, text=True, encoding='utf-8', timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        log = ''
    commits = [dict(day=d, text=t) for d, _, t in (l.partition('\t') for l in log.splitlines()) if t]
    return dict(open=opens[:12], decisions=decisions[:8], commits=commits, digests=len(digests))


# -- settings ------------------------------------------------------------------

PRESET_LABELS = {
    'keyword_assistant': 'Keyword search · your assistant checks',
    'keyword_jev': 'Keyword search · Jev judges',
    'semantic_assistant': 'Semantic search · your assistant checks',
    'semantic_jev': 'Semantic search · Jev judges',
    'custom': 'Custom configuration',
}
EMBEDDING_ONLY = ('qwen3-embedding:0.6b', 'nomic-embed-text')
# Fields the app may change. Chunking is left out: it needs a full rebuild and
# has no user-facing meaning.
RETRIEVAL_FIELDS = dict(top_k=(int, 1, 40), max_chars=(int, 1000, 60000), max_per_document=(int, 1, 10),
                        reranker_min_score=(float, 0.0, 1.0), auto_refresh=(bool, None, None),
                        refresh_seconds=(int, 10, 86400), github_sync_seconds=(int, 30, 86400))
SOURCE_FIELDS = ('writable', 'exclude')


def current_preset(ws):
    semantic = ws.embedding.provider != 'hashing'
    judge = 'jev' if ws.retrieval.reranker == 'jev' else 'assistant'
    if ws.retrieval.reranker == 'cross':
        return 'accurate_multilingual'
    if semantic and ws.embedding.model != 'embeddinggemma':
        return ws.embedding.model if ws.embedding.model in EMBEDDING_ONLY and judge == 'assistant' else 'custom'
    return ('semantic_' if semantic else 'keyword_') + judge


def settings(ws):
    from . import harvest, jev
    try:
        key = bool(jev.api_key())
    except Exception:
        key = False
    preset = current_preset(ws)
    return dict(preset=preset, preset_label=PRESET_LABELS.get(preset, preset),
                embedding=ws.embedding.to_json(), retrieval=ws.retrieval.to_json(),
                limits={k: dict(min=lo, max=hi) for k, (_, lo, hi) in RETRIEVAL_FIELDS.items() if lo is not None},
                sources=[s.to_json() | dict(root_available=Path(s.root).expanduser().exists()) for s in ws.sources],
                jev_key=key, schedule=schedule_for(ws), default_vault=default_vault(ws),
                draft_language=harvest.draft_language(ws, _vault_root(ws)),
                state_dir=str(ws.state_dir))


def _vault_root(ws):
    sid = default_vault(ws)
    return Path(ws.source(sid).root).expanduser().resolve() if sid else None


def _coerce(name, value):
    kind, lo, hi = RETRIEVAL_FIELDS[name]
    if kind is bool:
        if type(value) is not bool:
            raise CarryError('invalid_setting')
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CarryError('invalid_setting')
    value = kind(value)
    if not lo <= value <= hi:
        raise CarryError('setting_out_of_range')
    return value


def update(ws, retrieval=None, sources=None):
    """Apply a partial settings change. `sources` maps source_id to changed fields."""
    retrieval, sources = retrieval or {}, sources or {}
    if not isinstance(retrieval, dict) or not isinstance(sources, dict):
        raise CarryError('invalid_setting')
    unknown = set(retrieval) - set(RETRIEVAL_FIELDS)
    if unknown or any(not isinstance(v, dict) or set(v) - set(SOURCE_FIELDS) for v in sources.values()):
        raise CarryError('invalid_setting')
    with writer_lock(ws):
        current = Workspace.load(ws.state_dir)
        changed = {k: _coerce(k, v) for k, v in retrieval.items()}
        updated_sources = []
        for s in current.sources:
            fields = dict(sources.get(s.source_id, {}))
            if 'exclude' in fields:
                if not isinstance(fields['exclude'], list) or not all(isinstance(p, str) for p in fields['exclude']):
                    raise CarryError('invalid_source_exclusions')
                fields['exclude'] = tuple(p.strip() for p in fields['exclude'] if p.strip())
            for flag in ('writable',):
                if flag in fields and type(fields[flag]) is not bool:
                    raise CarryError('invalid_setting')
            updated_sources.append(replace(s, **fields))
        if set(sources) - {s.source_id for s in current.sources}:
            raise CarryError('unknown_source')
        updated = replace(current, retrieval=replace(current.retrieval, **changed), sources=tuple(updated_sources))
        updated.save()
    return updated


def remove_source(ws, source_id):
    """Forget a source. Its folder and files stay untouched; the index drops it on rebuild."""
    with writer_lock(ws):
        current = Workspace.load(ws.state_dir)
        current.source(source_id)
        updated = replace(current, sources=tuple(s for s in current.sources if s.source_id != source_id))
        updated.save()
    return updated


assert set(RETRIEVAL_FIELDS) <= set(RetrievalConfig.__dataclass_fields__)
