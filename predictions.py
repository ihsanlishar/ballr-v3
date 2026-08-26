# predictions.py
"""
Connects upcoming fixtures to current Elo ratings and the Monte Carlo
simulator to produce real Win/Draw/Win predictions. Handles teams with
no historical rating (newly promoted clubs) by falling back to the
default starting rating rather than crashing.
"""
from elo_engine import build_current_ratings, INITIAL_RATING
from match_simulator import simulate_match
from fixtures import get_upcoming_fixtures


def get_predictions(league_key, limit=20):
    """
    Returns a list of upcoming fixtures, each enriched with Ballr's
    predicted Win/Draw/Win probabilities.
    """
    ratings = build_current_ratings(league_key)
    fixtures = get_upcoming_fixtures(league_key, limit=limit)

    predictions = []

    for f in fixtures:
        home_team = f["home_team"]
        away_team = f["away_team"]

        # Teams with no rating yet (newly promoted, or first-ever
        # match in our dataset) start at the same INITIAL_RATING every
        # other team started at — a fair, neutral assumption rather
        # than a crash or a silently wrong default.
        home_rating = ratings.get(home_team, INITIAL_RATING)
        away_rating = ratings.get(away_team, INITIAL_RATING)

        home_pct, draw_pct, away_pct = simulate_match(home_rating, away_rating)

        predictions.append({
            "home_team": home_team,
            "away_team": away_team,
            "utc_date": f["utc_date"],
            "home_pct": home_pct,
            "draw_pct": draw_pct,
            "away_pct": away_pct,
            "home_rating_known": home_team in ratings,
            "away_rating_known": away_team in ratings,
        })

    return predictions


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