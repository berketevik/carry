"""Carry Meet: a meeting transcribed on this Mac, kept as a note, summarised once.

The app records the microphone (the owner) and, on macOS 14.2+, the system audio (the
other side, also in headphones) and transcribes both live with Apple's on-device recognizer.
It also writes each side to a temporary WAV file in the workspace (meet-audio/). At Stop the
transcript is saved at once, as one file under sources/carry/meet/, so nothing later can lose
a meeting. Then, in the background and still on this Mac, Whisper transcribes each side again
(carry.whisper_model; it pauses while a call has the microphone) and its transcript replaces
Apple's; the audio is deleted whatever happens, and nothing goes online. When no Whisper model
is installed the audio is deleted at Stop and Apple's transcript stays.

The summary runs once, only on an assistant connected to the notes folder (Claude Sonnet,
else Codex), the transcript in parts within a fixed budget; with none connected the note
keeps its raw transcript and says how to connect one. Every summary point and item carries a
quote that code finds word for word in the cited line, or it is dropped. The file gets the
summary and the minutes; decisions, open items and facts also go to one draft digest in +/,
which the owner reviews item by item like chat drafts (carry.digest). Nothing is accepted
automatically.
"""
import bisect
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from . import filelock, harvest, okf
from .background import detached
from .errors import CarryError
from .markdown import FRONTMATTER_RE, parse_frontmatter
from .mask import mask
from .paths import resolve_within
from .persistence import atomic_text

MEET_DIR = 'sources/carry/meet'
STATE_NAME = 'meet.json'
LOG_NAME = 'meet.log'
AUDIO_DIR = 'meet-audio'          # in the workspace: the app's temporary WAV files, one folder a meeting
AUDIO_NAME = re.compile(r'^[A-Za-z0-9-]{1,64}$')
ORPHAN_SECONDS = 24 * 3600        # audio left behind by a job that never ended is deleted after a day
BUSY_NAME = 'meet-busy'           # the app touches it every 3 s while a call has the microphone
BUSY_SECONDS = 10
LOCK_NAME = 'meet-whisper.lock'   # one transcription at a time: two meetings in a row take turns
WHISPER_LANGUAGES = {'Turkish': 'tr', 'English': 'en'}
# Budget, measured on Turkish speech at about 900 characters a minute: a part is about
# 18 minutes, twelve parts about three and a half hours. A longer meeting is summarised up
# to there and says so. At most PARTS + 1 calls, plus one per ten items to compare them
# with the notes.
PART_CHARS = 16000
MAX_PARTS = 12
# Apple's on-device model has a 4,096-token window, and Turkish runs at 2.6 characters a
# token there (measured): a part is about 5 minutes, forty parts the same three and a half
# hours. It does not compare the items with the notes (a batch is far over its window).
APPLE_PART_CHARS = 5000
APPLE_MAX_PARTS = 40
APPLE = 'apple:on-device'
MAX_LINES = 20000
MAX_TEXT = 4000  # characters of one transcript line
TYPES = ('decision', 'open_item', 'fact')
SPEAKERS = ('me', 'others')

