# local_market.py
"""
Local, in-process replacement for the earlier oddscope_bridge.py's
HTTP calls. Reads live Polymarket odds directly from the shared
ballr.db, using polymarket_client's proven event-ID grouping and
bid/ask-midpoint pricing — no network hop, no separate process.
"""
from datetime import datetime

from polymarket_client import find_grouped_matches, get_yes_asset_id, get_latest_market_price_with_timestamp, get_final_yes_price


def get_grouped_market_matches(league_key):
    """
    Fetches Polymarket's current grouped matches for a league ONCE —
    call this once per prediction request, then pass the result into
    find_market_odds() per fixture, rather than re-fetching per match.
    """
    try:
        return find_grouped_matches(league_key, include_closed=True)
    except Exception as e:
        print(f"[local_market] Warning: failed to fetch Polymarket data for {league_key}: {e}")
        return {}


def find_market_odds(grouped_matches, home_team, away_team, utc_date=None, db_path="ballr.db"):
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
        if target_date and group["date"] and group["date"] != target_date:
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
            if match.get("closed"):
                return get_final_yes_price(match)
            price, _ = get_latest_market_price_with_timestamp(asset_id, db_path)
            return price

        home_price = resolve_price(home_match)
        away_price = resolve_price(away_match)
        draw_price = resolve_price(draw_match)

        if home_price is None or away_price is None:
            continue

        total = home_price + (draw_price or 0) + away_price
        if total == 0:
            continue

        return {
            "home_pct": round(home_price / total * 100, 1),
            "draw_pct": round(draw_price / total * 100, 1) if draw_price is not None else None,
            "away_pct": round(away_price / total * 100, 1),
        }

    return None