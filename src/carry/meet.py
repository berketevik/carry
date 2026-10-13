"""Carry Meet: a meeting transcribed on this Mac, kept as a note, summarised once.

The app records the microphone (the owner) and, on macOS 14.2+, the system audio (the
other side, also in headphones) and transcribes both on the Mac with Apple's on-device
recognizer; no audio is written to disk and nothing goes online. At Stop the transcript
is saved at once, as one file under sources/carry/meet/, so a failed summary never loses
a meeting. The summary runs once in the background on the owner's own assistant (harvest's
path: Claude Sonnet, else Codex), the transcript in parts within a fixed budget. Every
summary point and item carries a quote that code finds word for word in the cited line,
or it is dropped. The file gets the summary and the minutes; decisions, open items and
facts also go to one draft digest in +/, which the owner reviews item by item like chat
drafts (carry.digest). Nothing is accepted automatically.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from . import harvest
from .background import detached
from .errors import CarryError
from .markdown import parse_frontmatter
from .mask import mask
from .paths import resolve_within
from .persistence import atomic_text

MEET_DIR = 'sources/carry/meet'
STATE_NAME = 'meet.json'
LOG_NAME = 'meet.log'
# Budget, measured on Turkish speech at about 900 characters a minute: a part is about
# 18 minutes, twelve parts about three and a half hours. A longer meeting is summarised up
# to there and says so. At most PARTS + 1 calls, plus one per ten items to compare them
# with the notes.
PART_CHARS = 16000
MAX_PARTS = 12
MAX_LINES = 20000
MAX_TEXT = 4000  # characters of one transcript line
TYPES = ('decision', 'open_item', 'fact')
SPEAKERS = ('me', 'others')

LABELS = {
    'Turkish': dict(me='Ben', others='Karşı taraf', meeting='Toplantı', summary='Özet', minutes='Tutanak',
                    transcript='Transkript', decision='Kararlar', open_item='Açık işler', fact='Bilgiler',
                    pending='Özet hazırlanıyor; Carry toplantı bitince bir kez özetler.',
                    failed='Özet çıkarılamadı: {error}. Carry uygulamasında Toplantılar sayfasından yeniden deneyin.',
                    nothing='Bu toplantıdan kaydedilecek bir karar, açık iş ya da bilgi çıkmadı.',
                    cut='Bütçe aşıldı: transkriptin {line}. satırdan sonrası özetlenmedi.'),
    'English': dict(me='Me', others='Others', meeting='Meeting', summary='Summary', minutes='Minutes',
                    transcript='Transcript', decision='Decisions', open_item='Open items', fact='Facts',
                    pending='Summary in progress; Carry summarises once the meeting ends.',
                    failed='No summary: {error}. Retry from the Meetings page in the Carry app.',
                    nothing='No decision, open item or fact worth keeping came out of this meeting.',
                    cut='Over budget: the transcript after line {line} was not summarised.'),
}

SYSTEM = """You take notes from part of a meeting transcript. It was recorded and transcribed automatically on the owner's Mac, so expect recognition errors and missing punctuation. Lines are numbered; "{me}" is the owner's microphone, "{others}" is everyone else (the call's audio). Notes are in {language}.
Return ONLY JSON: {{"items": [{{"type": "summary|decision|open_item|fact", "statement": "<one self-contained sentence in {language}>", "line": <n>, "quote": "<verbatim 4-25 words copied exactly from that line>", "keywords": ["<3-6 short search terms, in {language} and in English>"]}}]}}
- summary: the 2-6 main points of this part, in the order they came up.
- decision: what the meeting decided or agreed.
- open_item: work still to do, with who and by when if it was said.
- fact: a durable fact worth remembering after the meeting (a number, date, name, setting, result).
Exclude greetings, small talk, recognition noise and anything withdrawn later in the part. Never state anything the quote does not support. If nothing fits, return {{"items": []}}."""

MERGE = """These are the main points of a meeting, part by part, in {language}. Merge them into at most 8 points for the whole meeting, in order. Keep each point's line and quote exactly as given (choose one when you merge several).
Return ONLY JSON: {{"items": [{{"statement": "<one sentence in {language}>", "line": <n>, "quote": "<the quote, unchanged>"}}]}}"""


def labels(language):
    return LABELS.get(language, LABELS['English'])


def words(text):
    """Lower-case words: a quote matches its line word for word, whatever punctuation the
    recognizer or the assistant put around them."""
    return ' '.join(re.findall(r'\w+', (text or '').lower()))


def _clock(seconds):
    seconds = int(seconds)
    return f'{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}' if seconds >= 3600 else f'{seconds // 60:02d}:{seconds % 60:02d}'


def _seconds(clock):
    parts = [int(p) for p in clock.split(':')]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def safe_title(title, language):
    """A file-name-safe title: no path, wikilink or Obsidian-reserved characters."""
    text = re.sub(r'[\\/:*?"<>|#^\[\]\x00-\x1f]', ' ', str(title or ''))
    text = ' '.join(text.split()).strip(' .')[:80].strip()
    return text or labels(language)['meeting']


def meeting_source(ws, override=None):
    """The notes folder meetings go to: the app's own vault, never a team's knowledge base."""
    from .vaultview import default_vault
    own = [s for s in ws.sources if not s.github and s.scope != 'team']
    if override:
        root = Path(override).expanduser().resolve()
        chosen = next((s for s in own if Path(s.root).expanduser().resolve() == root), None)
        if chosen is None:
            raise CarryError('meeting_vault_not_own_source')
        return chosen
    chosen = next((s for s in own if s.source_id == default_vault(ws)), own[0] if own else None)
    if chosen is None:
        raise CarryError('meeting_needs_own_notes')
    return chosen


def meeting_vault(ws, override=None):
    return Path(meeting_source(ws, override).root).expanduser().resolve()


# -- the transcript file --------------------------------------------------------

LINE = re.compile(r'^- \*\*\[(?P<n>\d+) · (?P<at>\d+:\d{2}(?::\d{2})?)\] (?P<speaker>[^*]+?):\*\* (?P<text>.*)$')


def clean_lines(segments):
    """The app's segments as transcript lines, in time order; anything malformed is left out."""
    if not isinstance(segments, list):
        raise CarryError('invalid_meeting_transcript')
    out = []
    for s in segments[:MAX_LINES]:
        if not isinstance(s, dict) or s.get('speaker') not in SPEAKERS:
            continue
        try:
            start = max(0.0, float(s.get('start', 0)))
        except (TypeError, ValueError):
            continue
        text = ' '.join(str(s.get('text') or '').split())[:MAX_TEXT]
        if text:
            out.append(dict(start=start, speaker=s['speaker'], text=text))
    return drop_echo(sorted(out, key=lambda x: x['start']))


# On speakers the microphone also hears the other side, so their words come back as the
# owner's. A line of the owner's is an echo when the other side said most of its words
# within a few seconds of it; short replies ("evet, tamam") are always kept.
ECHO_SECONDS = 10
ECHO_SHARE = 0.7
ECHO_WORDS = 4


def drop_echo(lines):
    others = [(x['start'], set(words(x['text']).split())) for x in lines if x['speaker'] == 'others']
    if not others:
        return lines
    out = []
    for x in lines:
        own = set(words(x['text']).split())
        if x['speaker'] == 'me' and len(own) >= ECHO_WORDS:
            heard = set().union(*(w for at, w in others if abs(at - x['start']) <= ECHO_SECONDS))
            if len(own & heard) >= ECHO_SHARE * len(own):
                continue
        out.append(x)
    return out


def save(ws, segments, title='', started=None, duration=None, root=None, language=None):
    """Write the transcript as a meeting note at once and return its path in the vault.
    `started` is the recording's start (ISO 8601, local time); `duration` its length in seconds."""
    root = meeting_vault(ws, root)
    language = language or harvest.draft_language(ws, root)
    L = labels(language)
    lines = clean_lines(segments)
    if not lines:
        raise CarryError('meeting_transcript_empty')
    try:
        when = datetime.datetime.fromisoformat(str(started)) if started else datetime.datetime.now()
    except ValueError:
        raise CarryError('invalid_meeting_start')
    when = when.astimezone() if when.tzinfo else when
    try:
        minutes = max(1, round(float(duration) / 60)) if duration is not None else max(1, round(lines[-1]['start'] / 60))
    except (TypeError, ValueError):
        raise CarryError('invalid_meeting_duration')
    name = safe_title(title, language)
    stem, n = f'{when:%Y-%m-%d %H%M} — {name}', 2
    rel = f'{MEET_DIR}/{stem}.md'
    while (root / rel).exists():
        rel, n = f'{MEET_DIR}/{stem} ({n}).md', n + 1
    body = '\n'.join(f"- **[{i + 1} · {_clock(x['start'])}] {L[x['speaker']]}:** {x['text']}" for i, x in enumerate(lines))
    summary = f'{name}, {when:%Y-%m-%d %H:%M}, {minutes} min'
    text = (f'---\ntype: meeting\nsummary: {json.dumps(summary, ensure_ascii=False)}\ncreated: {when:%Y-%m-%d}\n'
            f'started: {when.isoformat(timespec="seconds")}\nduration_minutes: {minutes}\nsource_type: meet\n'
            f'meet_status: pending\n---\n\n# {name}\n\n## {L["summary"]}\n\n*{L["pending"]}*\n\n## {L["transcript"]}\n\n{body}\n')
    atomic_text(root / rel, text)
    return dict(path=rel, root=str(root), lines=len(lines), minutes=minutes, title=name)


def read(root, rel):
    """(frontmatter, title, transcript lines) of a meeting note."""
    path = resolve_within(root, rel)
    if not path.is_file():
        raise CarryError('source_file_missing')
    front, body = parse_frontmatter(path.read_text(encoding='utf-8'))
    if front.get('type') != 'meeting':
        raise CarryError('not_a_meeting')
    title = next((l[2:].strip() for l in body.splitlines() if l.startswith('# ')), Path(rel).stem)
    lines = []
    for l in body.splitlines():
        m = LINE.match(l)
        if m:
            lines.append(dict(n=int(m['n']), at=m['at'], speaker=m['speaker'].strip(), text=m['text']))
    return front, title, lines, body


# -- summary ----------------------------------------------------------------------

def parts(lines, limit=PART_CHARS, most=MAX_PARTS):
    """Transcript parts within the budget, and the first line left out (None if none was)."""
    out, cur, size = [], [], 0
    for x in lines:
        n = len(x['text']) + 24
        if cur and size + n > limit:
            out.append(cur)
            cur, size = [], 0
            if len(out) == most:
                return out, x['n']
        cur.append(x)
        size += n
    return out + ([cur] if cur else []), None


def render(part):
    return '\n'.join(f"[{x['n']}] {x['at']} {x['speaker']}: {mask(x['text'])[0]}" for x in part)


def check(items, lines, kinds):
    """Type, line and quote in code: a quote must appear word for word in a transcript line
    (the cited one first); anything else is dropped. Returns (kept, dropped)."""
    by_n = {x['n']: x for x in lines}
    seen, kept, dropped = set(), [], 0
    for it in items:
        quote = words(it.get('quote'))
        statement = ' '.join(str(it.get('statement') or '').split())
        kind = it.get('type', 'summary')
        try:
            cited = int(it.get('line'))
        except (TypeError, ValueError):
            cited = None
        order = ([by_n[cited]] if cited in by_n else []) + lines
        hit = next((x for x in order if quote and len(quote.split()) >= 3 and quote in words(mask(x['text'])[0])), None)
        digest = hashlib.sha1((kind + statement.lower()).encode()).hexdigest()[:12]
        if kind not in kinds or not hit or not statement or digest in seen:
            dropped += 1
            continue
        seen.add(digest)
        keywords = [k.strip() for k in (it.get('keywords') or []) if isinstance(k, str) and k.strip()][:6]
        kept.append(dict(type=kind, statement=statement, quote=' '.join(str(it['quote']).split()), line=hit['n'],
                         speaker=hit['speaker'], clock=hit['at'], keywords=keywords))
    return kept, dropped


def extract(lines, language, which, ask=None):
    """Summary points and items of the whole meeting: one call per part, one to merge the points."""
    ask = ask or harvest.ask_agent
    L = labels(language)
    chosen, cut = parts(lines)
    system = SYSTEM.format(me=L['me'], others=L['others'], language=language)
    raw = []
    for part in chosen:
        raw += ask(system, render(part), which, 'items')
    kept, dropped = check(raw, lines, ('summary',) + TYPES)
    points = [it for it in kept if it['type'] == 'summary']
    items = [it for it in kept if it['type'] != 'summary']
    if len(points) > 8:
        body = json.dumps([dict(statement=p['statement'], line=p['line'], quote=p['quote']) for p in points], ensure_ascii=False, indent=1)
        try:
            merged, _ = check([dict(m, type='summary') for m in ask(MERGE.format(language=language), body, which, 'items')],
                              lines, ('summary',))
            points = merged or points
        except (CarryError, subprocess.SubprocessError, OSError):
            pass  # the points of every part are still right, only longer
    return points, items, dropped, cut


def _quote(it, L, day):
    return f"  > {it['quote']} *(segment {it['line']}, {it['speaker']}, {day})*"




def _digest_name(root, day, stem, L):
    """`+/<day> — meeting <HHMM> <title>.md`: never the meeting note's own name, so a link
    to the meeting stays unambiguous; a second summary the same day gets a number."""
    name = f"+/{day} — {L['meeting'].lower()} {stem[11:]}"
    rel, n = f'{name}.md', 2
    while (root / rel).exists():
        rel, n = f'{name} ({n}).md', n + 1
    return rel


def write_digest(root, rel, title, groups, meta, language, day):
    """The meeting's items as a draft digest in +/, in the harvest digest's format, so the
    Review page decides them item by item and accepted ones go to that day's log."""
    L, H = labels(language), harvest.LABELS.get(language, harvest.LABELS['English'])
    stem = Path(rel).stem
    counts = {k: len(v) for k, v in groups.items()}
    summary = f"{title}: {counts.get('new', 0)} new, {counts.get('conflict', 0)} conflict, {counts.get('known', 0)} already recorded"
    lines = ['---', 'type: output', f'summary: {json.dumps(summary, ensure_ascii=False)}', f'created: {day}',
             'draft: true', 'provenance: meeting', 'sources:', f'  - "[[{stem}]]"',
             f'harvest: {json.dumps(meta, ensure_ascii=False)}', '---', '', f"# {L['meeting']}: {title}", '']

    def item_line(it):
        where = f" ↔ [[{Path(it['match']).stem}]]" if it.get('match') else ''
        line = f"- **{H[it['type']]}:** {it['statement']}{where}\n{_quote(it, L, day)}"
        line += f"\n  ≠ {it['match_text']}" if it.get('match_text') else ''
        return line + (f"\n  ∵ {it['why']}" if it.get('why') else '')
    for key in ('new', 'conflict'):
        if groups.get(key):
            lines += [f'## {H[key]}', ''] + [item_line(it) for it in groups[key]] + ['']
    if groups.get('known'):
        lines += [f"## {H['known']}", ''] + [f"- {it['statement']} → [[{Path(it['match']).stem}]]" for it in groups['known']] + ['']
    if meta.get('compared') is False:
        lines += [f"*{H['unchecked']}*", '']
    digest_rel = _digest_name(root, day, stem, L)
    atomic_text(root / digest_rel, '\n'.join(lines))
    return digest_rel


def _front(front, status, **extra):
    keep = ('summary', 'created', 'started', 'duration_minutes')
    head = ['---', 'type: meeting'] + [f'{k}: {json.dumps(front[k], ensure_ascii=False) if k == "summary" else front[k]}'
                                       for k in keep if front.get(k) is not None]
    head += ['source_type: meet', f'meet_status: {status}']
    head += [f'meet_{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in extra.items() if v]
    return '\n'.join(head + ['---', '', ''])


def write_note(root, rel, front, title, body, points, items, language, digest_rel=None, cut=None):
    """The meeting note with its summary and minutes above the transcript, which stays as it was."""
    L = labels(language)
    day = str(front.get('created') or datetime.date.today().isoformat())
    marker = f"## {L['transcript']}"
    transcript = body[body.index(marker):] if marker in body else ''
    out = [f'# {title}', '', f"## {L['summary']}", '']
    out += [f"- {p['statement']} *({p['clock']})*" for p in points] or [f"*{L['nothing']}*"]
    out += ['', f"## {L['minutes']}", '']
    for kind in TYPES:
        chosen = [it for it in items if it['type'] == kind]
        if chosen:
            out += [f'### {L[kind]}', ''] + [f"- {it['statement']}\n{_quote(it, L, day)}" for it in chosen] + ['']
    if not items:
        out += [f"*{L['nothing']}*", '']
    if cut:
        out += [f"*{L['cut'].format(line=cut - 1)}*", '']
    atomic_text(root / rel, _front(front, 'done', digest=digest_rel) + '\n'.join(out) + '\n' + transcript)


def mark_failed(root, rel, error, language):
    """A failed summary says so in the note; the transcript is never touched."""
    front, title, _, body = read(root, rel)
    L = labels(language)
    marker = f"## {L['transcript']}"
    transcript = body[body.index(marker):] if marker in body else ''
    text = f"# {title}\n\n## {L['summary']}\n\n*{L['failed'].format(error=error)}*\n\n"
    atomic_text(root / rel, _front(front, 'failed', error=str(error)[:200]) + text + transcript)


def summarize(ws, rel, root=None, language=None, which=None):
    """The one summary of a saved meeting. The owner's assistant compares the items with the
    notes (known or a real conflict); meeting text never goes to an online checker."""
    root = meeting_vault(ws, root)
    front, title, lines, body = read(root, rel)
    language = language or harvest.draft_language(ws, root)
    if not lines:
        raise CarryError('meeting_transcript_empty')
    day = str(front.get('created') or datetime.date.today().isoformat())
    _record(ws, rel, status='running', pid=os.getpid(), started_at=time.time())
    try:
        which = which or harvest.extractor()
        points, items, dropped, cut = extract(lines, language, which)
        groups = dict(new=[], conflict=[], known=[])
        ran = False
        if items:
            for it in items:
                it['at'] = day  # the day it was said, for the comparison and the log
            verdicts, ran = harvest.agent_compare(items, ws, which, language)
            for it, (verdict, it['match']) in zip(items, verdicts):
                groups[verdict].append(it)
        meta = dict(extractor=which, judge='agent' if ran else None, compared=ran, kind='meeting', meeting=rel,
                    dropped_without_quote=dropped)
        digest_rel = write_digest(root, rel, title, groups, meta, language, day) if items else None
        write_note(root, rel, front, title, body, points, items, language, digest_rel, cut)
    except (CarryError, subprocess.SubprocessError, OSError) as exc:
        _record(ws, rel, status='failed', error=str(exc))
        try:
            mark_failed(root, rel, exc, language)
        except (CarryError, OSError):
            pass
        raise exc if isinstance(exc, CarryError) else CarryError(f'meeting_summary_failed: {exc}') from exc
    _record(ws, rel, status='done', digest=digest_rel)
    return dict(path=rel, digest=digest_rel, points=len(points), items=len(items), dropped=dropped, cut=cut)


# -- background run and the Meetings page ---------------------------------------------

def _state(ws):
    try:
        return json.loads((ws.state_dir / STATE_NAME).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _record(ws, rel, **entry):
    # Only the app's spawn and the summary process itself write an entry, minutes apart.
    state = _state(ws)
    state[rel] = dict(entry, updated=time.time())
    atomic_text(ws.state_dir / STATE_NAME, json.dumps(state, indent=1, ensure_ascii=False) + '\n')


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def spawn(ws, rel, root, python=None):
    """Start the summary in the background and return at once. Started from the app, the
    process has launchd's short PATH: give it the folders Claude Code and Codex live in."""
    args = [python or sys.executable, '-I', '-m', 'carry.cli', '--workspace', str(ws.state_dir), 'meet', 'summarize',
            '--path', rel, '--vault', str(root)]
    env = dict(os.environ, PATH=':'.join(dict.fromkeys(harvest.schedule_path_env().split(':') + os.environ.get('PATH', '').split(':'))))
    with open(ws.state_dir / LOG_NAME, 'a') as log:
        proc = detached(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
    _record(ws, rel, status='running', pid=proc.pid, started_at=time.time())
    return proc


def listing(ws, root=None, limit=100):
    """Meetings, newest first, with their summary state: pending, running, done or failed."""
    try:
        source = meeting_source(ws, root)
    except CarryError:
        return dict(meetings=[], root=None, source_id=None)
    root = Path(source.root).expanduser().resolve()
    folder = root / MEET_DIR
    state = _state(ws)
    out = []
    for path in sorted(folder.glob('*.md'), reverse=True)[:limit] if folder.is_dir() else []:
        rel = f'{MEET_DIR}/{path.name}'
        try:
            front, _ = parse_frontmatter(path.read_text(encoding='utf-8'))
        except OSError:
            continue
        if front.get('type') != 'meeting':
            continue
        status = str(front.get('meet_status') or 'pending')
        job = state.get(rel, {})
        if status != 'done' and job.get('status') == 'running':  # a retry runs over a failed one
            status = 'running' if _alive(job.get('pid')) else 'failed'
        title = path.stem.split(' — ', 1)[-1]
        out.append(dict(path=rel, title=title, started=str(front.get('started') or ''), minutes=front.get('duration_minutes'),
                        status=status, digest=front.get('meet_digest') or job.get('digest'),
                        error=front.get('meet_error') or (job.get('error') if status == 'failed' else None)))
    return dict(meetings=out, root=str(root), source_id=source.source_id)
