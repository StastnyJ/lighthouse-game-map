from django.http import JsonResponse
from django.utils import timezone
from .models import Zone, Team, Game, GameSnapshot
from .models import PlayerLocation
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.cache import never_cache
import datetime
import json
import math
import uuid
from django.core import signing
from .config import load_game_settings


def location_role(request):
    try:
        return signing.loads(request.headers.get('X-Map-Token', ''), salt='map-locations', max_age=86400)
    except signing.BadSignature:
        return None


@require_POST
def report_player_location(request):
    role = location_role(request)
    team = Team.objects.filter(name__iexact=role).first() if role else None
    if not team:
        return JsonResponse({'error': 'Open a team map first.'}, status=403)
    try:
        data = json.loads(request.body)
        latitude = float(data['latitude'])
        longitude = float(data['longitude'])
        if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        return JsonResponse({'error': 'Invalid coordinates.'}, status=400)
    player_id = request.session.get('player_location_id')
    if not player_id:
        player_id = str(uuid.uuid4())
        request.session['player_location_id'] = player_id
    now = timezone.now()
    PlayerLocation.objects.filter(updated_at__lt=now - datetime.timedelta(minutes=5)).delete()
    PlayerLocation.objects.update_or_create(pk=player_id, defaults={
        'team': team, 'latitude': latitude, 'longitude': longitude, 'updated_at': now,
    })
    return JsonResponse({'ok': True})


@never_cache
@require_GET
def admin_player_locations(request):
    if location_role(request) != 'admin2536':
        return JsonResponse({'error': 'Admin access required.'}, status=403)
    locations = PlayerLocation.objects.select_related('team').filter(
        updated_at__gte=timezone.now() - datetime.timedelta(seconds=60)
    )
    return JsonResponse({'players': [{
        'id': str(player.pk), 'team': player.team.name, 'color': player.team.color,
        'latitude': player.latitude, 'longitude': player.longitude,
        'updated_at': player.updated_at.isoformat(),
    } for player in locations]})

def game_state(request):
    from .management.commands.process_game_state import complete_due_captures
    complete_due_captures(timezone.now())
    capture_duration_seconds = load_game_settings()['capture_duration_seconds']
    zones = Zone.objects.all()
    capturing_zones = Zone.objects.filter(status='CAPTURING')
    teams = Team.objects.all()
    game = Game.objects.first()
    
    scores = {team.name: {'score': team.score, 'color': team.color} for team in teams}
    
    zones_data = {}
    for zone in zones:
        owner_color = zone.owner.color if zone.owner else zone.default_color
        
        capturing_color = None
        if zone.status == 'CAPTURING' and zone.capturing_team:
            capturing_color = zone.capturing_team.color

        zones_data[zone.id] = {
            'id': zone.id,
            'color': owner_color,
            'owner': zone.owner.name if zone.owner else None,
            'is_capturing': zone.status == 'CAPTURING',
            'capturing_color': capturing_color,
            'is_base': zone.is_base
        }
        
    capturing_data = []
    now = timezone.now()
    
    game_remaining_seconds = 0
    if game and game.end_time:
        if now < game.end_time:
             game_remaining_seconds = int((game.end_time - now).total_seconds())
        else:
             game_remaining_seconds = 0

    for zone in capturing_zones:
        remaining = 0
        if zone.capture_started_at:
            elapsed = (now - zone.capture_started_at).total_seconds()
            remaining = max(0, capture_duration_seconds - int(elapsed))
            
        capturing_data.append({
            'name': zone.name,
            'team_name': zone.capturing_team.name,
            'team_color': zone.capturing_team.color,
            'remaining_seconds': remaining
        })
        
    return JsonResponse({
        'zones': zones_data,
        'capturing': capturing_data,
        'scores': scores,
        'game_remaining_seconds': game_remaining_seconds
    })


def stats_timeline(request):
    game = Game.objects.first()
    teams = Team.objects.all()
    zones = Zone.objects.all().order_by('id')
    snapshots = GameSnapshot.objects.select_related('game').order_by('minute_index')

    teams_data = [
        {
            'name': team.name,
            'color': team.color,
        }
        for team in teams
    ]

    zones_data = [
        {
            'id': zone.id,
            'name': zone.name,
            'lat': zone.latitude,
            'lng': zone.longitude,
            'adjacent': list(zone.adjacent_zones.values_list('id', flat=True)),
            'is_base': zone.is_base,
        }
        for zone in zones
    ]

    timeline = [
        {
            'minute_index': snapshot.minute_index,
            'captured_at': snapshot.captured_at.isoformat(),
            'scores': snapshot.scores,
            'zones': snapshot.zones,
        }
        for snapshot in snapshots
    ]

    return JsonResponse({
        'game': {
            'start_time': game.start_time.isoformat() if game and game.start_time else None,
            'end_time': game.end_time.isoformat() if game and game.end_time else None,
        },
        'teams': teams_data,
        'zones': zones_data,
        'timeline': timeline,
    })
