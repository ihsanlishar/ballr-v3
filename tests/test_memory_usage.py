"""Provider payload trimming preserves the fields used by the application."""
import json
import unittest
from unittest.mock import Mock, patch

import football_client
import polymarket_client
from request_cache import RequestCache


class MemoryTests(unittest.TestCase):
    def test_football_cache_retains_scores_and_fixture_fields(self):
        essential = {'id': 1, 'utcDate': '2026-10-21T18:00:00Z', 'status': 'TIMED',
                     'homeTeam': {'name': 'Home'}, 'awayTeam': {'name': 'Away'},
                     'score': {'fullTime': {'home': None, 'away': None}}}
        raw = dict(essential, referees=[{'name': 'unused'}] * 100,
                   homeTeam={'name': 'Home', 'crest': 'unused'})
        response = Mock()
        response.json.return_value = {'matches': [raw]}
        football_client._cache.clear()
        with patch.dict('os.environ', {'FOOTBALL_DATA_API_KEY': 'test'}), \
             patch.object(football_client.requests, 'get', return_value=response) as get, \
             patch.object(football_client.time, 'sleep'):
            actual = football_client.get_season_matches('PL', 2026)
            self.assertEqual(actual, [essential])
            self.assertEqual(football_client.get_season_matches('PL', 2026), actual)
            self.assertEqual(get.call_count, 1)
            self.assertLess(len(json.dumps(actual)), len(json.dumps([raw])))
        football_client._cache.clear()

    def test_market_compaction_preserves_parsed_matches(self):
        market = {'clobTokenIds': '["a", "b"]', 'outcomes': '["Yes", "No"]',
                  'outcomePrices': '["0.4", "0.6"]', 'question': 'Home wins?',
                  'closed': False, 'description': 'unused' * 1000}
        raw = [{'id': '1', 'title': 'Home vs Away', 'markets': [market],
                'description': 'unused' * 1000}]
        response = Mock()
        response.json.return_value = raw
        with patch.object(polymarket_client.requests, 'get', return_value=response):
            compact = polymarket_client._fetch_events_uncached('test', False)
        with patch.object(polymarket_client, '_fetch_events', return_value=raw):
            expected = polymarket_client.find_all_matches('premier_league')
        with patch.object(polymarket_client, '_fetch_events', return_value=compact):
            self.assertEqual(polymarket_client.find_all_matches('premier_league'), expected)
        self.assertLess(len(json.dumps(compact)), len(json.dumps(raw)))

    def test_pruning_preserves_live_cache_and_error_backoff(self):
        cache = RequestCache(ttl=60)
        with patch('request_cache.time.monotonic', return_value=100):
            cache._store('expired', (99, object(), False))
            cache._store('live', (160, 'kept', False))
            cache._store('failure', (130, None, True))
            cache.prune_expired()
            self.assertNotIn('expired', cache.entries)
            self.assertEqual(cache.get('live', lambda: self.fail('Unnecessary fetch')), 'kept')
            with self.assertRaises(RuntimeError):
                cache.get('failure', lambda: self.fail('Backoff lost'))
