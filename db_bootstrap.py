"""Fill missing historical coverage per league, retrying failed leagues later."""
import logging
from contextlib import closing
from config import DB_PATH, current_season
from database import connect
from ingest_last_season import ensure_schema, fetch_and_store
from leagues import LEAGUES

logger = logging.getLogger(__name__)


def bootstrap_if_needed(db_path=DB_PATH):
    ensure_schema(db_path)
    season = current_season() - 1
    for league, cfg in LEAGUES.items():
        with closing(connect(db_path)) as conn:
            exists = conn.execute('''SELECT 1 FROM matches WHERE competition = ?
                AND season = ? AND status = 'FINISHED' LIMIT 1''', (league, season)).fetchone()
        if exists:
            continue
        try:
            count = fetch_and_store(cfg['football_data_code'], league, db_path, season=season)
            logger.info('Stored %s historical matches for %s', count, league)
        except Exception:
            logger.exception('Historical ingestion failed for %s; will retry', league)
