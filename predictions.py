# predictions.py
"""
Connects upcoming fixtures to current Elo ratings, the Monte Carlo
simulator, the scoreline model, and live Polymarket market odds for
the same fixture — all read locally now, no separate Oddscope process.
"""
import hashlib
import json
from contextlib import closing
from config import DB_PATH
from database import connect
from elo_engine import ratings_from_rows, INITIAL_RATING
from request_cache import RequestCache
from readiness import league_status

MODEL_VERSION = 'elo-mc-v1'
_context_cache = RequestCache(ttl=86400, max_entries=12)
_prediction_cache = RequestCache(ttl=86400, max_entries=512)


def model_context(league_key, db_path=DB_PATH):
    # Hash actual results: ordinary syncs do not invalidate the model, but
    # corrected scores do. Both models read the same SQLite snapshot.
    with closing(connect(db_path)) as conn:
        conn.execute('BEGIN')
        rows = conn.execute("""SELECT home_team, away_team, home_score, away_score, utc_date
            FROM matches WHERE competition=? AND status='FINISHED'
            AND home_score IS NOT NULL AND away_score IS NOT NULL
            ORDER BY utc_date, id""", (league_key,)).fetchall()
        revision = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
        def build():
            ratings = ratings_from_rows(rows)
            profiles = build_goal_profiles(league_key, ratings, connection=conn)
            outcomes = [sum(int((r[2] > r[3], r[2] == r[3], r[2] < r[3])[i])
                            for r in rows) for i in range(3)]
            baseline = [n / len(rows) for n in outcomes] if rows else [1/3] * 3
            return ratings, profiles, revision, baseline
        return _context_cache.get((str(db_path), league_key, revision), build)

from match_simulator import build_goal_profiles, score_projection, simulate_match
from fixtures import get_upcoming_fixtures
from local_market import get_grouped_market_matches, find_market_odds


def build_prediction(league_key, fixture, ratings, goal_profiles, grouped_market_matches):
    home_team = fixture["home_team"]
    away_team = fixture["away_team"]

    home_rating = ratings.get(home_team, INITIAL_RATING)
    away_rating = ratings.get(away_team, INITIAL_RATING)
    seed = int.from_bytes(hashlib.sha256(
        f'{MODEL_VERSION}:{home_rating!r}:{away_rating!r}'.encode()).digest()[:8], 'big')
    home_pct, draw_pct, away_pct = simulate_match(home_rating, away_rating, seed=seed)
    projection = score_projection(
        home_rating, away_rating, goal_profiles, home_team, away_team
    )
    default_stats = goal_profiles["default_stats"]
    home_stats = goal_profiles["teams"].get(home_team, default_stats)
    away_stats = goal_profiles["teams"].get(away_team, default_stats)

    market = find_market_odds(grouped_market_matches, home_team, away_team, fixture.get("utc_date"))

    return {
        **fixture,
        "league": league_key,
        "model_version": MODEL_VERSION,
        "home_pct": home_pct,
        "draw_pct": draw_pct,
        "away_pct": away_pct,
        "home_rating_known": home_team in ratings,
        "away_rating_known": away_team in ratings,
        "home_rating": round(home_rating),
        "away_rating": round(away_rating),
        "home_form": home_stats["form_string"] or "—",
        "away_form": away_stats["form_string"] or "—",
        "market": market,
        **projection,
    }


def cached_prediction(league_key, fixture, context, db_path=DB_PATH):
    ratings, profiles, revision, _ = context
    key = (str(db_path), league_key, MODEL_VERSION, revision,
           fixture['home_team'], fixture['away_team'])
    core = _prediction_cache.get(key, lambda: build_prediction(
        league_key, {'home_team': fixture['home_team'], 'away_team': fixture['away_team']},
        ratings, profiles, {}))
    return {**core, **fixture}


def _predictions_for(league_key, fixtures):
    context = model_context(league_key)
    grouped = get_grouped_market_matches(league_key)
    status = league_status(league_key)
    output = []
    for fixture in fixtures:
        prediction = cached_prediction(league_key, fixture, context)
        prediction['market'] = find_market_odds(grouped, fixture['home_team'],
                                                fixture['away_team'], fixture.get('utc_date'))
        prediction['data_status'] = {**status, 'stale': status['stale'] or fixture.get('fixtures_stale', False)}
        output.append(prediction)
    return output


def get_match_prediction(league_key, home_team, away_team, utc_date=None):
    fixtures = get_upcoming_fixtures(league_key, limit=None)
    fixture = next((item for item in fixtures if item['home_team'] == home_team
                    and item['away_team'] == away_team
                    and (utc_date is None or item['utc_date'] == utc_date)), None)
    if fixture is None:
        raise LookupError('Upcoming match not found')
    return _predictions_for(league_key, [fixture])[0]


def get_predictions(league_key, limit=20):
    return _predictions_for(league_key, get_upcoming_fixtures(league_key, limit=limit))


if __name__ == "__main__":
    for league_key in ["premier_league", "la_liga", "champions_league"]:
        print("=" * 70)
        print(f" {league_key} — predictions for upcoming fixtures")
        print("=" * 70)
        preds = get_predictions(league_key, limit=5)
        if not preds:
            print("  No upcoming fixtures found.\n")
            continue
        for p in preds:
            flag = "  ⚠️  limited historical data" if not p["home_rating_known"] or not p["away_rating_known"] else ""
            market_str = "no market data yet"
            if p["market"]:
                m = p["market"]
                market_str = f"Market: H{m['home_pct']}/D{m.get('draw_pct')}/A{m['away_pct']}"
            print(f"  {p['utc_date']}  {p['home_team']} vs {p['away_team']}{flag}")
            print(f"     Ballr:  Home: {p['home_pct']}%  Draw: {p['draw_pct']}%  Away: {p['away_pct']}%")
            print(f"     {market_str}")
        print()