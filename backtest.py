# backtest.py
"""
Backtests the Elo + Monte Carlo model against real historical results.
CRITICAL RULE: for each match tested, ratings are built using ONLY
matches that occurred strictly before it — never the season's final
ratings. Testing with lookahead data would silently make the model
look far more accurate than it actually is.
"""
import sqlite3
from elo_engine import INITIAL_RATING, update_ratings
from match_simulator import simulate_match

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

def actual_result(home_score, away_score):
    if home_score > away_score:
        return "HOME"
    elif home_score < away_score:
        return "AWAY"
    return "DRAW"

def run_backtest(competition, warmup_matches=60, n_simulations=5000):
    """
    warmup_matches: how many early-season matches to skip testing on,
    since a team's Elo rating is meaningless before it's played any
    games.
    """
    matches = get_chronological_matches(competition)
    ratings = {}

    results = []

    for i, (home, away, home_score, away_score, date) in enumerate(matches):
        ratings.setdefault(home, INITIAL_RATING)
        ratings.setdefault(away, INITIAL_RATING)

        if i >= warmup_matches:
            home_pct, draw_pct, away_pct = simulate_match(
                ratings[home], ratings[away], n_simulations=n_simulations
            )
            predicted_probs = {"HOME": home_pct, "DRAW": draw_pct, "AWAY": away_pct}
            predicted_outcome = max(predicted_probs, key=predicted_probs.get)
            actual = actual_result(home_score, away_score)

            results.append({
                "date": date,
                "home": home, "away": away,
                "predicted": predicted_outcome,
                "actual": actual,
                "correct": predicted_outcome == actual,
                "home_pct": home_pct, "draw_pct": draw_pct, "away_pct": away_pct,
            })

        new_home, new_away = update_ratings(ratings[home], ratings[away], home_score, away_score)
        ratings[home] = new_home
        ratings[away] = new_away
    return results

# Add to backtest.py, call it after the two competition loops in __main__
def home_bias_check(results, competition):
    home_predictions = [r for r in results if r["predicted"] == "HOME"]
    home_correct = sum(1 for r in home_predictions if r["correct"])
    print(f"{competition}: when model predicts HOME ({len(home_predictions)} times), "
          f"it's correct {home_correct}/{len(home_predictions)} "
          f"({home_correct/len(home_predictions)*100:.1f}%)")

def brier_score(results):
    """
    Lower is better. Perfect prediction = 0. Always guessing 33/33/33
    for every match scores around 0.667 — this is the baseline to beat.
    Rewards well-calibrated uncertainty rather than just rewarding
    lucky top-label guesses, unlike raw accuracy.
    """
    total_score = 0
    for r in results:
        actual_vec = {"HOME": 0, "DRAW": 0, "AWAY": 0}
        actual_vec[r["actual"]] = 1

        predicted_vec = {
            "HOME": r["home_pct"] / 100,
            "DRAW": r["draw_pct"] / 100,
            "AWAY": r["away_pct"] / 100,
        }

        match_score = sum(
            (predicted_vec[outcome] - actual_vec[outcome]) ** 2
            for outcome in ["HOME", "DRAW", "AWAY"]
        )
        total_score += match_score

    return total_score / len(results) if results else None

def summarize(results, competition):
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    accuracy = correct / total * 100 if total else 0

    actual_draw_rate = sum(1 for r in results if r["actual"] == "DRAW") / total * 100 if total else 0
    predicted_draw_avg = sum(r["draw_pct"] for r in results) / total if total else 0

    predicted_counts = {"HOME": 0, "DRAW": 0, "AWAY": 0}
    actual_counts = {"HOME": 0, "DRAW": 0, "AWAY": 0}
    for r in results:
        predicted_counts[r["predicted"]] += 1
        actual_counts[r["actual"]] += 1
    brier = brier_score(results)

    print("=" * 60)
    print(f" {competition} — backtest summary ({total} matches tested)")
    print("=" * 60)
    print(f"Overall accuracy (most-likely outcome correct): {accuracy:.1f}%")
    print(f"Brier score (lower = better; 0 = perfect, ~0.667 = random guessing): {brier:.3f}")
    print(f"Real draw rate in this data: {actual_draw_rate:.1f}%")
    print(f"Model's average predicted draw probability: {predicted_draw_avg:.1f}%")
    print()
    print("Predicted outcome distribution (what the model actually picks):")
    for outcome, count in predicted_counts.items():
        print(f"  {outcome}: {count} times ({count/total*100:.1f}%)")
    print()
    print("Real outcome distribution (what actually happened):")
    for outcome, count in actual_counts.items():
        print(f"  {outcome}: {count} times ({count/total*100:.1f}%)")
    print()

    print("Sample of 10 predictions vs. actual results:")
    for r in results[:10]:
        mark = "✅" if r["correct"] else "❌"
        print(f"  {mark} {r['home']:<25} vs {r['away']:<25} "
              f"predicted={r['predicted']:<5} actual={r['actual']:<5} "
              f"(H{r['home_pct']}/D{r['draw_pct']}/A{r['away_pct']})")
    print()

if __name__ == "__main__":
    for competition in ["premier_league", "la_liga"]:
        results = run_backtest(competition)
        summarize(results, competition)
        home_bias_check(results, competition)