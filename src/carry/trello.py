"""The owner's open Trello cards for the app's home page: read-only, cached in the state.

Carry asks Trello for a read-only token (`scope=read`) under an API key: the team's, from the
team notes, or one the owner gives the CLI. Once connected, both are read from TRELLO_API_KEY
and TRELLO_TOKEN or the macOS Keychain (service carry-trello), never from workspace files. Only
GET requests are made. The cards go to the state's trello.json and are fetched again in the
background once they are five minutes old, so the page never waits for Trello; nothing is
written into the notes. Which boards the page shows and from which day is the owner's choice in
trello-settings.json, applied when the cards are read, so changing it never waits for Trello
either.

In the app a person connects with one click (Login): the team's key comes from the team notes,
Trello's permission page sends the browser back to this Mac with the token in the address's
fragment, and a page on localhost hands it to Carry. That listener answers on loopback only, at
a random path, and stops after the token or ten minutes. The CLI keeps the pasted token.
"""
import datetime
import json
import os
import re
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

from .errors import CarryError
from .persistence import atomic_text

API = 'https://api.trello.com/1'
KEYCHAIN = 'carry-trello'
CACHE = 'trello.json'
SETTINGS = 'trello-settings.json'
MAX_AGE = 300
TIMEOUT = 15
CARD_FIELDS = 'name,idShort,idBoard,idList,due,dueComplete,shortUrl,dateLastActivity'
# Trello marks a card complete only when it has a due date; teams close the rest by moving it to such a list.
DONE_LISTS = re.compile(r'\b(done|completed|tamamlan|bitti|bitenler)', re.I)
# The team's key allows this origin in Trello, so the port is fixed.
PORT = 47821
LOGIN_TIMEOUT = 600
TOKEN_SHAPE = re.compile(r'[A-Za-z0-9]{32,128}')
_refreshing = threading.Lock()


def authorize_url(key, return_url=None):
    """Trello's page that hands the owner a read-only token for this key, never expiring: shown
    to copy, or sent back to return_url in the address's fragment."""
    fields = dict(expiration='never', scope='read', response_type='token', name='Carry', key=key)
    if return_url:
        fields.update(callback_method='fragment', return_url=return_url)
    return f'https://trello.com/1/authorize?{urllib.parse.urlencode(fields)}'


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
    try:
        subprocess.run(['/usr/bin/security', 'add-generic-password', '-U', '-s', KEYCHAIN, '-a', account, '-w', value],
                       capture_output=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        # The error's text holds the command line, the token with it: it must not reach a screen or a log.
        raise CarryError('trello_keychain_failed') from None


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


LOGIN_TEXT = {
    'en': dict(waiting='Connecting Carry to Trello…', connected='Trello is connected. You can close this tab and go back to Carry.',
               failed='Carry could not connect to Trello. Try again in Carry.', denied='Trello was not allowed. You can try again in Carry.',
               used='This link was already used. Go back to Carry.'),
    'tr': dict(waiting='Carry Trello\'ya bağlanıyor…', connected='Trello bağlandı. Bu sekmeyi kapatıp Carry\'ye dönebilirsiniz.',
               failed='Carry Trello\'ya bağlanamadı. Carry\'de yeniden deneyin.', denied='Trello\'ya izin verilmedi. Carry\'de yeniden deneyebilirsiniz.',
               used='Bu bağlantı zaten kullanıldı. Carry\'ye dönün.'),
}
# The fragment never reaches a server: the page reads it, wipes it from the address bar and posts it here.
LOGIN_PAGE = '''<!doctype html><meta charset="utf-8"><title>Carry</title>
<style>body{font:15px -apple-system,system-ui,sans-serif;max-width:30em;margin:18vh auto;padding:0 16px;color:#222;background:#fff}
@media (prefers-color-scheme:dark){body{color:#eee;background:#1e1e1e}}</style>
<p id="m">%(waiting)s</p>
<script>
const h = new URLSearchParams(location.hash.slice(1)), m = document.getElementById('m');
history.replaceState(null, '', location.pathname);
fetch(location.pathname + '/token', {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(h.get('token') ? {token: h.get('token')} : {error: h.get('error') || 'denied'})})
  .then(r => r.json()).then(j => { m.textContent = j.message; }).catch(() => { m.textContent = %(failed)s; });
</script>
'''


class _Handler(BaseHTTPRequestHandler):
    server_version = 'Carry'
    sys_version = ''

    def log_message(self, *args):
        pass  # nothing about a sign-in reaches a log

    def _send(self, code, body, kind='application/json'):
        data = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        if kind == 'text/html':
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(data)

    def _reply(self, code, message):
        self._send(code, json.dumps(dict(message=message), ensure_ascii=False))

    def do_GET(self):
        login = self.server.login
        if self.headers.get('Host') != login.host or self.path.split('?')[0] != login.path:
            return self._send(404, '{}')
        self._send(200, LOGIN_PAGE % dict(login.text, failed=json.dumps(login.text['failed'], ensure_ascii=False)), 'text/html')

    def do_POST(self):
        login = self.server.login
        if self.headers.get('Host') != login.host or self.path != login.path + '/token':
            return self._send(404, '{}')
        if self.headers.get('Origin') != login.origin:
            return self._send(403, '{}')
        try:
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(length)) if 0 < length <= 4096 else None
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return self._send(400, '{}')
        try:
            self._reply(*login.receive(body))
        finally:
            login.stop()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Server6(_Server):
    address_family = socket.AF_INET6


