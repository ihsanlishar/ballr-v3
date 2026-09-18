# backtest_scoreline.py
"""
Backtests the Poisson/Dixon-Coles scoreline model (score_projection)
against real historical results — same no-lookahead discipline as
backtest.py: for each match tested, goal profiles and Elo ratings are
built using ONLY matches that happened strictly before it.
"""
from database import connect
from config import DB_PATH
import sqlite3
from elo_engine import INITIAL_RATING, update_ratings
from match_simulator import build_goal_profiles, score_projection, MAX_MODEL_GOALS


def get_chronological_matches(competition, db_path=DB_PATH):
    conn = connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT home_team, away_team, home_score, away_score, utc_date
        FROM matches
        WHERE competition = ? AND status = 'FINISHED'
        ORDER BY utc_date ASC
    """, (competition,))
    rows = cursor.fetchall()
    conn.close()
    return [r for r in rows if r[2] is not None and r[3] is not None]


def run_scoreline_backtest(competition, warmup_matches=100, db_path=DB_PATH):
    """
    warmup_matches is higher than backtest.py's (100 vs 60) because
    build_goal_profiles needs real match history per team to compute
    recency-weighted scoring/conceding rates — too early in the season
    and most teams would still be on the flat default_stats fallback,
    which isn't a fair test of the model's real behavior.
    """
    all_matches = get_chronological_matches(competition, db_path)
    ratings = {}
    results = []

    for i, (home, away, home_score, away_score, date) in enumerate(all_matches):
        ratings.setdefault(home, INITIAL_RATING)
        ratings.setdefault(away, INITIAL_RATING)

        if i >= warmup_matches:
            # Build goal profiles using ONLY matches before this one —
            # write a temporary in-memory snapshot rather than querying
            # the full ballr.db, which would leak future matches.
            past_matches = [match for match in all_matches[:i] if match[4] < date]

            conn = sqlite3.connect(":memory:")
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE matches (
                    competition TEXT, home_team TEXT, away_team TEXT,
                    home_score INTEGER, away_score INTEGER,
                    utc_date TEXT, status TEXT
                )
            """)
            for h, a, hs, as_, d in past_matches:
                cursor.execute(
                    "INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, 'FINISHED')",
                    (competition, h, a, hs, as_, d)
                )
            conn.commit()

            profiles = build_goal_profiles(competition, ratings, connection=conn)

            conn.close()

            projection = score_projection(
                ratings[home], ratings[away], profiles, home, away, max_goals=MAX_MODEL_GOALS
            )

            likely = projection['top_scorelines'][0]
            exact_score_correct = (likely['home_goals'] == home_score and likely['away_goals'] == away_score)
            # Outcomes beyond the model grid have no represented mass; never
            # substitute the probability of a different score at the boundary.
            actual_key_prob = (projection['score_matrix'][home_score][away_score]
                               if home_score <= MAX_MODEL_GOALS and away_score <= MAX_MODEL_GOALS
                               else 0.0)

            results.append({
                "home": home, "away": away,
                "actual_home": home_score, "actual_away": away_score,
                "predicted_home_xg": projection["home_xg"],
                "predicted_away_xg": projection["away_xg"],
                "exact_score_correct": exact_score_correct,
                "prob_assigned_to_actual": actual_key_prob,
            })

        new_home, new_away = update_ratings(ratings[home], ratings[away], home_score, away_score)
        ratings[home] = new_home
        ratings[away] = new_away

    return results


def summarize_scoreline(results, competition):
    total = len(results)
    exact_correct = sum(1 for r in results if r["exact_score_correct"])
    avg_prob_on_actual = sum(r["prob_assigned_to_actual"] or 0 for r in results) / total if total else 0

    print("=" * 60)
    print(f" {competition} — scoreline backtest ({total} matches)")
    print("=" * 60)
    print(f"Exact score correct: {exact_correct}/{total} ({(exact_correct/total*100 if total else 0):.1f}%)")
    print(f"Avg probability the model assigned to the ACTUAL scoreline: {avg_prob_on_actual:.2f}%")
    print()

    print("Sample of 10 predictions vs. actual:")
    for r in results[:10]:
        print(f"  {r['home']:<22} {r['actual_home']}-{r['actual_away']} {r['away']:<22} "
              f"(predicted xG: {r['predicted_home_xg']}-{r['predicted_away_xg']}, "
              f"model gave actual score {r['prob_assigned_to_actual']}%)")
    print()


if __name__ == "__main__":
    for competition in ["premier_league", "la_liga"]:
        results = run_scoreline_backtest(competition)
        summarize_scoreline(results, competition)