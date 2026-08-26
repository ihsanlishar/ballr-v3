# api.py
"""
Ballr's backend. Serves predictions for upcoming fixtures across all
tracked leagues. Same proven single-process pattern as Oddscope.
"""
from flask import Flask, jsonify, request

from predictions import get_predictions
from fixtures import COMPETITIONS

app = Flask(__name__)
PORT = 5051  # different port from Oddscope's 5050, so both can run side by side


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
        # Predictions depend on an external API call (fixtures) plus a
        # potentially expensive local computation (Elo replay + Monte
        # Carlo). Fail predictably with a clear error instead of a raw
        # 500 traceback if anything upstream breaks.
        return jsonify({"error": f"Failed to generate predictions: {str(e)}"}), 500

    return jsonify({"league": league_key, "predictions": preds})


if __name__ == "__main__":
    app.run(port=PORT, debug=True)