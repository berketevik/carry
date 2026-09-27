"""A harvest digest, item by item: the owner accepts, fixes or skips each item.

An accepted item goes to the day's log (`log/<chat date>.md`) with its quote and a link to the
raw chat, so recall finds it under the day it was said. Accepting a conflict never edits the
note it contradicts; the log line links it for conflict resolution. The digest line keeps the
decision as a trailing HTML comment: Obsidian hides it and the state pack still reads the line.
Already recorded, done and dropped items are folded and need no decision. A digest leaves the
review queue (its draft flag is removed) once no item waits.
"""
import datetime
import hashlib
from pathlib import Path
import re

from .errors import CarryError
from .markdown import parse_frontmatter
from .paths import resolve_within
from .persistence import atomic_text
from .vaultview import is_digest

DECISIONS = ('accepted', 'fixed', 'skipped')
MARK = re.compile(r'\s*<!-- carry: (accepted|fixed|skipped) (\d{4}-\d{2}-\d{2}) -->\s*$')
ITEM = re.compile(r'^- \*\*(?P<tag>[^*]+?):\*\* (?P<statement>.+?)(?: ↔ (?P<match>\[\[[^\]]+\]\]))?$')
QUOTE = re.compile(r'^\s+> (?P<quote>.*?) \*\(exchange (?P<exchange>\d+), (?P<speaker>[^,)]+)(?:, (?P<date>\d{4}-\d{2}-\d{2}))?\)\*\s*$')
NOTE_SIDE = re.compile(r'^\s+≠ (?P<text>.+)$')
WHY = re.compile(r'^\s+∵ (?P<text>.+)$')
FOLDED_ITEM = re.compile(r'^- (?P<statement>.+?) → (?P<match>\[\[[^\]]+\]\])\s*$')
ACTIONABLE = ('new', 'conflict', 'review')
FOLDED = ('resolved', 'dropped', 'known')
LOG_HEADING = dict(Turkish='Sohbetlerden', English='From chats')
LOG_SUMMARY = dict(Turkish='{day}: sohbetlerden onaylanan maddeler.', English='{day}: items approved from chats.')




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
                              exchange=int(q['exchange']) if q else None, speaker=q['speaker'] if q else '',
                              date=(q['date'] if q else None) or str(front.get('created') or ''),
                              said=q['date'] if q and q['date'] else '',
                              match_text=side['text'].strip() if side else '', why=why['text'].strip() if why else '',
                              decision=decision, line=n))
        elif section in FOLDED and (m := FOLDED_ITEM.match(bare)):
            items.append(dict(id=item_id(m['statement']), section=section, statement=m['statement'].strip(),
                              match=m['match'], decision='folded', line=n))
    raw = next((s for s in front.get('sources') or [] if isinstance(s, str)), '')
    return dict(items=items, raw=raw, language=language or 'English', created=str(front.get('created') or ''))


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


def items(ws, source_id, relative):
    _, _, text, front = _open(ws, source_id, relative)
    parsed = parse(text)
    for it in parsed['items']:
        if it['match'] and it['decision'] is None:
            it['match_path'], it['match_text'] = note_side(ws, source_id, it)
    waiting = sum(1 for it in parsed['items'] if it['decision'] is None)
    return dict(path=relative, items=parsed['items'], waiting=waiting, draft=front.get('draft') is True)


def _append_log(root, day, language, entry, raw):
    rel = f'log/{day}.md'
    path = root / rel
    heading = f'## {LOG_HEADING.get(language, LOG_HEADING["English"])}'
    if path.exists():
        text = path.read_text(encoding='utf-8').rstrip('\n')
        headings = [l for l in text.split('\n') if l.startswith('## ')]
        if not headings or headings[-1] != heading:
            text += f'\n\n{heading}\n'
        text += '\n' + entry + '\n'
    else:
        summary = LOG_SUMMARY.get(language, LOG_SUMMARY['English']).format(day=day)
        text = (f'---\ntype: daily\nsummary: "{summary}"\ncreated: {day}\nsources:\n  - "{raw}"\n---\n\n'
                f'# {day}\n\n{heading}\n\n{entry}\n')
    atomic_text(path, text)
    return rel


def decide(ws, source_id, relative, item, action, text=None, today=None):
    """accept, fix (with new text) or skip one item; the digest leaves the queue when none waits."""
    if action not in ('accept', 'fix', 'skip'):
        raise CarryError('invalid_decision')
    statement = ' '.join((text or '').split())
    if action == 'fix' and not statement:
        raise CarryError('fix_needs_text')
    root, path, body, front = _open(ws, source_id, relative)
    if front.get('lock') is True:
        raise CarryError('note_locked')
    parsed = parse(body)
    found = next((it for it in parsed['items'] if it['id'] == item), None)
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
        source = f"{parsed['raw']}, exchange {found['exchange']}" if found['exchange'] else parsed['raw']
        entry = f"- **{found['tag']}:** {said}{where} ({source})"
        if found['quote']:
            entry += f"\n  > {found['quote']}"
        logged = _append_log(root, found['date'] or parsed['created'] or today, parsed['language'], entry, parsed['raw'])
    atomic_text(path, '\n'.join(lines))
    waiting = sum(1 for it in parse('\n'.join(lines))['items'] if it['decision'] is None)
    done = False
    if waiting == 0 and front.get('draft') is True:
        from .vaultview import clear_draft
        done = clear_draft(path, relative).get('changed', False)
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
        out.append(f"  > {it['quote']} *(exchange {it['exchange']}, {it['speaker']}{at})*")
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
    root, path, body, front = _open(ws, source_id, relative)
    if front.get('lock') is True:
        raise CarryError('note_locked')
    parsed = parse(body)
    todo = [it for it in parsed['items'] if it['section'] == 'conflict' and it['decision'] is None]
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
