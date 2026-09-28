"""Share one approved chat item into a team repo's inbox, following that repo's own rules.

The target is the team knowledge base convention (conventions by İsmail Aykut): new material
goes to `90_Inbox/` only, named `YYYY-MM-DD — Topic.md`, with a `Kaynak:` line, committed as
`docs(inbox): <topic>`; the team moves it into its lasting files later. A source opts in with
`share` in its config. Only the approved statement leaves, never the raw chat; the chat quote
only on request. Secrets found by `mask` stop the share instead of being masked away.

This module renders and checks. It does not write to GitHub yet: `github.py` stays GET-only
and the live write waits for the owner's go.
"""
import datetime

from . import digest
from .errors import CarryError
from .markdown import parse_frontmatter
from .mask import mask

SHAREABLE = ('accepted', 'fixed')
# Task state lives in the team's tracker, not in its knowledge base.
NOT_SHARED_KINDS = ('open item', 'açık iş')
PRIVATE = ('private', 'secret')
DEFAULT_FOLDER = '90_Inbox'
MAX_TOPIC = 100


def team_source(ws, source_id):
    source = ws.source(source_id)
    if not source.github:
        raise CarryError('share_requires_github_source')
    return source


def topic_name(topic):
    topic = ' '.join((topic or '').split())
    if not topic or len(topic) > MAX_TOPIC or topic.startswith('.') or any(c in topic for c in '/\\\x00'):
        raise CarryError('invalid_share_topic')
    return topic


def _linked_sensitivity(ws, source_id, item):
    if not item.get('match'):
        return ''
    from .vaultview import _local
    path, _ = digest.note_side(ws, source_id, item)
    if not path:
        return ''
    _, root = _local(ws, source_id)
    try:
        front, _ = parse_frontmatter((root / path).read_text(encoding='utf-8', errors='replace'))
    except OSError:
        return ''
    return str(front.get('sensitivity') or '')


def render(item, topic, author, day, with_quote=False):
    """(file name, text) of the inbox file for one item."""
    said = item.get('said') or item.get('date') or day
    lines = [f'# {day} — {topic}', '',
             f'Kaynak: Carry, {author} sohbetinden onaylanan madde ({said}). Ham sohbet paylaşılmadı.', '',
             f"- **{item['tag']}:** {item['statement']}"]
    if with_quote and item.get('quote'):
        lines.append(f"  > {item['quote']}")
    return f'{day} — {topic}.md', '\n'.join(lines) + '\n'


def plan(ws, team_id, digest_source, digest_path, item_id, topic, author, with_quote=False, today=None):
    """What sharing this item would write, checked; nothing is sent."""
    team = team_source(ws, team_id)
    topic = topic_name(topic)
    author = ' '.join((author or '').split())
    if not author:
        raise CarryError('share_needs_author')
    _, _, body, _ = digest._open(ws, digest_source, digest_path)
    found = next((it for it in digest.parse(body)['items'] if it['id'] == item_id), None)
    if found is None:
        raise CarryError('item_not_found')
    if found['decision'] not in SHAREABLE:
        raise CarryError('share_needs_approved_item')
    if found['kind'].lower() in NOT_SHARED_KINDS:
        raise CarryError('share_not_for_open_items')
    if _linked_sensitivity(ws, digest_source, found) in PRIVATE:
        raise CarryError('share_linked_note_private')
    day = (today or datetime.date.today()).isoformat()
    name, text = render(found, topic, author, day, with_quote)
    _, secrets = mask(text)
    folder = team.share.get('folder', DEFAULT_FOLDER)
    return dict(repository=team.github.get('repository', ''), branch=team.github.get('branch', ''),
                path=f'{folder}/{name}', text=text, message=f'docs(inbox): {topic}',
                mode=team.share.get('mode', 'direct'), enabled=bool(team.share.get('enabled')),
                blocked=sorted(set(secrets)), sent=False)
