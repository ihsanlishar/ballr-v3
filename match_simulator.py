# match_simulator.py
"""
Given two teams' current Elo ratings, simulates a match outcome
distribution via Monte Carlo — same 50k-100k iteration technique as
Ballr V2, adapted to properly model draws (Elo's raw win/lose formula
doesn't include them on its own).
"""
from database import connect
from config import DB_PATH
import math

import numpy as np
from elo_engine import expected_score, HOME_ADVANTAGE, INITIAL_RATING

# How much raw randomness to inject per simulated match — this is
# what turns a single deterministic Elo expectation into a real
# distribution of outcomes across many simulated matches.
GOAL_VARIANCE = 1.3

# Controls how often simulated matches land as exact draws. Tuned
# during backtesting on Day 2 — this default is a starting point,
# not yet calibrated against real results.
DRAW_DAMPENING = 0.55

# Scoreline-model settings ported from Ballr V2. The score projection remains
# separate from V3's calibrated Win/Draw/Win simulation above.
RECENCY_DECAY = 0.85
RECENT_DEFENSE_WINDOW = 3
RECENT_DEFENSE_BOOST = 0.5
QUALITY_SCALE = 0.35
QUALITY_ELO_CAP = 400
# Ballr V2's World Cup-specific rho of -0.13 adds 13% to every 1-1 cell.
# That is not transferable to these league datasets: it caused 1-1 to become
# the modal score for a majority of fixtures. Keep the Dixon-Coles machinery
# available, but use its neutral setting until a league-specific backtest
# estimates a coefficient from pre-match data.
DIXON_COLES_RHO = 0.0
ELO_RATIO_PER_100 = 0.05
ELO_RATIO_MIN = 0.80
ELO_RATIO_MAX = 1.20
MAX_DISPLAY_GOALS = 5
MAX_MODEL_GOALS = 8


def simulate_match(home_rating, away_rating, n_simulations=50000, seed=None):
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

    rng = np.random.default_rng(seed)

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


