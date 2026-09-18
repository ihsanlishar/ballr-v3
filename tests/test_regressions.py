"""Regression checks with isolated SQLite files and no external API requests."""
import os
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

_SANDBOX = tempfile.TemporaryDirectory()
os.environ['BALLR_DB_PATH'] = str(Path(_SANDBOX.name) / 'test.db')
os.environ['FOOTBALL_DATA_API_KEY'] = 'test-only'

import api
import config
import database
import db_bootstrap
import football_client
import readiness
from fixtures import get_upcoming_fixtures
from ingest_last_season import store_matches
from live_feed import LeagueFeed
from local_market import find_market_odds
from request_cache import RequestCache
from results_refresh import fetch_and_upsert_season
from match_simulator import build_goal_profiles, score_projection
from backtest_scoreline import run_scoreline_backtest


def fixture(identifier=1, status='FINISHED', date='2026-08-01T12:00:00Z', home=2, away=1):
    return {'id': identifier, 'status': status, 'utcDate': date,
            'homeTeam': {'name': 'Home FC'}, 'awayTeam': {'name': 'Away FC'},
            'score': {'fullTime': {'home': home, 'away': away}}}


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected network call'))
        self.network.start()
        self.addCleanup(self.network.stop)
        database.initialize()
        with closing(database.connect()) as conn, conn:
            for table in ('matches', 'price_updates', 'season_sync'):
                conn.execute(f'DELETE FROM {table}')
        self.client = api.app.test_client()

    def seed(self, league='premier_league'):
        store_matches([fixture()], league, config.current_season())

    def test_season_rollover(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config.current_season(datetime(2026, 6, 30)), 2025)
            self.assertEqual(config.current_season(datetime(2026, 7, 1)), 2026)
        with patch.dict(os.environ, {'FOOTBALL_SEASON': '2024'}):
            self.assertEqual(config.current_season(), 2024)

    def test_timed_and_scheduled_sorted_before_limit(self):
        matches = [fixture(1, 'TIMED', '2099-09-02T12:00:00Z'),
                   fixture(2, 'SCHEDULED', '2099-09-01T12:00:00Z'),
                   fixture(3, 'FINISHED', '2099-08-01T12:00:00Z'),
                   fixture(4, 'TIMED', '2000-01-01T12:00:00Z')]
        with patch('fixtures.get_season_matches', return_value=matches):
            self.assertEqual(len(get_upcoming_fixtures('premier_league', None)), 2)
            self.assertEqual(get_upcoming_fixtures('premier_league', 1)[0]['utc_date'], '2099-09-01T12:00:00Z')

    def test_current_season_refresh_updates_existing_result(self):
        with patch('results_refresh.get_season_matches', return_value=[fixture(status='TIMED', home=None, away=None)]) as fetch:
            self.assertEqual(fetch_and_upsert_season('PL', 'premier_league'), (1, 0))
            fetch.assert_called_once_with('PL', config.current_season())
        with patch('results_refresh.get_season_matches', return_value=[fixture()]):
            self.assertEqual(fetch_and_upsert_season('PL', 'premier_league'), (1, 1))
            self.assertEqual(fetch_and_upsert_season('PL', 'premier_league'), (1, 0))
        self.assertTrue(readiness.league_status('premier_league')['ready'])

    def test_failed_store_rolls_back_and_does_not_mark_ready(self):
        bad = fixture(2)
        bad.pop('id')
        with self.assertRaises(ValueError):
            store_matches([fixture(), bad], 'premier_league', config.current_season())
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM matches').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM season_sync').fetchone()[0], 0)

    def test_bootstrap_checks_each_league_and_retries_failure(self):
        store_matches([fixture()], 'premier_league', config.current_season() - 1)
        with self.assertLogs('db_bootstrap', level='ERROR'), patch('db_bootstrap.fetch_and_store', side_effect=RuntimeError('offline')) as fetch:
            db_bootstrap.bootstrap_if_needed()
            db_bootstrap.bootstrap_if_needed()
            self.assertEqual(fetch.call_count, 4)
            self.assertEqual({call.args[1] for call in fetch.call_args_list}, {'la_liga', 'champions_league'})

    def test_cache_reuses_success_and_backs_off_failure(self):
        cache = RequestCache(ttl=60)
        fetch = Mock(return_value=[1])
        self.assertEqual(cache.get('one', fetch), [1])
        self.assertEqual(cache.get('one', fetch), [1])
        fetch.assert_called_once()
        fail = Mock(side_effect=ValueError('offline'))
        with self.assertRaises(ValueError): cache.get('two', fail)
        with self.assertRaises(RuntimeError): cache.get('two', fail)
        fail.assert_called_once()

    def test_football_requests_have_timeout_pacing_and_shared_cache(self):
        response = Mock()
        response.json.return_value = {'matches': [fixture()]}
        with patch.object(football_client, '_cache', RequestCache(120)), \
             patch.object(football_client, '_last_request', float('-inf')), \
             patch('football_client.requests.get', return_value=response) as get, \
             patch('football_client.time.sleep') as sleep:
            football_client.get_season_matches('PL')
            football_client.get_season_matches('PL')
            football_client.get_season_matches('PD')
            self.assertEqual(get.call_count, 2)
            self.assertEqual(get.call_args.kwargs['timeout'], config.HTTP_TIMEOUT)
            self.assertGreater(sleep.call_args.args[0], 0)

    def test_empty_feed_can_connect_after_discovery(self):
        feed = LeagueFeed('premier_league')
        with patch('live_feed.websocket.WebSocketApp') as factory:
            feed._start_connection()
            factory.assert_not_called()
            with patch.object(feed, 'build_tracked_asset_ids', return_value=(['asset'], [])):
                feed._refresh_assets()
            self.assertTrue(feed.wake.is_set())
            feed._start_connection()
            factory.return_value.run_forever.assert_called_once()

    def test_discovery_failure_retains_existing_assets(self):
        feed = LeagueFeed('premier_league')
        feed.current_asset_ids = ['old']
        with patch.object(feed, 'build_tracked_asset_ids', side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): feed._refresh_assets()
        self.assertEqual(feed.current_asset_ids, ['old'])

    def test_subscription_change_closes_for_fresh_snapshot(self):
        feed = LeagueFeed('premier_league')
        feed.current_asset_ids = ['old']
        feed.current_ws = Mock()
        with patch.object(feed, 'build_tracked_asset_ids', return_value=(['new'], [])):
            feed._refresh_assets()
        feed.current_ws.close.assert_called_once()
        socket = Mock()
        feed._on_open(socket)
        self.assertIn('new', socket.send.call_args.args[0])

    def test_close_callback_does_not_recurse(self):
        feed = LeagueFeed('premier_league')
        socket = Mock()
        feed.current_ws = socket
        with patch.object(feed, '_start_connection') as reconnect:
            feed._on_close(socket, None, None)
            reconnect.assert_not_called()
        self.assertIsNone(feed.current_ws)

    def test_book_snapshots_and_valid_midpoints_are_stored(self):
        import json
        feed = LeagueFeed('premier_league')
        feed._on_message(Mock(), 'PONG')
        feed._on_message(Mock(), 'bad JSON')
        feed._on_message(Mock(), json.dumps([{'event_type': 'book', 'asset_id': 'a',
            'bids': [{'price': '0.3'}, {'price': '0.4'}], 'asks': [{'price': '0.6'}]}]))
        feed._on_message(Mock(), json.dumps({'event_type': 'price_change', 'price_changes': [
            {'asset_id': 'b', 'price': '0.99'},
            {'asset_id': 'c', 'best_bid': 'nan', 'best_ask': '0.8'},
            {'asset_id': 'd', 'best_bid': '0.8', 'best_ask': '0.7'},
            {'asset_id': 'e', 'best_bid': '0.1', 'best_ask': '0.3'}]}))
        with closing(database.connect()) as conn:
            rows = conn.execute('SELECT asset_id, price FROM price_updates ORDER BY id').fetchall()
        self.assertEqual(rows, [('a', 0.5), ('e', 0.2)])

    def test_market_rejects_stale_or_incomplete_quotes(self):
        def market(asset): return {'outcomes': ['Yes'], 'clob_token_ids': [asset], 'closed': False}
        group = {'one': {'date': None, 'teams': {'Home FC': market('h'), 'Away FC': market('a')}, 'draw': market('d')}}
        now = datetime.now(timezone.utc).isoformat()
        with patch('local_market.get_latest_market_price_with_timestamp', side_effect=[(.4, now), (.3, now), (.3, now)]):
            self.assertEqual(find_market_odds(group, 'Home FC', 'Away FC')['draw_pct'], 30)
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with patch('local_market.get_latest_market_price_with_timestamp', return_value=(.3, old)):
            self.assertIsNone(find_market_odds(group, 'Home FC', 'Away FC'))
        group['one']['draw'] = None
        with patch('local_market.get_latest_market_price_with_timestamp', return_value=(.3, now)):
            self.assertIsNone(find_market_odds(group, 'Home FC', 'Away FC'))

    def test_indexes_and_wal(self):
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            query = conn.execute("EXPLAIN QUERY PLAN SELECT price FROM price_updates WHERE asset_id = 'x' ORDER BY id DESC LIMIT 1").fetchall()
            self.assertIn('price_asset_latest', str(query))

    def test_pruning_preserves_recent_prices(self):
        from db_maintenance import prune_old_price_updates
        now = datetime.now(timezone.utc)
        with closing(database.connect()) as conn, conn:
            conn.executemany('INSERT INTO price_updates (asset_id, timestamp) VALUES (?, ?)',
                [('old', (now - timedelta(hours=49)).isoformat()), ('new', now.isoformat())])
        self.assertEqual(prune_old_price_updates(), 1)
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute('SELECT asset_id FROM price_updates').fetchall(), [('new',)])

    def test_readiness_requires_all_leagues_and_fresh_sync(self):
        self.assertEqual(self.client.get('/health').status_code, 200)
        self.assertEqual(self.client.get('/ready').status_code, 503)
        for index, league in enumerate(('premier_league', 'la_liga', 'champions_league')):
            store_matches([fixture(index + 1)], league, config.current_season())
        self.assertEqual(self.client.get('/ready').status_code, 200)
        with closing(database.connect()) as conn, conn:
            conn.execute("UPDATE season_sync SET synced_at = '2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.client.get('/ready').status_code, 503)
        with self.assertLogs('api', level='WARNING'):
            self.assertEqual(self.client.get('/api/predictions?league=premier_league').status_code, 503)

    def test_api_validation_and_generic_failure(self):
        self.assertEqual(self.client.get('/api/predictions?league=bad').status_code, 400)
        self.assertEqual(self.client.get('/api/match?league=premier_league').status_code, 400)
        self.assertEqual(self.client.get('/api/match?league=premier_league&home=a&away=b&date=bad').status_code, 400)
        self.seed()
        with self.assertLogs('api', level='ERROR'), patch('api.get_predictions', side_effect=ValueError('private details')):
            result = self.client.get('/api/predictions?league=premier_league')
            self.assertEqual(result.status_code, 500)
            self.assertNotIn('private details', result.get_data(as_text=True))

    def test_api_predictions_and_real_fixture_detail(self):
        self.seed()
        future = {'home_team': 'Home FC', 'away_team': 'Away FC', 'utc_date': '2099-09-01T12:00:00Z'}
        with patch('predictions.get_upcoming_fixtures', return_value=[future]), \
             patch('predictions.get_grouped_market_matches', return_value={}):
            result = self.client.get('/api/predictions?league=premier_league')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(result.json['predictions']), 1)
            result = self.client.get('/api/match?league=premier_league&home=Home+FC&away=Away+FC')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json['utc_date'], future['utc_date'])
            self.assertEqual(self.client.get('/api/match?league=premier_league&home=Fake&away=Away+FC').status_code, 404)

    def test_worker_start_is_idempotent_and_initializes_configured_path(self):
        with patch.object(api, '_workers_started', False), patch('api.initialize') as init, \
             patch('live_feed.start_all_league_feeds') as feeds, \
             patch('results_refresh.start_results_refresh') as results, \
             patch('db_maintenance.start_db_maintenance') as maintenance:
            api.start_background_workers()
            api.start_background_workers()
            init.assert_called_once_with(config.DB_PATH)
            feeds.assert_called_once_with(config.DB_PATH)
            results.assert_called_once_with(config.DB_PATH)
            maintenance.assert_called_once_with(config.DB_PATH)

    def test_zero_goal_history_does_not_divide_by_zero(self):
        store_matches([fixture(home=0, away=0)], 'premier_league', config.current_season())
        profiles = build_goal_profiles('premier_league')
        projection = score_projection(1500, 1500, profiles, 'Home FC', 'Away FC')
        self.assertGreater(projection['home_xg'], 0)

    def test_backtest_uses_actual_high_score_without_creating_file(self):
        matches = [fixture(), fixture(2, date='2026-08-02T12:00:00Z', home=6, away=0)]
        store_matches(matches, 'premier_league', config.current_season())
        result = run_scoreline_backtest('premier_league', warmup_matches=1)
        from elo_engine import update_ratings
        with closing(database.connect()) as conn, conn:
            conn.execute('DELETE FROM matches WHERE id = 2')
        ratings = dict(zip(('Home FC', 'Away FC'), update_ratings(1500, 1500, 2, 1)))
        profiles = build_goal_profiles('premier_league', ratings)
        expected = score_projection(ratings['Home FC'], ratings['Away FC'], profiles, 'Home FC', 'Away FC', max_goals=8)
        self.assertEqual(result[0]['prob_assigned_to_actual'], expected['score_matrix'][6][0])
        self.assertNotEqual(result[0]['prob_assigned_to_actual'], expected['score_matrix'][5][0])
        self.assertEqual(len(result), 1)
        self.assertIsNotNone(result[0]['prob_assigned_to_actual'])
        self.assertFalse(Path(':memory-not-supported').exists())
        self.assertIn('Home FC', profiles['teams'])


    def test_closed_event_lookup_does_not_contaminate_open_cache(self):
        import polymarket_client as markets
        open_events = [{'id': 'open', 'markets': []}]
        closed_events = [{'id': 'closed', 'markets': []}]
        with patch.object(markets, '_fetch_events', side_effect=lambda slug, closed: closed_events if closed else open_events):
            markets.find_all_matches('premier_league', include_closed=True)
        self.assertEqual(len(open_events), 1)

    def test_unverified_yes_token_is_not_used(self):
        from polymarket_client import get_yes_asset_id
        self.assertIsNone(get_yes_asset_id({'clob_token_ids': ['unverified']}))
        self.assertEqual(get_yes_asset_id({'outcomes': ['No', 'Yes'], 'clob_token_ids': ['n', 'y']}), 'y')

    def test_cache_coalesces_concurrent_requests(self):
        from concurrent.futures import ThreadPoolExecutor
        import time
        cache = RequestCache(60)
        fetch = Mock(side_effect=lambda: (time.sleep(.02), 'value')[1])
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(lambda _: cache.get('one', fetch), range(4))), ['value'] * 4)
        fetch.assert_called_once()

    def test_gunicorn_binds_railway_port_with_one_worker(self):
        import runpy
        with patch.dict(os.environ, {'PORT': '8765'}):
            settings = runpy.run_path('gunicorn.conf.py')
        self.assertEqual(settings['bind'], '0.0.0.0:8765')
        self.assertEqual(settings['workers'], 1)
        self.assertFalse(settings['preload_app'])

    def test_fresh_wsgi_start_and_restart_preserve_data(self):
        import subprocess
        import sys
        script = r"""
import os, time
from unittest.mock import patch
import config
from database import connect

def provider(code, season):
    return [{'id': {'PL': 1, 'PD': 2, 'CL': 3}[code] * 10000 + season,
             'status': 'FINISHED', 'utcDate': '2026-08-01T12:00:00Z',
             'homeTeam': {'name': 'Home FC'}, 'awayTeam': {'name': 'Away FC'},
             'score': {'fullTime': {'home': 2, 'away': 1}}}]

with patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')), \
     patch('live_feed.start_all_league_feeds'), \
     patch('results_refresh.get_season_matches', side_effect=provider), \
     patch('ingest_last_season.get_season_matches', side_effect=provider) as history:
    import wsgi
    client = wsgi.app.test_client()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        conn = connect()
        count = conn.execute('SELECT COUNT(*) FROM matches').fetchone()[0]
        conn.close()
        if client.get('/ready').status_code == 200 and count == 6:
            break
        time.sleep(.01)
    assert client.get('/health').status_code == 200
    assert client.get('/ready').status_code == 200
    assert count == 6, count
    assert history.call_count == int(os.environ['EXPECTED_HISTORY_CALLS']), history.call_count
print('BOOT_OK')
"""
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, BALLR_DB_PATH=str(Path(folder) / 'nested' / 'ballr.db'))
            for expected in ('3', '0'):
                env['EXPECTED_HISTORY_CALLS'] = expected
                result = subprocess.run([sys.executable, '-c', script], env=env,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertIn('BOOT_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
