# db_maintenance.py
"""Prune price history in small transactions; SQLite reuses freed pages."""
from database import connect
from config import DB_PATH
from contextlib import closing
import time
import threading
from datetime import datetime, timezone, timedelta

# Retain recent ticks for diagnostics while bounding storage growth.
RETENTION_HOURS = 48

PRUNE_INTERVAL_SECONDS = 60 * 60  # run once per hour


def prune_old_price_updates(db_path=DB_PATH, retention_hours=RETENTION_HOURS):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention_hours)).isoformat()

    to_delete = 0
    with closing(connect(db_path)) as conn:
        while True:
            with conn:
                cursor = conn.execute("""DELETE FROM price_updates WHERE id IN (
                    SELECT id FROM price_updates WHERE timestamp < ? LIMIT 5000
                )""", (cutoff,))
            to_delete += cursor.rowcount
            if cursor.rowcount < 5000:
                break
            time.sleep(0.01)
    # No hourly VACUUM: new ticks reuse these pages without blocking readers.

    print(f"[db_maintenance] Pruned {to_delete} price ticks older than "
          f"{retention_hours}h. Retained the rest.")
    return to_delete


def _prune_loop(db_path=DB_PATH):
    while True:
        try:
            prune_old_price_updates(db_path)
        except Exception as e:
            print(f"[db_maintenance] Warning: prune failed: {e}")
        time.sleep(PRUNE_INTERVAL_SECONDS)


def start_db_maintenance(db_path=DB_PATH):
    """Run an immediate prune in the maintenance thread, then repeat hourly."""
    thread = threading.Thread(target=_prune_loop, args=(db_path,), daemon=True, name="db-maintenance")
    thread.start()
    print(f"[db_maintenance] Background pruning started — checking every "
          f"{PRUNE_INTERVAL_SECONDS // 3600}h, retaining {RETENTION_HOURS}h of price history.\n")


if __name__ == "__main__":
    print("Running one manual prune pass...")
    prune_old_price_updates()