def build_goal_profiles(competition, ratings=None, db_path=DB_PATH, connection=None):
    """Build the recency-weighted team statistics used by Ballr V2.

    V3 has one local database per competition rather than V2's multi-source
    team histories. Within that constraint, this reproduces V2's inputs:
    weighted goals scored/conceded, clean-sheet rate, win rate, goal
    difference, and a newest-first W/D/L form string. As in V2, a goal's
    contribution is adjusted for the current Elo strength of its opponent and
    the three most-recent defensive performances receive extra weight.
    """
    conn = connection if connection is not None else connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT home_team, away_team, home_score, away_score, utc_date
        FROM matches
        WHERE competition = ? AND status = 'FINISHED'
          AND home_score IS NOT NULL AND away_score IS NOT NULL
        ORDER BY utc_date DESC
    """, (competition,))
    rows = cursor.fetchall()
    if connection is None:
        conn.close()

    if not rows:
        raise ValueError(f"No finished matches available for '{competition}'")

    ratings = ratings or {}
    league_home_goals = sum(row[2] for row in rows) / len(rows)
    league_away_goals = sum(row[3] for row in rows) / len(rows)
    team_matches = {}
    for home_team, away_team, home_goals, away_goals, _ in rows:
        team_matches.setdefault(home_team, []).append((home_goals, away_goals, away_team, True))
        team_matches.setdefault(away_team, []).append((away_goals, home_goals, home_team, False))

    league_goals_per_team = sum(row[2] + row[3] for row in rows) / (2 * len(rows))
    profiles = {}
    for team, matches in team_matches.items():
        goal_weights = []
        conceded_weights = []
        for index, (_, _, opponent, _) in enumerate(matches):
            base_weight = RECENCY_DECAY ** index
            opponent_rating = ratings.get(opponent, INITIAL_RATING)
            strength = max(-QUALITY_ELO_CAP, min(
                opponent_rating - INITIAL_RATING, QUALITY_ELO_CAP
            )) / QUALITY_ELO_CAP
            goal_weights.append(base_weight * (1 + strength * QUALITY_SCALE))
            defense_weight = base_weight * (1 - strength * QUALITY_SCALE)
            if index < RECENT_DEFENSE_WINDOW:
                defense_weight *= 1 + RECENT_DEFENSE_BOOST
            conceded_weights.append(defense_weight)

        total_goal_weight = sum(goal_weights)
        total_conceded_weight = sum(conceded_weights)
        weighted_goals = sum(
            gf * weight for (gf, _, _, _), weight in zip(matches, goal_weights)
        )
        weighted_conceded = sum(
            ga * weight for (_, ga, _, _), weight in zip(matches, conceded_weights)
        )
        results = ["W" if gf > ga else "D" if gf == ga else "L" for gf, ga, _, _ in matches]

        def venue_rates(is_home, league_scoring_rate, league_conceding_rate):
            venue_matches = [match for match in matches if match[3] is is_home]
            if not venue_matches:
                return league_scoring_rate, league_conceding_rate
            venue_weights = [RECENCY_DECAY ** index for index in range(len(venue_matches))]
            total_venue_weight = sum(venue_weights)
            scored = sum(
                gf * weight for (gf, _, _, _), weight in zip(venue_matches, venue_weights)
            ) / total_venue_weight
            conceded = sum(
                ga * weight for (_, ga, _, _), weight in zip(venue_matches, venue_weights)
            ) / total_venue_weight
            return scored, conceded

        home_scored, home_conceded = venue_rates(True, league_home_goals, league_away_goals)
        away_scored, away_conceded = venue_rates(False, league_away_goals, league_home_goals)

        profiles[team] = {
            "goals_per_game": weighted_goals / total_goal_weight,
            "conceded_per_game": weighted_conceded / total_conceded_weight,
            "clean_sheet_rate": sum(
                weight for (_, ga, _, _), weight in zip(matches, conceded_weights) if ga == 0
            ) / total_conceded_weight,
            "win_rate": results.count("W") / len(results),
            "gd_per_game": weighted_goals / total_goal_weight - weighted_conceded / total_conceded_weight,
            "form_string": "".join(results[:5]),
            "home_goals_per_game": home_scored,
            "home_conceded_per_game": home_conceded,
            "away_goals_per_game": away_scored,
            "away_conceded_per_game": away_conceded,
        }

    default_stats = {
        "goals_per_game": league_goals_per_team,
        "conceded_per_game": league_goals_per_team,
        "clean_sheet_rate": 0.2,
        "win_rate": 1 / 3,
        "gd_per_game": 0.0,
        "form_string": "",
        "home_goals_per_game": league_home_goals,
        "home_conceded_per_game": league_away_goals,
        "away_goals_per_game": league_away_goals,
        "away_conceded_per_game": league_home_goals,
    }
    return {
        "teams": profiles,
        "default_stats": default_stats,
        "league_home_goals": league_home_goals,
        "league_away_goals": league_away_goals,
    }


def form_momentum(stats):
    """Port of Ballr V2's recency-weighted W/D/L form signal."""
    form = stats.get("form_string", "")
    if not form:
        return 0.0

    score = 0.0
    for index, result in enumerate(form):
        weight = RECENCY_DECAY ** index
        if result == "W":
            score += weight
        elif result == "D":
            score += 0.2 * weight
        else:
            score -= 0.6 * weight

    score /= sum(RECENCY_DECAY ** index for index in range(len(form)))
    score += (stats.get("win_rate", 1 / 3) - 1 / 3) * 0.5
    return max(-1.0, min(score, 1.0))


def dixon_coles_correction(home_goals, away_goals, home_xg, away_xg,
                            rho=DIXON_COLES_RHO):
    """Port of Ballr V2's low-score Dixon-Coles correction."""
    if home_goals > 1 or away_goals > 1:
        return 1.0
    if home_goals == 0 and away_goals == 0:
        return 1 - home_xg * away_xg * rho
    if home_goals == 1 and away_goals == 0:
        return 1 + away_xg * rho
    if home_goals == 0 and away_goals == 1:
        return 1 + home_xg * rho
    return 1 - rho


