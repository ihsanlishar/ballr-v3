# ingest_last_season.py
"""
One-time ingestion: pulls last season's (2025-26) full match results
for Premier League, La Liga, and Champions League from football-data.org,
and stores them in a local database. Run once; the data doesn't change
after the fact, so there's no need to re-run this regularly.
"""
import requests
import sqlite3
import time

API_KEY = "08f4fa177c9b4ba084b75ed187f88e4b"
BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_KEY}

COMPETITIONS = {
    "PL": "premier_league",
    "PD": "la_liga",
    "CL": "champions_league",
}

SEASON = 2025  # 2025-26 season


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
            m.get("id"),
            league_key,
            SEASON,
            m.get("utcDate"),
            m.get("homeTeam", {}).get("name"),
            m.get("awayTeam", {}).get("name"),
            score.get("home"),
            score.get("away"),
            m.get("status"),
        ))
        stored += 1

    conn.commit()
    conn.close()
    return stored


if __name__ == "__main__":
    ensure_schema()

    for comp_code, league_key in COMPETITIONS.items():
        print(f"Fetching {league_key} ({comp_code})...")
        count = fetch_and_store(comp_code, league_key)
        print(f"  Stored {count} matches for {league_key}")
        time.sleep(6)  # comfortable buffer, still only 3 calls total

    print("\nDone — all historical data cached locally.")