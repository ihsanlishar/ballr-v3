# api.py
"""
Ballr's backend. Serves predictions, live market comparisons, and the
static frontend — plus starts every league's live Polymarket feed as
a background thread at boot, all in one process.
"""
from flask import Flask, jsonify, request, send_from_directory

from predictions import get_match_prediction, get_predictions
from fixtures import COMPETITIONS

app = Flask(__name__, static_folder="static", static_url_path="")
PORT = 5051


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/match")
def match_page():
    return send_from_directory(app.static_folder, "match.html")


@app.route("/api/leagues")
def leagues():
    display_names = {
        "premier_league": "Premier League",
        "la_liga": "La Liga",
        "champions_league": "Champions League",
    }
    return jsonify([
        {"key": key, "display_name": display_names.get(key, key)}
        for key in COMPETITIONS
    ])


@app.route("/api/predictions")
def predictions():
    league_key = request.args.get("league")
    if league_key not in COMPETITIONS:
        return jsonify({
            "error": f"Unknown league '{league_key}'",
            "available_leagues": list(COMPETITIONS.keys())
        }), 400
    try:
        preds = get_predictions(league_key, limit=20)
    except Exception as e:
        return jsonify({"error": f"Failed to generate predictions: {str(e)}"}), 500
    return jsonify({"league": league_key, "predictions": preds})


@app.route("/api/match")
def match_prediction():
    league_key = request.args.get("league")
    home_team = request.args.get("home")
    away_team = request.args.get("away")
    utc_date = request.args.get("date")

    if league_key not in COMPETITIONS:
        return jsonify({"error": f"Unknown league '{league_key}'"}), 400
    if not home_team or not away_team:
        return jsonify({"error": "Both home and away teams are required"}), 400

    try:
        return jsonify(get_match_prediction(league_key, home_team, away_team, utc_date))
    except Exception as e:
        return jsonify({"error": f"Failed to generate match prediction: {str(e)}"}), 500


if __name__ == "__main__":
    import os
    import threading
    import webbrowser
    from live_feed import start_all_league_feeds

    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        start_all_league_feeds()
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    app.run(port=PORT, debug=True)