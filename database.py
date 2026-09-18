"""Consistent SQLite connections; callers close connections explicitly."""
import sqlite3
from pathlib import Path
from config import DB_PATH


def connect(db_path=DB_PATH):
    return sqlite3.connect(db_path, timeout=10)


def initialize(db_path=DB_PATH):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute('PRAGMA journal_mode=WAL')
    finally:
        conn.close()
    from ingest_last_season import ensure_schema
    from polymarket_client import ensure_price_schema
    ensure_schema(db_path)
    ensure_price_schema(db_path)
