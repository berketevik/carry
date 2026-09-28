"""Reviewed proposals. One accepted Markdown file is the correction commit.

Targets stay byte-for-byte on disk. Accepted target links determine current
versions; there is no second canonical write to interrupt halfway through.
"""
import datetime as dt
import difflib
import hashlib
import json
from pathlib import Path

from . import sidecar, store
from .errors import CarryError, RevisionConflict, SourceError
from .markdown import parse_frontmatter
from .mask import mask
from .paths import resolve_within, walk_markdown
from .persistence import atomic_text, writer_lock
from .records import read_record_meta

CLIENTS = ('claude', 'codex', 'agent')  # 'agent': any other MCP client, enabled only by the owner
MAX_CONTENT = 100000


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds')


def target_of(metadata):
    value = metadata.get('carry_target')
    if not value:
        return None
    try:
        value = json.loads(value)
        if (not isinstance(value, dict) or not all(isinstance(value.get(k), str) and value[k]
                for k in ('source_id', 'record_id', 'path', 'digest'))
                or type(value.get('revision')) is not int):
            raise ValueError()
        return value
    except (TypeError, ValueError):
        raise CarryError('invalid_correction_target')


def catalog(workspace):
    """Read canonical files, using the same rename reconciliation as indexing."""
    workspace.validate()
    files, snapshot = {}, {}
    for source in workspace.sources:
        for relative, path in walk_markdown(source.root, extensions=source.extensions, exclude=source.exclude):
            data = path.read_bytes()
            raw = data.decode('utf-8', errors='replace')
            fm, body = parse_frontmatter(raw)
            key = (source.source_id, relative)
            snapshot[key] = hashlib.sha256(data).hexdigest()
            files[key] = dict(source_id=source.source_id, path=relative, absolute=path,
                              raw=raw, metadata=fm, body=body, digest=snapshot[key])
    mapping = sidecar.reconcile(sidecar.load(workspace.sidecar_path), snapshot)
    for key, entry in files.items():
        meta = read_record_meta(entry['metadata'], *key, sidecar.lookup(mapping, *key))
        entry.update(record_id=meta.record_id, revision=meta.revision, state=meta.state,
                     current=meta.current, superseded_by=meta.superseded_by)
    apply_targets(files)
    return files


MARKER = 'carry_superseded_by'


def with_marker(raw, record_id):
    """The superseded note gains one frontmatter line so a human sees the correction."""
    line = f'{MARKER}: {record_id}\n'
    if raw.startswith('---\n'):
        end = raw.find('\n---', 3)
        if end != -1:
            return raw[:end + 1] + line + raw[end + 1:]
    return f'---\n{line}---\n' + raw


def _marked_digest_matches(entry, target, record_id):
    # Tolerate exactly the marker Carry wrote for this correction; any other edit is a conflict.
    line = f'{MARKER}: {record_id}\n'
    raw = entry['raw']
    candidates = [raw.replace(line, '', 1)] if line in raw else []
    block = f'---\n{line}---\n'
    if raw.startswith(block):
        candidates.append(raw[len(block):])
    return any(hashlib.sha256(c.encode('utf-8')).hexdigest() == target['digest'] for c in candidates)


def apply_targets(files):
    """Derive current pointers without editing imported or historical files.

    A target edited after acceptance is not silently hidden: both claims remain
    visible with a conflict diagnostic. Chains retain all predecessor links.
    """
    by_id = {}
    for entry in files.values():
        by_id.setdefault((entry['source_id'], entry['record_id']), []).append(entry)
    claims = {}
    for entry in files.values():
        if entry['state'] not in ('accepted', 'superseded'):
            continue
        try:
            target = target_of(entry['metadata'])
        except CarryError:
            entry['correction_conflict'] = 'invalid_target'
            continue
        if not target:
            continue
        matches = by_id.get((target['source_id'], target['record_id']), [])
        # Path+digest permits a rebuild when a disposable sidecar was removed.
        if not matches:
            fallback = files.get((target['source_id'], target['path']))
            matches = [fallback] if fallback and fallback['digest'] == target['digest'] else []
        if len(matches) != 1 or (matches[0]['digest'] != target['digest']
                                 and not _marked_digest_matches(matches[0], target, entry['record_id'])):
            entry['correction_conflict'] = 'target_changed_or_missing'
            continue
        previous = matches[0]
        if previous is entry:
            entry['correction_conflict'] = 'self_target'
            continue
        key = (previous['source_id'], previous['path'])
        claims.setdefault(key, []).append(entry)
    for key, corrections in claims.items():
        if len(corrections) > 1:
            for correction in corrections:
                correction['correction_conflict'] = 'competing_accepted_corrections'
            continue
        previous = files[key]
        previous.update(state='superseded', current=False,
                        superseded_by=corrections[0]['record_id'])


