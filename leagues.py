# leagues.py
"""
Unified league config — the single source of truth for both
football-data.org (historical results, fixtures) and Polymarket
(live market odds) per league. Replaces Oddscope's standalone
leagues.py and Ballr's inline COMPETITIONS dict.
"""

LEAGUES = {
    "premier_league": {
        "display_name": "Premier League",
        "football_data_code": "PL",
        "polymarket_tag_slug": "premier-league",
        "title_separator": "vs.",
        "question_prefix": "Will",
        "question_keyword": "win on",
        "draw_keyword": "end in a draw",
    },
    "la_liga": {
        "display_name": "La Liga",
        "football_data_code": "PD",
        "polymarket_tag_slug": "la-liga",
        "title_separator": "vs.",
        "question_prefix": "Will",
        "question_keyword": "win on",
        "draw_keyword": "end in a draw",
    },
    "champions_league": {
        "display_name": "Champions League",
        "football_data_code": "CL",
        "polymarket_tag_slug": "champions-league",
        "title_separator": "vs.",
        "question_prefix": "Will",
        "question_keyword": "win on",
        "draw_keyword": "end in a draw",
    },
}


def get_league_config(league_key):
    if league_key not in LEAGUES:
        raise ValueError(
            f"Unknown league '{league_key}'. Available leagues: {list(LEAGUES.keys())}"
        )
    return LEAGUES[league_key]