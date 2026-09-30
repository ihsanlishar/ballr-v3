"""Readiness from successful season syncs, without calling upstream services."""
from contextlib import closing
from datetime import datetime, timezone
from config import DB_PATH, current_season
from database import connect
from leagues import LEAGUES


class DataUnavailable(RuntimeError):
    pass


def league_status(league, db_path=DB_PATH):
    with closing(connect(db_path)) as conn:
        sync = conn.execute('SELECT synced_at FROM season_sync WHERE competition = ? AND season = ?',
                            (league, current_season())).fetchone()
        history = conn.execute("SELECT 1 FROM matches WHERE competition = ? AND status = 'FINISHED' LIMIT 1",
                               (league,)).fetchone()
    timestamp = sync[0] if sync else None
    age = ((datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds()
           if timestamp else float('inf'))
    return {'ready': bool(history) and 0 <= age <= 7200,
            'available': bool(history) and 0 <= age <= 86400,
            'stale': age > 7200, 'last_results_sync': timestamp}


def require_league_data(league):
    if not league_status(league)['available']:
        raise DataUnavailable('Match data is updating or temporarily unavailable. Please retry shortly.')


def all_league_status(db_path=DB_PATH):
    return {league: league_status(league, db_path) for league in LEAGUES}
