"""Runtime settings shared by the web process and command-line tools."""
import os
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
DB_PATH = os.environ.get('BALLR_DB_PATH', str(ROOT / 'ballr.db'))
HTTP_TIMEOUT = (3.05, 15)
MARKET_MAX_AGE_SECONDS = 300


def current_season(today=None):
    """Start year for our European competitions, with a July rollover."""
    override = os.environ.get('FOOTBALL_SEASON')
    if override:
        return int(override)
    today = today or datetime.now(timezone.utc)
    return today.year if today.month >= 7 else today.year - 1
