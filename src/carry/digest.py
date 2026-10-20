"""A session recap (a harvest digest before 0.14) or meeting digest, item by item: the owner
accepts, fixes, rejects or passes each item.

An accepted item goes to the day's log (`log/<chat date>.md`) with its quote and a link to the
raw chat, so recall finds it under the day it was said. A passed item (the owner is not sure)
goes there too, its tag marked unsure, so no one reads it as settled; it stays off the work list
and the state pack. A rejected item goes nowhere (its marker is `skipped`, the name it had when
the button was Skip). Accepting a conflict never edits the note it contradicts; the log line
links it for conflict resolution. The digest line keeps the decision as a trailing HTML comment:
Obsidian hides it and the state pack still reads the line. Already recorded, done and dropped
items are folded and need no decision. A digest leaves the review queue (its `status: draft`
becomes `stable`, with the owner's check in `verified`) once no item waits.

A decision can be taken back for two days (`undo`, newest first); a passed item can be settled
later, once the owner knows (`unsure`, `settle`).
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from . import okf
from .errors import CarryError
from .markdown import parse_frontmatter
from .paths import resolve_within
from .persistence import atomic_text
from .vaultview import is_digest

DECISIONS = ('accepted', 'fixed', 'skipped', 'unsure')
# What the owner answers, and the marker it leaves. `skip` is how apps before the Reject button said reject.
ACTIONS = {'accept': 'accepted', 'fix': 'fixed', 'reject': 'skipped', 'skip': 'skipped', 'pass': 'unsure'}
MARK = re.compile(r'\s*<!-- carry: (accepted|fixed|skipped|unsure) (\d{4}-\d{2}-\d{2}) -->\s*$')
ITEM = re.compile(r'^- \*\*(?P<tag>[^*]+?):\*\* (?P<statement>.+?)(?: ↔ (?P<match>\[\[[^\]]+\]\]))?$')
# A chat item cites its exchange, a meeting item (carry.meet) the transcript line it quotes.
QUOTE = re.compile(r'^\s+> (?P<quote>.*?) \*\((?P<unit>exchange|segment) (?P<exchange>\d+), (?P<speaker>[^,)]+)(?:, (?P<date>\d{4}-\d{2}-\d{2}))?\)\*\s*$')
NOTE_SIDE = re.compile(r'^\s+≠ (?P<text>.+)$')
WHY = re.compile(r'^\s+∵ (?P<text>.+)$')
FOLDED_ITEM = re.compile(r'^- (?P<statement>.+?) → (?P<match>\[\[[^\]]+\]\])\s*$')
ACTIONABLE = ('new', 'conflict', 'review')
FOLDED = ('resolved', 'dropped', 'known')
LOG_HEADING = dict(Turkish='Sohbetlerden', English='From chats')
LOG_SUMMARY = dict(Turkish='{day}: sohbetlerden onaylanan maddeler.', English='{day}: items approved from chats.')
MEETING_LOG_HEADING = dict(Turkish='Toplantılardan', English='From meetings')
MEETING_LOG_SUMMARY = dict(Turkish='{day}: toplantılardan onaylanan maddeler.', English='{day}: items approved from meetings.')




def item_id(statement):
    return hashlib.sha1(statement.strip().lower().encode()).hexdigest()[:10]


def decision_of(line):
    """(decision, line without its marker) for one digest line."""
    m = MARK.search(line)
    return (m.group(1), line[:m.start()]) if m else (None, line)


def _sections():
    from .harvest import LABELS
    out = {}
    for language, labels in LABELS.items():
        for key in ACTIONABLE + FOLDED:
            out[labels[key]] = (key, language)
    return out


def parse(text):
    """Items of a digest with their section, decision and where they sit in the text."""
    front, _ = parse_frontmatter(text)
    lines = text.split('\n')
    sections, section, language = _sections(), None, None
    items = []
    for n, line in enumerate(lines):
        if line.startswith('## '):
            section, lang = sections.get(line[3:].strip(), (None, None))
            language = language or lang
            continue
        if section is None:
            continue
        decision, bare = decision_of(line)
        if section in ACTIONABLE and (m := ITEM.match(bare)):
            # The item's indented lines, in any order: quote (>), the note's side (≠), the judge's reason (∵).
            follow = []
            for extra in lines[n + 1:]:
                if not extra.startswith((' ', '\t')) or not extra.strip():
                    break
                follow.append(extra)
            first = lambda pattern: next(filter(None, map(pattern.match, follow)), None)
            q, side, why = first(QUOTE), first(NOTE_SIDE), first(WHY)
            items.append(dict(id=item_id(m['statement']), section=section, tag=m['tag'].strip(),
                              kind=m['tag'].split(' · ')[0].strip(), statement=m['statement'].strip(),
                              match=m['match'] or '', quote=q['quote'] if q else '',
                              exchange=int(q['exchange']) if q else None, unit=q['unit'] if q else 'exchange',
                              speaker=q['speaker'] if q else '',
                              date=(q['date'] if q else None) or str(front.get('created') or ''),
                              said=q['date'] if q and q['date'] else '',
                              match_text=side['text'].strip() if side else '', why=why['text'].strip() if why else '',
                              decision=decision, line=n))
        elif section in FOLDED and (m := FOLDED_ITEM.match(bare)):
            items.append(dict(id=item_id(m['statement']), section=section, statement=m['statement'].strip(),
                              match=m['match'], decision='folded', line=n))
    # The raw chat (or meeting) the digest came from: its first `sources` entry. Log lines link
    # it as a wikilink; a new log file's frontmatter keeps it as an OKF entry.
    entries = okf.source_entries(front)
    raw = okf.as_wikilink(entries[0]['resource']) if entries else ''
    run = okf.run_record(front)
    return dict(items=items, raw=raw, raw_entry=entries[0] if entries else None, language=language or 'English',
                created=str(front.get('created') or ''), kind='meeting' if run.get('kind') == 'meeting' else 'chat')


def _open(ws, source_id, relative):
    from .vaultview import _local
    _, root = _local(ws, source_id)
    path = resolve_within(root, relative)
    if not path.is_file():
        raise CarryError('source_file_missing')
    text = path.read_text(encoding='utf-8')
    front, _ = parse_frontmatter(text)
    if not is_digest(front):
        raise CarryError('not_a_digest')
    return root, path, text, front


MATCH_TEXT = 600


def note_side(ws, source_id, item):
    """(path, passage) of the note an item points to: the stored passage, else the paragraph of
    that note that shares most words with the item (digests written before passages were kept)."""
    from .harvest import _terms
    from .vaultview import resolve_link
    try:
        path = resolve_link(ws, source_id, item['match'].strip('[]'))['path']
    except (CarryError, OSError):
        return '', item.get('match_text', '')
    if item.get('match_text'):
        return path, item['match_text']
    from .vaultview import _local
    _, root = _local(ws, source_id)
    try:
        _, body = parse_frontmatter((root / path).read_text(encoding='utf-8', errors='replace'))
    except OSError:
        return path, ''
    terms = _terms(item['statement'] + ' ' + item.get('quote', ''))
    lines = [' '.join(l.split()) for l in body.splitlines() if l.strip() and not l.lstrip().startswith('#')]
    stems = [_terms(l) for l in lines]
    # Words that run through the whole note (the project's name) say little about which line it is.
    df = {t: sum(t in st for st in stems) for t in terms}
    score = [sum(1 / df[t] for t in st & terms) for st in stems]
    if not lines or max(score) == 0:
        return path, ''
    return path, lines[score.index(max(score))][:MATCH_TEXT]


FIRST_PROMPT = 200
ASKED = 300
OWNER_LINE = re.compile(r'^\*\*Owner:\*\* ?(.*)$')


def raw_chat(root, parsed):
    """The text of the raw chat a recap came from, or '' (a meeting, a legacy link, a missing file)."""
    resource = (parsed['raw_entry'] or {}).get('resource', '') if isinstance(parsed['raw_entry'], dict) else ''
    if parsed['kind'] == 'meeting' or not resource.startswith('/'):
        return ''
    try:
        return resolve_within(root, resource.lstrip('/')).read_text(encoding='utf-8', errors='ignore')
    except (OSError, CarryError):
        return ''


def owner_message(raw, exchange=None, limit=FIRST_PROMPT):
    """What the owner wrote in one exchange of a raw chat (the first one without `exchange`), on
    one line and cut short."""
    lines = raw.split('\n')
    start = 0 if exchange is None else next((n for n, l in enumerate(lines) if l.strip() == f'## Exchange {exchange}'), None)
    if start is None:
        return ''
    said = []
    for n in range(start, len(lines)):
        found = OWNER_LINE.match(lines[n])
        if found:
            # The message runs until the reply or the next exchange.
            said = [found.group(1)] + lines[n + 1:n + 20]
            said = said[:next((i for i, l in enumerate(said) if l.startswith(('**Assistant:**', '## '))), len(said))]
            break
        if exchange is not None and n > start and lines[n].startswith('## '):
            break
    text = ' '.join(' '.join(said).split())
    return text if len(text) <= limit else text[:limit].rsplit(' ', 1)[0] + '…'


def source_chat(root, relative, parsed, raw, front):
    """Where a draft came from, for Review: the assistant and day from its name, the chat's name
    (`topic`, as the client lists it) and its first message from the raw chat, cut short.
    Meetings say only that they are one."""
    if parsed['kind'] == 'meeting':
        return dict(kind='meeting', day=parsed['created'])
    from .harvest import recap_topic
    stem = Path(relative).stem
    m = re.match(r'^(\d{4}-\d{2}-\d{2}) — (?:session|harvest) (\w+) ', stem)
    return dict(kind='chat', client=m.group(2) if m else '', day=m.group(1) if m else parsed['created'],
                topic=recap_topic(root, front), first=owner_message(raw) if raw else '')


def items(ws, source_id, relative):
    """The items with what Review asks about each: the note a conflict points to and, for an item
    the assistant's words support, what the owner had asked in that exchange (`asked`), so the
    question reads "you asked this, the assistant answered that, I am saving this: right?"."""
    root, _, text, front = _open(ws, source_id, relative)
    parsed = parse(text)
    raw = raw_chat(root, parsed)
    for it in parsed['items']:
        if it['match'] and it['decision'] is None:
            it['match_path'], it['match_text'] = note_side(ws, source_id, it)
        if raw and it['decision'] is None and it.get('speaker') == 'assistant' and it.get('exchange'):
            it['asked'] = owner_message(raw, it['exchange'], ASKED)
    waiting = sum(1 for it in parsed['items'] if it['decision'] is None)
    return dict(path=relative, items=parsed['items'], waiting=waiting, draft=okf.is_draft(front),
                chat=source_chat(root, relative, parsed, raw, front))


def accept_all(ws, source_id, relative, today=None):
    """Accept every item that waits, except conflicts with a note and items marked To review
    (probably withdrawn): those need the owner to choose. So does a statement that waits twice:
    both copies share one id, and the owner tells them apart. One undo takes all of them back."""
    root, _, text, _ = _open(ws, source_id, relative)
    parsed = parse(text)
    waiting = [it for it in parsed['items'] if it['decision'] is None]
    copies = {}
    for it in waiting:
        copies[it['id']] = copies.get(it['id'], 0) + 1
    chosen = [it for it in waiting if it['section'] not in ('conflict', 'review') and copies[it['id']] == 1]
    plain = [it['id'] for it in chosen]
    day = (today or datetime.date.today()).isoformat()
    before = {relative: text, **{rel: _read(root, rel) for rel in {f'log/{_log_day(it, parsed, day)}.md' for it in chosen}}}
    result = dict(waiting=len(waiting), done=False, logged=None)
    for item in plain:
        result = decide(ws, source_id, relative, item, 'accept', today=today, record=False)
    undo_id = _remember(ws, source_id, relative, root, before) if plain else None
    return dict(accepted=len(plain), waiting=result['waiting'], done=result['done'], logged=result.get('logged'), undo=undo_id)


# -- undo ----------------------------------------------------------------------
# A decision writes the recap and maybe a daily log. What both were before stays in this device's
# state for a while, so the owner can take the decision back: the files come back exactly as they
# were (the draft mark, a fixed statement's first wording, a log file that did not exist), and only
# when nothing changed them since. Decisions of one recap are taken back newest first.

UNDO_NAME = 'review-undo.json'
UNDO_KEEP = 20          # decisions per recap
UNDO_SECONDS = 2 * 86400


def _read(root, rel):
    path = Path(root) / rel
    return path.read_text(encoding='utf-8') if path.is_file() else None


def _sha(text):
    return None if text is None else hashlib.sha1(text.encode('utf-8')).hexdigest()


def _undo_load(ws):
    try:
        saved = json.loads((Path(ws.state_dir) / UNDO_NAME).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def _undo_save(ws, saved):
    now = time.time()
    kept = {key: [e for e in stack if now - e.get('at', 0) < UNDO_SECONDS][-UNDO_KEEP:] for key, stack in saved.items()}
    atomic_text(Path(ws.state_dir) / UNDO_NAME, json.dumps({k: v for k, v in kept.items() if v}, ensure_ascii=False))


def _remember(ws, source_id, relative, root, before):
    """Keep what the files were before a decision (`before`: path → text, None for no file) and
    what they are now; returns the id the owner takes it back with."""
    saved = _undo_load(ws)
    entry = dict(id=uuid.uuid4().hex[:12], at=time.time(),
                 files={rel: [text, _sha(_read(root, rel))] for rel, text in before.items()})
    saved.setdefault(f'{source_id}:{relative}', []).append(entry)
    _undo_save(ws, saved)
    return entry['id']


def undo(ws, source_id, relative, undo_id):
    """Take back the newest decision of a recap: its files as they were before it."""
    from .vaultview import _editable, _local
    _editable(ws, source_id)
    _, root = _local(ws, source_id)
    saved = _undo_load(ws)
    stack = saved.get(f'{source_id}:{relative}') or []
    if not stack or stack[-1].get('id') != undo_id:
        raise CarryError('undo_not_available')
    files = stack[-1]['files']
    if any(_sha(_read(root, rel)) != after for rel, (_, after) in files.items()):
        raise CarryError('undo_changed_since')
    for rel, (text, _) in files.items():
        target = resolve_within(root, rel)
        if text is None:
            target.unlink(missing_ok=True)
        else:
            atomic_text(target, text)
    stack.pop()
    _undo_save(ws, saved)
    _, _, body, front = _open(ws, source_id, relative)
    waiting = sum(1 for it in parse(body)['items'] if it['decision'] is None)
    return dict(path=relative, undone=undo_id, waiting=waiting, draft=okf.is_draft(front))


def _log_source(root, raw_entry):
    """The OKF `sources` entry of a new log file: the digest's raw as a bundle path. A legacy
    digest names its raw as a [[wikilink]]; it is resolved only when exactly one file matches."""
    if not raw_entry:
        return []
    resource = raw_entry['resource']
    link = okf.WIKILINK.match(resource)
    if link:
        from .paths import walk_markdown
        stem = link.group(1).strip()
        found = [rel for rel, _ in walk_markdown(root) if Path(rel).stem == stem]
        return [okf.entry(found[0])] if len(found) == 1 else [dict(resource=resource, title=stem)]
    return [dict(raw_entry)]


def _append_log(root, day, language, entry, raw_entry, kind='chat'):
    rel = f'log/{day}.md'
    path = root / rel
    headings, summaries = (MEETING_LOG_HEADING, MEETING_LOG_SUMMARY) if kind == 'meeting' else (LOG_HEADING, LOG_SUMMARY)
    heading = f'## {headings.get(language, headings["English"])}'
    if path.exists():
        text = path.read_text(encoding='utf-8').rstrip('\n')
        headings = [l for l in text.split('\n') if l.startswith('## ')]
        if not headings or headings[-1] != heading:
            text += f'\n\n{heading}\n'
        text += '\n' + entry + '\n'
    else:
        summary = summaries.get(language, summaries['English']).format(day=day)
        front = ['---', 'type: daily', f'description: {okf.flow(summary)}', f'created: {day}',
                 f'generated: {okf.flow(okf.event(okf.carry_actor()))}'] + okf.source_lines(_log_source(root, raw_entry)) + ['---']
        text = '\n'.join(front) + f'\n\n# {day}\n\n{heading}\n\n{entry}\n'
    atomic_text(path, text)
    return rel


def _log_day(found, parsed, today):
    """The day whose log an item goes to: the day it was said."""
    return found['date'] or parsed['created'] or today


def _entry(found, parsed, said, unsure=False):
    """An item's daily-log line, with its source and quote; an unsure one says so in its tag."""
    tag = found['tag']
    if unsure:
        # The doubt travels with the text: recall shows it to whoever reads the line.
        from .harvest import LABELS
        tag += ' · ' + LABELS.get(parsed['language'], LABELS['English'])['unsure']
    where = f" ↔ {found['match']}" if found['match'] else ''
    source = f"{parsed['raw']}, {found['unit']} {found['exchange']}" if found['exchange'] else parsed['raw']
    entry = f"- **{tag}:** {said}{where} ({source})"
    if found['quote']:
        entry += f"\n  > {found['quote']}"
    return entry


