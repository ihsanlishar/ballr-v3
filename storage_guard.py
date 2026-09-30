"""Protect essential match data by stopping optional writes under pressure."""
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from config import DB_PATH

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_cached = {}
_warned = {}
_cooldown = {}


def storage_status(db_path=DB_PATH, force=False):
    path = str(Path(db_path).resolve())
    with _lock:
        cached = _cached.get(path)
        if cached and not force and time.monotonic() - cached[0] < 5:
            return dict(cached[1])
        try:
            usage = shutil.disk_usage(Path(path).parent)
            size = sum(Path(path + suffix).stat().st_size for suffix in ('', '-wal', '-shm')
                       if Path(path + suffix).exists())
            reserve = max(64 * 1024**2, int(os.environ.get('BALLR_DISK_RESERVE_MB', '128')) * 1024**2)
            ceiling = max(64, int(os.environ.get('BALLR_DB_BUDGET_MB', '256'))) * 1024**2
            disk_pressure = usage.free < reserve or usage.free / usage.total < .1
            paused = disk_pressure or size >= ceiling or time.monotonic() < _cooldown.get(path, 0)
            state = {'state': 'pressure' if paused else 'ok', 'optional_writes_paused': paused,
                     'database_bytes': size, 'free_bytes': usage.free, 'budget_bytes': ceiling}
        except OSError:
            state = {'state': 'unavailable', 'optional_writes_paused': True}
        _cached[path] = (time.monotonic(), state)
        return dict(state)


def allow_optional_writes(db_path=DB_PATH):
    state = storage_status(db_path)
    allowed = not state['optional_writes_paused']
    if not allowed:
        now = time.monotonic()
        with _lock:
            if now - _warned.get(str(db_path), float('-inf')) >= 60:
                logger.warning('Optional writes paused: storage needs attention')
                _warned[str(db_path)] = now
    return allowed


def note_write_failure(db_path=DB_PATH):
    path = str(Path(db_path).resolve())
    with _lock:
        _cooldown[path] = time.monotonic() + 60
        _cached.pop(path, None)
    logger.error('Optional database write failed; backing off for 60 seconds')


def write_quotes(rows, db_path=DB_PATH):
    """Bound rows even if a provider emits unexpected new outcome IDs."""
    if not rows or not allow_optional_writes(db_path):
        return False
    from contextlib import closing
    from database import connect
    from operations import report
    cap = max(100, int(os.environ.get('BALLR_MAX_QUOTES', '10000')))
    # Keep only the last update in this incoming batch for each outcome.
    latest = {row[0]: row for row in rows}
    try:
        with closing(connect(db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            remaining = max(0, cap - conn.execute('SELECT COUNT(*) FROM market_prices').fetchone()[0])
            accepted = []
            for asset, row in latest.items():
                exists = conn.execute('SELECT 1 FROM market_prices WHERE asset_id=?', (asset,)).fetchone()
                if exists or remaining > 0:
                    accepted.append(row)
                    remaining -= int(not exists)
            conn.executemany('''INSERT INTO market_prices
                (asset_id, price, side, timestamp, league) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET price=excluded.price, side=excluded.side,
                timestamp=excluded.timestamp, league=excluded.league
                WHERE excluded.timestamp >= market_prices.timestamp''', accepted)
        report('quote_storage', 'ok' if len(accepted) == len(latest) else 'capacity')
        return bool(accepted)
    except sqlite3.Error:
        note_write_failure(db_path)
        report('quote_storage', 'error')
        return False
