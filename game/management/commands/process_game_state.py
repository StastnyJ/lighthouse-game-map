from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from django.db.models import Count, F
from game.models import Zone, Team, Game, GameSnapshot
import datetime
import time
import json
from pathlib import Path


def save_minute_snapshot(game, now, minute_index=None, force=False):
    if not game.start_time or now < game.start_time:
        return

    if minute_index is None:
        minute_index = int((now - game.start_time).total_seconds() // 60)
    if minute_index < 0:
        return

    if not force and GameSnapshot.objects.filter(game=game, minute_index=minute_index).exists():
        return

    teams = Team.objects.all()
    zones = Zone.objects.all()

    scores_data = {
        team.name: {
            'score': team.score,
            'color': team.color,
        }
        for team in teams
    }

    zones_data = []
    for zone in zones:
        zones_data.append({
            'id': zone.id,
            'owner': zone.owner.name if zone.owner else None,
            'status': zone.status,
            'is_base': zone.is_base,
            'capturing_team': zone.capturing_team.name if zone.capturing_team else None,
        })

    defaults = {
        'captured_at': now,
        'scores': scores_data,
        'zones': zones_data,
    }

    if force:
        GameSnapshot.objects.update_or_create(
            game=game,
            minute_index=minute_index,
            defaults=defaults,
        )
    else:
        GameSnapshot.objects.create(
            game=game,
            minute_index=minute_index,
            **defaults,
        )


def load_scoring_config():
    """Load scoring configuration from game_config.json with sensible defaults."""
    config_path = Path(__file__).resolve().parent.parent.parent.parent / 'game_config.json'
    
    default_scoring = {
        'capture': 5,
        'recapture_min': 0,
        'recapture_max': 5,
        'per_zone_per_minute': 1,
        'end_game_bonus_per_zone': 10,
        'zone_bonus_interval_minutes': 0,
        'zone_bonus_points_per_zone': 0,
    }
    
    try:
        config_data = json.loads(config_path.read_text(encoding='utf-8'))
        scoring = config_data.get('scoring', {})
        
        # Merge with defaults (user config overrides defaults)
        return {**default_scoring, **scoring}
    except (OSError, json.JSONDecodeError):
        return default_scoring

def award_zone_bonuses(game, now, scoring_config):
    """Pay elapsed start-aligned intervals once, using current zone ownership."""
    if not game.start_time or not game.end_time or now < game.start_time:
        return
    cutoff = min(now, game.end_time)
    interval = scoring_config['zone_bonus_interval_minutes']
    points = scoring_config['zone_bonus_points_per_zone']
    with transaction.atomic():
        locked_game = Game.objects.select_for_update().get(pk=game.pk)
        previous = locked_game.zone_bonus_processed_at or game.start_time
        if cutoff <= previous:
            return
        if interval > 0 and points:
            interval_seconds = interval * 60
            elapsed = int((cutoff - game.start_time).total_seconds() // interval_seconds)
            processed = max(0, int((previous - game.start_time).total_seconds() // interval_seconds))
            payouts = elapsed - processed
            if payouts > 0:
                ownership = Zone.objects.filter(status='OWNED', owner__isnull=False).values('owner_id').annotate(zone_count=Count('id'))
                for row in ownership:
                    Team.objects.filter(pk=row['owner_id']).update(
                        score=F('score') + payouts * points * row['zone_count']
                    )
        locked_game.zone_bonus_processed_at = cutoff
        locked_game.save(update_fields=['zone_bonus_processed_at'])


class Command(BaseCommand):
    help = 'Process game state: update scores and handle captures'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting game state processor loop...")
        while True:
            now = timezone.now()
            
            # Load scoring config on each iteration (allows live updates)
            scoring_config = load_scoring_config()
            
            # Check Game Status
            game = Game.objects.first()
            if not game:
                self.stdout.write(self.style.WARNING("No game instance found. Retrying in 5s..."))
                time.sleep(5)
                continue

            if not game.start_time or not game.end_time:
                 self.stdout.write(self.style.WARNING("Game start/end time not set. Retrying in 5s..."))
                 time.sleep(5)
                 continue

            if now < game.start_time:
                 self.stdout.write(self.style.WARNING(f"Game has not started yet. Starts at {game.start_time}. Waiting..."))
                 time.sleep(5)
                 continue
            
            if now > game.end_time:
                award_zone_bonuses(game, now, scoring_config)
                if not game.end_bonus_applied:
                    final_owned_zones = Zone.objects.filter(status='OWNED', owner__isnull=False)
                    final_bonus_by_team = {}

                    for zone in final_owned_zones:
                        final_bonus_by_team[zone.owner_id] = final_bonus_by_team.get(zone.owner_id, 0) + scoring_config['end_game_bonus_per_zone']

                    for team in Team.objects.filter(id__in=final_bonus_by_team.keys()):
                        bonus = final_bonus_by_team.get(team.id, 0)
                        team.score += bonus
                        team.save(update_fields=['score'])
                        self.stdout.write(self.style.SUCCESS(
                            f"End-game bonus: {team.name} +{bonus}"
                        ))

                    game.end_bonus_applied = True
                    game.save(update_fields=['end_bonus_applied'])

                # Save one final snapshot after game end (including final bonuses),
                # so statistics contain an explicit final frame/values.
                final_minute_index = int((game.end_time - game.start_time).total_seconds() // 60)
                save_minute_snapshot(
                    game,
                    now,
                    minute_index=max(final_minute_index, 0),
                    force=True,
                )

                self.stdout.write(self.style.WARNING(f"Game has ended. Ended at {game.end_time}"))
                time.sleep(10)
                continue

            # 1. Handle Captures
            capturing_zones = Zone.objects.filter(status='CAPTURING')
            for zone in capturing_zones:
                if zone.capture_started_at:
                    diff = now - zone.capture_started_at
                    if diff.total_seconds() >= 60: # 1 minute
                        capturing_team = zone.capturing_team
                        if capturing_team is None:
                            zone.status = 'NEUTRAL'
                            zone.capture_started_at = None
                            zone.save(update_fields=['status', 'capture_started_at'])
                            continue

                        previous_owner = zone.owner
                        capture_points = scoring_config['capture']

                        # Recapture rule: if the capturing team lost this zone before,
                        # award min(minutes since loss, max) instead of the standard points.
                        if (
                            zone.last_lost_by_team_name
                            and zone.last_lost_at
                            and zone.last_lost_by_team_name.lower() == capturing_team.name.lower()
                        ):
                            minutes_since_loss = int((now - zone.last_lost_at).total_seconds() // 60)
                            capture_points = min(
                                max(minutes_since_loss, scoring_config['recapture_min']),
                                scoring_config['recapture_max']
                            )

                        if previous_owner and previous_owner != capturing_team:
                            zone.last_lost_at = now
                            zone.last_lost_by_team_name = previous_owner.name

                        zone.status = 'OWNED'
                        zone.owner = capturing_team

                        # Reward for capturing
                        capturing_team.score += capture_points
                        capturing_team.save(update_fields=['score'])
                        
                        zone.capturing_team = None
                        zone.capture_started_at = None
                        zone.last_score_update = now
                        zone.save()
                        self.stdout.write(self.style.SUCCESS(
                            f"Zone {zone.name} captured by {zone.owner.name} (+{capture_points} points)"
                        ))

            # 2. Update Scores
            # Points are counted for each minute holding the point of interest (excluding bases)
            owned_zones = Zone.objects.filter(status='OWNED', is_base=False, owner__isnull=False)
            for zone in owned_zones:
                if zone.last_score_update:
                    diff = now - zone.last_score_update
                    minutes = int(diff.total_seconds() / 60)
                    
                    if minutes >= 1:
                        points = minutes * scoring_config['per_zone_per_minute']
                        zone.owner.score += points
                        zone.owner.save(update_fields=['score'])
                        zone.last_score_update += datetime.timedelta(minutes=minutes)
                        zone.save()
                        self.stdout.write(self.style.SUCCESS(f"Added {points} points to {zone.owner.name} for {zone.name}"))
                else:
                    zone.last_score_update = now
                    zone.save()

            # 3. Award start-aligned zone bonuses before recording scores.
            award_zone_bonuses(game, now, scoring_config)

            # 4. Persist one snapshot per minute for statistics and replay.
            save_minute_snapshot(game, now)
            
            time.sleep(1)
