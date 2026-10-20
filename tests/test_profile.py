"""The app's sign-in: who opens Carry on this Mac, by company mail and the name the team knowledge base uses.

No password and no server: the profile only tells Carry whose cards and notes to show. The mail
stays in the state folder; it never becomes the owner id, which goes into notes."""
import json
import os
from unittest.mock import patch

from _support import HASHING, WorkspaceCase

from carry import okf, profile
from carry.config import RetrievalConfig, SourceConfig, Workspace
from carry.errors import CarryError


def okb_note(root, name, owner, status='active'):
    root.mkdir(parents=True, exist_ok=True)
    (root / f'{name}.md').write_text(f'---\ntype: note\nowner: {owner}\nstatus: {status}\n---\n# {name}\n', encoding='utf-8')


class ProfileTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.team = self.base / 'team'
        okb_note(self.team, 'Marka', 'İsmail')
        okb_note(self.team, 'Playbook', 'ismail')
        okb_note(self.team, 'Rapor', 'İsmail, Sude')
        okb_note(self.team, 'Plan', 'Deniz Kaya')
        okb_note(self.team, 'Ortak', 'team')
        okb_note(self.team, 'Şablon', 'NAME')
        okb_note(self.team, 'Not', 'Deniz')
        okb_note(self.corpus, 'Kişisel', 'Kimse')  # a personal source never names the team
        self.workspace = Workspace.create(
            self.base / 'state2', sources=[SourceConfig('corpus', self.corpus), SourceConfig('team', self.team, scope='team')],
            embedding=HASHING, retrieval=RetrievalConfig(top_k=6, auto_refresh=False))
        self.state = self.workspace.state_dir

    def team_json(self, payload):
        (self.team / '.carry').mkdir(exist_ok=True)
        (self.team / '.carry' / 'team.json').write_text(json.dumps(payload), encoding='utf-8')

    def test_names_come_from_the_team_notes_owners(self):
        self.assertEqual(profile.names(self.workspace), ['Deniz Kaya', 'İsmail', 'Sude'])

    def test_the_name_is_guessed_from_the_mail(self):
        names = profile.names(self.workspace)
        self.assertEqual(profile.guess('ismaily@ornek.com.tr', names), 'İsmail')
        self.assertEqual(profile.guess('deniz.kaya@ornek.dev', names), 'Deniz Kaya')
        self.assertEqual(profile.guess('ece@ornek.com.tr', names), '')

    def test_same_name_ignores_case_and_turkish_letters(self):
        self.assertTrue(profile.same_person('İsmail', 'ismail'))
        self.assertTrue(profile.same_person('Ismail', 'İSMAİL'))
        self.assertTrue(profile.same_person('Deniz', 'Deniz Kaya'))
        self.assertFalse(profile.same_person('Deniz', 'Denizhan'))
        self.assertTrue(profile.owned_by('İsmail, Sude', 'ismail'))
        self.assertFalse(profile.owned_by('team', 'İsmail'))

    def test_signing_in_keeps_the_mail_here_and_sets_a_short_owner_id(self):
        view = profile.save(self.workspace, ' Ismaily@Ornek.com.tr ', 'İsmail')
        self.assertEqual((view['profile']['email'], view['profile']['name']), ('ismaily@ornek.com.tr', 'İsmail'))
        saved = json.loads((self.state / profile.PROFILE).read_text(encoding='utf-8'))
        self.assertEqual(saved['email'], 'ismaily@ornek.com.tr')
        self.assertEqual(okf.owner(self.state), 'human:ismail')
        # An owner id already chosen is never replaced, and the mail never becomes one.
        okf.set_owner(self.state, 'isa')
        profile.save(self.workspace, 'deniz.kaya@ornek.com.tr', 'Deniz Kaya')
        self.assertEqual(okf.owner(self.state), 'human:isa')

    def test_the_mail_must_be_a_mail_and_on_a_team_domain(self):
        with self.assertRaises(CarryError) as caught:
            profile.save(self.workspace, 'ismail', 'İsmail')
        self.assertEqual(str(caught.exception), 'profile_invalid_email')
        with self.assertRaises(CarryError) as caught:
            profile.save(self.workspace, 'ismail@ornek.com.tr', '  ')
        self.assertEqual(str(caught.exception), 'profile_name_required')
        self.team_json({'email_domains': ['ornek.com.tr', 'ornek.dev']})
        with self.assertRaises(CarryError) as caught:
            profile.save(self.workspace, 'ismail@gmail.com', 'İsmail')
        self.assertEqual(str(caught.exception), 'profile_email_domain')
        profile.save(self.workspace, 'ismail@ornek.dev', 'İsmail')

    def test_view_offers_the_names_the_guess_and_how_to_connect_trello(self):
        self.team_json({'trello_api_key': '0123456789abcdef0123456789abcdef'})
        with patch.object(profile.trello, 'credentials', return_value=None):
            view = profile.view(self.workspace)
        self.assertIsNone(view['profile'])
        self.assertEqual(view['names'], ['Deniz Kaya', 'İsmail', 'Sude'])
        self.assertFalse(view['trello_connected'])
        self.assertTrue(view['trello_key_known'])
        self.assertIn('scope=read', view['trello_authorize_url'])
        self.assertNotIn('trello_api_key', view)
        self.assertEqual(profile.view(self.workspace, 'deniz.kaya@ornek.com.tr')['guess'], 'Deniz Kaya')

    def test_trello_connects_with_the_team_key_and_only_a_pasted_token(self):
        self.team_json({'trello_api_key': '0123456789abcdef0123456789abcdef'})
        with patch.object(profile.trello, 'connect', return_value=dict(username='ismail', name='İsmail')) as connect, \
                patch.object(profile.trello, 'credentials', return_value=None):
            profile.connect_trello(self.workspace, ' tok ')
        connect.assert_called_once_with(self.state, '0123456789abcdef0123456789abcdef', 'tok')
        (self.team / '.carry' / 'team.json').unlink()
        with patch.object(profile.trello, 'credentials', return_value=None), self.assertRaises(CarryError) as caught:
            profile.connect_trello(self.workspace, 'tok')
        self.assertEqual(str(caught.exception), 'trello_key_and_token_required')

    def test_signing_out_forgets_the_profile_and_trello_but_keeps_the_notes(self):
        profile.save(self.workspace, 'ismail@ornek.com.tr', 'İsmail')
        with patch.object(profile.trello, 'disconnect') as disconnect, patch.object(profile.trello, 'credentials', return_value=None):
            view = profile.logout(self.workspace)
        disconnect.assert_called_once_with(self.state)
        self.assertIsNone(view['profile'])
        self.assertFalse((self.state / profile.PROFILE).exists())
        self.assertTrue((self.team / 'Marka.md').exists())
        self.assertEqual(okf.owner(self.state), 'human:ismail')  # notes already carry it

    def test_the_app_signs_in_and_out_through_the_bridge(self):
        from carry.desktop import Bridge
        call = lambda action, **fields: Bridge().dispatch(dict(action=action, workspace=str(self.state), **fields))
        with patch.object(profile.trello, 'credentials', return_value=None), patch.object(profile.trello, 'disconnect'):
            self.assertIsNone(call('profile')['profile'])
            self.assertEqual(call('profile_save', email='ismail@ornek.com.tr', name='İsmail')['profile']['name'], 'İsmail')
            self.assertEqual(call('profile')['profile']['email'], 'ismail@ornek.com.tr')
            self.assertIsNone(call('profile_logout')['profile'])
