# live_feed.py
"""
Streams live Polymarket price data for every configured league, each
in its own background thread inside Ballr's single process — same
single-process pattern proven in Oddscope's final architecture.
Called once from api.py at startup via start_all_league_feeds().
"""
import websocket
import json
import ssl
import certifi
import sqlite3
import time
import threading
from datetime import datetime, timezone

from polymarket_client import find_all_matches, is_moneyline_market, is_draw_market, ensure_price_schema
from leagues import LEAGUES, get_league_config

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SSL_OPT = {"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": certifi.where()}
REFRESH_INTERVAL_SECONDS = 15 * 60


class LeagueFeed:
    def __init__(self, league_key, db_path="ballr.db"):
        self.league_key = league_key
        self.db_path = db_path
        self.state_lock = threading.Lock()
        self.current_asset_ids = []
        self.current_ws = None

    def build_tracked_asset_ids(self):
        all_matches = find_all_matches(self.league_key, include_closed=False)
        asset_ids = []
        matched_questions = []
        for m in all_matches:
            title = m['event_title']
            question = m['question']
            if not is_moneyline_market(self.league_key, title, question) and \
               not is_draw_market(self.league_key, title, question):
                continue
            asset_ids.extend(m['clob_token_ids'])
            matched_questions.append(question)
        return asset_ids, matched_questions

    def _refresh_loop(self):
        while True:
            time.sleep(REFRESH_INTERVAL_SECONDS)
            new_asset_ids, new_questions = self.build_tracked_asset_ids()
            with self.state_lock:
                changed = set(new_asset_ids) != set(self.current_asset_ids)
                if changed:
                    self.current_asset_ids = new_asset_ids
                    if self.current_ws is not None:
                        try:
                            self.current_ws.send(json.dumps({"type": "market", "assets_ids": self.current_asset_ids}))
                            print(f"[{self.league_key}] Pushed updated subscription "
                                  f"({len(new_asset_ids)} assets, {len(new_questions)} markets).")
                        except Exception as e:
                            print(f"[{self.league_key}] Couldn't push updated subscription: {e}")
                else:
                    print(f"[{self.league_key}] Checked for new markets — no changes.")

    def _on_message(self, ws, message):
        data = json.loads(message)
        if isinstance(data, dict) and data.get("event_type") == "price_change":
            timestamp = datetime.now(timezone.utc).isoformat()
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            for change in data.get("price_changes", []):
                asset_id = change.get("asset_id")
                best_bid = change.get("best_bid")
                best_ask = change.get("best_ask")
                if best_bid is None or best_ask is None:
                    mid_price = float(change.get("price"))
                else:
                    mid_price = (float(best_bid) + float(best_ask)) / 2
                side = change.get("side")
                cursor.execute(
                    "INSERT INTO price_updates (asset_id, price, side, timestamp, league) VALUES (?, ?, ?, ?, ?)",
                    (asset_id, mid_price, side, timestamp, self.league_key)
                )
            conn.commit()
            conn.close()

    def _on_error(self, ws, error):
        print(f"[{self.league_key}] Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        print(f"[{self.league_key}] Connection closed — reconnecting in 5 seconds...")
        time.sleep(5)
        self._start_connection()

    def _on_open(self, ws):
        self.current_ws = ws
        print(f"[{self.league_key}] Connected — subscribing to market updates")
        with self.state_lock:
            ws.send(json.dumps({"type": "market", "assets_ids": self.current_asset_ids}))

    def _start_connection(self):
        with self.state_lock:
            if not self.current_asset_ids:
                print(f"[{self.league_key}] No open markets to track right now.")
                return
        ws = websocket.WebSocketApp(
            WS_URL, on_open=self._on_open, on_message=self._on_message,
            on_error=self._on_error, on_close=self._on_close
        )
        ws.run_forever(sslopt=SSL_OPT)

    def start(self):
        initial_asset_ids, initial_questions = self.build_tracked_asset_ids()
        self.current_asset_ids = initial_asset_ids
        display_name = get_league_config(self.league_key)["display_name"]
        print(f"[{self.league_key}] Tracking {len(self.current_asset_ids)} asset IDs "
              f"across {len(initial_questions)} markets ({display_name})")

        refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        refresh_thread.start()
        self._start_connection()


def start_all_league_feeds(db_path="ballr.db"):
    """
    Called once by api.py at startup. Creates one LeagueFeed per
    configured league and starts each in its own background thread.
    Leagues with no open match-level markets yet (e.g. Champions
    League before its fixtures exist) simply track 0 assets — this is
    expected and handled gracefully, not an error.
    """
    ensure_price_schema(db_path)

    feeds = {}
    for league_key in LEAGUES:
        feed = LeagueFeed(league_key, db_path=db_path)
        feeds[league_key] = feed
        thread = threading.Thread(target=feed.start, daemon=True, name=f"feed-{league_key}")
        thread.start()

    return feeds