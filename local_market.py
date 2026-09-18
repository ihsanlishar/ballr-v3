# local_market.py
"""
Local, in-process replacement for the earlier oddscope_bridge.py's
HTTP calls. Reads live Polymarket odds directly from the shared
ballr.db, using polymarket_client's proven event-ID grouping and
bid/ask-midpoint pricing — no network hop, no separate process.
"""
from config import DB_PATH
from datetime import datetime, timezone
import math
from config import MARKET_MAX_AGE_SECONDS

from polymarket_client import find_grouped_matches, get_yes_asset_id, get_latest_market_price_with_timestamp


def get_grouped_market_matches(league_key):
    """
    Fetches Polymarket's current grouped matches for a league ONCE —
    call this once per prediction request, then pass the result into
    find_market_odds() per fixture, rather than re-fetching per match.
    """
    try:
        return find_grouped_matches(league_key, include_closed=False)
    except Exception as e:
        print(f"[local_market] Warning: failed to fetch Polymarket data for {league_key}: {e}")
        return {}


def find_market_odds(grouped_matches, home_team, away_team, utc_date=None, db_path=DB_PATH):
    """
    Finds live market odds for a specific fixture within an
    already-fetched grouped_matches dict. Returns None if no priced
    market is found — callers must handle this gracefully.
    """
    target_date = None
    if utc_date:
        try:
            target_date = datetime.fromisoformat(utc_date.replace("Z", "+00:00")).date()
        except ValueError:
            target_date = None

    for event_id, group in grouped_matches.items():
        if target_date and group["date"] != target_date:
            continue

        teams = group["teams"]
        if home_team not in teams or away_team not in teams:
            continue

        home_match = teams[home_team]
        away_match = teams[away_team]
        draw_match = group.get("draw")

        def resolve_price(match):
            if match is None:
                return None
            asset_id = get_yes_asset_id(match)
            if asset_id is None or match.get("closed"):
                return None
            price, timestamp = get_latest_market_price_with_timestamp(asset_id, db_path)
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds()
                if not 0 <= age <= MARKET_MAX_AGE_SECONDS or not math.isfinite(price) or not 0 <= price <= 1:
                    return None
            except (TypeError, ValueError):
                return None
            return price

        home_price = resolve_price(home_match)
        away_price = resolve_price(away_match)
        draw_price = resolve_price(draw_match)

        if home_price is None or away_price is None or draw_price is None:
            continue

        total = home_price + draw_price + away_price
        if total == 0:
            continue

        return {
            "home_pct": round(home_price / total * 100, 1),
            "draw_pct": round(draw_price / total * 100, 1) if draw_price is not None else None,
            "away_pct": round(away_price / total * 100, 1),
        }

    return None