"""İşleriniz: the home page's one list of the signed-in person's work.

Three sources, one row per piece of work, the sources as badges:
- Trello: the open cards the person chose to see (trello.view).
- The team notes (sources with scope "team", the team knowledge base), by the team's own rules
  (`work` in its .carry/team.json): notes the person owns in one of the team's review statuses,
  and inbox notes whose title hands them to the person ("{name} için"). Without the rules nothing
  waits; their notes are counted, not listed.
- Chats and meetings: open items the owner accepted in Review. An item not yet decided waits in
  Review, not here; a rejected one is gone, one passed as unsure is only in the daily log; a
  decision goes to the daily log and the decisions list.
An item stays until the person marks it done. Done is kept in the state (work-done.json), never in
the notes, and the state pack a new chat starts from leaves done items out too.

A note or chat item that links a card (trello.com/c/<code>), or names its number ("kart #213")
when only one of the person's cards has it, becomes a badge on that card's row. Nothing else is
merged. Rows with a due date come first, nearest at the top; the rest by their latest activity.
"""
import datetime
import json
from pathlib import Path
import re

from . import digest, profile, team, trello
from .persistence import atomic_text

DONE = 'work-done.json'
CARD_LINK = re.compile(r'trello\.com/c/([A-Za-z0-9]+)')
CARD_NUMBER = re.compile(r'\b(?:kart|card)\s*#(\d+)\b', re.I)
OPEN_KINDS = ('open item', 'açık iş')
DECISION_KINDS = ('decision', 'karar')
DECISION_DAYS = 14
INBOX_DIR = '+'


def done_items(state_dir):
    try:
        saved = json.loads((Path(state_dir) / DONE).read_text(encoding='utf-8')).get('done', [])
    except (OSError, ValueError, AttributeError):
        return set()
    return {(d.get('digest'), d.get('item')) for d in saved if isinstance(d, dict)}


def mark_done(workspace, targets, today=None):
    """Mark chat items done: [{digest, item}], as a row's `done` lists them."""
    path = workspace.state_dir / DONE
    try:
        saved = json.loads(path.read_text(encoding='utf-8')).get('done', [])
    except (OSError, ValueError, AttributeError):
        saved = []
    known = {(d.get('digest'), d.get('item')) for d in saved if isinstance(d, dict)}
    day = (today or datetime.date.today()).isoformat()
    for t in targets if isinstance(targets, list) else []:
        if isinstance(t, dict) and isinstance(t.get('digest'), str) and isinstance(t.get('item'), str) \
                and (t['digest'], t['item']) not in known:
            saved.append(dict(digest=t['digest'], item=t['item'], at=day))
            known.add((t['digest'], t['item']))
    atomic_text(path, json.dumps(dict(done=saved), ensure_ascii=False, indent=1) + '\n')


def _vault(workspace):
    """(source id, root) of the vault the inbox recaps are in, or (None, None)."""
    from .vaultview import default_vault
    sid = default_vault(workspace)
    if not sid:
        return None, None
    source = workspace.source(sid)
    root = Path(source.root).expanduser()
    return (None, None) if source.github or not root.is_dir() else (sid, root)


def _from_digests(workspace, today):
    """(open items accepted and not done, decisions accepted lately) from the inbox recaps."""
    sid, root = _vault(workspace)
    if root is None:
        return [], []
    done = done_items(workspace.state_dir)
    since = (today - datetime.timedelta(days=DECISION_DAYS)).isoformat()
    opens, decisions, seen = [], [], set()
    for path in sorted((root / INBOX_DIR).glob('*.md'), key=lambda p: p.name, reverse=True):
        try:
            parsed = digest.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for it in parsed['items']:
            if it['decision'] not in ('accepted', 'fixed') or it['statement'].lower() in seen:
                continue
            seen.add(it['statement'].lower())
            day = it['said'] or it['date'] or parsed['created']
            if it['kind'].lower() in OPEN_KINDS and (rel, it['id']) not in done:
                opens.append(dict(id=f'chat:{rel}:{it["id"]}', title=it['statement'], due=None, touched=day,
                                  open=dict(kind='note', source_id=sid, path=rel),
                                  badges=[dict(kind=parsed['kind'], day=day, source_id=sid, path=rel)],
                                  done=[dict(digest=rel, item=it['id'])]))
            elif it['kind'].lower() in DECISION_KINDS and day >= since:
                decisions.append(dict(text=it['statement'], day=day, source_id=sid, digest=rel))
    decisions.sort(key=lambda d: d['day'], reverse=True)
    return opens, decisions[:8]