LABELS = {
    'Turkish': dict(me='Ben', others='Karşı taraf', meeting='Toplantı', summary='Özet', minutes='Tutanak',
                    transcript='Transkript', decision='Kararlar', open_item='Açık işler', fact='Bilgiler',
                    pending='Özet hazırlanıyor; Carry toplantı bitince bir kez özetler.',
                    whisper='Canlı transkript boş kaldı; transkripti bu Mac\'te Whisper yazıyor.',
                    failed='Özet çıkarılamadı: {error}. Carry uygulamasında Toplantılar sayfasından yeniden deneyin.',
                    raw='Özet yok: bu not klasörüne bağlı bir asistan yok, transkript ham kaldı. Ayarlar › Asistanlar '
                        'bölümünden Claude Code ya da Codex\'i bağlayıp Toplantılar sayfasından özetleyin.',
                    mic_only='Sistem sesi alınamadı; yalnız mikrofon kaydedildi, karşı tarafın sözleri transkriptte yok.',
                    by='Özeti {who} çıkardı.',
                    nothing='Bu toplantıdan kaydedilecek bir karar, açık iş ya da bilgi çıkmadı.',
                    cut='Bütçe aşıldı: transkriptin {line}. satırdan sonrası özetlenmedi.'),
    'English': dict(me='Me', others='Others', meeting='Meeting', summary='Summary', minutes='Minutes',
                    transcript='Transcript', decision='Decisions', open_item='Open items', fact='Facts',
                    pending='Summary in progress; Carry summarises once the meeting ends.',
                    whisper='The live transcript stayed empty; Whisper writes the transcript on this Mac.',
                    failed='No summary: {error}. Retry from the Meetings page in the Carry app.',
                    raw='No summary: no assistant is connected to this notes folder, so the transcript stays raw. '
                        'Connect Claude Code or Codex under Settings › Assistants, then summarise it from the Meetings page.',
                    mic_only='The system audio could not be recorded; only the microphone was, so the other side is missing.',
                    by='Summarised by {who}.',
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
    return ' '.join(re.findall(r'\w+', (text or '').lower().replace('\u0307', '')))  # 'İ' lowers to i + a dot


# Apple's on-device model copies Turkish letters without their marks ('yayına' as 'yayina').
FOLD = str.maketrans('çğıöşüâîû', 'cgiosuaiu')


def locate(quote, order):
    """The first line in order that says the quote, and the quote as that line says it when
    only the marks of Turkish letters differ (None when they match as they are)."""
    if not quote or len(quote.split()) < 3:
        return None, None
    loose = quote.translate(FOLD)
    for x in order:
        line = words(mask(x['text'])[0])
        if quote in line:
            return x, None
        at = line.translate(FOLD).find(loose)
        if at >= 0:
            return x, line[at:at + len(loose)]
    return None, None


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
SHORT_ECHO_SECONDS = 4


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
        # A short line is an echo when one line of the other side, begun just before it, says
        # every one of its words ("Aynen geliyor" two seconds into the other side's sentence).
        if x['speaker'] == 'me' and 2 <= len(own) < ECHO_WORDS and any(
                own <= w for at, w in others if -1 <= x['start'] - at <= SHORT_ECHO_SECONDS):
            continue
        out.append(x)
    return out


def _section(lines, L, mic_only=False):
    """The transcript section: numbered lines, and a word when the other side was not heard."""
    head = f"## {L['transcript']}\n\n" + (f"*{L['mic_only']}*\n\n" if mic_only else '')
    return head + '\n'.join(f"- **[{i + 1} · {_clock(x['start'])}] {L[x['speaker']]}:** {x['text']}" for i, x in enumerate(lines)) + '\n'


def save(ws, segments, title='', started=None, duration=None, root=None, language=None, mic_only=False, whisper=False):
    """Write the transcript as a meeting note at once and return its path in the vault.
    `started` is the recording's start (ISO 8601, local time); `duration` its length in seconds;
    `mic_only` says the system audio could not be recorded. With `whisper` (the audio goes to
    Whisper next) a meeting the live recognizer heard nothing of is saved too, for Whisper to fill."""
    root = meeting_vault(ws, root)
    language = language or harvest.draft_language(ws, root)
    L = labels(language)
    lines = clean_lines(segments)
    if not lines and not whisper:
        raise CarryError('meeting_transcript_empty')
    try:
        when = datetime.datetime.fromisoformat(str(started)) if started else datetime.datetime.now()
    except ValueError:
        raise CarryError('invalid_meeting_start')
    when = when.astimezone() if when.tzinfo else when
    try:
        minutes = max(1, round(float(duration) / 60)) if duration is not None else max(1, round(lines[-1]['start'] / 60) if lines else 1)
    except (TypeError, ValueError):
        raise CarryError('invalid_meeting_duration')
    name = safe_title(title, language)
    stem, n = f'{when:%Y-%m-%d %H%M} — {name}', 2
    rel = f'{MEET_DIR}/{stem}.md'
    while (root / rel).exists():
        rel, n = f'{MEET_DIR}/{stem} ({n}).md', n + 1
    summary = f'{name}, {when:%Y-%m-%d %H:%M}, {minutes} min'
    text = (f'---\ntype: meeting\ndescription: {json.dumps(summary, ensure_ascii=False)}\ncreated: {when:%Y-%m-%d}\n'
            f'generated: {okf.flow(okf.event(okf.carry_actor()))}\n'
            f'started: {when.isoformat(timespec="seconds")}\nduration_minutes: {minutes}\nsource_type: meet\n'
            f'meet_language: {language}\nmeet_status: pending\n' + ('meet_mic_only: true\n' if mic_only else '') +
            f'---\n\n# {name}\n\n## {L["summary"]}\n\n*{L["pending"]}*\n\n'
            + (_section(lines, L, mic_only) if lines else f"## {L['transcript']}\n\n*{L['whisper']}*\n"))
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
        hit, said = locate(quote, order)
        digest = hashlib.sha1((kind + statement.lower()).encode()).hexdigest()[:12]
        if kind not in kinds or not hit or not statement or digest in seen:
            dropped += 1
            continue
        seen.add(digest)
        keywords = [k.strip() for k in (it.get('keywords') or []) if isinstance(k, str) and k.strip()][:6]
        kept.append(dict(type=kind, statement=statement, quote=said or ' '.join(str(it['quote']).split()), line=hit['n'],
                         speaker=hit['speaker'], clock=hit['at'], keywords=keywords))
    return kept, dropped


def _ask_part(ask, system, part, which):
    """One part's items; a part over the model's window is asked again in halves."""
    try:
        return ask(system, render(part), which, 'items')
    except CarryError as exc:
        if 'context_window_exceeded' not in str(exc) or len(part) < 2:
            raise
        half = len(part) // 2
        return _ask_part(ask, system, part[:half], which) + _ask_part(ask, system, part[half:], which)


def extract(lines, language, which, ask=None):
    """Summary points and items of the whole meeting: one call per part, one to merge the points."""
    ask = ask or harvest.ask_agent
    L = labels(language)
    chosen, cut = parts(lines, APPLE_PART_CHARS, APPLE_MAX_PARTS) if which == APPLE else parts(lines)
    system = SYSTEM.format(me=L['me'], others=L['others'], language=language)
    raw = []
    for part in chosen:
        raw += _ask_part(ask, system, part, which)
    kept, dropped = check(raw, lines, ('summary',) + TYPES)
    points = [it for it in kept if it['type'] == 'summary']
    items = [it for it in kept if it['type'] != 'summary']
    if len(points) > 8 and which == APPLE:
        # Asked to merge, the on-device model returned 11 points out of order with a name
        # misspelt (measured): keep each part's first points instead, in transcript order.
        starts, taken, quota = [part[0]['n'] for part in chosen], {}, max(1, 8 // len(chosen))
        firsts = []
        for p in sorted(points, key=lambda p: p['line']):
            k = bisect.bisect_right(starts, p['line'])
            taken[k] = taken.get(k, 0) + 1
            if taken[k] <= quota:
                firsts.append(p)
        points = firsts
    elif len(points) > 8:
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
    lines = ['---', 'type: output', f'description: {json.dumps(summary, ensure_ascii=False)}', f'created: {day}',
             'status: draft', f'generated: {okf.flow(okf.event(okf.carry_actor()))}', 'provenance: meeting',
             *okf.source_lines([okf.entry(rel)]),
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


def _own_front(text):
    """The owner's frontmatter lines, as written: every key but type, source_type, generated and meet_*."""
    m = FRONTMATTER_RE.match(text.replace('\r\n', '\n'))
    out, keep = [], False
    for l in m.group(1).splitlines() if m else []:
        if l and not l[0].isspace() and not l.startswith('-'):
            key = l.split(':', 1)[0].strip()
            keep = key not in ('type', 'source_type', 'generated') and not key.startswith('meet_')
        if keep:
            out.append(l)
    return out


def _front(text, front, status, **extra):
    """The note's frontmatter for a rewrite: the owner's keys (tags, related, sources...) stay as
    they were written; Carry sets only type, source_type, `generated` (it rewrote the summary and
    minutes) and meet_*."""
    own = _own_front(text) or [f'{k}: {json.dumps(front[k], ensure_ascii=False) if k in ("description", "summary") else front[k]}'
                               for k in ('description', 'summary', 'created', 'started', 'duration_minutes')
                               if front.get(k) is not None]
    head = ['---', 'type: meeting'] + own + [f'generated: {okf.flow(okf.event(okf.carry_actor()))}', 'source_type: meet']
    if front.get('meet_language'):
        head.append(f"meet_language: {front['meet_language']}")
    head.append(f'meet_status: {status}')
    # Who wrote the transcript and whether the other side was heard outlive every rewrite.
    extra = dict(dict(transcript=front.get('meet_transcript'), mic_only=front.get('meet_mic_only')), **extra)
    head += [f'meet_{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in extra.items() if v]
    return '\n'.join(head + ['---', '', ''])


def _note(root, rel):
    """(path, text, frontmatter, body) of a meeting note Carry may rewrite: a locked note is
    never rewritten."""
    path = resolve_within(root, rel)
    if not path.is_file():
        raise CarryError('source_file_missing')
    text = path.read_text(encoding='utf-8')
    front, body = parse_frontmatter(text)
    if front.get('lock') is True or str(front.get('lock')).strip().lower() == 'true':
        raise CarryError('meeting_note_locked')
    return path, text, front, body


HEADING = re.compile(r'^## (?:%s)\b[^\n]*\n' % '|'.join(re.escape(L['transcript']) for L in LABELS.values()), re.M)


def note_language(front, body):
    """The language a meeting note was saved in: recorded since it was saved, else its headings."""
    if front.get('meet_language') in LABELS:
        return front['meet_language']
    m = HEADING.search(body)
    return next((lang for lang, L in LABELS.items() if m and m.group().startswith(f"## {L['transcript']}")), None)


def _transcript(body):
    """The transcript section, kept as it was, whatever language the note was saved in. A note
    whose lines are there without the heading is never rewritten: that would drop them."""
    m = HEADING.search(body)
    if m:
        return body[m.start():]
    if any(LINE.match(l) for l in body.splitlines()):
        raise CarryError('meeting_transcript_heading_missing')
    return ''


def _who(which):
    """'claude:sonnet' as Claude (sonnet); Apple's model by its name."""
    if which == APPLE:
        return 'Apple Foundation Models'
    client, _, model = str(which).partition(':')
    return f"{dict(claude='Claude', codex='Codex').get(client, client)} ({model})" if model else client


def write_note(root, rel, front, title, body, points, items, language, digest_rel=None, cut=None, which=None):
    """The meeting note with its summary and minutes above the transcript, which stays as it was
    (read again here: the summary takes minutes, and the owner may have edited the note)."""
    L = labels(language)
    day = str(front.get('created') or datetime.date.today().isoformat())
    path, text, front, body = _note(root, rel)
    transcript = _transcript(body)
    out = [f'# {title}', '', f"## {L['summary']}", '']
    out += [f"- {p['statement']} *({p['clock']})*" for p in points] or [f"*{L['nothing']}*"]
    if which:
        out += ['', f"*{L['by'].format(who=_who(which))}*"]
    out += ['', f"## {L['minutes']}", '']
    for kind in TYPES:
        chosen = [it for it in items if it['type'] == kind]
        if chosen:
            out += [f'### {L[kind]}', ''] + [f"- {it['statement']}\n{_quote(it, L, day)}" for it in chosen] + ['']
    if not items:
        out += [f"*{L['nothing']}*", '']
    if cut:
        out += [f"*{L['cut'].format(line=cut - 1)}*", '']
    atomic_text(path, _front(text, front, 'done', digest=digest_rel, extractor=which) + '\n'.join(out) + '\n' + transcript)


def mark_failed(root, rel, error, language):
    """A failed summary says so in the note; the transcript is never touched."""
    _, title, _, _ = read(root, rel)
    path, text, front, body = _note(root, rel)
    L = labels(language)
    transcript = _transcript(body)
    above = f"# {title}\n\n## {L['summary']}\n\n*{L['failed'].format(error=error)}*\n\n"
    atomic_text(path, _front(text, front, 'failed', error=str(error)[:200]) + above + transcript)


def mark_raw(root, rel, language):
    """No assistant is connected: the note keeps its transcript and says how to summarise it."""
    _, title, _, _ = read(root, rel)
    path, text, front, body = _note(root, rel)
    L = labels(language)
    transcript = _transcript(body)
    above = f"# {title}\n\n## {L['summary']}\n\n*{L['raw']}*\n\n"
    atomic_text(path, _front(text, front, 'raw') + above + transcript)


def write_transcript(root, rel, lines, transcript):
    """Another transcript in place of the note's: the summary part above it stays as it was."""
    path, text, front, body = _note(root, rel)
    L = labels(note_language(front, body) or 'English')
    old = _transcript(body)
    head = body[:len(body) - len(old)] if old else body
    atomic_text(path, _front(text, front, str(front.get('meet_status') or 'pending'), transcript=transcript)
                + head.lstrip('\n') + _section(lines, L, bool(front.get('meet_mic_only'))))


def clients(ws=None, root=None, which=None):
    """Who summarises, in order: the owner's assistants that are connected to the notes folder
    (Settings › Assistants). `which` names one by hand, Apple's on-device model too."""
    if which:
        return [which]
    from .vaultview import assistants
    connected = assistants(ws, root) if ws is not None else {}
    return [c for c in harvest.assistants() if (connected.get(c.split(':')[0]) or {}).get('connected')]


def summarize(ws, rel, root=None, language=None, which=None, audio=None):
    """The one summary of a saved meeting, after Whisper's transcript when the app left its
    `audio`. The owner's assistant compares the items with the notes (known or a real
    conflict); meeting text never goes to an online checker. When an assistant cannot answer
    (signed out, say), the next connected one summarises; with none connected the note stays raw."""
    root = meeting_vault(ws, root)
    audio = audio_folder(ws, audio)  # only the app's own folder is read, then deleted
    if audio:
        transcribe(ws, rel, root, audio)
    _note(root, rel)  # a locked note goes to no assistant
    front, title, lines, body = read(root, rel)
    language = language or note_language(front, body) or harvest.draft_language(ws, root)
    if not lines:
        _record(ws, rel, status='failed', error='meeting_transcript_empty')
        try:
            mark_failed(root, rel, CarryError('meeting_transcript_empty'), language)
        except (CarryError, OSError):
            pass
        raise CarryError('meeting_transcript_empty')
    day = str(front.get('created') or datetime.date.today().isoformat())
    chain = clients(ws, root, which)
    if not chain:
        mark_raw(root, rel, language)
        _record(ws, rel, status='raw')
        reindex(ws)
        return dict(path=rel, raw=True, digest=None, points=0, items=0, dropped=0, cut=None)
    _record(ws, rel, status='running', pid=os.getpid(), started_at=time.time())
    try:
        failures = []
        for which in chain:
            try:
                points, items, dropped, cut = extract(lines, language, which)
                break
            except (CarryError, subprocess.SubprocessError, OSError) as exc:
                failures.append((which, str(exc)))
        else:
            if len(failures) == 1:
                raise CarryError(failures[0][1])
            raise CarryError('assistant_failed: ' + ' | '.join(f"{w.split(':')[0]}: {e.split(': ', 1)[-1]}" for w, e in failures))
        groups = dict(new=[], conflict=[], known=[])
        ran = False
        if items:
            for it in items:
                it['at'] = day  # the day it was said, for the comparison and the log
            if which == APPLE:
                verdicts = [('new', None)] * len(items)
            else:
                verdicts, ran = harvest.agent_compare(items, ws, which, language)
            for it, (verdict, it['match']) in zip(items, verdicts):
                groups[verdict].append(it)
        meta = dict(extractor=which, judge='agent' if ran else None, compared=ran, kind='meeting', meeting=rel,
                    dropped_without_quote=dropped)
        digest_rel = write_digest(root, rel, title, groups, meta, language, day) if items else None
        write_note(root, rel, front, title, body, points, items, language, digest_rel, cut, which)
    except (CarryError, subprocess.SubprocessError, OSError) as exc:
        _record(ws, rel, status='failed', error=str(exc))
        try:
            mark_failed(root, rel, exc, language)
        except (CarryError, OSError):
            pass
        reindex(ws)
        raise exc if isinstance(exc, CarryError) else CarryError(f'meeting_summary_failed: {exc}') from exc
    _record(ws, rel, status='done', digest=digest_rel)
    reindex(ws)
    return dict(path=rel, raw=False, digest=digest_rel, points=len(points), items=len(items), dropped=dropped, cut=cut)


# -- Whisper after the meeting --------------------------------------------------------

def _audio_root(ws):
    return Path(ws.state_dir) / AUDIO_DIR


def audio_folder(ws, audio):
    """The name of a meeting's audio folder the app wrote under meet-audio/, or None for any
    other path (the request names it; nothing outside that folder is ever read or deleted)."""
    if not audio:
        return None
    path = Path(str(audio)).expanduser()
    name = path.name
    if not AUDIO_NAME.match(name):
        return None
    if path.is_absolute() and path.resolve().parent != _audio_root(ws).resolve():
        return None
    return name if (_audio_root(ws) / name).is_dir() else None


def drop_audio(ws, name):
    if name and AUDIO_NAME.match(name):
        shutil.rmtree(_audio_root(ws) / name, ignore_errors=True)


def whisper_ready():
    from . import whisper_model
    return whisper_model.runtime_available() and whisper_model.installed()


def clean_audio(ws, keep=()):
    """Audio a job left behind (the Mac restarted mid-way) is deleted after a day."""
    folder = _audio_root(ws)
    if not folder.is_dir():
        return
    live = {job.get('audio') for job in _state(ws).values() if _alive(job.get('pid'))}
    for each in folder.iterdir():
        if each.name in keep or each.name in live or not each.is_dir():
            continue
        try:
            newest = max([each.stat().st_mtime] + [f.stat().st_mtime for f in each.iterdir()])
        except OSError:
            continue
        if time.time() - newest > ORPHAN_SECONDS:
            shutil.rmtree(each, ignore_errors=True)


def take_audio(ws, audio):
    """At Stop: the folder Whisper will transcribe, or None. Without Whisper the audio is
    deleted at once and Apple's transcript stays."""
    name = audio_folder(ws, audio)
    clean_audio(ws, keep={name})
    if name and not whisper_ready():
        drop_audio(ws, name)
        return None
    return name


def busy(ws):
    """A call has the microphone, or Carry records: the app has touched the flag lately."""
    try:
        return time.time() - (Path(ws.state_dir) / BUSY_NAME).stat().st_mtime < BUSY_SECONDS
    except OSError:
        return False


def _whisper_lock(ws):
    """Wait for any other transcription to end: one model on the GPU at a time."""
    handle = open(Path(ws.state_dir) / LOCK_NAME, 'a+')
    while not filelock.try_lock(handle):
        time.sleep(10)
    return handle


WHISPER_SHARE = 0.3  # a side Whisper heard far less of than Apple did keeps Apple's lines


def _keep_apple_sides(front, body, segments):
    """Whisper's segments, with Apple's lines kept for a side Whisper mostly missed (its voice
    detection dropped a quiet speaker, or the side's audio stopped early): the audio is deleted
    next, so Apple's lines are the only other copy. Returns (segments, sides kept)."""
    L = labels(note_language(front, body) or 'English')
    by_label = {L[s]: s for s in SPEAKERS}
    apple = [dict(start=_seconds(m['at']), speaker=by_label.get(m['speaker'].strip()), text=m['text'])
             for m in (LINE.match(l) for l in body.splitlines()) if m]
    kept = []
    for side in SPEAKERS:
        heard = sum(len(words(x['text']).split()) for x in apple if x['speaker'] == side)
        got = sum(len(words(x['text']).split()) for x in segments if x['speaker'] == side)
        if heard and got < WHISPER_SHARE * heard:
            segments = [x for x in segments if x['speaker'] != side] + [x for x in apple if x['speaker'] == side]
            kept.append(side)
    return segments, kept


def transcribe(ws, rel, root, name):
    """Whisper's transcript of each side in place of Apple's, merged in time order with the
    echo left out; the audio is deleted whatever happens. When Whisper cannot run, Apple's
    transcript stays and the summary goes on. Returns whether Whisper's transcript was kept."""
    from . import whisper_model
    folder = _audio_root(ws) / name
    try:
        _, _, front, body = _note(root, rel)
        language = WHISPER_LANGUAGES.get(note_language(front, body), 'en')
        lock = _whisper_lock(ws)
        try:
            _record(ws, rel, status='transcribing', pid=os.getpid(), started_at=time.time(), audio=name)
            model = whisper_model.load(language)
            segments = []
            for speaker in SPEAKERS:
                wav = folder / f'{speaker}.wav'
                if not wav.is_file():
                    continue
                samples = whisper_model.read_wav(wav)
                segments += [dict(start=t0, speaker=speaker, text=text)
                             for t0, _, text in whisper_model.transcribe(model, samples, busy=lambda: busy(ws))]
                del samples  # a two-hour side is about 460 MB
            del model
        finally:
            filelock.unlock(lock)
            lock.close()
        segments, kept = _keep_apple_sides(front, body, segments)
        lines = clean_lines(segments)
        if not lines:
            return False
        write_transcript(root, rel, lines, 'whisper+apple' if kept else 'whisper')
        return True
    except Exception as exc:  # Whisper never costs a meeting: Apple's transcript is already saved
        print(f"{time.strftime('%Y-%m-%d %H:%M')} whisper {rel}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return False
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        import gc
        gc.collect()


# -- background run and the Meetings page ---------------------------------------------

REINDEX_WAIT = 600  # seconds


def reindex(ws, wait=REINDEX_WAIT):
    """The note and the digest changed: index them. A build already running when they were
    written refuses to publish (index_failed, measured on the first retry), so wait for it to
    end and start one after."""
    from . import maintenance
    end = time.time() + wait
    while not maintenance.start(ws).get('started') and time.time() < end:
        time.sleep(5)

def _state(ws):
    try:
        return json.loads((ws.state_dir / STATE_NAME).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _record(ws, rel, **entry):
    # Two meetings in a row run two jobs: each read-modify-write holds the file's lock.
    with open(Path(ws.state_dir) / (STATE_NAME + '.lock'), 'a+') as handle:
        while not filelock.try_lock(handle):
            time.sleep(0.05)
        try:
            state = _state(ws)
            state[rel] = dict(entry, updated=time.time())
            atomic_text(ws.state_dir / STATE_NAME, json.dumps(state, indent=1, ensure_ascii=False) + '\n')
        finally:
            filelock.unlock(handle)


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


TASKPOLICY = '/usr/sbin/taskpolicy'


def spawn(ws, rel, root, python=None, audio=None):
    """Start Whisper (when the app left `audio`) and the summary in the background, at low
    priority, and return at once. Started from the app, the process has launchd's short PATH:
    give it the folders Claude Code and Codex live in. Variables Claude Code sets in its own
    sessions are left out, so an app started from one still runs the assistant."""
    args = [python or sys.executable, '-I', '-m', 'carry.cli', '--workspace', str(ws.state_dir), 'meet', 'summarize',
            '--path', rel, '--vault', str(root)] + (['--audio', audio] if audio else [])
    if sys.platform == 'darwin' and os.path.exists(TASKPOLICY):
        args = [TASKPOLICY, '-c', 'utility'] + args  # measured: no stutter in a video call while it runs
    env = {k: v for k, v in os.environ.items() if k != 'CLAUDECODE' and not k.startswith('CLAUDE_CODE_')}
    env['PATH'] = ':'.join(dict.fromkeys(harvest.schedule_path_env().split(':') + os.environ.get('PATH', '').split(':')))
    with open(ws.state_dir / LOG_NAME, 'a') as log:
        proc = detached(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
    _record(ws, rel, status='running', pid=proc.pid, started_at=time.time(), audio=audio)
    return proc


WHISPER_JOB = 'whisper.json'


def _whisper_note(ws, **fields):
    atomic_text(Path(ws.state_dir) / WHISPER_JOB, json.dumps(dict(fields, at=time.time())) + '\n')


def download_whisper(ws):
    """The Whisper model, downloaded once, with its progress for the Meetings page."""
    from . import whisper_model
    total = whisper_model.DOWNLOAD_BYTES
    _whisper_note(ws, state='running', pid=os.getpid(), completed=0, total=total)
    try:
        whisper_model.download(progress=lambda done, size: _whisper_note(ws, state='running', pid=os.getpid(), completed=done, total=size))
    except CarryError as exc:
        _whisper_note(ws, state='failed', error=str(exc).split(':')[0])
        raise
    _whisper_note(ws, state='done', completed=total, total=total)
    return dict(installed=True, bytes=total)


def whisper_download_job(ws):
    try:
        job = json.loads((Path(ws.state_dir) / WHISPER_JOB).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(state='idle')
    if job.get('state') == 'running' and not _alive(job.get('pid')):
        job['state'] = 'interrupted'
    return job


def start_whisper_download(ws):
    """Start the download in the background (its own job: the index never waits for it)."""
    from . import whisper_model
    if not whisper_model.runtime_available():
        raise CarryError('whisper_runtime_missing')
    if whisper_model.installed():
        return dict(started=False, installed=True)
    job = whisper_download_job(ws)
    if job.get('state') == 'running':
        return dict(job, started=False)
    args = [sys.executable, '-I', '-m', 'carry.cli', '--workspace', str(ws.state_dir), 'meet', 'download-whisper']
    with open(Path(ws.state_dir) / LOG_NAME, 'a') as log:
        proc = detached(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    _whisper_note(ws, state='running', pid=proc.pid, completed=0, total=whisper_model.DOWNLOAD_BYTES)
    return dict(started=True, pid=proc.pid)


def leftover_audio(ws, rel):
    """The audio of a meeting whose job ended before Whisper did, for a retry."""
    name = _state(ws).get(rel, {}).get('audio')
    return name if name and AUDIO_NAME.match(name) and (_audio_root(ws) / name).is_dir() else None


def listing(ws, root=None, limit=100):
    """Meetings, newest first, with their state: pending, transcribing (Whisper), running (the
    summary), raw (no assistant connected), done or failed."""
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
        if status != 'done' and job.get('status') in ('running', 'transcribing'):  # a retry runs over a failed one
            status = job['status'] if _alive(job.get('pid')) else 'failed'
        title = path.stem.split(' — ', 1)[-1]
        out.append(dict(path=rel, title=title, started=str(front.get('started') or ''), minutes=front.get('duration_minutes'),
                        status=status, digest=front.get('meet_digest') or job.get('digest'),
                        error=front.get('meet_error') or (job.get('error') if status == 'failed' else None)))
    return dict(meetings=out, root=str(root), source_id=source.source_id)