def decide(ws, source_id, relative, item, action, text=None, today=None, record=True):
    """accept, fix (with new text), reject or pass (not sure) one item; the digest leaves the
    queue when none waits. `undo` in the result takes it back."""
    if action not in ACTIONS:
        raise CarryError('invalid_decision')
    statement = ' '.join((text or '').split())
    if action == 'fix' and not statement:
        raise CarryError('fix_needs_text')
    from .vaultview import _editable
    _editable(ws, source_id)
    root, path, body, front = _open(ws, source_id, relative)
    if front.get('lock') is True:
        raise CarryError('note_locked')
    parsed = parse(body)
    # An id comes from the statement, so a statement found twice has one id: the copy that
    # still waits is the one decided, and the other can be decided after it.
    same = [it for it in parsed['items'] if it['id'] == item]
    found = next((it for it in same if it['decision'] is None), same[0] if same else None)
    if found is None:
        raise CarryError('item_not_found')
    if found['decision'] == 'folded':
        raise CarryError('item_not_actionable')
    if found['decision']:
        raise CarryError('item_already_decided')
    today = (today or datetime.date.today()).isoformat()
    decision = ACTIONS[action]
    lines = body.split('\n')
    line = lines[found['line']]
    if action == 'fix':
        line = line.replace(f":** {found['statement']}", f":** {statement}", 1)
    lines[found['line']] = f'{line} <!-- carry: {decision} {today} -->'
    day = _log_day(found, parsed, today)
    before = {relative: body, **({f'log/{day}.md': _read(root, f'log/{day}.md')} if decision != 'skipped' else {})}
    logged = None
    if decision != 'skipped':
        entry = _entry(found, parsed, statement or found['statement'], unsure=decision == 'unsure')
        logged = _append_log(root, day, parsed['language'], entry, parsed['raw_entry'], parsed['kind'])
    atomic_text(path, '\n'.join(lines))
    waiting = sum(1 for it in parse('\n'.join(lines))['items'] if it['decision'] is None)
    done = False
    if waiting == 0 and okf.is_draft(front):
        # Every item decided by the owner: the digest is human-reviewed (OKF §5.3).
        from .vaultview import clear_draft
        done = clear_draft(path, relative, actor=okf.owner(ws.state_dir)).get('changed', False)
    undo_id = _remember(ws, source_id, relative, root, before) if record else None
    return dict(item=item, decision=decision, logged=logged, waiting=waiting, done=done, undo=undo_id)


