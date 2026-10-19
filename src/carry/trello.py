"""The owner's open Trello cards for the app's home page: read-only, cached in the state.

Carry asks Trello for a read-only token (`scope=read`) under an API key the owner already
has; both are read from TRELLO_API_KEY and TRELLO_TOKEN or the macOS Keychain (service
carry-trello), never from workspace files. Only GET requests are made. The cards go to the
state's trello.json and are fetched again in the background once they are five minutes old,
so the page never waits for Trello; nothing is written into the notes. Which boards the page
shows and from which day is the owner's choice in trello-settings.json, applied when the cards
are read, so changing it never waits for Trello either.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .errors import CarryError
from .persistence import atomic_text

API = 'https://api.trello.com/1'
KEYCHAIN = 'carry-trello'
CACHE = 'trello.json'
SETTINGS = 'trello-settings.json'
MAX_AGE = 300
TIMEOUT = 15
CARD_FIELDS = 'name,idBoard,idList,due,dueComplete,shortUrl,dateLastActivity'
# Trello marks a card complete only when it has a due date; teams close the rest by moving it to such a list.
DONE_LISTS = re.compile(r'\b(done|completed|tamamlan|bitti|bitenler)', re.I)
_refreshing = threading.Lock()


def authorize_url(key):
    """Trello's page that hands the owner a read-only token for this key, never expiring."""
    query = urllib.parse.urlencode(dict(expiration='never', scope='read', response_type='token', name='Carry', key=key))
    return f'https://trello.com/1/authorize?{query}'


def _keychain_read(account):
    if sys.platform != 'darwin':
        return None
    try:
        found = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', KEYCHAIN, '-a', account, '-w'],
                               capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if found.returncode != 0:
        return None
    return found.stdout.strip() or None


def _keychain_write(account, value):
    if sys.platform != 'darwin':
        raise CarryError('trello_needs_keychain')
    subprocess.run(['/usr/bin/security', 'add-generic-password', '-U', '-s', KEYCHAIN, '-a', account, '-w', value],
                   capture_output=True, check=True, timeout=10)


def _keychain_delete(account):
    if sys.platform == 'darwin':
        subprocess.run(['/usr/bin/security', 'delete-generic-password', '-s', KEYCHAIN, '-a', account],
                       capture_output=True, timeout=10)


def credentials():
    """(key, token), or None when Carry is not connected to Trello."""
    key = os.environ.get('TRELLO_API_KEY', '').strip() or _keychain_read('key')
    token = os.environ.get('TRELLO_TOKEN', '').strip() or _keychain_read('token')
    return (key, token) if key and token else None


