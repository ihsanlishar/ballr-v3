"""Recoverable Polymarket connections, one per league in the single web worker."""
import json
import logging
import math
import ssl
import threading
from datetime import datetime, timezone
import certifi
import websocket
from config import DB_PATH
from operations import report
from storage_guard import write_quotes
from leagues import LEAGUES
from polymarket_client import find_all_matches, is_moneyline_market, is_draw_market, ensure_price_schema

logger = logging.getLogger(__name__)
WS_URL = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'
SSL_OPT = {'cert_reqs': ssl.CERT_REQUIRED, 'ca_certs': certifi.where()}
REFRESH_INTERVAL_SECONDS = 15 * 60


class LeagueFeed:
    def __init__(self, league_key, db_path=DB_PATH):
        self.league_key = league_key
        self.db_path = db_path
        self.state_lock = threading.Lock()
        self.current_asset_ids = []
        self.current_ws = None
        self.stopped = threading.Event()
        self.wake = threading.Event()

    def build_tracked_asset_ids(self):
        ids, questions = set(), []
        for match in find_all_matches(self.league_key, include_closed=False):
            if match.get('closed'):
                continue
            title, question = match['event_title'], match['question']
            if (is_moneyline_market(self.league_key, title, question) or
                    is_draw_market(self.league_key, title, question)):
                ids.update(match['clob_token_ids'])
                questions.append(question)
        return sorted(ids), questions

    def _refresh_assets(self):
        # Exceptions leave the existing subscription intact.
        ids, _ = self.build_tracked_asset_ids()
        report(f'discovery:{self.league_key}')
        if not ids:
            report(f'feed:{self.league_key}', 'idle')
        with self.state_lock:
            changed = set(ids) != set(self.current_asset_ids)
            self.current_asset_ids = ids
            ws = self.current_ws if changed else None
        # Reconnect for a fresh book snapshot and the entire new subscription.
        # Never close while holding the lock: on_close acquires it too.
        if ws is not None:
            ws.close()
        if changed:
            self.wake.set()

    def _refresh_loop(self):
        while not self.stopped.is_set():
            try:
                self._refresh_assets()
                delay = REFRESH_INTERVAL_SECONDS
            except Exception:
                report(f'discovery:{self.league_key}', 'error')
                logger.exception('Market discovery failed for %s; retrying', self.league_key)
                delay = 60
            self.stopped.wait(delay)

    def _on_message(self, ws, message):
        if message in ('PING', 'PONG'):
            if message == 'PING':
                ws.send('PONG')
            return
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return
        events = data if isinstance(data, list) else [data]
        rows = []
        timestamp = datetime.now(timezone.utc).isoformat()
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get('event_type') == 'price_change':
                changes = event.get('price_changes') or []
            elif event.get('event_type') == 'book':
                try:
                    bids = [float(level['price']) for level in event.get('bids', [])]
                    asks = [float(level['price']) for level in event.get('asks', [])]
                    changes = [{'asset_id': event.get('asset_id'),
                                'best_bid': max(bids), 'best_ask': min(asks)}]
                except (ValueError, TypeError, KeyError):
                    continue
            else:
                continue
            for change in changes:
                try:
                    bid, ask = float(change['best_bid']), float(change['best_ask'])
                    asset = change['asset_id']
                    if not asset or not math.isfinite(bid + ask) or not 0 <= bid <= ask <= 1:
                        continue
                except (ValueError, TypeError, KeyError):
                    continue
                rows.append((asset, (bid + ask) / 2, change.get('side'), timestamp, self.league_key))
        if rows:
            report(f'feed:{self.league_key}')
            write_quotes(rows, self.db_path)

    def _on_error(self, ws, error):
        report(f'feed:{self.league_key}', 'error')
        logger.warning('Market connection error for %s: %s', self.league_key, error)

    def _on_close(self, ws, close_status_code, close_msg):
        with self.state_lock:
            if self.current_ws is ws:
                self.current_ws = None
        report(f'feed:{self.league_key}', 'disconnected')
        # The outer loop owns reconnects; callbacks must never recurse.

    def _on_open(self, ws):
        report(f'feed:{self.league_key}', 'connected')
        with self.state_lock:
            ws.send(json.dumps({'type': 'market', 'assets_ids': self.current_asset_ids}))

    def _start_connection(self):
        with self.state_lock:
            if not self.current_asset_ids:
                return
            ws = websocket.WebSocketApp(
                WS_URL, on_open=self._on_open, on_message=self._on_message,
                on_error=self._on_error, on_close=self._on_close)
            self.current_ws = ws
        try:
            ws.run_forever(sslopt=SSL_OPT, ping_interval=20, ping_timeout=10)
        finally:
            self._on_close(ws, None, None)

    def start(self):
        threading.Thread(target=self._refresh_loop, daemon=True,
                         name=f'discovery-{self.league_key}').start()
        while not self.stopped.is_set():
            self.wake.clear()
            try:
                self._start_connection()
            except Exception:
                logger.exception('Market stream failed for %s; reconnecting', self.league_key)
            self.wake.wait(5)


def start_all_league_feeds(db_path=DB_PATH):
    ensure_price_schema(db_path)
    feeds = {}
    for league in LEAGUES:
        feed = LeagueFeed(league, db_path)
        feeds[league] = feed
        threading.Thread(target=feed.start, daemon=True, name=f'feed-{league}').start()
    return feeds
