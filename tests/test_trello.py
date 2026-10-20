"""Trello: the owner's open cards for the home page, read-only, cached in the state."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import _support
from carry import trello
from carry.errors import CarryError

# Open boards come back with their open lists only.
BOARDS = [dict(id='b1', name='Ekip', lists=[dict(id='l1', name='IN PROGRESS'), dict(id='l3', name='DONE')]),
          dict(id='b2', name='Kişisel', lists=[dict(id='l2', name='Yapılacaklar'), dict(id='l4', name='Tamamlandı')])]
CARDS = [
    dict(id='c1', name='Son tarihsiz eski', idBoard='b1', idList='l1', due=None, dueComplete=False,
         shortUrl='https://trello.com/c/1', dateLastActivity='2026-10-01T10:00:00.000Z'),
    dict(id='c2', name='Yarın teslim', idShort=213, idBoard='b2', idList='l2', due='2026-10-20T09:00:00.000Z', dueComplete=False,
         shortUrl='https://trello.com/c/2', dateLastActivity='2026-10-18T10:00:00.000Z'),
    dict(id='c3', name='Son tarihsiz yeni', idBoard='b1', idList='l1', due=None, dueComplete=False,
         shortUrl='https://trello.com/c/3', dateLastActivity='2026-10-19T10:00:00.000Z'),
    dict(id='c4', name='Bugün teslim', idBoard='b1', idList='l1', due='2026-10-19T17:00:00.000Z', dueComplete=False,
         shortUrl='https://trello.com/c/4', dateLastActivity='2026-10-17T10:00:00.000Z'),
]
# Finished by moving, on a closed board, and long past: never on the home page, or hidden by the day.
DONE = dict(id='c5', name='Bitti', idBoard='b1', idList='l3', due=None, dueComplete=False,
            shortUrl='https://trello.com/c/5', dateLastActivity='2026-10-19T10:00:00.000Z')
DONE_TR = dict(DONE, id='c6', idBoard='b2', idList='l4')
CLOSED = dict(DONE, id='c7', idBoard='b9', idList='l9')
OLD = dict(id='c8', name='Eski teslim', idBoard='b2', idList='l2', due='2023-08-18T09:00:00.000Z', dueComplete=False,
           shortUrl='https://trello.com/c/8', dateLastActivity='2026-02-01T10:00:00.000Z')


def api(member=None, cards=CARDS, boards=BOARDS):
    """A fake Trello API: one reply per path."""
    def get(path, key, token):
        if path.startswith('/members/me/cards'):
            return cards
        if path.startswith('/members/me/boards'):
            return boards
        if path.startswith('/members/me'):
            return member or dict(username='ismail', fullName='İsmail')
        raise AssertionError(path)
    return get


class TrelloTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        env = patch.dict(os.environ, {'TRELLO_API_KEY': 'k' * 32, 'TRELLO_TOKEN': 't' * 64}); env.start(); self.addCleanup(env.stop)

    def test_authorize_url_asks_for_a_read_only_token_that_does_not_expire(self):
        url = trello.authorize_url('abc')
        for part in ('key=abc', 'scope=read', 'expiration=never', 'name=Carry', 'response_type=token'):
            self.assertIn(part, url)
        self.assertNotIn('return_url', url)

    def test_credentials_come_from_the_environment_first(self):
        self.assertEqual(trello.credentials(), ('k' * 32, 't' * 64))

    def test_not_connected_without_credentials(self):
        with patch.dict(os.environ, {'TRELLO_API_KEY': '', 'TRELLO_TOKEN': ''}), patch.object(trello, '_keychain_read', return_value=None):
            self.assertEqual(trello.view(self.state), dict(connected=False))

    def test_refresh_keeps_open_cards_with_board_names_due_first(self):
        with patch.object(trello, '_get', side_effect=api()):
            result = trello.refresh(self.state)
        self.assertEqual([c['name'] for c in result['cards']], ['Bugün teslim', 'Yarın teslim', 'Son tarihsiz yeni', 'Son tarihsiz eski'])
        self.assertEqual(result['cards'][0]['board'], 'Ekip')
        self.assertEqual(result['cards'][0]['list'], 'IN PROGRESS')
        self.assertEqual(result['cards'][0]['url'], 'https://trello.com/c/4')
        self.assertEqual(result['username'], 'ismail')
        self.assertTrue((self.state / trello.CACHE).is_file())

    def test_completed_cards_are_left_out(self):
        done = [dict(CARDS[1], dueComplete=True)]
        with patch.object(trello, '_get', side_effect=api(cards=done)):
            self.assertEqual(trello.refresh(self.state)['cards'], [])

    def test_cards_in_done_lists_or_on_closed_boards_are_left_out(self):
        with patch.object(trello, '_get', side_effect=api(cards=CARDS + [DONE, DONE_TR, CLOSED])):
            names = [c['name'] for c in trello.refresh(self.state)['cards']]
        self.assertEqual(len(names), 4)
        self.assertNotIn('Bitti', names)

    def test_the_chosen_boards_and_day_apply_when_read_without_asking_trello(self):
        with patch.object(trello, '_get', side_effect=api(cards=CARDS + [OLD])):
            trello.refresh(self.state)
        trello.save_settings(self.state, boards=['b2'], since='2026-01-01')
        with patch.object(trello, '_get', side_effect=AssertionError('no request for a settings change')):
            view = trello.view(self.state, start=lambda: None)
        self.assertEqual([c['name'] for c in view['cards']], ['Yarın teslim'])
        self.assertEqual(view['since'], '2026-01-01')
        # Every board stays listed for the settings, with its cards from the chosen day.
        self.assertEqual([(b['name'], b['cards'], b['shown']) for b in view['boards']], [('Ekip', 3, False), ('Kişisel', 1, True)])
        trello.save_settings(self.state, boards=None, since=None)
        self.assertEqual(len(trello.view(self.state, start=lambda: None)['cards']), 5)

    def test_settings_are_checked_before_they_are_kept(self):
        for boards, since in ((['b1'], '2026-13-01'), ('b1', None), ([1], None)):
            with self.assertRaises(CarryError):
                trello.save_settings(self.state, boards=boards, since=since)
        self.assertEqual(trello.settings(self.state), dict(boards=None, since=None))

    def test_view_serves_the_cache_and_refreshes_only_when_stale(self):
        with patch.object(trello, '_get', side_effect=api()):
            trello.refresh(self.state)
        with patch.object(trello, '_get', side_effect=AssertionError('no request while fresh')):
            fresh = trello.view(self.state, start=lambda: self.fail('no refresh while fresh'))
        self.assertTrue(fresh['connected'])
        self.assertEqual(len(fresh['cards']), 4)
        cache = json.loads((self.state / trello.CACHE).read_text(encoding='utf-8'))
        cache['fetched_at'] = time.time() - trello.MAX_AGE - 1
        (self.state / trello.CACHE).write_text(json.dumps(cache), encoding='utf-8')
        started = []
        stale = trello.view(self.state, start=lambda: started.append(True))
        self.assertEqual(started, [True])
        self.assertEqual(len(stale['cards']), 4)

    def test_a_failed_refresh_keeps_the_last_cards_and_says_why(self):
        with patch.object(trello, '_get', side_effect=api()):
            trello.refresh(self.state)
        with patch.object(trello, '_get', side_effect=CarryError('trello_unreachable: timed out')):
            result = trello.refresh(self.state)
        self.assertEqual(result['error'], 'trello_unreachable: timed out')
        self.assertEqual(len(result['cards']), 4)

    def test_a_revoked_permission_shows_as_not_connected_not_as_a_network_problem(self):
        with patch.object(trello, '_get', side_effect=api()):
            trello.refresh(self.state)
        with patch.object(trello, '_get', side_effect=CarryError('trello_unauthorized')):
            result = trello.refresh(self.state)
        self.assertEqual(result, dict(connected=False, error='trello_unauthorized', checked_at=result['checked_at']))
        self.assertTrue(trello.revoked(self.state))
        seen = trello.view(self.state, start=lambda: None)
        self.assertEqual((seen['connected'], seen['error'], seen.get('cards')), (False, 'trello_unauthorized', None))
        # Connecting again fetches the cards and clears it.
        with patch.object(trello, '_get', side_effect=api()):
            trello.refresh(self.state)
        self.assertFalse(trello.revoked(self.state))

    def test_connect_checks_the_token_before_storing_it(self):
        stored = []
        with patch.object(trello, '_get', side_effect=CarryError('trello_unauthorized')), \
             patch.object(trello, '_keychain_write', side_effect=lambda *a: stored.append(a)):
            with self.assertRaises(CarryError):
                trello.connect(self.state, 'abc', 'bad')
        self.assertEqual(stored, [])
        with patch.object(trello, '_get', side_effect=api()), \
             patch.object(trello, '_keychain_write', side_effect=lambda *a: stored.append(a)):
            member = trello.connect(self.state, 'abc', 'good')
        self.assertEqual(member['username'], 'ismail')
        self.assertEqual(stored, [('key', 'abc'), ('token', 'good')])

    def test_disconnect_forgets_the_token_and_the_cards(self):
        with patch.object(trello, '_get', side_effect=api()):
            trello.refresh(self.state)
        removed = []
        with patch.object(trello, '_keychain_delete', side_effect=removed.append):
            trello.disconnect(self.state)
        self.assertEqual(removed, ['key', 'token'])
        self.assertFalse((self.state / trello.CACHE).exists())


class LoginTest(unittest.TestCase):
    """The one-click connection, on a free loopback port: Trello itself is never asked."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        connect = patch.object(trello, 'connect', return_value=dict(username='ismail', name='İsmail'))
        self.connect = connect.start(); self.addCleanup(connect.stop)
        self.login = self.start()

    def start(self, port=0):
        login = trello.Login(self.state, 'k' * 32, 'tr', port=port)
        self.addCleanup(login.stop)
        return login

    def call(self, method, path, body=None, origin=True, host=None, address='127.0.0.1', login=None):
        login = login or self.login
        headers = {'Host': host or login.host}
        if origin:
            headers['Origin'] = login.origin if origin is True else origin
        data = None if body is None else (body if isinstance(body, str) else json.dumps(body))
        if data is not None:
            headers['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection(address, int(login.host.split(':')[1]), timeout=5)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            return response.status, response.read().decode('utf-8'), response
        finally:
            connection.close()

    def closed(self, login):
        """True once nothing answers on the login's port any more."""
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                socket = http.client.HTTPConnection('127.0.0.1', int(login.host.split(':')[1]), timeout=1)
                socket.connect(); socket.close()
            except OSError:
                return True
            time.sleep(0.05)
        return False

    def test_trellos_page_comes_back_to_a_random_path_on_localhost(self):
        result = self.login.result
        self.assertEqual(result['state'], 'waiting')
        query = trello.urllib.parse.parse_qs(trello.urllib.parse.urlsplit(result['authorize_url']).query)
        self.assertEqual(query['return_url'], [self.login.origin + self.login.path])
        self.assertEqual(query['callback_method'], ['fragment'])
        self.assertEqual(query['scope'], ['read'])
        self.assertTrue(self.login.origin.startswith('http://localhost:'))
        self.assertGreater(len(self.login.path), len('/trello/') + 16)
        self.assertNotEqual(self.start().path, self.login.path)
        status, page, response = self.call('GET', self.login.path)
        self.assertEqual(status, 200)
        self.assertIn('location.hash', page)
        self.assertIn('history.replaceState', page)
        self.assertIn("connect-src 'self'", response.getheader('Content-Security-Policy'))
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        self.assertEqual(self.call('GET', '/trello/another')[0], 404)
        if len(self.login.servers) > 1:  # browsers may reach localhost on either loopback
            self.assertEqual(self.call('GET', self.login.path, address='::1')[0], 200)

    def test_the_token_is_checked_kept_and_the_listener_closes(self):
        token = 'A1b2' * 16
        status, body, _ = self.call('POST', self.login.path + '/token', dict(token=token))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['message'], trello.LOGIN_TEXT['tr']['connected'])
        self.connect.assert_called_once_with(self.state, 'k' * 32, token)
        self.assertEqual(self.login.result, dict(state='connected', username='ismail', name='İsmail'))
        self.assertNotIn(token, json.dumps(self.login.result))
        self.assertTrue(self.closed(self.login))
        self.assertEqual(self.login.receive(dict(token=token))[0], 410)
        self.assertEqual(self.connect.call_count, 1)

    def test_another_page_path_or_host_is_turned_away(self):
        token = dict(token='a' * 64)
        self.assertEqual(self.call('POST', self.login.path + '/token', token, origin='https://ornek.com.tr')[0], 403)
        self.assertEqual(self.call('POST', self.login.path + '/token', token, origin=False)[0], 403)
        self.assertEqual(self.call('POST', '/trello/guessed/token', token)[0], 404)
        self.assertEqual(self.call('POST', self.login.path + '/token', token, host='ornek.com.tr')[0], 404)
        self.assertEqual(self.call('GET', self.login.path, host='ornek.com.tr')[0], 404)
        self.assertEqual(self.call('POST', self.login.path + '/token', 'not json')[0], 400)
        self.connect.assert_not_called()
        self.assertEqual(self.login.result['state'], 'waiting')

    def test_a_denied_or_odd_answer_fails_without_asking_trello(self):
        status, body, _ = self.call('POST', self.login.path + '/token', dict(error='denied'))
        self.assertEqual((status, json.loads(body)['message']), (200, trello.LOGIN_TEXT['tr']['denied']))
        self.assertEqual(self.login.result, dict(state='failed', error='trello_denied'))
        odd = self.start()
        self.assertEqual(self.call('POST', odd.path + '/token', dict(token='short'), login=odd)[0], 400)
        self.assertEqual(odd.result, dict(state='failed', error='trello_token_invalid'))
        self.connect.assert_not_called()

    def test_a_token_trello_refuses_says_why(self):
        self.connect.side_effect = CarryError('trello_unauthorized')
        status, body, _ = self.call('POST', self.login.path + '/token', dict(token='a' * 64))
        self.assertEqual((status, json.loads(body)['message']), (502, trello.LOGIN_TEXT['tr']['failed']))
        self.assertEqual(self.login.result, dict(state='failed', error='trello_unauthorized'))

    def test_an_unexpected_failure_while_checking_still_ends_the_sign_in(self):
        # Anything else (a payload Trello never sent before, a full disk) must not leave it at checking.
        self.connect.side_effect = TypeError('unexpected payload')
        status, body, _ = self.call('POST', self.login.path + '/token', dict(token='a' * 64))
        self.assertEqual((status, json.loads(body)['message']), (502, trello.LOGIN_TEXT['tr']['failed']))
        self.assertEqual(self.login.result, dict(state='failed', error='trello_connect_failed'))
        self.assertTrue(self.closed(self.login))

    def test_cancel_and_the_time_limit_close_the_listener(self):
        self.login.cancel()
        self.assertEqual(self.login.result, dict(state='cancelled'))
        self.assertTrue(self.closed(self.login))
        with patch.object(trello, 'LOGIN_TIMEOUT', 0.05):
            late = self.start()
        self.assertTrue(self.closed(late))
        self.assertEqual(late.result, dict(state='failed', error='trello_timeout'))

    def test_the_same_port_opens_again_after_an_answer_or_cancel(self):
        # The app always uses one port, so Try again must be able to take it back.
        port = int(self.login.host.split(':')[1])
        self.call('POST', self.login.path + '/token', dict(error='denied'))
        self.assertTrue(self.closed(self.login))
        again = self.start(port)
        self.assertEqual(self.call('GET', again.path, login=again)[0], 200)
        again.cancel()
        third = self.start(port)
        self.assertEqual(third.result['state'], 'waiting')

    def test_a_busy_port_is_named(self):
        port = int(self.login.host.split(':')[1])
        with self.assertRaises(CarryError) as caught:
            trello.Login(self.state, 'k' * 32, port=port)
        self.assertEqual(str(caught.exception), 'trello_port_busy')


