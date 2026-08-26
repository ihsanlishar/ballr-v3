# api.py
"""
Ballr's backend. Serves predictions for upcoming fixtures across all
tracked leagues, plus the static frontend.
"""
from flask import Flask, jsonify, request, send_from_directory

from predictions import get_predictions
from fixtures import COMPETITIONS

app = Flask(__name__, static_folder="static", static_url_path="")
PORT = 5051


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


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


if __name__ == "__main__":
    import os
    import threading
    import webbrowser

    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    app.run(port=PORT, debug=True)