def unsure(ws, source_id):
    """The items the owner passed as unsure, newest recap first, to settle once they know."""
    from .harvest import recap_topic
    from .vaultview import _local
    _, root = _local(ws, source_id)
    out = []
    for path in sorted((root / '+').glob('*.md'), key=lambda p: p.name, reverse=True):
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if '<!-- carry: unsure ' not in text:
            continue
        front, _ = parse_frontmatter(text)
        if not is_digest(front):
            continue
        parsed = parse(text)
        rel = path.relative_to(root).as_posix()
        out += [dict(path=rel, id=it['id'], statement=it['statement'], kind=it['kind'], tag=it['tag'],
                     day=it['said'] or it['date'] or parsed['created'], meeting=parsed['kind'] == 'meeting',
                     topic=recap_topic(root, front, look=False))
                for it in parsed['items'] if it['decision'] == 'unsure']
    return dict(items=out)


def settle(ws, source_id, relative, item, action, text=None, today=None):
    """Decide an item passed as unsure once the owner knows: accept or fix it (its daily-log line
    loses the unsure mark, or gains the fixed text) or reject it (the line goes)."""
    if action not in ('accept', 'fix', 'reject'):
        raise CarryError('invalid_decision')
    statement = ' '.join((text or '').split())
    if action == 'fix' and not statement:
        raise CarryError('fix_needs_text')
    from .vaultview import _editable
    _editable(ws, source_id)
    root, path, body, front = _open(ws, source_id, relative)
    if front.get('lock') is True:
        raise CarryError('note_locked')
    parsed = parse(body)
    found = next((it for it in parsed['items'] if it['id'] == item and it['decision'] == 'unsure'), None)
    if found is None:
        raise CarryError('item_not_unsure')
    today = (today or datetime.date.today()).isoformat()
    decision = ACTIONS[action]
    day = _log_day(found, parsed, today)
    log_rel = f'log/{day}.md'
    before = {relative: body, log_rel: _read(root, log_rel)}
    old = _entry(found, parsed, found['statement'], unsure=True)
    new = None if decision == 'skipped' else _entry(found, parsed, statement or found['statement'])
    log = before[log_rel]
    at = log.rfind(old) if log is not None else -1
    if at >= 0:
        end = at + len(old) + (1 if log[at + len(old):at + len(old) + 1] == '\n' and new is None else 0)
        atomic_text(root / log_rel, log[:at] + (new or '') + log[end:])
    elif new is not None:
        _append_log(root, day, parsed['language'], new, parsed['raw_entry'], parsed['kind'])
    lines = body.split('\n')
    _, line = decision_of(lines[found['line']])
    if action == 'fix':
        line = line.replace(f":** {found['statement']}", f":** {statement}", 1)
    lines[found['line']] = f'{line} <!-- carry: {decision} {today} -->'
    atomic_text(path, '\n'.join(lines))
    undo_id = _remember(ws, source_id, relative, root, before)
    return dict(item=item, decision=decision, logged=None if new is None else log_rel, undo=undo_id)


