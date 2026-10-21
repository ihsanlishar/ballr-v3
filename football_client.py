"""Cached season requests with shared pacing for football-data.org."""
import os
import threading
import time
import requests
from config import HTTP_TIMEOUT, current_season
from request_cache import RequestCache

_cache = RequestCache(ttl=120)
_request_lock = threading.Lock()
_last_request = float('-inf')


def get_season_matches(comp_code, season=None):
    season = current_season() if season is None else season

    def fetch():
        global _last_request
        key = os.environ.get('FOOTBALL_DATA_API_KEY')
        if not key:
            raise RuntimeError('FOOTBALL_DATA_API_KEY is not configured.')
        if not _request_lock.acquire(timeout=20):
            raise RuntimeError('Football data refresh is busy.')
        try:
            time.sleep(max(0, 6.2 - (time.monotonic() - _last_request)))
            _last_request = time.monotonic()
            response = requests.get(
                f'https://api.football-data.org/v4/competitions/{comp_code}/matches',
                headers={'X-Auth-Token': key}, params={'season': season},
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            matches = response.json()['matches']
            if not isinstance(matches, list):
                raise ValueError('Invalid football-data response')
            # Retain only fields consumed by fixtures and result ingestion.
            return [{
                'id': match.get('id'), 'utcDate': match.get('utcDate'),
                'status': match.get('status'),
                'homeTeam': {'name': (match.get('homeTeam') or {}).get('name')},
                'awayTeam': {'name': (match.get('awayTeam') or {}).get('name')},
                'score': {'fullTime': {
                    key: (((match.get('score') or {}).get('fullTime') or {}).get(key))
                    for key in ('home', 'away')
                }},
            } for match in matches]
        finally:
            _request_lock.release()

    return _cache.get((comp_code, season), fetch)
