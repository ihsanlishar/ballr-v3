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
            for table in ('matches', 'market_prices', 'season_sync', 'prediction_archive'):
                conn.execute(f'DELETE FROM {table}')
        from overload import RequestBudget
        import predictions
        import storage_guard
        predictions._context_cache.clear()
        predictions._prediction_cache.clear()
        storage_guard._cached.clear()
        storage_guard._cooldown.clear()
        api.request_budget = RequestBudget()
        api.backup_budget = RequestBudget(capacity=1, per_second=1/3600, concurrent=1)
        self.client = api.app.test_client()

    def seed(self, league='premier_league'):
        store_matches([fixture()], league, config.current_season())

    def test_quote_cap_and_existing_outcome_updates(self):
        from storage_guard import write_quotes
        timestamp = datetime.now(timezone.utc).isoformat()
        with patch.dict(os.environ, {'BALLR_MAX_QUOTES': '100'}):
            write_quotes([(str(i), .5, 'BUY', timestamp, 'premier_league') for i in range(150)])
            write_quotes([('0', .7, 'BUY', timestamp, 'premier_league'), ('extra', .6, 'BUY', timestamp, 'premier_league')])
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM market_prices').fetchone()[0], 100)
            self.assertEqual(conn.execute("SELECT price FROM market_prices WHERE asset_id='0'").fetchone()[0], .7)

    def test_disk_pressure_pauses_optional_writes(self):
        from storage_guard import write_quotes, storage_status
        from collections import namedtuple
        usage = namedtuple('usage', 'total used free')(5*1024**3, 5*1024**3-1024, 1024)
        with patch('storage_guard.shutil.disk_usage', return_value=usage), self.assertLogs('storage_guard', level='WARNING'):
            self.assertTrue(storage_status(force=True)['optional_writes_paused'])
            self.assertFalse(write_quotes([('1', .5, 'BUY', datetime.now(timezone.utc).isoformat(), 'premier_league')]))
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM market_prices').fetchone()[0], 0)

    def test_write_error_backs_off_without_crashing_feed(self):
        import sqlite3
        from storage_guard import write_quotes, storage_status
        with patch('database.connect', side_effect=sqlite3.OperationalError('database or disk is full')), self.assertLogs('storage_guard', level='ERROR'):
            self.assertFalse(write_quotes([('1', .5, 'BUY', datetime.now(timezone.utc).isoformat(), 'premier_league')]))
        self.assertTrue(storage_status(force=True)['optional_writes_paused'])

    def test_saved_fixtures_expire_and_are_labelled(self):
        self.seed()
        store_matches([fixture(2, 'TIMED', '2099-09-01T12:00:00Z', None, None)], 'premier_league', config.current_season())
        with patch('fixtures.get_season_matches', side_effect=RuntimeError('offline')):
            saved = get_upcoming_fixtures('premier_league')
            self.assertTrue(saved[0]['fixtures_stale'])
            with closing(database.connect()) as conn, conn:
                conn.execute("UPDATE season_sync SET synced_at=?", ((datetime.now(timezone.utc)-timedelta(hours=25)).isoformat(),))
            with self.assertRaises(readiness.DataUnavailable):
                get_upcoming_fixtures('premier_league')

    def test_prediction_cache_is_stable_and_invalidates_corrected_results(self):
        import predictions
        self.seed()
        future = {'home_team': 'Home FC', 'away_team': 'Away FC', 'utc_date': '2099-09-01T12:00:00Z'}
        with patch('predictions.simulate_match', wraps=predictions.simulate_match) as simulate:
            context = predictions.model_context('premier_league')
            first = predictions.cached_prediction('premier_league', future, context)
            second = predictions.cached_prediction('premier_league', future, context)
            self.assertEqual(first, second)
            self.assertEqual(simulate.call_count, 1)
            predictions._prediction_cache.clear()
            self.assertEqual(first, predictions.cached_prediction('premier_league', future, context))
            store_matches([fixture(home=0, away=5)], 'premier_league', config.current_season())
            updated = predictions.model_context('premier_league')
            self.assertNotEqual(context[2], updated[2])
            changed = predictions.cached_prediction('premier_league', future, updated)
            self.assertNotEqual(first['home_rating'], changed['home_rating'])

    def test_cache_entry_count_is_bounded(self):
        cache = RequestCache(60, max_entries=3)
        for i in range(100):
            self.assertEqual(cache.get(i, lambda: i), i)
        self.assertEqual(len(cache.entries), 3)
        self.assertEqual(len(cache.locks), 64)

    def test_overload_leaves_health_available_and_releases_slot(self):
        from overload import RequestBudget
        api.request_budget = RequestBudget(capacity=2, per_second=0, concurrent=1)
        self.assertTrue(api.request_budget.acquire())
        result = self.client.get('/api/performance')
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.headers['Retry-After'], '5')
        self.assertEqual(self.client.get('/health').status_code, 200)
        api.request_budget.release()
        self.assertEqual(self.client.get('/api/performance').status_code, 200)
        self.assertTrue(api.request_budget.slots.acquire(blocking=False))
        api.request_budget.slots.release()

    def test_archive_is_immutable_and_scores_only_after_results(self):
        from performance import capture_predictions, performance_report
        self.seed()
        kickoff = (datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
        store_matches([fixture(2, 'TIMED', kickoff, None, None)], 'premier_league', config.current_season())
        self.assertEqual(capture_predictions(), 1)
        with closing(database.connect()) as conn:
            original = conn.execute('SELECT * FROM prediction_archive').fetchone()
        self.assertLess(datetime.fromisoformat(original[5]), datetime.fromisoformat(kickoff))
        self.assertEqual(capture_predictions(), 0)
        self.assertEqual(performance_report('premier_league')['evaluated'], 0)
        store_matches([fixture(2, 'FINISHED', kickoff, 2, 1)], 'premier_league', config.current_season())
        result = performance_report('premier_league')
        self.assertEqual(result['evaluated'], 1)
        self.assertAlmostEqual(result['brier'], (original[7]-1)**2+original[8]**2+original[9]**2)
        self.assertEqual(sum(b['count'] for b in result['calibration']), 3)
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute('SELECT * FROM prediction_archive').fetchone(), original)
        store_matches([fixture(2, 'FINISHED', (datetime.now(timezone.utc)+timedelta(hours=3)).isoformat(), 2, 1)], 'premier_league', config.current_season())
        result = performance_report('premier_league')
        self.assertEqual(result['evaluated'], 0)
        self.assertEqual(result['excluded'], 1)

    def test_archive_skips_past_matches_and_stale_history(self):
        from performance import capture_predictions
        self.seed()
        past = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        future = (datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
        store_matches([fixture(2, 'TIMED', past, None, None)], 'premier_league', config.current_season())
        self.assertEqual(capture_predictions(), 0)
        store_matches([fixture(3, 'TIMED', future, None, None)], 'premier_league', config.current_season())
        with closing(database.connect()) as conn, conn:
            conn.execute('UPDATE season_sync SET synced_at=?', ((datetime.now(timezone.utc)-timedelta(hours=3)).isoformat(),))
        self.assertEqual(capture_predictions(), 0)

    def test_backup_authentication_integrity_and_restore(self):
        from performance import capture_predictions, performance_report
        from scripts.operations_check import verify_backup
        self.seed()
        future = (datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
        store_matches([fixture(2, 'TIMED', future, None, None)], 'premier_league', config.current_season())
        capture_predictions()
        token = 'test-secret-' * 4
        with patch.dict(os.environ, {'BALLR_BACKUP_TOKEN': token}):
            self.assertEqual(self.client.get('/api/admin/backup').status_code, 401)
            self.assertEqual(self.client.get('/api/admin/backup', headers={'Authorization': 'Bearer wrong'}).status_code, 401)
            response = self.client.get('/api/admin/backup', headers={'Authorization': 'Bearer '+token})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with tempfile.TemporaryDirectory() as folder:
                restored = Path(folder)/'restored.db'
                restored.write_bytes(response.data)
                self.assertEqual(verify_backup(restored)['prediction_archive'], 1)
                database.initialize(str(restored))
                self.assertTrue(readiness.league_status('premier_league', str(restored))['ready'])
                self.assertEqual(performance_report('premier_league', str(restored))['recorded'], 1)
            self.assertEqual(self.client.get('/api/admin/backup', headers={'Authorization': 'Bearer '+token}).status_code, 429)

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
            rows = conn.execute('SELECT asset_id, price FROM market_prices ORDER BY asset_id').fetchall()
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
            query = conn.execute("EXPLAIN QUERY PLAN SELECT price FROM market_prices WHERE asset_id = 'x'").fetchall()
            self.assertIn('PRIMARY KEY', str(query))

    def test_pruning_preserves_recent_prices(self):
        from db_maintenance import prune_old_price_updates
        now = datetime.now(timezone.utc)
        with closing(database.connect()) as conn, conn:
            conn.executemany("INSERT INTO market_prices (asset_id, timestamp, price, league) VALUES (?, ?, 0.5, 'premier_league')",
                [('old', (now - timedelta(hours=49)).isoformat()), ('new', now.isoformat())])
        self.assertEqual(prune_old_price_updates(), 1)
        with closing(database.connect()) as conn:
            self.assertEqual(conn.execute('SELECT asset_id FROM market_prices').fetchall(), [('new',)])

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
             patch('db_maintenance.start_db_maintenance') as maintenance, \
             patch('performance.start_prediction_archive') as archive:
            api.start_background_workers()
            api.start_background_workers()
            init.assert_called_once_with(config.DB_PATH)
            feeds.assert_called_once_with(config.DB_PATH)
            results.assert_called_once_with(config.DB_PATH)
            maintenance.assert_called_once_with(config.DB_PATH)
            archive.assert_called_once_with(config.DB_PATH)

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


    def test_repeated_price_updates_keep_one_row_per_outcome(self):
        import json
        feed = LeagueFeed('premier_league')
        with closing(database.connect()) as conn:
            for index in range(1000):
                feed._on_message(Mock(), json.dumps({'event_type': 'price_change', 'price_changes': [
                    {'asset_id': 'same', 'best_bid': '0.2', 'best_ask': '0.4'}]}))
            feed._on_message(Mock(), json.dumps({'event_type': 'price_change', 'price_changes': [
                {'asset_id': 'same', 'best_bid': '0.6', 'best_ask': '0.8'}]}))
            rows = conn.execute('SELECT asset_id, price FROM market_prices').fetchall()
        self.assertEqual(rows, [('same', .7)])
        from polymarket_client import get_latest_market_price_with_timestamp
        self.assertEqual(get_latest_market_price_with_timestamp('same')[0], .7)

    def test_export_preserves_football_and_omits_tick_history(self):
        from recover_storage import export_football_data
        self.seed()
        with closing(database.connect()) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS price_updates (id INTEGER PRIMARY KEY, price REAL)')
            conn.execute('INSERT INTO price_updates (price) VALUES (0.5)')
        with tempfile.TemporaryDirectory() as folder:
            output = str(Path(folder) / 'compact.db')
            counts = export_football_data(config.DB_PATH, output)
            self.assertEqual(counts, {'matches': 1, 'season_sync': 1, 'prediction_archive': 0})
            import sqlite3
            with closing(sqlite3.connect(output)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM market_prices').fetchone()[0], 0)
                self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='price_updates'").fetchone())
            with self.assertRaises(ValueError):
                export_football_data(config.DB_PATH, output)
        with closing(database.connect()) as conn, conn:
            self.assertGreater(conn.execute('SELECT COUNT(*) FROM price_updates').fetchone()[0], 0)
            conn.execute('DROP TABLE price_updates')

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
