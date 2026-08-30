# backtest_scoreline.py
"""
Backtests the Poisson/Dixon-Coles scoreline model (score_projection)
against real historical results — same no-lookahead discipline as
backtest.py: for each match tested, goal profiles and Elo ratings are
built using ONLY matches that happened strictly before it.
"""
import sqlite3
from elo_engine import INITIAL_RATING, update_ratings
from match_simulator import build_goal_profiles, score_projection


def get_chronological_matches(competition, db_path="ballr.db"):
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
    return [r for r in rows if r[2] is not None and r[3] is not None]


def run_scoreline_backtest(competition, warmup_matches=100, db_path="ballr.db"):
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
            past_matches = all_matches[:i]

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

            try:
                profiles = build_goal_profiles(competition, ratings, db_path=":memory-not-supported")
            except Exception:
                # build_goal_profiles takes a db_path, not a connection —
                # patch it to accept an existing connection for this
                # backtest-only use case.
                profiles = _build_goal_profiles_from_conn(conn, competition, ratings)

            conn.close()

            projection = score_projection(
                ratings[home], ratings[away], profiles, home, away
            )

            predicted_home_goals = round(projection["home_xg"])
            predicted_away_goals = round(projection["away_xg"])

            exact_score_correct = (predicted_home_goals == home_score and predicted_away_goals == away_score)

            # Find the probability the model assigned to the ACTUAL scoreline
            actual_key_prob = None
            for row_idx, row in enumerate(projection["score_matrix"]):
                for col_idx, prob in enumerate(row):
                    if row_idx == min(home_score, projection["max_display_goals"]) and \
                       col_idx == min(away_score, projection["max_display_goals"]):
                        actual_key_prob = prob

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


def _build_goal_profiles_from_conn(conn, competition, ratings):
    """Fallback: reimplements build_goal_profiles' query against an
    already-open in-memory connection instead of a file path, since
    the original function only accepts a db_path string."""
    import match_simulator as ms
    cursor = conn.cursor()
    cursor.execute("""
        SELECT home_team, away_team, home_score, away_score, utc_date
        FROM matches
        WHERE competition = ? AND status = 'FINISHED'
        ORDER BY utc_date DESC
    """, (competition,))
    rows = cursor.fetchall()

    # Reuse the exact same logic as build_goal_profiles by temporarily
    # monkeypatching sqlite3.connect just for this call — simplest way
    # to avoid duplicating ~80 lines of averaging logic here.
    import unittest.mock as mock
    class FakeConn:
        def cursor(self_inner):
            class FakeCursor:
                def execute(self_c, *a, **k): pass
                def fetchall(self_c): return rows
            return FakeCursor()
        def close(self_inner): pass

    with mock.patch("sqlite3.connect", return_value=FakeConn()):
        return ms.build_goal_profiles(competition, ratings, db_path="unused")


def summarize_scoreline(results, competition):
    total = len(results)
    exact_correct = sum(1 for r in results if r["exact_score_correct"])
    avg_prob_on_actual = sum(r["prob_assigned_to_actual"] or 0 for r in results) / total if total else 0

    print("=" * 60)
    print(f" {competition} — scoreline backtest ({total} matches)")
    print("=" * 60)
    print(f"Exact score correct: {exact_correct}/{total} ({exact_correct/total*100:.1f}%)")
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