def score_projection(home_rating, away_rating, goal_profiles, home_team, away_team,
                     max_goals=MAX_DISPLAY_GOALS):
    """Return a model-derived scoreline matrix and supporting metrics.

    Port of Ballr V2's scoreline algorithm: recency-weighted scoring form,
    Elo anchoring, opponent clean-sheet pressure, win-rate and goal-difference
    factors, form momentum, then a Dixon-Coles-corrected Poisson matrix.
    """
    home_stats = goal_profiles["teams"].get(home_team, goal_profiles["default_stats"])
    away_stats = goal_profiles["teams"].get(away_team, goal_profiles["default_stats"])

    # V2's capped Elo-strength ratio, with V3's existing home advantage.
    elo_difference = (home_rating + HOME_ADVANTAGE) - away_rating
    home_elo_ratio = min(ELO_RATIO_MAX, max(ELO_RATIO_MIN,
        1 + (elo_difference / 100) * ELO_RATIO_PER_100))
    away_elo_ratio = min(ELO_RATIO_MAX, max(ELO_RATIO_MIN,
        1 - (elo_difference / 100) * ELO_RATIO_PER_100))

    def defensive_factor(clean_sheet_rate):
        return 1.10 - clean_sheet_rate * 0.5

    def win_rate_factor(win_rate):
        return 0.93 + win_rate * 0.14

    # V2's original model used overall scoring rates and a clean-sheet proxy
    # for defence. In league football we have enough home/away results to use
    # the opponent's actual venue-specific concession rate too. Without this,
    # nearly every matchup clusters near 1.5 goals apiece and 1-1 becomes the
    # mechanical mode of most score matrices.
    def concession_factor(conceded_rate, league_rate):
        raw_factor = conceded_rate / league_rate if league_rate > 0 else 1.0
        return min(1.35, max(0.70, 0.5 + 0.5 * raw_factor))

    home_xg = home_stats["home_goals_per_game"] * home_elo_ratio
    away_xg = away_stats["away_goals_per_game"] * away_elo_ratio
    home_xg *= concession_factor(
        away_stats["away_conceded_per_game"], goal_profiles["league_home_goals"]
    )
    away_xg *= concession_factor(
        home_stats["home_conceded_per_game"], goal_profiles["league_away_goals"]
    )
    home_xg *= defensive_factor(away_stats["clean_sheet_rate"])
    away_xg *= defensive_factor(home_stats["clean_sheet_rate"])
    home_xg *= win_rate_factor(home_stats["win_rate"])
    away_xg *= win_rate_factor(away_stats["win_rate"])
    home_xg *= 1 + max(-0.09, min(home_stats["gd_per_game"] * 0.03, 0.09))
    away_xg *= 1 + max(-0.09, min(away_stats["gd_per_game"] * 0.03, 0.09))
    home_xg *= 1 + form_momentum(home_stats) * 0.06
    away_xg *= 1 + form_momentum(away_stats) * 0.06
    home_xg = min(max(home_xg, 0.3), 4.0)
    away_xg = min(max(away_xg, 0.3), 4.0)

    def poisson_pmf(goals, mean):
        return math.exp(-mean) * (mean ** goals) / math.factorial(goals)

    home_probs = [poisson_pmf(goals, home_xg) for goals in range(MAX_MODEL_GOALS + 1)]
    away_probs = [poisson_pmf(goals, away_xg) for goals in range(MAX_MODEL_GOALS + 1)]
    raw_probabilities = {}
    total_probability = 0.0
    for home_goals in range(MAX_MODEL_GOALS + 1):
        for away_goals in range(MAX_MODEL_GOALS + 1):
            probability = home_probs[home_goals] * away_probs[away_goals]
            probability *= dixon_coles_correction(home_goals, away_goals, home_xg, away_xg)
            raw_probabilities[(home_goals, away_goals)] = probability
            total_probability += probability

    normalized_probabilities = {
        score: probability / total_probability
        for score, probability in raw_probabilities.items()
    }
    matrix = [
        [round(normalized_probabilities[(home_goals, away_goals)] * 100, 2)
         for away_goals in range(max_goals + 1)]
        for home_goals in range(max_goals + 1)
    ]

    scorelines = [
        {
            "home_goals": home_goals,
            "away_goals": away_goals,
            "probability": round(probability * 100, 2),
        }
        for (home_goals, away_goals), probability in normalized_probabilities.items()
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
    profiles = build_goal_profiles("premier_league", ratings)

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
        projection = score_projection(ratings[home], ratings[away], profiles, home, away)
        print(f"\n{home} (home, Elo {ratings[home]:.0f}) vs {away} (away, Elo {ratings[away]:.0f})")
        print(f"  {home}: {home_pct}%")
        print(f"  Draw: {draw_pct}%")
        print(f"  {away}: {away_pct}%")
        print(f"  Model xG: {projection['home_xg']}–{projection['away_xg']}")
        print(f"  Sum: {round(home_pct + draw_pct + away_pct, 1)}%")
