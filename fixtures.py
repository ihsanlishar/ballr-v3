# fixtures.py
"""
Fetches upcoming (SCHEDULED) fixtures from football-data.org. League
codes now come from the unified leagues.py instead of a local dict.
"""
import requests
import os
from dotenv import load_dotenv

from leagues import LEAGUES

load_dotenv()

BASE_URL = "https://api.football-data.org/v4"
API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY")
HEADERS = {"X-Auth-Token": API_KEY}

# Kept for backward compatibility with code that imports COMPETITIONS
# directly (e.g. api.py) — now derived from leagues.py, not duplicated.
COMPETITIONS = {key: cfg["football_data_code"] for key, cfg in LEAGUES.items()}


def get_upcoming_fixtures(league_key, limit=20):
    if not API_KEY:
        raise RuntimeError(
            "FOOTBALL_DATA_API_KEY is not set. Create a .env file with "
            "FOOTBALL_DATA_API_KEY=<your key> in the project root."
        )

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