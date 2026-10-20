"""Who opens Carry on this Mac: the app's sign-in, by company mail and the team's name for them.

Signing in only tells Carry whose Trello cards and team notes the home page shows: there is no
password and no server. The profile is `profile.json` in the state folder; the mail stays there
and never goes into a note or becomes the owner id. The names offered are the `owner:` values of
the team sources' notes, so a member picks the spelling the team knowledge base already uses.
The team's `.carry/team.json` may limit the mail domains and carry the Trello API key; Carry never
writes it.
"""
import datetime
import json
from pathlib import Path
import re

from . import okf, team, trello
from .errors import CarryError
from .paths import walk_markdown
from .persistence import atomic_text

PROFILE = 'profile.json'
EMAIL_RE = re.compile(r'^[^@\s]+@([a-z0-9-]+(\.[a-z0-9-]+)+)$')
FOLD = str.maketrans('İIıŞşĞğÜüÖöÇç', 'iiissgguuoocc')
# Template placeholders and the team itself are no one's name.
NOT_PEOPLE = {'team', 'ekip', 'name', 'tbd', 'todo', 'owner', 'yok', '-'}


def fold(name):
    """A name without case or Turkish letters: İsmail, ismail and Ismail are one person."""
    return ' '.join((name or '').translate(FOLD).casefold().split())


def same_person(a, b):
    """The same full name, or one is the other's first name (Deniz = Deniz Kaya)."""
    a, b = fold(a), fold(b)
    if not a or not b:
        return False
    return a == b or a == b.split()[0] or b == a.split()[0]


def owners(value):
    """The people an `owner:` value names: comma-separated, or a list."""
    parts = value if isinstance(value, list) else str(value or '').split(',')
    return [p.strip() for p in parts if isinstance(p, str) and p.strip() and fold(p) not in NOT_PEOPLE]


def owned_by(value, name):
    return any(same_person(o, name) for o in owners(value))


def team_notes(workspace):
    """(source, relative path, path, frontmatter) of every note in the team sources."""
    from .vaultview import _meta
    for source in workspace.sources:
        if source.scope != 'team':
            continue
        root = Path(source.root).expanduser()
        if not root.is_dir():
            continue
        for relative, path in walk_markdown(root, extensions=tuple(source.extensions), max_bytes=float('inf')):
            yield source, relative.replace('\\', '/'), path, _meta(path)


def names(workspace):
    """The people the team notes name as owners, each in its most used spelling."""
    spellings = {}
    for _, _, _, front in team_notes(workspace):
        for name in owners(front.get('owner')):
            counts = spellings.setdefault(fold(name), {})
            counts[name] = counts.get(name, 0) + 1
    picked = [max(counts.items(), key=lambda kv: (kv[1], kv[0] != kv[0].lower()))[0] for counts in spellings.values()]
    # A first name alone is the same person as the one full name it starts (Deniz, Deniz Kaya).
    full = [n for n in picked if ' ' in fold(n)]
    picked = [n for n in picked if ' ' in fold(n) or [fold(f).split()[0] for f in full].count(fold(n)) != 1]
    return sorted(picked, key=fold)


def guess(email, candidates):
    """The name the mail's local part starts with: ismaily@ → İsmail, deniz.kaya@ → Deniz Kaya."""
    local = fold(re.sub(r'[._-]+', ' ', (email or '').split('@')[0]))
    best = ''
    for name in candidates:
        whole, first = fold(name), fold(name).split()[0]
        if local == whole or local.replace(' ', '') == whole.replace(' ', ''):
            return name
        if local.replace(' ', '').startswith(first) and len(name) > len(best):
            best = name
    return best


def load(state_dir):
    try:
        saved = json.loads((state_dir / PROFILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and saved.get('email') and saved.get('name') else None


def view(workspace, email=None):
    """What the sign-in screen and the profile menu show, with the name a mail being typed suggests.
    The Trello key itself stays in Python."""
    allowed = team.sign_in(workspace)
    candidates = names(workspace)
    key = allowed['trello_api_key']
    current = load(workspace.state_dir)
    email = email or (current or {}).get('email')
    return dict(profile=current, names=candidates, email_domains=allowed['email_domains'],
                guess=guess(email, candidates) if email else '',
                trello_connected=trello.credentials() is not None, trello_key_known=bool(key),
                trello_authorize_url=trello.authorize_url(key) if key else None)


def save(workspace, email, name):
    email, name = (email or '').strip().lower(), ' '.join((name or '').split())
    found = EMAIL_RE.match(email)
    if not found:
        raise CarryError('profile_invalid_email')
    if not name:
        raise CarryError('profile_name_required')
    domains = team.sign_in(workspace)['email_domains']
    if domains and found.group(1) not in domains:
        raise CarryError('profile_email_domain')
    created = (load(workspace.state_dir) or {}).get('created') or datetime.date.today().isoformat()
    atomic_text(workspace.state_dir / PROFILE, json.dumps(dict(email=email, name=name, created=created), ensure_ascii=False) + '\n')
    # The owner id goes into notes: a short form of the name, set once, never the mail.
    if okf.owner(workspace.state_dir) == okf.DEFAULT_OWNER:
        short = re.sub(r'[^a-z0-9._-]', '', fold(name).split()[0])
        if short:
            okf.set_owner(workspace.state_dir, short)
    return view(workspace)


def connect_trello(workspace, token, key=None):
    """Check the pasted read-only token with Trello and keep it; the key is the team's unless given."""
    key = (key or '').strip() or team.sign_in(workspace)['trello_api_key'] or ''
    trello.connect(workspace.state_dir, key, (token or '').strip())
    return view(workspace)


def disconnect_trello(workspace):
    trello.disconnect(workspace.state_dir)
    return view(workspace)


def logout(workspace):
    """Forget who signed in and their Trello connection. Notes, index and settings stay."""
    trello.disconnect(workspace.state_dir)
    try:
        (workspace.state_dir / PROFILE).unlink()
    except FileNotFoundError:
        pass
    return view(workspace)
