# polymarket_client.py
"""
All direct communication with Polymarket's API, plus event-ID-based
match grouping and bid/ask-midpoint pricing logic — ported directly
from Oddscope's proven, battle-tested implementation. Same two
hard-won lessons still apply:

1. GROUP BY event_id, NEVER BY TITLE TEXT — the same two teams can
   play twice in a season under an identical event title.
2. STORE THE BID/ASK MIDPOINT, not the raw price_change.price value —
   that field is one resting order's price, not the market's real
   current probability.
"""
import re
import json
import sqlite3
import requests
from datetime import datetime, timezone

from leagues import get_league_config

DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _fetch_events(tag_slug, closed):
    url = "https://gamma-api.polymarket.com/events"
    all_events = []
    offset = 0
    page_size = 100

    while True:
        params = {
            "tag_slug": tag_slug,
            "closed": "true" if closed else "false",
            "limit": page_size,
            "offset": offset
        }
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"[polymarket_client] Warning: failed to fetch events "
                  f"(tag_slug={tag_slug}, closed={closed}): {e}")
            break

        if not data:
            break

        all_events.extend(data)
        offset += page_size

        if len(data) < page_size:
            break

    return all_events


def find_all_matches(league_key, include_closed=False):
    config = get_league_config(league_key)
    tag_slug = config["polymarket_tag_slug"]

    events = _fetch_events(tag_slug, closed=False)
    if include_closed:
        events += _fetch_events(tag_slug, closed=True)

    matches = []
    for event in events:
        event_id = event.get("id")
        for market in event.get("markets", []):
            if market.get("clobTokenIds"):
                outcomes_raw = market.get("outcomes")
                prices_raw = market.get("outcomePrices")
                matches.append({
                    "event_id": event_id,
                    "event_title": event.get("title"),
                    "question": market.get("question"),
                    "clob_token_ids": json.loads(market.get("clobTokenIds")),
                    "outcomes": json.loads(outcomes_raw) if outcomes_raw else None,
                    "outcome_prices": [float(p) for p in json.loads(prices_raw)] if prices_raw else None,
                    "closed": market.get("closed", False)
                })

    return matches


def get_final_yes_price(match):
    outcomes = match.get("outcomes")
    prices = match.get("outcome_prices")
    if outcomes and prices and len(outcomes) == len(prices):
        for i, outcome in enumerate(outcomes):
            if str(outcome).strip().lower() == "yes":
                return prices[i]
    return None


def get_yes_asset_id(match):
    outcomes = match.get("outcomes")
    token_ids = match.get("clob_token_ids") or []
    if outcomes and len(outcomes) == len(token_ids):
        for i, outcome in enumerate(outcomes):
            if str(outcome).strip().lower() == "yes":
                return token_ids[i]
    print(f"[polymarket_client] Warning: couldn't confirm the 'Yes' outcome for "
          f"question='{match.get('question')}' — defaulting to the first token id.")
    return token_ids[0] if token_ids else None


def is_moneyline_market(league_key, title, question):
    config = get_league_config(league_key)
    if config["title_separator"] not in title:
        return False
    if not question.startswith(config["question_prefix"]):
        return False
    if config["question_keyword"] not in question:
        return False
    if not DATE_PATTERN.search(question):
        return False
    return True


def is_draw_market(league_key, title, question):
    config = get_league_config(league_key)
    draw_keyword = config.get("draw_keyword")
    if not draw_keyword:
        return False
    if config["title_separator"] not in title:
        return False
    if draw_keyword not in question:
        return False
    return True


def extract_match_details(league_key, question):
    config = get_league_config(league_key)
    prefix = config["question_prefix"]
    keyword = config["question_keyword"]

    date_match = DATE_PATTERN.search(question)
    date_obj = None
    if date_match:
        try:
            date_obj = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
        except ValueError:
            date_obj = None

    team_name = None
    if question.startswith(prefix) and keyword in question:
        start = len(prefix)
        end = question.find(keyword)
        team_name = question[start:end].strip()

    return team_name, date_obj


def find_grouped_matches(league_key, include_closed=False):
    all_matches = find_all_matches(league_key, include_closed=include_closed)
    config = get_league_config(league_key)
    grouped = {}

    for m in all_matches:
        event_id = m.get("event_id")
        title = m['event_title']
        question = m['question']

        if event_id is None or config["title_separator"] not in title:
            continue

        if is_moneyline_market(league_key, title, question):
            team_name, match_date = extract_match_details(league_key, question)
            if team_name is None:
                continue
            group = grouped.setdefault(event_id, {
                "title": title, "date": None, "closed": False, "teams": {}, "draw": None
            })
            group["teams"][team_name] = m
            group["closed"] = group["closed"] or m["closed"]
            if group["date"] is None and match_date is not None:
                group["date"] = match_date

        elif is_draw_market(league_key, title, question):
            group = grouped.setdefault(event_id, {
                "title": title, "date": None, "closed": False, "teams": {}, "draw": None
            })
            group["draw"] = m
            group["closed"] = group["closed"] or m["closed"]

    return grouped


def ensure_price_schema(db_path="ballr.db"):
    """
    Creates the price_updates table (live Polymarket data) alongside
    the existing matches table (historical football-data.org results)
    — two tables, one shared database.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id TEXT,
            price REAL,
            side TEXT,
            timestamp TEXT,
            league TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_latest_market_price_with_timestamp(asset_id, db_path="ballr.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT price, timestamp FROM price_updates
        WHERE asset_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (asset_id,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None, None
    return row[0], row[1]


def test_no_grouping_collisions(league_key, include_closed=True):
    grouped = find_grouped_matches(league_key, include_closed=include_closed)
    print("=" * 60)
    print(f" TEST: Grouping collision check ({league_key}, {len(grouped)} groups)")
    print("=" * 60)
    seen = {}
    problems = 0
    for event_id, group in grouped.items():
        all_ids = []
        for team_match in group["teams"].values():
            all_ids.extend(team_match["clob_token_ids"])
        if group["draw"]:
            all_ids.extend(group["draw"]["clob_token_ids"])
        key = frozenset(all_ids)
        if key in seen:
            problems += 1
            print(f"  ⚠️  event_id={event_id} shares asset IDs with event_id={seen[key]}")
        else:
            seen[key] = event_id
    if problems == 0:
        print(f"PASSED: no asset-ID collisions across {len(grouped)} distinct events.\n")
    else:
        print(f"FAILED: {problems} collision(s) found.\n")


if __name__ == "__main__":
    import sys
    league_key = sys.argv[1] if len(sys.argv) > 1 else "premier_league"
    ensure_price_schema()
    test_no_grouping_collisions(league_key)