"""Upcoming scheduled and timed fixtures, sharing the results API cache."""
from datetime import datetime, timezone
from football_client import get_season_matches
from leagues import LEAGUES
from contextlib import closing
from database import connect
from config import DB_PATH, current_season
from readiness import league_status, DataUnavailable

COMPETITIONS = {key: cfg['football_data_code'] for key, cfg in LEAGUES.items()}


def get_upcoming_fixtures(league_key, limit=20):
    comp_code = COMPETITIONS.get(league_key)
    if comp_code is None:
        return []
    fixtures = []
    now = datetime.now(timezone.utc)
    stale = False
    try:
        matches = get_season_matches(comp_code)
    except Exception:
        status = league_status(league_key)
        if not status['available']:
            raise DataUnavailable('Fixture provider unavailable and saved data has expired.')
        stale = True
        with closing(connect(DB_PATH)) as conn:
            rows = conn.execute("""SELECT id, home_team, away_team, utc_date, status FROM matches
                WHERE competition=? AND season=?""", (league_key, current_season())).fetchall()
        matches = [{'id': row[0], 'homeTeam': {'name': row[1]}, 'awayTeam': {'name': row[2]},
                    'utcDate': row[3], 'status': row[4]} for row in rows]
    for match in matches:
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
        fixtures.append({'match_id': match.get('id'), 'home_team': home, 'away_team': away,
                         'utc_date': date, 'fixtures_stale': stale})
    fixtures.sort(key=lambda fixture: fixture['utc_date'])
    return fixtures if limit is None else fixtures[:limit]