def _get(path, key, token):
    request = urllib.request.Request(API + path, headers={
        'Accept': 'application/json',
        # The token goes in a header, not the URL, so it never shows in a log line.
        'Authorization': f'OAuth oauth_consumer_key="{key}", oauth_token="{token}"'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise CarryError('trello_unauthorized' if exc.code in (401, 403) else f'trello_http_{exc.code}')
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CarryError('trello_unreachable:' + type(exc).__name__)


def connect(state_dir, key, token):
    """Check the token with Trello, then keep the key and token in the Keychain."""
    key, token = (key or '').strip(), (token or '').strip()
    if not key or not token:
        raise CarryError('trello_key_and_token_required')
    member = _get('/members/me?fields=username,fullName', key, token)
    _keychain_write('key', key)
    _keychain_write('token', token)
    refresh(state_dir, (key, token))
    return dict(username=member.get('username', ''), name=member.get('fullName', ''))


def settings(state_dir):
    """The boards the home page shows (None: every board) and the oldest day it shows (None: any)."""
    try:
        saved = json.loads((state_dir / SETTINGS).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    boards = saved.get('boards')
    return dict(boards=boards if isinstance(boards, list) else None, since=saved.get('since') or None)


def save_settings(state_dir, boards=None, since=None):
    if boards is not None and not (isinstance(boards, list) and all(isinstance(b, str) for b in boards)):
        raise CarryError('invalid_trello_boards')
    if since:
        try:
            datetime.date.fromisoformat(since)
        except (TypeError, ValueError):
            raise CarryError('invalid_trello_since')
    atomic_text(state_dir / SETTINGS, json.dumps(dict(boards=boards, since=since or None)))


def disconnect(state_dir):
    for account in ('key', 'token'):
        _keychain_delete(account)
    try:
        (state_dir / CACHE).unlink()
    except FileNotFoundError:
        pass


def _read_cache(state_dir):
    try:
        return json.loads((state_dir / CACHE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def refresh(state_dir, creds=None):
    """Fetch the owner's open cards and keep them; a failure keeps the last cards with its reason."""
    creds = creds or credentials()
    if not creds:
        return dict(connected=False)
    cached = _read_cache(state_dir)
    try:
        member = _get('/members/me?fields=username,fullName', *creds)
        boards = _get('/members/me/boards?filter=open&fields=name&lists=open', *creds)
        cards = _get(f'/members/me/cards?filter=open&fields={CARD_FIELDS}', *creds)
    except CarryError as exc:
        result = dict(cached, connected=True, error=str(exc), checked_at=time.time())
        atomic_text(state_dir / CACHE, json.dumps(result, ensure_ascii=False))
        return result
    # Only the open lists of open boards come back, so a card on a closed board or list drops out here.
    lists = {l['id']: (b['id'], b.get('name', ''), l.get('name', '')) for b in boards for l in b.get('lists') or []}
    cards = [c for c in cards if not c.get('dueComplete') and c.get('idList') in lists and not DONE_LISTS.search(lists[c['idList']][2])]
    # Cards with a due date first, the nearest at the top; the rest by the latest activity.
    cards.sort(key=lambda c: c.get('dateLastActivity') or '', reverse=True)
    cards.sort(key=lambda c: (not c.get('due'), c.get('due') or ''))
    result = dict(connected=True, username=member.get('username', ''), fetched_at=time.time(), error=None,
                  boards=[dict(id=b['id'], name=b.get('name', '')) for b in boards],
                  cards=[dict(name=c.get('name', ''), board_id=lists[c['idList']][0], board=lists[c['idList']][1],
                              list=lists[c['idList']][2], due=c.get('due'), url=c.get('shortUrl', ''),
                              touched=c.get('dateLastActivity')) for c in cards])
    atomic_text(state_dir / CACHE, json.dumps(result, ensure_ascii=False))
    return result


def _start_refresh(state_dir):
    def run():
        try:
            refresh(state_dir)
        finally:
            _refreshing.release()
    if _refreshing.acquire(blocking=False):
        threading.Thread(target=run, daemon=True).start()


def shown(state_dir, cached):
    """The cards on the owner's chosen boards from the chosen day, with every board and its count for the settings."""
    chosen = settings(state_dir)
    since = chosen['since']
    # A card's day is its due date, or its latest activity when it has none.
    dated = [c for c in cached.get('cards', []) if not since or (c.get('due') or c.get('touched') or '')[:10] >= since]
    counts = {}
    for c in dated:
        counts[c.get('board_id')] = counts.get(c.get('board_id'), 0) + 1
    picked = chosen['boards']
    boards = [dict(id=b['id'], name=b['name'], cards=counts.get(b['id'], 0), shown=picked is None or b['id'] in picked)
              for b in cached.get('boards', [])]
    boards.sort(key=lambda b: (-b['cards'], b['name'].casefold()))
    return dict(cached, boards=boards, since=since, cards=[c for c in dated if picked is None or c.get('board_id') in picked])


def view(state_dir, start=None):
    """What the home page shows, at once: the cached cards, fetched again in the background when stale."""
    if not credentials():
        return dict(connected=False)
    cached = _read_cache(state_dir)
    last = max(cached.get('fetched_at') or 0, cached.get('checked_at') or 0)
    if time.time() - last > MAX_AGE:
        (start or (lambda: _start_refresh(state_dir)))()
    return dict(shown(state_dir, cached), connected=True)