def _block(lines, n):
    """The item line at n and its indented lines."""
    end = n + 1
    while end < len(lines) and lines[end].startswith((' ', '\t')) and lines[end].strip():
        end += 1
    return n, end


def _item_lines(it, match, note_text='', why=''):
    at = f", {it['said']}" if it.get('said') else ''  # only a date the digest had, never the fallback
    out = [f"- **{it['tag']}:** {it['statement']}" + (f' ↔ {match}' if match else '')]
    if it.get('quote'):
        out.append(f"  > {it['quote']} *({it.get('unit', 'exchange')} {it['exchange']}, {it['speaker']}{at})*")
    if note_text:
        out.append(f'  ≠ {note_text}')
    if why:
        out.append(f'  ∵ {why}')
    return out


def recheck(ws, source_id, relative, today=None):
    """Judge a digest's undecided conflicts again with the current rule (Jev if chosen, else the
    assistant): a real conflict stays with the note's side refreshed, an already recorded one is
    folded, anything else moves to New with the note kept as related. Decided items never move."""
    from . import harvest, jev
    from .vaultview import _editable
    _editable(ws, source_id)
    root, path, body, front = _open(ws, source_id, relative)
    if front.get('lock') is True:
        raise CarryError('note_locked')
    parsed = parse(body)
    todo =[it for it in parsed['items'] if it['section'] == 'conflict' and it['decision'] is None]
    if not todo:
        return dict(path=relative, rechecked=0, moved={})
    labels = harvest.LABELS.get(parsed['language'], harvest.LABELS['English'])
    kinds = {labels[k]: k for k in harvest.TYPES}
    cands = [dict(type=kinds.get(it['kind'], 'fact'), statement=it['statement'], quote=it['quote'], at=it['date'])
             for it in todo]
    key = jev.api_key() if ws.retrieval.reranker == 'jev' else None
    if key:
        verdicts = [harvest.compare(c, ws, key, since=c['at']) for c in cands]
    else:
        verdicts, ran = harvest.agent_compare(cands, ws, harvest.extractor(), parsed['language'])
        if not ran:
            raise CarryError('recheck_unavailable')
    lines = body.split('\n')
    placed = {'conflict': [], 'new': [], 'known': []}
    for it, c, (verdict, match_path) in zip(todo, cands, verdicts):
        link = f'[[{Path(match_path).stem}]]' if match_path else ''
        if verdict == 'conflict':
            placed['conflict'].append((it, _item_lines(it, link, c.get('match_text', ''), c.get('why', ''))))
        elif verdict == 'known':
            placed['known'].append((it, [f"- {it['statement']} → {link}"]))
        else:
            placed['new'].append((it, _item_lines(it, link if c.get('related') else '', c.get('match_text', '') if c.get('related') else '')))
    # Take the old blocks out (bottom up), then put each item under its section.
    for it in sorted(todo, key=lambda x: -x['line']):
        start, end = _block(lines, it['line'])
        del lines[start:end]
    for key_, entries in placed.items():
        if not entries:
            continue
        heading = f'## {labels[key_]}'
        if heading not in lines:
            anchor = next((i for i, l in enumerate(lines) if l.startswith('# ')), len(lines) - 1)
            at = anchor + 2 if key_ == 'new' else len(lines)
            lines[at:at] = [heading, '', ''] if key_ == 'new' else ['', heading, '']
        h = lines.index(heading)
        end = h + 1
        while end < len(lines) and not lines[end].startswith('## '):
            end += 1
        while end > h + 1 and not lines[end - 1].strip():
            end -= 1
        block = [l for _, entry in entries for l in entry]
        lines[end:end] = ([''] if end == h + 1 else []) + block
    # A conflict section left without items goes away.
    heading = f"## {labels['conflict']}"
    if heading in lines:
        h = lines.index(heading)
        end = h + 1
        while end < len(lines) and not lines[end].startswith('## '):
            end += 1
        if not any(l.startswith('- ') for l in lines[h + 1:end]):
            del lines[h:end]
    atomic_text(path, re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)))
    return dict(path=relative, rechecked=len(todo), moved={k: len(v) for k, v in placed.items()})
