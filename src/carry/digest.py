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
import re

from .errors import CarryError
from .markdown import parse_frontmatter
from .paths import resolve_within
from .persistence import atomic_text

DECISIONS = ('accepted', 'fixed', 'skipped')
MARK = re.compile(r'\s*<!-- carry: (accepted|fixed|skipped) (\d{4}-\d{2}-\d{2}) -->\s*$')
ITEM = re.compile(r'^- \*\*(?P<tag>[^*]+?):\*\* (?P<statement>.+?)(?: ↔ (?P<match>\[\[[^\]]+\]\]))?$')
QUOTE = re.compile(r'^\s+> (?P<quote>.*?) \*\(exchange (?P<exchange>\d+), (?P<speaker>[^,)]+)(?:, (?P<date>\d{4}-\d{2}-\d{2}))?\)\*\s*$')
FOLDED_ITEM = re.compile(r'^- (?P<statement>.+?) → (?P<match>\[\[[^\]]+\]\])\s*$')
ACTIONABLE = ('new', 'conflict', 'review')
FOLDED = ('resolved', 'dropped', 'known')
LOG_HEADING = dict(Turkish='Sohbetlerden', English='From chats')
LOG_SUMMARY = dict(Turkish='{day}: sohbetlerden onaylanan maddeler.', English='{day}: items approved from chats.')


def is_digest(front):
    return isinstance(front.get('harvest'), dict) and 'extractor' in front['harvest']


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
            q = QUOTE.match(lines[n + 1]) if n + 1 < len(lines) else None
            items.append(dict(id=item_id(m['statement']), section=section, tag=m['tag'].strip(),
                              kind=m['tag'].split(' · ')[0].strip(), statement=m['statement'].strip(),
                              match=m['match'] or '', quote=q['quote'] if q else '',
                              exchange=int(q['exchange']) if q else None, speaker=q['speaker'] if q else '',
                              date=(q['date'] if q else None) or str(front.get('created') or ''),
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


def items(ws, source_id, relative):
    _, _, text, front = _open(ws, source_id, relative)
    parsed = parse(text)
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
        from .vaultview import approve_note
        done = approve_note(ws, source_id, relative).get('changed', False)
    return dict(item=item, decision=decision, logged=logged, waiting=waiting, done=done)
