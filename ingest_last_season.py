"""Historical ingestion and transactional storage shared by results refresh."""
from contextlib import closing
from datetime import datetime, timezone
from config import DB_PATH, current_season
from database import connect
from football_client import get_season_matches
from leagues import LEAGUES


def ensure_schema(db_path=DB_PATH):
    with closing(connect(db_path)) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY, competition TEXT, season INTEGER,
            utc_date TEXT, home_team TEXT, away_team TEXT,
            home_score INTEGER, away_score INTEGER, status TEXT
        )''')
        conn.execute('''CREATE INDEX IF NOT EXISTS matches_competition_status_date
            ON matches (competition, status, utc_date)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS season_sync (
            competition TEXT, season INTEGER, synced_at TEXT NOT NULL,
            PRIMARY KEY (competition, season)
        )''')


def store_matches(matches, league_key, season, db_path=DB_PATH):
    if not matches:
        raise ValueError('Provider returned no season matches; leaving existing data intact.')
    newly_finished = 0
    with closing(connect(db_path)) as conn, conn:
        for match in matches:
            home = (match.get('homeTeam') or {}).get('name')
            away = (match.get('awayTeam') or {}).get('name')
            if not match.get('id') or not match.get('utcDate'):
                raise ValueError('Provider returned an invalid match')
            score = (match.get('score') or {}).get('fullTime') or {}
            status = match.get('status')
            if status == 'FINISHED' and (not home or not away or
                    score.get('home') is None or score.get('away') is None):
                raise ValueError('Provider returned an incomplete final result')
            row = conn.execute('SELECT status FROM matches WHERE id = ?', (match['id'],)).fetchone()
            newly_finished += int(status == 'FINISHED' and (row is None or row[0] != 'FINISHED'))
            conn.execute('''INSERT OR REPLACE INTO matches
                (id, competition, season, utc_date, home_team, away_team,
                 home_score, away_score, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (match['id'], league_key, season, match['utcDate'], home, away,
                 score.get('home'), score.get('away'), status))
        conn.execute('INSERT OR REPLACE INTO season_sync VALUES (?, ?, ?)',
                     (league_key, season, datetime.now(timezone.utc).isoformat()))
    return len(matches), newly_finished


def fetch_and_store(comp_code, league_key, db_path=DB_PATH, season=None):
    season = current_season() - 1 if season is None else season
    matches = get_season_matches(comp_code, season)
    return store_matches(matches, league_key, season, db_path)[0]


if __name__ == '__main__':
    from database import initialize
    initialize()
    for league, cfg in LEAGUES.items():
        print(league, fetch_and_store(cfg['football_data_code'], league))
