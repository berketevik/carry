"""The owner's open Trello cards for the app's home page: read-only, cached in the state.

Carry asks Trello for a read-only token (`scope=read`) under an API key the owner already
has; both are read from TRELLO_API_KEY and TRELLO_TOKEN or the macOS Keychain (service
carry-trello), never from workspace files. Only GET requests are made. The cards go to the
state's trello.json and are fetched again in the background once they are five minutes old,
so the page never waits for Trello; nothing is written into the notes.
"""
import datetime
import json
import os
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
MAX_AGE = 300
TIMEOUT = 15
CARD_FIELDS = 'name,idBoard,due,dueComplete,shortUrl,dateLastActivity'
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
        boards = {b['id']: b.get('name', '') for b in _get('/members/me/boards?filter=open&fields=name', *creds)}
        cards = [c for c in _get(f'/members/me/cards?filter=open&fields={CARD_FIELDS}', *creds) if not c.get('dueComplete')]
    except CarryError as exc:
        result = dict(cached, connected=True, error=str(exc), checked_at=time.time())
        atomic_text(state_dir / CACHE, json.dumps(result, ensure_ascii=False))
        return result
    # Cards with a due date first, the nearest at the top; the rest by the latest activity.
    cards.sort(key=lambda c: c.get('dateLastActivity') or '', reverse=True)
    cards.sort(key=lambda c: (not c.get('due'), c.get('due') or ''))
    result = dict(connected=True, username=member.get('username', ''), fetched_at=time.time(), error=None,
                  cards=[dict(name=c.get('name', ''), board=boards.get(c.get('idBoard'), ''), due=c.get('due'),
                              url=c.get('shortUrl', ''), touched=c.get('dateLastActivity')) for c in cards])
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


def view(state_dir, start=None):
    """What the home page shows, at once: the cached cards, fetched again in the background when stale."""
    if not credentials():
        return dict(connected=False)
    cached = _read_cache(state_dir)
    last = max(cached.get('fetched_at') or 0, cached.get('checked_at') or 0)
    if time.time() - last > MAX_AGE:
        (start or (lambda: _start_refresh(state_dir)))()
    return dict(cached, connected=True, cards=cached.get('cards', []))
