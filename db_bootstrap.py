# db_bootstrap.py
"""
Ensures the `matches` table exists and is populated before the app
starts serving requests. On a fresh deploy (like Render, where
ballr.db is gitignored and never pushed), the database starts
completely empty — this fills it in automatically instead of
requiring a manual one-time script run against a server you can't
easily shell into.
"""
import sqlite3

from ingest_last_season import ensure_schema, fetch_and_store
from leagues import LEAGUES


def matches_table_has_data(db_path="ballr.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM matches")
        count = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        # Table doesn't exist yet at all
        count = 0
    conn.close()
    return count > 0


def bootstrap_if_needed(db_path="ballr.db"):
    """
    Called once at startup, before any other background worker. Safe
    to call every time the app boots — if data already exists (e.g.
    on your local machine), this does nothing and returns instantly.
    """
    ensure_schema(db_path)

    if matches_table_has_data(db_path):
        print("[db_bootstrap] matches table already populated — skipping ingestion.")
        return

    print("[db_bootstrap] matches table is empty — running one-time historical ingestion...")
    import time
    for league_key, cfg in LEAGUES.items():
        comp_code = cfg["football_data_code"]
        try:
            count = fetch_and_store(comp_code, league_key, db_path=db_path)
            print(f"[db_bootstrap] Stored {count} matches for {league_key}")
        except Exception as e:
            print(f"[db_bootstrap] Warning: failed to ingest {league_key}: {e}")
        time.sleep(6)  # stay under football-data.org's rate limit

    print("[db_bootstrap] Historical ingestion complete.")