# Swordmap Game

Swordmap (KSP bojovka) is an interactive, location-based territorial control game where teams compete to capture and hold zones on a real-world map.

## Configuration

Set `page_title` (default `"KSP bojovka"`) and `capture_duration_seconds` (a positive integer, default `60`) at the top level of `game_config.json`. The title appears in all page titles and the team selection heading. Capture duration controls both capture completion and the displayed countdown. These settings are read live; refresh a page to see a changed title. A duration change also affects captures already in progress. QR captures retain their existing instant-capture behavior.

Edit `game_config.json` to define zones, bases, and their coordinates. Real-world coordinates (`latitude`, `longitude`) are now used instead of 2D coordinates:

```json
{
  "teams": [
    {
      "name": "TeamName",
      "color": "#FFFFFF"
    }
  ],
  "zones": [
    {
      "id": 1,
      "name": "Zone Name",
      "latitude": 50.087,
      "longitude": 14.421,
      "is_base": true,
      "owner": "TeamName",
      "color": "#FFFFFF",
      "adjacent_zones": [2, 3]
    }
  ]
}
```
- Teams are defined in the `teams` section of `game_config.json`. The `owner` field in the zones must match the name of a team defined there.
- Running `init_game_data` will apply these configurations and reset all team scores to 0.

The `scoring` section also supports recurring zone bonuses alongside the existing scoring methods:

```json
"scoring": {
  "zone_bonus_interval_minutes": 10,
  "zone_bonus_points_per_zone": 5
}
```

This awards 5 bonus points per currently owned zone at minutes 10, 20, 30, etc. from the game start, including an interval exactly at game end. Bases count; neutral, contested, and capturing zones do not. Set either value to `0` to disable this method (the default). Payout progress is stored in the database to prevent duplicates after a restart. If processing is delayed, missed intervals are paid using ownership when processing resumes, capped at game end. Configure these values before starting the game; live changes apply from the last processing time. Run `python manage.py migrate` after updating the application.

## Setup (Docker)

The recommended way to run the application is via Docker Compose. Ensure your `.env` file is set up with PostgreSQL database credentials.

1. **Build and Run the Containers**:
    ```bash
    docker-compose up -d --build
    ```

2. **Initialize Database Data**:
    ```bash
    docker-compose exec web python manage.py migrate
    docker-compose exec web python manage.py init_game_data
    ```

*Note: The game loop for processing real-time game state (score increments & captures) is handled automatically by the `game_loop` container defined in `docker-compose.yml`.*

## Usage

- Navigate to `http://localhost:8000/`.
- Join a Team (or connect as an Admin).
- Check the **Pravidla** tab for complete game documentation and combat rules.
- View real-time scores in the **Statistiky** tab.
- The admin map (`/map/admin2536/`) shows players as team-colored markers, refreshed every five seconds. Players must keep their team map open and allow browser location access (HTTPS or localhost required). Locations are reported every ten seconds and disappear from the admin map after sixty seconds without updates. Location access follows the existing admin map role; anyone with the admin URL can obtain that role.
- Switch to the **Hra** tab, follow the OSM map using your live GPS location, and tap reachable nodes to capture them for your team!

