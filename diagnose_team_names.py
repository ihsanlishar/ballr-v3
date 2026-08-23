# diagnose_team_names.py
"""
Compares team names between Ballr's football-data.org data and
Oddscope's real, tracked moneyline questions — not raw event titles,
which include unrelated prop/futures markets and produce false noise.
"""
import sqlite3
import sys

def get_ballr_teams(league, db_path="ballr.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT home_team FROM matches WHERE competition = ?
        UNION
        SELECT DISTINCT away_team FROM matches WHERE competition = ?
    """, (league, league))
    teams = sorted(row[0] for row in cursor.fetchall())
    conn.close()
    return teams


def get_oddscope_teams(league_key, oddscope_repo_path):
    """
    Uses Oddscope's own real match-detection logic (the same functions
    live_feed.py and market_data.py actually run) instead of naive
    string splitting — so this reflects real tracked teams only.
    """
    sys.path.insert(0, oddscope_repo_path)
    from polymarket_client import find_all_matches, is_moneyline_market, extract_match_details

    all_matches = find_all_matches(league_key, include_closed=True)
    teams = set()

    for m in all_matches:
        title = m['event_title']
        question = m['question']
        if is_moneyline_market(league_key, title, question):
            team_name, _ = extract_match_details(league_key, question)
            if team_name:
                teams.add(team_name)

    return sorted(teams)


if __name__ == "__main__":
    league = "premier_league"

    ballr_teams = set(get_ballr_teams(league))
    oddscope_teams = set(get_oddscope_teams(league, "/Users/ihsanlishar/oddscope"))

    print("=" * 70)
    print(f" {league} — exact matches")
    print("=" * 70)
    for t in sorted(ballr_teams & oddscope_teams):
        print(f"  ✅ {t}")

    print()
    print("=" * 70)
    print(f" {league} — in Ballr but NOT matched in Oddscope")
    print("=" * 70)
    for t in sorted(ballr_teams - oddscope_teams):
        print(f"  ⚠️  {t}")

    print()
    print("=" * 70)
    print(f" {league} — in Oddscope but NOT matched in Ballr")
    print("=" * 70)
    for t in sorted(oddscope_teams - ballr_teams):
        print(f"  ⚠️  {t}")