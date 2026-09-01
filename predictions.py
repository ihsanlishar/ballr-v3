# predictions.py
"""
Connects upcoming fixtures to current Elo ratings, the Monte Carlo
simulator, the scoreline model, and live Polymarket market odds for
the same fixture — all read locally now, no separate Oddscope process.
"""
from elo_engine import build_current_ratings, INITIAL_RATING
from match_simulator import build_goal_profiles, score_projection, simulate_match
from fixtures import get_upcoming_fixtures
from local_market import get_grouped_market_matches, find_market_odds


def build_prediction(league_key, fixture, ratings, goal_profiles, grouped_market_matches):
    home_team = fixture["home_team"]
    away_team = fixture["away_team"]

    home_rating = ratings.get(home_team, INITIAL_RATING)
    away_rating = ratings.get(away_team, INITIAL_RATING)
    home_pct, draw_pct, away_pct = simulate_match(home_rating, away_rating)
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


def get_match_prediction(league_key, home_team, away_team, utc_date=None):
    ratings = build_current_ratings(league_key)
    goal_profiles = build_goal_profiles(league_key, ratings)
    grouped_market_matches = get_grouped_market_matches(league_key)
    return build_prediction(league_key, {
        "home_team": home_team,
        "away_team": away_team,
        "utc_date": utc_date,
    }, ratings, goal_profiles, grouped_market_matches)


def get_predictions(league_key, limit=20):
    ratings = build_current_ratings(league_key)
    goal_profiles = build_goal_profiles(league_key, ratings)
    fixtures = get_upcoming_fixtures(league_key, limit=limit)
    grouped_market_matches = get_grouped_market_matches(league_key)

    return [
        build_prediction(league_key, fixture, ratings, goal_profiles, grouped_market_matches)
        for fixture in fixtures
    ]


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