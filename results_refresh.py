# results_refresh.py
"""
Keeps the `matches` table current as the season progresses, so Elo
ratings and goal profiles automatically reflect real results instead
of staying frozen on last season forever.

Design: periodically re-pull the FULL current season for each league
(all statuses — SCHEDULED, FINISHED, etc.) and upsert everything. This
is deliberately simple rather than trying to fetch only "new" results:
football-data.org's free tier returns an entire season in one call
(confirmed during initial ingestion — 380/380/189 matches, one call
each), so re-pulling the whole season periodically costs only 3 API
calls per refresh cycle — trivial against the 10 req/min limit, and
avoids any risk of missing a result due to a fragile "since last
check" filter.

Runs as a background thread, started once from api.py at boot —
same pattern as live_feed.py's LeagueFeed refresh loop.
"""
import requests
import sqlite3
import time
import os
import threading
from dotenv import load_dotenv

from leagues import LEAGUES

load_dotenv()

BASE_URL = "https://api.football-data.org/v4"
API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY")
HEADERS = {"X-Auth-Token": API_KEY}

CURRENT_SEASON = 2025  # 2025-26 season — update when a new season starts

REFRESH_INTERVAL_SECONDS = 30 * 60  # every 30 minutes; well within rate limits


def fetch_and_upsert_season(comp_code, league_key, db_path="ballr.db"):
    """
    Pulls the full current season (all match statuses) for one
    competition and upserts every match. INSERT OR REPLACE means a
    match that was SCHEDULED on the last refresh and is now FINISHED
    (with a real score) correctly overwrites the old row.
    """
    if not API_KEY:
        raise RuntimeError(
            "FOOTBALL_DATA_API_KEY is not set. Create a .env file with "
            "FOOTBALL_DATA_API_KEY=<your key> in the project root."
        )

    url = f"{BASE_URL}/competitions/{comp_code}/matches"
    params = {"season": CURRENT_SEASON}
    response = requests.get(url, headers=HEADERS, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    matches = data.get("matches", [])

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    newly_finished = 0
    for m in matches:
        score = m.get("score", {}).get("fullTime", {})
        status = m.get("status")

        # Check whether this match was previously stored as something
        # other than FINISHED, to report how many results actually
        # changed this refresh — useful for the printed test/log output.
        cursor.execute("SELECT status FROM matches WHERE id = ?", (m.get("id"),))
        row = cursor.fetchone()
        was_not_finished_before = row is None or row[0] != "FINISHED"

        cursor.execute("""
            INSERT OR REPLACE INTO matches
            (id, competition, season, utc_date, home_team, away_team, home_score, away_score, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            m.get("id"), league_key, CURRENT_SEASON, m.get("utcDate"),
            m.get("homeTeam", {}).get("name"), m.get("awayTeam", {}).get("name"),
            score.get("home"), score.get("away"), status,
        ))

        if status == "FINISHED" and was_not_finished_before:
            newly_finished += 1

    conn.commit()
    conn.close()
    return len(matches), newly_finished


def refresh_all_leagues(db_path="ballr.db"):
    """
    One pass across every configured league. Called immediately at
    startup (so results are fresh the moment Ballr boots) and then
    repeatedly by the background loop.
    """
    for league_key, cfg in LEAGUES.items():
        comp_code = cfg["football_data_code"]
        try:
            total, newly_finished = fetch_and_upsert_season(comp_code, league_key, db_path)
            if newly_finished > 0:
                print(f"[results_refresh] {league_key}: {total} matches synced, "
                      f"{newly_finished} newly finished since last check.")
            else:
                print(f"[results_refresh] {league_key}: {total} matches synced, no new results.")
        except Exception as e:
            print(f"[results_refresh] Warning: failed to refresh {league_key}: {e}")

        time.sleep(6)  # stay comfortably under the 10 req/min rate limit


def _refresh_loop(db_path="ballr.db"):
    while True:
        time.sleep(REFRESH_INTERVAL_SECONDS)
        print("[results_refresh] Running scheduled refresh...")
        refresh_all_leagues(db_path)


def start_results_refresh(db_path="ballr.db"):
    """
    Called once from api.py at startup. Does one immediate refresh
    (so ratings reflect the latest results right away, not just after
    waiting a full interval), then starts the periodic background loop.
    """
    refresh_all_leagues(db_path)

    thread = threading.Thread(target=_refresh_loop, args=(db_path,), daemon=True, name="results-refresh")
    thread.start()
    print(f"[results_refresh] Background refresh started — checking every "
          f"{REFRESH_INTERVAL_SECONDS // 60} minutes.\n")


if __name__ == "__main__":
    print("Running one manual refresh pass (no background loop)...\n")
    refresh_all_leagues()
    print("\nDone. Check ratings with: python3 elo_engine.py")