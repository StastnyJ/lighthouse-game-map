import json

from django.conf import settings


def load_game_settings():
    """Read live page and capture settings, preserving defaults for older configs."""
    try:
        config = json.loads((settings.BASE_DIR / 'game_config.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    duration = config.get('capture_duration_seconds', 60)
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        duration = 60
    title = config.get('page_title', 'KSP bojovka')
    if not isinstance(title, str) or not title.strip():
        title = 'KSP bojovka'
    return {'capture_duration_seconds': duration, 'page_title': title}


def game_settings(request):
    return load_game_settings()
