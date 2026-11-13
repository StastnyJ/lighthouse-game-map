from django.test import TestCase, Client, override_settings
from django.utils import timezone
from datetime import timedelta

from game.models import Game, Team, Zone
from game.models import PlayerLocation
from game.management.commands.process_game_state import award_zone_bonuses
from unittest.mock import patch
from game.config import load_game_settings


class GameSettingsTests(TestCase):
    def test_defaults_for_old_or_invalid_configuration(self):
        for config in ('{}', 'invalid json', '{"capture_duration_seconds": -5, "page_title": ""}'):
            with self.subTest(config=config), patch('pathlib.Path.read_text', return_value=config):
                self.assertEqual(load_game_settings(), {'capture_duration_seconds': 60, 'page_title': 'KSP bojovka'})

    @patch('pathlib.Path.read_text', return_value='{"capture_duration_seconds": 120, "page_title": "My <Game>"}')
    def test_configured_countdown_and_escaped_title(self, read_config):
        team = Team.objects.create(name='Red', color='#ff0000')
        Zone.objects.create(name='Zone', status='CAPTURING', capturing_team=team, capture_started_at=timezone.now() - timedelta(seconds=30))
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['capturing'][0]['remaining_seconds'], 90)
        page = self.client.get('/').content.decode()
        self.assertIn('<title>My &lt;Game&gt; - Výběr týmu</title>', page)
        self.assertIn('<h1>My &lt;Game&gt;</h1>', page)


class PlayerLocationTests(TestCase):
    def setUp(self):
        self.team = Team.objects.create(name='Red', color='#ff0000')

    def set_role(self, role):
        from django.core import signing
        self.client.defaults['HTTP_X_MAP_TOKEN'] = signing.dumps(role, salt='map-locations')

    def test_only_admin_can_read_locations(self):
        self.assertEqual(self.client.get('/api/admin/player-locations/').status_code, 403)
        self.set_role('Red')
        self.assertEqual(self.client.get('/api/admin/player-locations/').status_code, 403)
        self.assertEqual(self.client.post('/api/player-location/', {'latitude': 49, 'longitude': 16}, content_type='application/json').status_code, 200)
        self.set_role('admin2536')
        players = self.client.get('/api/admin/player-locations/').json()['players']
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0]['color'], '#ff0000')
        self.assertEqual(players[0]['team'], 'Red')

    def test_updates_same_player_and_hides_stale_locations(self):
        self.set_role('Red')
        for latitude in (49, 50):
            self.client.post('/api/player-location/', {'latitude': latitude, 'longitude': 16}, content_type='application/json')
        self.assertEqual(PlayerLocation.objects.count(), 1)
        self.assertEqual(PlayerLocation.objects.get().latitude, 50)
        PlayerLocation.objects.update(updated_at=timezone.now() - timedelta(seconds=61))
        self.set_role('admin2536')
        self.assertEqual(self.client.get('/api/admin/player-locations/').json()['players'], [])

    def test_rejects_invalid_reports(self):
        self.assertEqual(self.client.post('/api/player-location/', {}, content_type='application/json').status_code, 403)
        self.set_role('Red')
        for latitude in (91, 'NaN', None):
            response = self.client.post('/api/player-location/', {'latitude': latitude, 'longitude': 16}, content_type='application/json')
            self.assertEqual(response.status_code, 400)
        self.assertFalse(PlayerLocation.objects.exists())

    def test_roles_are_independent_between_map_tabs(self):
        from django.core import signing
        self.set_role('admin2536')
        team_token = signing.dumps('Red', salt='map-locations')
        response = self.client.post('/api/player-location/', {'latitude': 49, 'longitude': 16}, content_type='application/json', HTTP_X_MAP_TOKEN=team_token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/admin/player-locations/').status_code, 200)
        self.client.defaults['HTTP_X_MAP_TOKEN'] = team_token + 'tampered'
        self.assertEqual(self.client.get('/api/admin/player-locations/').status_code, 403)

    @override_settings(CSRF_TRUSTED_ORIGINS=['https://game.example'])
    def test_https_proxy_location_report_requires_trusted_origin_and_csrf_token(self):
        from django.core import signing
        client = Client(enforce_csrf_checks=True)
        client.get('/map/red/')
        headers = {
            'HTTP_ORIGIN': 'https://game.example',
            'HTTP_X_MAP_TOKEN': signing.dumps('Red', salt='map-locations'),
            'HTTP_X_CSRFTOKEN': client.cookies['csrftoken'].value,
        }
        data = {'latitude': 49, 'longitude': 16}
        self.assertEqual(client.post('/api/player-location/', data, content_type='application/json', **headers).status_code, 200)
        headers['HTTP_ORIGIN'] = 'https://untrusted.example'
        self.assertEqual(client.post('/api/player-location/', data, content_type='application/json', **headers).status_code, 403)
        headers['HTTP_ORIGIN'] = 'https://game.example'
        del headers['HTTP_X_CSRFTOKEN']
        self.assertEqual(client.post('/api/player-location/', data, content_type='application/json', **headers).status_code, 403)


class ZoneBonusTests(TestCase):
    def setUp(self):
        self.start = timezone.now()
        self.game = Game.objects.create(start_time=self.start, end_time=self.start + timedelta(minutes=30))
        self.team = Team.objects.create(name='Red', color='#ff0000')
        self.other = Team.objects.create(name='Blue', color='#0000ff')
        self.zone = Zone.objects.create(name='Zone', owner=self.team, status='OWNED')
        Zone.objects.create(name='Base', owner=self.team, status='OWNED', is_base=True)
        for status in ('NEUTRAL', 'CONTESTED', 'CAPTURING'):
            Zone.objects.create(name=status, owner=self.team, status=status)
        Zone.objects.create(name='Unowned', status='OWNED')
        self.config = {'zone_bonus_interval_minutes': 10, 'zone_bonus_points_per_zone': 5}

    def award(self, minutes):
        award_zone_bonuses(self.game, self.start + timedelta(minutes=minutes), self.config)
        self.team.refresh_from_db()
        self.other.refresh_from_db()

    def test_boundaries_and_persistent_deduplication(self):
        for minute in (-1, 0, 9.99):
            self.award(minute)
            self.assertEqual(self.team.score, 0)
        self.award(10)
        self.assertEqual(self.team.score, 10)
        self.game = Game.objects.get(pk=self.game.pk)
        self.award(10)
        self.award(19.99)
        self.assertEqual(self.team.score, 10)
        self.zone.owner = self.other
        self.zone.save()
        self.award(20)
        self.assertEqual(self.team.score, 15)
        self.assertEqual(self.other.score, 5)

    def test_catchup_and_end_boundary(self):
        self.award(45)
        self.assertEqual(self.team.score, 30)
        self.award(60)
        self.assertEqual(self.team.score, 30)

    def test_no_interval_after_end(self):
        self.game.end_time = self.start + timedelta(minutes=29)
        self.game.save()
        self.award(45)
        self.assertEqual(self.team.score, 20)

    def test_disabled_settings(self):
        for key in self.config:
            with self.subTest(key=key):
                config = {**self.config, key: 0}
                award_zone_bonuses(self.game, self.start + timedelta(minutes=10), config)
                self.team.refresh_from_db()
                self.assertEqual(self.team.score, 0)

