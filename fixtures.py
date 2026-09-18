"""Upcoming scheduled and timed fixtures, sharing the results API cache."""
from datetime import datetime, timezone
from football_client import get_season_matches
from leagues import LEAGUES

COMPETITIONS = {key: cfg['football_data_code'] for key, cfg in LEAGUES.items()}


def get_upcoming_fixtures(league_key, limit=20):
    comp_code = COMPETITIONS.get(league_key)
    if comp_code is None:
        return []
    fixtures = []
    now = datetime.now(timezone.utc)
    for match in get_season_matches(comp_code):
        if match.get('status') not in {'SCHEDULED', 'TIMED'}:
            continue
        home = (match.get('homeTeam') or {}).get('name')
        away = (match.get('awayTeam') or {}).get('name')
        date = match.get('utcDate')
        if not home or not away or not date:
            continue
        try:
            if datetime.fromisoformat(date.replace('Z', '+00:00')) <= now:
                continue
        except (ValueError, TypeError):
            continue
        fixtures.append({'home_team': home, 'away_team': away, 'utc_date': date})
    fixtures.sort(key=lambda fixture: fixture['utc_date'])
    return fixtures if limit is None else fixtures[:limit]
