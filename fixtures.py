# fixtures.py
"""
Fetches upcoming (SCHEDULED) fixtures from football-data.org — separate
from the one-time historical ingestion, since this needs to be called
regularly as the season progresses, not just once.
"""
import requests
import os

BASE_URL = "https://api.football-data.org/v4"
API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "08f4fa177c9b4ba084b75ed187f88e4b")
HEADERS = {"X-Auth-Token": API_KEY}

COMPETITIONS = {
    "premier_league": "PL",
    "la_liga": "PD",
    "champions_league": "CL",
}


def get_upcoming_fixtures(league_key, limit=20):
    """
    Returns a list of upcoming scheduled matches for a league, each as
    {home_team, away_team, utc_date}. Only returns matches that
    haven't been played yet (status=SCHEDULED or TIMED).
    """
    comp_code = COMPETITIONS.get(league_key)
    if comp_code is None:
        return []

    url = f"{BASE_URL}/competitions/{comp_code}/matches"
    params = {"status": "SCHEDULED"}

    response = requests.get(url, headers=HEADERS, params=params)
    response.raise_for_status()
    data = response.json()

    matches = data.get("matches", [])[:limit]

    return [
        {
            "home_team": m.get("homeTeam", {}).get("name"),
            "away_team": m.get("awayTeam", {}).get("name"),
            "utc_date": m.get("utcDate"),
        }
        for m in matches
    ]


if __name__ == "__main__":
    for league_key in COMPETITIONS:
        print("=" * 60)
        print(f" {league_key} — upcoming fixtures")
        print("=" * 60)
        fixtures = get_upcoming_fixtures(league_key, limit=5)
        for f in fixtures:
            print(f"  {f['utc_date']}  {f['home_team']} vs {f['away_team']}")
        print()