"""Share one approved chat item into a team repo's inbox, following that repo's own rules.

The target is the team knowledge base convention (conventions by İsmail Aykut): new material
goes to `90_Inbox/` only, named `YYYY-MM-DD — Topic.md`, with a `Kaynak:` line, committed as
`docs(inbox): <topic>`; the team moves it into its lasting files later. A source opts in with
`share` in its config. Only the approved statement leaves, never the raw chat; the chat quote
only on request. Secrets found by `mask` stop the share instead of being masked away.

`plan` renders and checks; `send` writes through the GitHub CLI, directly to the branch or as a
pull request. The writes live here so `github.py` stays GET-only. A share is logged in the
owner's own day log with the link it got.
"""
import base64
import datetime
import json
import subprocess
from urllib.parse import quote

from . import digest
from .errors import CarryError
from .markdown import parse_frontmatter
from .mask import mask

SHAREABLE = ('accepted', 'fixed')
# Task state lives in the team's tracker, not in its knowledge base.
NOT_SHARED_KINDS = ('open item', 'açık iş')
PRIVATE = ('private', 'secret')
INBOX = '90_Inbox'  # the only folder a share may write to, by the team repo's rule
MAX_TOPIC = 100
PR_BODY = 'Carry ile paylaşıldı: sohbetten onaylanan tek madde, ham sohbet yok.'


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
    parsed = digest.parse(body)
    found = next((it for it in parsed['items'] if it['id'] == item_id), None)
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
    return dict(team=team.source_id, repository=team.github.get('repository', ''),
                branch=team.github.get('branch', ''), path=f'{INBOX}/{name}', text=text,
                message=f'docs(inbox): {topic}', mode=team.share.get('mode', 'direct'),
                enabled=bool(team.share.get('enabled')), blocked=sorted(set(secrets)), sent=False,
                source=digest_source, item=found['id'], tag=found['tag'], statement=found['statement'],
                date=found['date'] or day, raw=parsed['raw'], language=parsed['language'])


def _call(method, path, body=None, missing_ok=False, timeout=45):
    """One GitHub API request through gh; None for a 404 when missing_ok. Never returns stderr."""
    from . import github
    command = [github.executable(), 'api', '--hostname', 'github.com', '--method', method, path]
    if body is not None:
        command += ['--input', '-']
    try:
        result = subprocess.run(command, input=json.dumps(body).encode() if body is not None else None,
                                env=github.environment(), capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise CarryError('github_unreachable')
    try:
        data = json.loads(result.stdout) if result.stdout.strip() else {}
    except (ValueError, UnicodeError):
        data = None
    if result.returncode:
        if missing_ok and isinstance(data, dict) and str(data.get('status')) == '404':
            return None
        raise CarryError('github_write_failed')
    if data is None:
        raise CarryError('github_invalid_response')
    return data


def _contents(repo, path):
    return f'/repos/{repo}/contents/{quote(path, safe="/")}'


def free_path(repo, branch, path, tries=9):
    """The path, or `Name (2).md` and so on when a file of that name is already there."""
    stem = path[:-len('.md')]
    for n in range(1, tries + 1):
        candidate = path if n == 1 else f'{stem} ({n}).md'
        if _call('GET', f'{_contents(repo, candidate)}?ref={quote(branch, safe="")}', missing_ok=True) is None:
            return candidate
    raise CarryError('share_name_taken')


def send(ws, planned, today=None):
    """Write a planned share; checks again instead of trusting the plan."""
    team = team_source(ws, planned['team'])
    if not team.share.get('enabled'):
        raise CarryError('share_not_enabled')
    _, secrets = mask(planned['text'])
    if secrets or planned.get('blocked'):
        raise CarryError('share_blocked_secrets')
    repo, branch = team.github['repository'], team.github['branch']
    mode = team.share.get('mode', 'direct')
    path = free_path(repo, branch, planned['path'])
    body = dict(message=planned['message'], content=base64.b64encode(planned['text'].encode()).decode())
    if mode == 'direct':
        url = _call('PUT', _contents(repo, path), dict(body, branch=branch))['content']['html_url']
    else:
        base = _call('GET', f'/repos/{repo}/git/ref/heads/{quote(branch, safe="")}')['object']['sha']
        head = f"carry/share-{(today or datetime.date.today()).isoformat()}-{planned['item']}"
        _call('POST', f'/repos/{repo}/git/refs', dict(ref=f'refs/heads/{head}', sha=base))
        try:
            _call('PUT', _contents(repo, path), dict(body, branch=head))
            url = _call('POST', f'/repos/{repo}/pulls',
                        dict(title=planned['message'], head=head, base=branch, body=PR_BODY))['html_url']
        except CarryError:
            # The branch stays on the team repo; deleting it is the owner's call, not Carry's.
            raise CarryError(f'share_pr_incomplete: branch {head} was created')
    _log(ws, planned, url)
    return dict(sent=True, mode=mode, repository=repo, path=path, url=url)


def _log(ws, planned, url):
    from .vaultview import _local
    _, root = _local(ws, planned['source'])
    entry = f"- **{planned['tag']}:** {planned['statement']} → ekibe paylaşıldı: {url}"
    digest._append_log(root, planned['date'], planned['language'], entry, planned['raw'])
