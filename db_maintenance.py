# db_maintenance.py
"""Remove inactive market quotes; no price history is accumulated."""
from database import connect
from config import DB_PATH
from contextlib import closing
import time
import threading
from datetime import datetime, timezone, timedelta

# Remove quotes for inactive outcomes after an hour.
RETENTION_HOURS = 1

PRUNE_INTERVAL_SECONDS = 60


def prune_old_price_updates(db_path=DB_PATH, retention_hours=RETENTION_HOURS):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention_hours)).isoformat()

    to_delete = 0
    with closing(connect(db_path)) as conn:
        while True:
            with conn:
                cursor = conn.execute("""DELETE FROM market_prices WHERE asset_id IN (
                    SELECT asset_id FROM market_prices WHERE timestamp < ? LIMIT 5000
                )""", (cutoff,))
            to_delete += cursor.rowcount
            if cursor.rowcount < 5000:
                break
            time.sleep(0.01)
    # No hourly VACUUM: new ticks reuse these pages without blocking readers.

    print(f"[db_maintenance] Pruned {to_delete} market quotes older than "
          f"{retention_hours}h. Retained the rest.")
    return to_delete


def _prune_loop(db_path=DB_PATH):
    while True:
        try:
            from operations import report
            from storage_guard import storage_status
            state = storage_status(db_path, force=True)
            report('storage', state['state'])
            from football_client import _cache as football_cache
            from polymarket_client import _event_cache
            from predictions import _context_cache, _prediction_cache
            for cache in (football_cache, _event_cache, _context_cache, _prediction_cache):
                cache.prune_expired()
            prune_old_price_updates(db_path)
            with closing(connect(db_path)) as conn:
                conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
            report('maintenance')
        except Exception as e:
            report('maintenance', 'error')
            print(f"[db_maintenance] Warning: prune failed: {e}")
        time.sleep(PRUNE_INTERVAL_SECONDS)


def start_db_maintenance(db_path=DB_PATH):
    """Run an immediate prune in the maintenance thread, then repeat every minute."""
    thread = threading.Thread(target=_prune_loop, args=(db_path,), daemon=True, name="db-maintenance")
    thread.start()
    print(f"[db_maintenance] Background pruning started — checking every "
          f"{PRUNE_INTERVAL_SECONDS}s, retaining current quotes for {RETENTION_HOURS}h.\n")


if __name__ == "__main__":
    print("Running one manual prune pass...")
    prune_old_price_updates()