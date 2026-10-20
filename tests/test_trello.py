"""Trello: the owner's open cards for the home page, read-only, cached in the state."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import _support  # noqa: F401
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
        with patch.object(trello, '_get', side_effect=CarryError('trello_unauthorized')):
            result = trello.refresh(self.state)
        self.assertEqual(result['error'], 'trello_unauthorized')
        self.assertEqual(len(result['cards']), 4)

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


if __name__ == '__main__':
    unittest.main()


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
                trello._keychain_write('token', 'secret-token')
        self.assertEqual(str(caught.exception), 'trello_keychain_failed')
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn('secret-token', repr(caught.exception))
