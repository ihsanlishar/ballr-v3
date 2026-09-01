# ingest_last_season.py
"""
One-time ingestion: pulls last season's full match results for every
league configured in leagues.py, storing them locally. Run once.
"""
import requests
import sqlite3
import time
import os
from dotenv import load_dotenv

from leagues import LEAGUES

load_dotenv()

API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY")
BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_KEY}

SEASON = 2025


def ensure_schema(db_path="ballr.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY,
            competition TEXT,
            season INTEGER,
            utc_date TEXT,
            home_team TEXT,
            away_team TEXT,
            home_score INTEGER,
            away_score INTEGER,
            status TEXT
        )
    """)
    conn.commit()
    conn.close()


def fetch_and_store(comp_code, league_key, db_path="ballr.db"):
    if not API_KEY:
        raise RuntimeError(
            "FOOTBALL_DATA_API_KEY is not set. Create a .env file with "
            "FOOTBALL_DATA_API_KEY=<your key> in the project root."
        )

    url = f"{BASE_URL}/competitions/{comp_code}/matches"
    params = {"season": SEASON}
    response = requests.get(url, headers=HEADERS, params=params)
    response.raise_for_status()
    data = response.json()
    matches = data.get("matches", [])

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    stored = 0
    for m in matches:
        score = m.get("score", {}).get("fullTime", {})
        cursor.execute("""
            INSERT OR REPLACE INTO matches
            (id, competition, season, utc_date, home_team, away_team, home_score, away_score, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            m.get("id"), league_key, SEASON, m.get("utcDate"),
            m.get("homeTeam", {}).get("name"), m.get("awayTeam", {}).get("name"),
            score.get("home"), score.get("away"), m.get("status"),
        ))
        stored += 1

    conn.commit()
    conn.close()
    return stored


if __name__ == "__main__":
    ensure_schema()
    for league_key, cfg in LEAGUES.items():
        comp_code = cfg["football_data_code"]
        print(f"Fetching {league_key} ({comp_code})...")
        count = fetch_and_store(comp_code, league_key)
        print(f"  Stored {count} matches for {league_key}")
        time.sleep(6)
    print("\nDone — all historical data cached locally.")