"""Refresh the active season immediately in a background thread, then periodically."""
import logging
import threading
import time
from config import DB_PATH, current_season
from football_client import get_season_matches
from ingest_last_season import store_matches
from leagues import LEAGUES

logger = logging.getLogger(__name__)
REFRESH_INTERVAL_SECONDS = 30 * 60


def fetch_and_upsert_season(comp_code, league_key, db_path=DB_PATH):
    season = current_season()
    return store_matches(get_season_matches(comp_code, season), league_key, season, db_path)


def refresh_all_leagues(db_path=DB_PATH):
    succeeded = True
    for league, cfg in LEAGUES.items():
        try:
            total, finished = fetch_and_upsert_season(cfg['football_data_code'], league, db_path)
            logger.info('%s: %s matches synced, %s newly finished', league, total, finished)
        except Exception:
            succeeded = False
            logger.exception('Results refresh failed for %s; will retry', league)
    return succeeded


def _refresh_loop(db_path=DB_PATH):
    from db_bootstrap import bootstrap_if_needed
    while True:
        try:
            succeeded = refresh_all_leagues(db_path)
            bootstrap_if_needed(db_path)
        except Exception:
            succeeded = False
            logger.exception('Results worker failed; will retry')
        time.sleep(REFRESH_INTERVAL_SECONDS if succeeded else 60)


def start_results_refresh(db_path=DB_PATH):
    thread = threading.Thread(target=_refresh_loop, args=(db_path,), daemon=True, name='results-refresh')
    thread.start()
    return thread


if __name__ == '__main__':
    from database import initialize
    initialize()
    refresh_all_leagues()
