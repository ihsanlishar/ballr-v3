# predictions.py
"""
Connects upcoming fixtures to current Elo ratings and the Monte Carlo
simulator to produce real Win/Draw/Win predictions. Handles teams with
no historical rating (newly promoted clubs) by falling back to the
default starting rating rather than crashing.
"""
from elo_engine import build_current_ratings, INITIAL_RATING
from match_simulator import build_goal_profiles, score_projection, simulate_match
from fixtures import get_upcoming_fixtures


def build_prediction(league_key, fixture, ratings, goal_profiles):
    """Enrich one fixture with outcome, scoreline, and team-form data."""
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
        **projection,
    }


def get_match_prediction(league_key, home_team, away_team, utc_date=None):
    """Build a prediction for one fixture without refetching the schedule."""
    ratings = build_current_ratings(league_key)
    goal_profiles = build_goal_profiles(league_key, ratings)
    return build_prediction(league_key, {
        "home_team": home_team,
        "away_team": away_team,
        "utc_date": utc_date,
    }, ratings, goal_profiles)


def get_predictions(league_key, limit=20):
    """
    Returns a list of upcoming fixtures, each enriched with Ballr's
    predicted Win/Draw/Win probabilities.
    """
    ratings = build_current_ratings(league_key)
    goal_profiles = build_goal_profiles(league_key, ratings)
    fixtures = get_upcoming_fixtures(league_key, limit=limit)

    return [build_prediction(league_key, fixture, ratings, goal_profiles) for fixture in fixtures]


if __name__ == "__main__":
    for league_key in ["premier_league", "la_liga", "champions_league"]:
        print("=" * 70)
        print(f" {league_key} — predictions for upcoming fixtures")
        print("=" * 70)

        preds = get_predictions(league_key, limit=5)

        if not preds:
            print("  No upcoming fixtures found (expected if the season/competition hasn't started).\n")
            continue

        for p in preds:
            flag = ""
            if not p["home_rating_known"] or not p["away_rating_known"]:
                flag = "  ⚠️  one team has no historical rating — using default"

            print(f"  {p['utc_date']}  {p['home_team']} vs {p['away_team']}{flag}")
            print(f"     Home: {p['home_pct']}%  Draw: {p['draw_pct']}%  Away: {p['away_pct']}%")
        print()
