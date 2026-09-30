"""CI helper: monitor status or download and verify an essential-data backup."""
import argparse
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

MAX_BACKUP_BYTES = 25 * 1024**2


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Redirect refused; configure the final HTTPS app URL')


def fetch(path, token=None, maximum=1024*1024):
    base = os.environ['BALLR_URL'].rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('BALLR_URL must be the HTTPS origin of your app')
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    with build_opener(NoRedirects()).open(Request(base + path, headers=headers), timeout=90) as response:
        content = response.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError('Response exceeded size limit')
    return content


def verify_backup(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Backup failed integrity check')
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'matches', 'season_sync', 'prediction_archive'} <= tables or 'price_updates' in tables:
            raise ValueError('Unexpected backup schema')
        counts = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ('matches', 'season_sync', 'prediction_archive')}
        if not counts['matches'] or not counts['season_sync']:
            raise ValueError('Refusing to accept an empty football backup')
        return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['monitor', 'backup'])
    args = parser.parse_args()
    if args.mode == 'monitor':
        status = json.loads(fetch('/status'))
        if status.get('status') != 'ok':
            raise RuntimeError('Service needs attention; check /status and Railway logs')
        print('Service status is healthy')
    else:
        token = os.environ['BALLR_BACKUP_TOKEN']
        if len(token) < 32:
            raise ValueError('Backup token is missing or too short')
        output = Path('ballr-backup.db')
        with output.open('xb') as destination:
            destination.write(fetch('/api/admin/backup', token, MAX_BACKUP_BYTES))
        print('Verified essential-data backup:', verify_backup(output))


if __name__ == '__main__':
    main()