def _find(files, record_id, source_id=None):
    matches = [f for f in files.values() if f['record_id'] == record_id
               and (source_id is None or f['source_id'] == source_id)]
    if not matches:
        raise SourceError('record_not_found')
    if len(matches) != 1:
        raise SourceError('ambiguous_record_identity')
    return matches[0]


def ensure_current(workspace, record_id):
    item = _find(catalog(workspace), record_id)
    if not item['current']:
        raise RevisionConflict('record_not_current')


def _target(files, record_id, revision, source_id=None):
    if type(revision) is not int or revision < 1:
        raise CarryError('expected_target_revision_required')
    item = _find(files, record_id, source_id)
    if not item['current'] or item['state'] not in ('accepted', 'imported'):
        raise RevisionConflict('target_not_current_accepted_record')
    if item['revision'] != revision:
        raise RevisionConflict('target_revision_changed')
    return {k: item[k] for k in ('source_id', 'record_id', 'revision', 'path', 'digest')}


def _policy(workspace):
    try:
        value = json.loads((workspace.state_dir / 'proposals.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise CarryError('invalid_proposal_policy')
    return value


def configure_client(workspace, client, source_id, enabled):
    if client not in CLIENTS or type(enabled) is not bool:
        raise CarryError('invalid_proposal_client')
    source = workspace.writable_source(source_id)
    if source.scope != 'personal':
        raise SourceError('proposals_require_personal_source')
    with writer_lock(workspace):
        policy = _policy(workspace)
        policy[client] = dict(enabled=enabled, source_id=source_id)
        atomic_text(workspace.state_dir / 'proposals.json', json.dumps(policy, indent=2))
    return dict(client=client, enabled=enabled, source_id=source_id)


def client_enabled(workspace, client):
    return client in CLIENTS and _policy(workspace).get(client, {}).get('enabled') is True


def _destination(workspace, client, source_id):
    if client == 'cli':
        source = workspace.writable_source(source_id)
    else:
        if not client_enabled(workspace, client):
            raise CarryError('proposals_not_enabled_for_client')
        conf = _policy(workspace)[client]
        if source_id is not None and source_id != conf['source_id']:
            raise SourceError('proposal_destination_not_enabled')
        from .capture import _config
        if _config(workspace)['clients'].get(client, {}).get('paused'):
            raise CarryError('client_capture_paused')
        source = workspace.writable_source(conf['source_id'])
    if source.scope != 'personal':
        raise SourceError('proposals_require_personal_source')
    return source


def _serialize(fm, body):
    return store._frontmatter_block(fm) + '\n\n' + body.strip() + '\n'


def _archive(source, item):
    """Keep each pre-review draft version; these files never enter recall."""
    relative = Path(source.records_dir) / '.history' / (item['record_id'] + '-' + item['digest'] + '.md')
    path = resolve_within(source.root, relative)
    if not path.exists():
        atomic_text(path, item['raw'])


def _unchanged(item):
    if hashlib.sha256(item['absolute'].read_bytes()).hexdigest() != item['digest']:
        raise RevisionConflict('record_changed_during_review_commit')


def _description(item):
    result = {k: item[k] for k in ('record_id', 'revision', 'state', 'source_id', 'path')}
    result['title'] = str(item['metadata'].get('summary') or item['path'])
    result.update(current=item['current'], superseded_by=item['superseded_by'])
    for key in ('created', 'updated', 'reviewed_at'):
        if item['metadata'].get(key):
            result[key] = str(item['metadata'][key])
    return result


def propose(workspace, content, event_id, source_refs=(), *, title='Proposed decision',
            client='cli', source_id=None, target_id=None, expected_revision=None,
            target_source_id=None, expected_proposal_revision=None):
    """Client-synthesized content stays a draft. A captured event refines its own
    existing draft, rather than creating a second proposal through MCP."""
    if (not isinstance(content, str) or not content.strip() or len(content) > MAX_CONTENT
            or not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 512
            or not isinstance(title, str) or not title.strip() or len(title) > 200):
        raise CarryError('invalid_proposal_content_or_event')
    if not isinstance(source_refs, (list, tuple)) or not all(isinstance(s, str) for s in source_refs):
        raise CarryError('invalid_source_refs')
    content, categories = mask(content)
    title = mask(title)[0]
    with writer_lock(workspace):
        source = _destination(workspace, client, source_id)
        files = catalog(workspace)
        key = event_id if event_id.startswith('evt_') else 'proposal_' + digest(json.dumps([client, event_id]))
        existing = [f for f in files.values() if f['metadata'].get('carry_event') == key]
        if len(existing) > 1:
            raise RevisionConflict('ambiguous_event_identity')
        item = existing[0] if existing else None
        if event_id.startswith('evt_') and not item:
            raise CarryError('capture_event_not_found_retry_hook')
        if item and (item['source_id'] != source.source_id or
                     (client != 'cli' and item['metadata'].get('carry_surface') != client)):
            raise CarryError('event_owned_by_another_client_or_source')
        refs = list(dict.fromkeys(source_refs))
        for ref in refs:
            if ':' not in ref:
                raise SourceError('source_ref_requires_source_and_path')
            sid, relative = ref.split(':', 1)
            path = resolve_within(workspace.source(sid).root, relative)
            if not path.is_file():
                raise SourceError('source_ref_not_found')
        if item:
            refs = list(dict.fromkeys([*item['metadata'].get('sources', []), *refs]))
        # A request hash allows exact retries after a commit/receipt interruption,
        # including after acceptance. It is distinct from native event identity.
        request_hash = digest(json.dumps([content, title, refs, target_id, expected_revision,
                                         target_source_id], ensure_ascii=False))
        if item and item['metadata'].get('carry_proposal_hash') == request_hash:
            return dict(_description(item), duplicate=True)
        if item and (item['state'] != 'draft' or not item['current']):
            raise RevisionConflict('proposal_already_reviewed')
        if item and (type(expected_proposal_revision) is not int or
                     expected_proposal_revision != item['revision']):
            raise RevisionConflict('proposal_revision_changed')
        target = _target(files, target_id, expected_revision, target_source_id) if target_id else None
        if not target_id and (expected_revision is not None or target_source_id is not None):
            raise CarryError('target_id_required')
        if target and item and target['record_id'] == item['record_id']:
            raise RevisionConflict('self_correction')
        if item is None:
            # Publish metadata and body together; no intermediate unlinked draft.
            from .records import new_record_id
            rid = new_record_id()
            path = store.record_path(workspace, source, rid, title, 1)
            fm = dict(carry_record=rid, carry_revision=1, carry_state='draft', carry_current=True,
                      carry_event=key, carry_surface=client, carry_author='user' if client == 'cli' else client,
                      created=now(), type='record')
        else:
            _archive(source, item)
            path = item['absolute']
            fm = dict(item['metadata'], carry_revision=item['revision'] + 1)
        fm.update(carry_proposal_hash=request_hash, carry_proposal=True, updated=now(),
                  summary=title, sources=refs, carry_synthesis='client' if client != 'cli' else 'manual')
        fm.pop('carry_target', None)
        if target:
            fm['carry_target'] = json.dumps(target, sort_keys=True)
        if item:
            _unchanged(item)
        atomic_text(path, _serialize(fm, '# ' + title + '\n\n' + content.strip()))
        return dict(record_id=fm['carry_record'], revision=fm['carry_revision'], state='draft',
                    source_id=source.source_id, path=str(path.relative_to(source.root.resolve())),
                    duplicate=False, masked=categories)


def review(workspace, record_id):
    with writer_lock(workspace):
        files = catalog(workspace)
        item = _find(files, record_id)
        target = target_of(item['metadata'])
        old_body = ''
        conflict = None
        if target:
            try:
                current = _target(files, target['record_id'], target['revision'], target['source_id'])
                if current['digest'] != target['digest']:
                    raise RevisionConflict('target_content_changed')
                old_body = _find(files, target['record_id'], target['source_id'])['body']
            except (SourceError, RevisionConflict) as exc:
                conflict = str(exc)
        return dict(_description(item), content=item['body'], sources=item['metadata'].get('sources', []),
                    target=target, conflict=conflict, review_token=item['digest'],
                    diff=''.join(difflib.unified_diff(old_body.splitlines(True), item['body'].splitlines(True),
                        fromfile='current target' if target else 'new decision', tofile='proposed decision')))


def list_proposals(workspace, include_reviewed=False):
    files = catalog(workspace)
    return [_description(item) for item in files.values() if item['record_id'].startswith('rec_')
            and (include_reviewed or item['state'] == 'draft')]


def _finish(workspace, record_id, expected_revision, review_token, state):
    if type(expected_revision) is not int or expected_revision < 1 or not isinstance(review_token, str):
        raise CarryError('review_preconditions_required')
    with writer_lock(workspace):
        files = catalog(workspace)
        item = _find(files, record_id)
        source = workspace.writable_source(item['source_id'])
        fm = item['metadata']
        if fm.get('carry_review_token') == review_token and fm.get('carry_state') == state:
            return dict(_description(item), duplicate=True)
        if item['state'] != 'draft' or not item['current']:
            raise RevisionConflict('proposal_already_reviewed')
        if item['revision'] != expected_revision or item['digest'] != review_token:
            raise RevisionConflict('proposal_changed_since_review')
        target = target_of(fm)
        if target and state == 'accepted':
            current = _target(files, target['record_id'], target['revision'], target['source_id'])
            if current['digest'] != target['digest']:
                raise RevisionConflict('target_content_changed')
        _archive(source, item)
        changed = dict(fm, carry_state=state, carry_review_token=review_token, reviewed_at=now(), updated=now())
        if state == 'accepted' and target:
            changed.update(carry_supersedes=target['record_id'], carry_revision=max(item['revision'], target['revision'] + 1))
        _unchanged(item)
        if target and state == 'accepted':
            _unchanged(_find(files, target['record_id'], target['source_id']))
        atomic_text(item['absolute'], _serialize(changed, item['body']))
        marked = ''
        if target and state == 'accepted':
            previous = _find(files, target['record_id'], target['source_id'])
            owner = next((s for s in workspace.sources if s.source_id == target['source_id']), None)
            marked = 'read_only_source'
            lossless = hashlib.sha256(previous['raw'].encode('utf-8')).hexdigest() == previous['digest']
            if owner is not None and owner.writable and not lossless:
                marked = 'not_utf8'
            elif owner is not None and owner.writable:
                try:
                    atomic_text(previous['absolute'], with_marker(previous['raw'], record_id))
                    marked = 'marked'
                except OSError:
                    # Acceptance is already canonical; recall still treats the target as history.
                    marked = 'marker_write_failed'
        return dict(record_id=record_id, revision=changed['carry_revision'], state=state,
                    source_id=source.source_id, path=item['path'], duplicate=False,
                    supersedes=target['record_id'] if target and state == 'accepted' else '',
                    target_marker=marked)


def accept(workspace, record_id, expected_revision, review_token):
    result = _finish(workspace, record_id, expected_revision, review_token, 'accepted')
    # Canonical acceptance survives index failure; retry can rebuild without
    # accepting twice. No receipt can turn a failed index into a success claim.
    from .index import build
    result['index'] = build(workspace)
    result['status'] = 'accepted' if result['index']['status'] == 'built' else 'accepted_index_pending'
    return result


def reject(workspace, record_id, expected_revision, review_token):
    return _finish(workspace, record_id, expected_revision, review_token, 'rejected')