class Login:
    """One click to connect: the person allows Carry on Trello's page, the browser comes back to
    localhost with the token, Carry checks and keeps it. Neither the key nor the token is typed.
    `result` is what the app polls: waiting (with the page to open), checking, connected, failed
    (with the reason) or cancelled; it never holds the token."""
    def __init__(self, state_dir, key, language='en', port=None):
        self.state_dir, self.key = state_dir, key
        self.text = LOGIN_TEXT.get(language, LOGIN_TEXT['en'])
        self.path = '/trello/' + secrets.token_urlsafe(16)
        self._lock = threading.Lock()
        self._stopped = False
        try:
            self.servers = [_Server(('127.0.0.1', PORT if port is None else port), _Handler)]
        except OSError:
            raise CarryError('trello_port_busy') from None
        port = self.servers[0].server_address[1]
        try:
            self.servers.append(_Server6(('::1', port), _Handler))
        except OSError:
            pass  # no IPv6 loopback: the browser reaches localhost on 127.0.0.1
        self.host = f'localhost:{port}'
        self.origin = 'http://' + self.host
        for server in self.servers:
            server.login = self
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.result = dict(state='waiting', authorize_url=authorize_url(key, self.origin + self.path))
        self.timer = threading.Timer(LOGIN_TIMEOUT, self._expire)
        self.timer.daemon = True
        self.timer.start()

    def receive(self, body):
        """The fragment the page posted: (HTTP status, what the page shows)."""
        with self._lock:
            if self.result['state'] != 'waiting':
                return 410, self.text['used']
            token = body.get('token')
            if not (isinstance(token, str) and TOKEN_SHAPE.fullmatch(token)):
                denied = 'error' in body
                self.result = dict(state='failed', error='trello_denied' if denied else 'trello_token_invalid')
                return (200, self.text['denied']) if denied else (400, self.text['failed'])
            self.result = dict(state='checking')
        try:
            member = connect(self.state_dir, self.key, token)
        except Exception as exc:  # anything else must not leave the app at checking for good
            self.result = dict(state='failed', error=str(exc) if isinstance(exc, CarryError) else 'trello_connect_failed')
            return 502, self.text['failed']
        self.result = dict(state='connected', username=member['username'], name=member['name'])
        return 200, self.text['connected']

    def _expire(self):
        with self._lock:
            if self.result['state'] in ('waiting', 'checking'):
                self.result = dict(state='failed', error='trello_timeout')
        self.stop()

    def cancel(self):
        with self._lock:
            if self.result['state'] == 'waiting':
                self.result = dict(state='cancelled')
        self.stop()

    def stop(self):
        """Close the listener; a second call does nothing."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        self.timer.cancel()
        for server in self.servers:
            server.shutdown()
            server.server_close()


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
                              touched=c.get('dateLastActivity'), number=c.get('idShort')) for c in cards])
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
