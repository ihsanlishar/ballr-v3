# diagnose_team_names.py
"""
Compares team names between Ballr's football-data.org data and
Polymarket's real, tracked moneyline questions — not raw event titles,
which include unrelated prop/futures markets and produce false noise.
"""
from database import connect
from config import DB_PATH

def get_ballr_teams(league, db_path=DB_PATH):
    conn = connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT home_team FROM matches WHERE competition = ?
        UNION
        SELECT DISTINCT away_team FROM matches WHERE competition = ?
    """, (league, league))
    teams = sorted(row[0] for row in cursor.fetchall())
    conn.close()
    return teams


def get_polymarket_teams(league_key):
    """
    Uses Polymarket's own real match-detection logic (the same functions
    live_feed.py actually runs) instead of naive
    string splitting — so this reflects real tracked teams only.
    """
    from polymarket_client import find_all_matches, is_moneyline_market, extract_match_details

    all_matches = find_all_matches(league_key, include_closed=False)
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
    polymarket_teams = set(get_polymarket_teams(league))

    print("=" * 70)
    print(f" {league} — exact matches")
    print("=" * 70)
    for t in sorted(ballr_teams & polymarket_teams):
        print(f"  ✅ {t}")

    print()
    print("=" * 70)
    print(f" {league} — in Ballr but NOT matched in Polymarket")
    print("=" * 70)
    for t in sorted(ballr_teams - polymarket_teams):
        print(f"  ⚠️  {t}")

    print()
    print("=" * 70)
    print(f" {league} — in Polymarket but NOT matched in Ballr")
    print("=" * 70)
    for t in sorted(polymarket_teams - ballr_teams):
        print(f"  ⚠️  {t}")