def _rules(source, known):
    """The source's rules for what waits for a member, read once per build."""
    if source.source_id not in known:
        settings, _ = team.load_settings(source)
        known[source.source_id] = settings['work'] if settings else team.NO_WORK
    return known[source.source_id]


def handed_to(title, phrases, name):
    """True when the title has one of the phrases, `{name}` being the person's first name, as
    whole words side by side; case and Turkish letters aside, so "Can" is not "Ercan"."""
    first = (profile.fold(name).split() or [''])[0]
    words = re.findall(r'\w+', profile.fold(title))
    for phrase in phrases:
        wanted = re.findall(r'\w+', profile.fold(phrase.replace('{name}', first)))
        if first and wanted and any(words[i:i + len(wanted)] == wanted for i in range(len(words))):
            return True
    return False


def _from_team(workspace, name):
    """(rows waiting for the person, the rest of their notes) from the team sources."""
    from .vaultview import _history, _modified
    rows, owned, histories, known = [], [], {}, {}
    for source, rel, path, front in profile.team_notes(workspace):
        rules = _rules(source, known)
        title = str(front.get('title') or front.get('_heading') or Path(rel).stem)
        mine = profile.owned_by(front.get('owner'), name)
        status = str(front.get('status') or '').lower()
        handed = rel.startswith(rules['inbox']) and handed_to(title, rules['handed'], name)
        if not (mine or handed):
            continue
        if source.source_id not in histories:
            histories[source.source_id] = _history(source)
        modified, _ = _modified(rel, path, *histories[source.source_id])
        touched = datetime.datetime.fromtimestamp(modified).isoformat(timespec='seconds')
        note = dict(title=title, source_id=source.source_id, path=rel, touched=touched)
        if handed or status in rules['review_statuses']:
            try:
                text = path.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                text = ''
            rows.append(dict(id=f'okb:{source.source_id}:{rel}', title=title, due=None, touched=touched,
                             open=dict(kind='note', source_id=source.source_id, path=rel),
                             badges=[dict(kind='okb', state='handed' if handed else 'review', source_id=source.source_id, path=rel)],
                             done=[], text=text))
        else:
            owned.append(note)
    owned.sort(key=lambda n: n['touched'], reverse=True)
    return rows, owned


def _card_rows(cards):
    return [dict(id='trello:' + c.get('url', ''), title=c.get('name', ''), due=c.get('due'), touched=c.get('touched') or '',
                 open=dict(kind='url', url=c.get('url', '')),
                 badges=[dict(kind='trello', board=c.get('board', ''), list=c.get('list', ''), url=c.get('url', ''))],
                 done=[], code=(CARD_LINK.search(c.get('url', '')) or [None, None])[1], number=c.get('number'))
            for c in cards]


def _card_for(text, by_code, by_number):
    for code in CARD_LINK.findall(text):
        if code in by_code:
            return by_code[code]
    for number in CARD_NUMBER.findall(text):
        found = by_number.get(int(number), [])
        if len(found) == 1:
            return found[0]
    return None


def _merge(cards, others):
    """Notes and chat items that point at one of the cards become badges on its row."""
    by_code = {c['code']: c for c in cards if c['code']}
    by_number = {}
    for c in cards:
        if c['number'] is not None:
            by_number.setdefault(c['number'], []).append(c)
    rows = []
    for row in others:
        card = _card_for(row['title'] + '\n' + row.get('text', ''), by_code, by_number)
        if card is None:
            rows.append(row)
        else:
            card['badges'] += row['badges']
            card['done'] += row['done']
    return cards + rows


def build(workspace, today=None):
    today = today or datetime.date.today()
    signed = profile.load(workspace.state_dir)
    board = trello.view(workspace.state_dir)
    opens, decisions = _from_digests(workspace, today)
    team_rows, owned = _from_team(workspace, signed['name']) if signed else ([], [])
    rows = _merge(_card_rows(board.get('cards') or []), team_rows + opens)
    # Dated work first, the nearest at the top; then the latest activity.
    rows.sort(key=lambda r: r['touched'] or '', reverse=True)
    rows.sort(key=lambda r: (r['due'] is None, r['due'] or ''))
    for row in rows:
        for key in ('text', 'code', 'number'):
            row.pop(key, None)
    return dict(items=rows, owned=owned, decisions=decisions, signed_in=signed is not None,
                name=signed['name'] if signed else '',
                trello=dict(connected=board.get('connected') is True, error=board.get('error')))
