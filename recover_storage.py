"""Export essential football data, leaving the source and its WAL untouched.

A read-only transaction provides a consistent live snapshot. Output must be a new
file, preferably on a separate disk when the source volume is full. This
command deliberately does not replace/delete any production database.
"""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from config import DB_PATH
from database import initialize


def export_football_data(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file():
        raise ValueError('Source database does not exist')
    if output.exists() or any(Path(str(output) + suffix).exists() for suffix in ('-wal', '-shm')):
        raise ValueError('Output must be a new database path')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        src.execute('BEGIN')  # Consistent snapshot across essential tables.
        tables = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        unknown = tables - {'matches', 'season_sync', 'price_updates', 'market_prices', 'prediction_archive', 'sqlite_sequence'}
        if unknown:
            raise ValueError(f'Unexpected tables require manual review: {sorted(unknown)}')
        if 'matches' not in tables:
            raise ValueError('No football history table found')
        initialize(str(output))
        with closing(sqlite3.connect(output)) as dst:
            counts = {}
            with dst:
                for table in ('matches', 'season_sync', 'prediction_archive'):
                    if table not in tables:
                        continue
                    columns = [row[1] for row in src.execute(f'PRAGMA table_info({table})')]
                    target_columns = [row[1] for row in dst.execute(f'PRAGMA table_info({table})')]
                    if columns != target_columns:
                        raise ValueError(f'Unexpected schema in {table}; original is unchanged')
                    placeholders = ','.join('?' for _ in columns)
                    dst.executemany(f'INSERT INTO {table} VALUES ({placeholders})',
                                    src.execute(f'SELECT * FROM {table}'))
                    # Compare complete small football tables, not only counts.
                    order = {'matches': 'id', 'season_sync': 'competition, season',
                             'prediction_archive': 'match_id'}[table]
                    original = list(src.execute(f'SELECT * FROM {table} ORDER BY {order}'))
                    copied = list(dst.execute(f'SELECT * FROM {table} ORDER BY {order}'))
                    if original != copied:
                        raise RuntimeError(f'Export verification failed for {table}')
                    counts[table] = len(copied)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Export integrity check failed')
            dst.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            dst.execute('PRAGMA journal_mode=DELETE')
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=DB_PATH)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    counts = export_football_data(args.source, args.output)
    print(f'Verified export: {counts}; size={Path(args.output).stat().st_size} bytes')
    print('Original database unchanged. Price history was not copied; feeds will refill current odds.')
