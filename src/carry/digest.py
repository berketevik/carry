"""A session recap (a harvest digest before 0.14) or meeting digest, item by item: the owner
accepts, fixes or skips each item.

An accepted item goes to the day's log (`log/<chat date>.md`) with its quote and a link to the
raw chat, so recall finds it under the day it was said. Accepting a conflict never edits the
note it contradicts; the log line links it for conflict resolution. The digest line keeps the
decision as a trailing HTML comment: Obsidian hides it and the state pack still reads the line.
Already recorded, done and dropped items are folded and need no decision. A digest leaves the
review queue (its `status: draft` becomes `stable`, with the owner's check in `verified`) once
no item waits.
"""
import datetime
import hashlib
from pathlib import Path
import re

from . import okf
from .errors import CarryError
from .markdown import parse_frontmatter
from .paths import resolve_within
from .persistence import atomic_text
from .vaultview import is_digest

DECISIONS = ('accepted', 'fixed', 'skipped')
MARK = re.compile(r'\s*<!-- carry: (accepted|fixed|skipped) (\d{4}-\d{2}-\d{2}) -->\s*$')
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
OWNER_LINE = re.compile(r'^\*\*Owner:\*\* ?(.*)$')


def source_chat(root, relative, parsed):
    """Where a draft came from, for Review: the assistant and day from its name, and the chat's
    first message from the raw chat, cut short. Meetings say only that they are one."""
    if parsed['kind'] == 'meeting':
        return dict(kind='meeting', day=parsed['created'])
    stem = Path(relative).stem
    m = re.match(r'^(\d{4}-\d{2}-\d{2}) — (?:session|harvest) (\w+) ', stem)
    chat = dict(kind='chat', client=m.group(2) if m else '', day=m.group(1) if m else parsed['created'], first='')
    resource = (parsed['raw_entry'] or {}).get('resource', '') if isinstance(parsed['raw_entry'], dict) else ''
    if not resource.startswith('/'):
        return chat
    try:
        raw = resolve_within(root, resource.lstrip('/')).read_text(encoding='utf-8', errors='ignore')
    except (OSError, CarryError):
        return chat
    lines, said = raw.split('\n'), []
    for n, line in enumerate(lines):
        found = OWNER_LINE.match(line)
        if found:
            # The message runs until the reply or the next exchange.
            said = [found.group(1)] + lines[n + 1:n + 20]
            said = said[:next((i for i, l in enumerate(said) if l.startswith(('**Assistant:**', '## '))), len(said))]
            break
    text = ' '.join(' '.join(said).split())
    chat['first'] = text if len(text) <= FIRST_PROMPT else text[:FIRST_PROMPT].rsplit(' ', 1)[0] + '…'
    return chat


def items(ws, source_id, relative):
    root, _, text, front = _open(ws, source_id, relative)
    parsed = parse(text)
    for it in parsed['items']:
        if it['match'] and it['decision'] is None:
            it['match_path'], it['match_text'] = note_side(ws, source_id, it)
    waiting = sum(1 for it in parsed['items'] if it['decision'] is None)
    return dict(path=relative, items=parsed['items'], waiting=waiting, draft=okf.is_draft(front),
                chat=source_chat(root, relative, parsed))


def accept_all(ws, source_id, relative, today=None):
    """Accept every item that waits, except conflicts with a note and items marked To review
    (probably withdrawn): those need the owner to choose. So does a statement that waits twice:
    both copies share one id, and the owner tells them apart."""
    _, _, text, _ = _open(ws, source_id, relative)
    waiting = [it for it in parse(text)['items'] if it['decision'] is None]
    copies = {}
    for it in waiting:
        copies[it['id']] = copies.get(it['id'], 0) + 1
    plain = [it['id'] for it in waiting if it['section'] not in ('conflict', 'review') and copies[it['id']] == 1]
    result = dict(waiting=len(waiting), done=False, logged=None)
    for item in plain:
        result = decide(ws, source_id, relative, item, 'accept', today=today)
    return dict(accepted=len(plain), waiting=result['waiting'], done=result['done'], logged=result.get('logged'))


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


def decide(ws, source_id, relative, item, action, text=None, today=None):
    """accept, fix (with new text) or skip one item; the digest leaves the queue when none waits."""
    if action not in ('accept', 'fix', 'skip'):
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
    decision = dict(accept='accepted', fix='fixed', skip='skipped')[action]
    lines = body.split('\n')
    line = lines[found['line']]
    if action == 'fix':
        line = line.replace(f":** {found['statement']}", f":** {statement}", 1)
    lines[found['line']] = f'{line} <!-- carry: {decision} {today} -->'
    logged = None
    if action != 'skip':
        said = statement or found['statement']
        where = f" ↔ {found['match']}" if found['match'] else ''
        source = f"{parsed['raw']}, {found['unit']} {found['exchange']}" if found['exchange'] else parsed['raw']
        entry = f"- **{found['tag']}:** {said}{where} ({source})"
        if found['quote']:
            entry += f"\n  > {found['quote']}"
        logged = _append_log(root, found['date'] or parsed['created'] or today, parsed['language'], entry,
                             parsed['raw_entry'], parsed['kind'])
    atomic_text(path, '\n'.join(lines))
    waiting = sum(1 for it in parse('\n'.join(lines))['items'] if it['decision'] is None)
    done = False
    if waiting == 0 and okf.is_draft(front):
        # Every item decided by the owner: the digest is human-reviewed (OKF §5.3).
        from .vaultview import clear_draft
        done = clear_draft(path, relative, actor=okf.owner(ws.state_dir)).get('changed', False)
    return dict(item=item, decision=decision, logged=logged, waiting=waiting, done=done)


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
