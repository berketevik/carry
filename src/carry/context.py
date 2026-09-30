"""`carry context`: the state pack a new thread starts from (Continuity 1c).

At most about 2k tokens: recent commits, open items and decisions from recent
harvest drafts (items shown as done are left out), what waits for review, and
index health. Claude Code runs it as a SessionStart hook in a Carry vault; Codex
is told to run it first. Read-only: it writes nothing.
"""
import datetime
import json
from pathlib import Path
import re
import subprocess

from .digest import decision_of

MAX_CHARS = 7000
LOCAL = '.carry/local.json'
DAYS = 14

HEAD = {
    'Turkish': dict(title='Carry durum paketi', commits='Son commit\'ler', open='Açık işler (son sohbetlerden)',
                    decisions='Son kararlar', review='İnceleme bekleyen', index='Index', none='yok',
                    drafts='taslak özet', proposals='öneri', hint='Derin ya da eski sorular için carry_recall kullan.'),
    'English': dict(title='Carry state pack', commits='Recent commits', open='Open items (recent chats)',
                    decisions='Recent decisions', review='Waiting for review', index='Index', none='none',
                    drafts='draft digests', proposals='proposals', hint='Use carry_recall for deep or older questions.'),
}
OPEN_TAGS = ('açık iş', 'open item')
DECISION_TAGS = ('karar', 'decision')
SKIP_SECTIONS = ('Yapılmış görünüyor', 'Apparently done', 'Vazgeçilmiş görünüyor', 'Apparently dropped',
                 'Zaten kayıtlı', 'Already recorded')


def workspace_for(root):
    try:
        return json.loads((Path(root) / LOCAL).read_text(encoding='utf-8')).get('workspace')
    except (OSError, ValueError):
        return None


def _digests(root, days=DAYS, today=None):
    today = today or datetime.date.today()
    inbox = Path(root) / '+'
    found = []
    for p in sorted(inbox.glob('* — harvest *.md'), reverse=True):
        try:
            day = datetime.date.fromisoformat(p.name[:10])
        except ValueError:
            continue
        if (today - day).days <= days:
            found.append((day, p))
    return found


def _still_draft(path):
    try:
        head = path.read_text(encoding='utf-8', errors='ignore').split('\n---', 1)[0]
    except OSError:
        return False
    return bool(re.search(r'(?m)^draft:\s*true\s*$', head))


def _items(path):
    """(kind, statement) per item line, skipping sections for done or already recorded items."""
    section, out = '', []
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        if line.startswith('## '):
            section = line[3:].strip()
            continue
        if section in SKIP_SECTIONS:
            continue
        decision, line = decision_of(line)
        if decision == 'skipped':
            continue
        m = re.match(r'- \*\*([^*:]+?)(?: · [^*]*)?:\*\* (.+)', line)
        if m:
            out.append((m.group(1).strip().lower(), m.group(2).strip(), '·' in line.split(':**')[0]))
    return out


def pack(root, workspace=None, language=None, max_chars=MAX_CHARS, today=None):
    root = Path(root).resolve()
    if language is None:
        try:
            language = json.loads((root / '.carry/vault.json').read_text(encoding='utf-8')).get('language', 'English')
        except (OSError, ValueError):
            language = 'English'
    H = HEAD.get(language, HEAD['English'])
    lines = [f'# {H["title"]} · {root.name}', '']
    try:
        log = subprocess.run(['git', '-C', str(root), 'log', '-10', '--format=%ad %s', '--date=short'],
                             capture_output=True, text=True, encoding='utf-8', timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        log = ''
    if log:
        lines += [f'## {H["commits"]}', ''] + [f'- {l}' for l in log.splitlines()] + ['']
    opens, decisions, seen = [], [], set()
    digests = _digests(root, today=today)
    for day, path in digests:
        for kind, statement, assistant in _items(path):
            key = statement.lower()
            if key in seen:
                continue
            seen.add(key)
            if kind in OPEN_TAGS:
                opens.append(f'- {day.isoformat()}: {statement}')
            elif kind in DECISION_TAGS and not assistant:
                decisions.append(f'- {day.isoformat()}: {statement}')
    lines += [f'## {H["open"]}', ''] + (opens[:12] or [f'- {H["none"]}']) + ['']
    if decisions:
        lines += [f'## {H["decisions"]}', ''] + decisions[:8] + ['']
    waiting = [p for _, p in digests if _still_draft(p)]  # a fully decided digest waits for nobody
    review = [f'{len(waiting)} {H["drafts"]} (+/)'] if waiting else []
    if workspace is not None:
        try:
            from .lifecycle import list_proposals
            pending = [p for p in list_proposals(workspace) if p.get('state') == 'draft']
            if pending:
                review.append(f'{len(pending)} {H["proposals"]} (carry proposal list)')
        except Exception:
            pass
        try:
            from .index import health
            h = health(workspace)
            lines += [f'## {H["index"]}', '', f'- {h.get("state")}', '']
        except Exception:
            pass
    if review:
        lines += [f'## {H["review"]}', ''] + [f'- {r}' for r in review] + ['']
    lines += [H['hint']]
    text = '\n'.join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit('\n', 1)[0] + '\n…'
    return text