class DoneListTest(unittest.TestCase):
    def test_only_lists_of_finished_work_count_as_done(self):
        for name in ('Done', 'DONE ✅', 'Completed', 'Tamamlandı', 'TAMAMLANDI', 'Tamamlananlar', 'Bitti', 'Bitenler', 'Ekim: done'):
            self.assertTrue(trello.is_done_list(name), name)
        # Work still to finish, in either language, keeps its cards on Home.
        for name in ('Tamamlanacaklar', 'Tamamlanmayanlar', 'Not done', 'To be completed', 'Yapılacaklar', 'Bitmeyenler', 'Doing'):
            self.assertFalse(trello.is_done_list(name), name)


class CardNumberTest(unittest.TestCase):
    def test_a_card_keeps_its_number_so_notes_can_name_it(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(trello, '_get', side_effect=api()):
            result = trello.refresh(Path(tmp), ('k', 't'))
        self.assertEqual({c['name']: c['number'] for c in result['cards']}['Yarın teslim'], 213)


class KeychainTest(unittest.TestCase):
    def test_a_failed_keychain_write_never_shows_the_token(self):
        import subprocess
        failed = subprocess.CalledProcessError(1, ['/usr/bin/security', '-w', 'secret-token'])
        with patch.object(trello.sys, 'platform', 'darwin'), patch.object(trello.subprocess, 'run', side_effect=failed):
            with self.assertRaises(CarryError) as caught:
                _support.KEYCHAIN['write']('token', 'secret-token')
        self.assertEqual(str(caught.exception), 'trello_keychain_failed')
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn('secret-token', repr(caught.exception))


if __name__ == '__main__':
    unittest.main()
