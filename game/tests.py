from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from game.models import Game, Team, Zone
from game.management.commands.process_game_state import award_zone_bonuses


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

