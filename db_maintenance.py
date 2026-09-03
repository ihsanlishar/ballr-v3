# db_maintenance.py
"""
Prunes old price_updates rows that the app will never query again.
Nothing in Ballr reads price history beyond the most recent ~60 ticks
per asset (see get_price_history's limit=60 default) — so retaining
everything forever is pure waste. Runs as a periodic background task,
same pattern as results_refresh.py.
"""
import sqlite3
import time
import threading
from datetime import datetime, timezone, timedelta

# Keep 48 hours of tick history per asset — comfortably more than the
# 60-point chart window needs, while still bounding growth. Adjust if
# you find the charts want a longer visible window later.
RETENTION_HOURS = 48

PRUNE_INTERVAL_SECONDS = 60 * 60  # run once per hour


def prune_old_price_updates(db_path="ballr.db", retention_hours=RETENTION_HOURS):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention_hours)).isoformat()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM price_updates WHERE timestamp < ?", (cutoff,))
    to_delete = cursor.fetchone()[0]

    cursor.execute("DELETE FROM price_updates WHERE timestamp < ?", (cutoff,))
    conn.commit()

    # VACUUM reclaims the freed disk space — without it, SQLite keeps
    # the file at its largest-ever size even after deleting rows.
    cursor.execute("VACUUM")
    conn.commit()
    conn.close()

    print(f"[db_maintenance] Pruned {to_delete} price ticks older than "
          f"{retention_hours}h. Retained the rest.")
    return to_delete


def _prune_loop(db_path="ballr.db"):
    while True:
        time.sleep(PRUNE_INTERVAL_SECONDS)
        try:
            prune_old_price_updates(db_path)
        except Exception as e:
            print(f"[db_maintenance] Warning: prune failed: {e}")


def start_db_maintenance(db_path="ballr.db"):
    """
    Called once from api.py at startup. Does one immediate prune (to
    shrink an already-bloated database right away), then runs hourly.
    """
    prune_old_price_updates(db_path)
    thread = threading.Thread(target=_prune_loop, args=(db_path,), daemon=True, name="db-maintenance")
    thread.start()
    print(f"[db_maintenance] Background pruning started — checking every "
          f"{PRUNE_INTERVAL_SECONDS // 3600}h, retaining {RETENTION_HOURS}h of price history.\n")


if __name__ == "__main__":
    print("Running one manual prune pass...")
    prune_old_price_updates()