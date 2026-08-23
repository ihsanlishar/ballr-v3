# elo_engine.py
"""
Elo rating engine for league play. Adapted from Ballr V2's World Cup
approach with two key additions league play needs and knockout
tournaments didn't: home-field advantage, and a running rating that
updates match-by-match through a season rather than being computed
fresh per knockout round.
"""
import sqlite3

INITIAL_RATING = 1500
K_FACTOR = 20  # how much one match result shifts a team's rating
HOME_ADVANTAGE = 65  # Elo points added to the home team's rating before computing expected outcome


def expected_score(rating_a, rating_b):
    """
    Standard Elo expected-score formula: probability team A beats team B,
    based purely on the rating gap (this does NOT yet include draws —
    that's handled separately in the Monte Carlo layer, since Elo's
    classic formula is a binary win/lose model).
    """
    return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))


def update_ratings(rating_home, rating_away, home_score, away_score):
    """
    Given a completed match's actual score, returns updated (home, away)
    ratings. Home advantage is applied only when calculating the
    *expected* outcome, not stored as a permanent rating boost.
    """
    adjusted_home_rating = rating_home + HOME_ADVANTAGE

    expected_home = expected_score(adjusted_home_rating, rating_away)
    expected_away = 1 - expected_home

    if home_score > away_score:
        actual_home, actual_away = 1.0, 0.0
    elif home_score < away_score:
        actual_home, actual_away = 0.0, 1.0
    else:
        actual_home, actual_away = 0.5, 0.5

    new_rating_home = rating_home + K_FACTOR * (actual_home - expected_home)
    new_rating_away = rating_away + K_FACTOR * (actual_away - expected_away)

    return new_rating_home, new_rating_away


def build_current_ratings(competition, db_path="ballr.db"):
    """
    Replays every FINISHED match for a competition, in chronological
    order, updating Elo ratings match by match. Returns a dict of
    {team_name: current_rating} reflecting each team's strength as of
    the most recent match in the dataset.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT home_team, away_team, home_score, away_score, utc_date
        FROM matches
        WHERE competition = ? AND status = 'FINISHED'
        ORDER BY utc_date ASC
    """, (competition,))
    rows = cursor.fetchall()
    conn.close()

    ratings = {}

    for home_team, away_team, home_score, away_score, utc_date in rows:
        if home_score is None or away_score is None:
            continue

        ratings.setdefault(home_team, INITIAL_RATING)
        ratings.setdefault(away_team, INITIAL_RATING)

        new_home, new_away = update_ratings(
            ratings[home_team], ratings[away_team], home_score, away_score
        )
        ratings[home_team] = new_home
        ratings[away_team] = new_away

    return ratings


if __name__ == "__main__":
    for competition in ["premier_league", "la_liga", "champions_league"]:
        print("=" * 60)
        print(f" {competition} — final Elo ratings after last season")
        print("=" * 60)
        ratings = build_current_ratings(competition)
        for team, rating in sorted(ratings.items(), key=lambda x: -x[1]):
            print(f"  {team:<35} {rating:.1f}")
        print()