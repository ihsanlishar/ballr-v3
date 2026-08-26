# match_simulator.py
"""
Given two teams' current Elo ratings, simulates a match outcome
distribution via Monte Carlo — same 50k-100k iteration technique as
Ballr V2, adapted to properly model draws (Elo's raw win/lose formula
doesn't include them on its own).
"""
import math

import numpy as np
from elo_engine import expected_score, HOME_ADVANTAGE

# How much raw randomness to inject per simulated match — this is
# what turns a single deterministic Elo expectation into a real
# distribution of outcomes across many simulated matches.
GOAL_VARIANCE = 1.3

# Controls how often simulated matches land as exact draws. Tuned
# during backtesting on Day 2 — this default is a starting point,
# not yet calibrated against real results.
DRAW_DAMPENING = 0.55

# The score projection is deliberately separate from the calibrated
# Win/Draw/Win simulation above.  It converts the same Elo strength signal
# into a conventional Poisson scoreline distribution for display purposes.
AVERAGE_TOTAL_GOALS = 2.65
SCORE_DIFF_SCALE = 0.70
MAX_DISPLAY_GOALS = 5


def simulate_match(home_rating, away_rating, n_simulations=50000):
    """
    Returns (home_win_pct, draw_pct, away_win_pct) as a fraction of
    n_simulations, based on simulating goal-scoring using each team's
    Elo-implied strength.
    """
    adjusted_home = home_rating + HOME_ADVANTAGE
    home_expected = expected_score(adjusted_home, away_rating)

    # Convert the Elo expected-score (a 0-1 win probability) into an
    # expected goal differential using a simple linear mapping — a
    # deliberately simple starting model, to be validated in backtesting.
    expected_goal_diff = (home_expected - 0.5) * 4

    home_wins = 0
    draws = 0
    away_wins = 0

    rng = np.random.default_rng()

    for _ in range(n_simulations):
        noise = rng.normal(loc=0, scale=GOAL_VARIANCE)
        simulated_diff = expected_goal_diff + noise

        if abs(simulated_diff) < DRAW_DAMPENING:
            draws += 1
        elif simulated_diff > 0:
            home_wins += 1
        else:
            away_wins += 1

    return (
        round(home_wins / n_simulations * 100, 1),
        round(draws / n_simulations * 100, 1),
        round(away_wins / n_simulations * 100, 1),
    )


def score_projection(home_rating, away_rating, max_goals=MAX_DISPLAY_GOALS):
    """Return a model-derived scoreline matrix and supporting metrics.

    Elo supplies the relative strength signal; a Poisson distribution turns
    it into plausible goal counts.  Scores 0 through ``max_goals`` are shown
    individually.  The omitted extreme-score tail is reported separately so
    callers do not mistake the displayed grid for the entire distribution.
    """
    adjusted_home = home_rating + HOME_ADVANTAGE
    home_expected = expected_score(adjusted_home, away_rating)
    goal_difference = (home_expected - 0.5) * 4 * SCORE_DIFF_SCALE

    home_xg = max(0.15, (AVERAGE_TOTAL_GOALS + goal_difference) / 2)
    away_xg = max(0.15, (AVERAGE_TOTAL_GOALS - goal_difference) / 2)

    def poisson_pmf(goals, mean):
        return math.exp(-mean) * (mean ** goals) / math.factorial(goals)

    home_probs = [poisson_pmf(goals, home_xg) for goals in range(max_goals + 1)]
    away_probs = [poisson_pmf(goals, away_xg) for goals in range(max_goals + 1)]
    matrix = [
        [round(home_prob * away_prob * 100, 2) for away_prob in away_probs]
        for home_prob in home_probs
    ]

    scorelines = [
        {
            "home_goals": home_goals,
            "away_goals": away_goals,
            "probability": matrix[home_goals][away_goals],
        }
        for home_goals in range(max_goals + 1)
        for away_goals in range(max_goals + 1)
    ]
    scorelines.sort(key=lambda item: item["probability"], reverse=True)

    shown_probability = sum(sum(row) for row in matrix)
    return {
        "home_xg": round(home_xg, 2),
        "away_xg": round(away_xg, 2),
        "score_matrix": matrix,
        "max_display_goals": max_goals,
        "shown_probability": round(shown_probability, 1),
        "top_scorelines": scorelines[:3],
    }


if __name__ == "__main__":
    from elo_engine import build_current_ratings

    ratings = build_current_ratings("premier_league")

    test_matchups = [
        ("Arsenal FC", "Manchester City FC"),
        ("Burnley FC", "Arsenal FC"),
        ("Liverpool FC", "Everton FC"),
    ]

    print("=" * 60)
    print(" Sample simulated matchups (Premier League)")
    print("=" * 60)

    for home, away in test_matchups:
        if home not in ratings or away not in ratings:
            print(f"Skipping {home} vs {away} — missing rating data")
            continue

        home_pct, draw_pct, away_pct = simulate_match(ratings[home], ratings[away])
        print(f"\n{home} (home, Elo {ratings[home]:.0f}) vs {away} (away, Elo {ratings[away]:.0f})")
        print(f"  {home}: {home_pct}%")
        print(f"  Draw: {draw_pct}%")
        print(f"  {away}: {away_pct}%")
        print(f"  Sum: {round(home_pct + draw_pct + away_pct, 1)}%")
