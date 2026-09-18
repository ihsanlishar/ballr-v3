# api.py
"""
Ballr's backend. Serves predictions, live market comparisons, and the
static frontend — starts every league's live Polymarket feed AND the
results-refresh loop as background threads at boot, all in one process.
"""
import os
import logging
import sqlite3
import threading
import requests
from config import DB_PATH
from database import connect, initialize
from readiness import DataUnavailable, require_league_data, all_league_status
from contextlib import closing
from flask import Flask, jsonify, request, send_from_directory

from predictions import get_match_prediction, get_predictions
from fixtures import COMPETITIONS

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, static_folder="static", static_url_path="")

PORT = int(os.environ.get("PORT", 5051))


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
        require_league_data(league_key)
        preds = get_predictions(league_key, limit=20)
    except (DataUnavailable, requests.RequestException, RuntimeError) as error:
        app.logger.warning('Predictions unavailable: %s', error)
        return jsonify({'error': 'Match data is updating or temporarily unavailable. Please retry shortly.'}), 503
    except Exception:
        app.logger.exception('Prediction request failed')
        return jsonify({'error': 'Prediction could not be generated.'}), 500
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

    if len(home_team) > 150 or len(away_team) > 150 or home_team == away_team:
        return jsonify({'error': 'Invalid teams'}), 400
    if utc_date:
        from datetime import datetime
        try:
            parsed = datetime.fromisoformat(utc_date.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('Timezone required')
        except ValueError:
            return jsonify({'error': 'Invalid match date'}), 400

    try:
        require_league_data(league_key)
        return jsonify(get_match_prediction(league_key, home_team, away_team, utc_date))
    except LookupError:
        return jsonify({'error': 'Upcoming match not found. Return to the fixture list.'}), 404
    except (DataUnavailable, requests.RequestException, RuntimeError) as error:
        app.logger.warning('Match unavailable: %s', error)
        return jsonify({'error': 'Match data is updating or temporarily unavailable. Please retry shortly.'}), 503
    except Exception:
        app.logger.exception('Match prediction request failed')
        return jsonify({'error': 'Prediction could not be generated.'}), 500


@app.route('/health')
def health():
    try:
        with closing(connect(DB_PATH)) as conn:
            conn.execute('SELECT 1 FROM matches LIMIT 1').fetchone()
            conn.execute('SELECT 1 FROM price_updates LIMIT 1').fetchone()
        if not os.environ.get('FOOTBALL_DATA_API_KEY'):
            return jsonify({'status': 'configuration_required'}), 503
        return jsonify({'status': 'ok'})
    except sqlite3.Error:
        return jsonify({'status': 'database_unavailable'}), 503


@app.route('/ready')
def ready():
    try:
        statuses = all_league_status()
    except sqlite3.Error:
        return jsonify({'status': 'initializing'}), 503
    is_ready = bool(os.environ.get('FOOTBALL_DATA_API_KEY')) and all(
        status['ready'] for status in statuses.values())
    return jsonify({'status': 'ready' if is_ready else 'updating', 'leagues': statuses}), 200 if is_ready else 503


_workers_started = False
_workers_lock = threading.Lock()


def start_background_workers():
    global _workers_started
    from live_feed import start_all_league_feeds
    from results_refresh import start_results_refresh
    from db_maintenance import start_db_maintenance

    with _workers_lock:
        if _workers_started:
            return
        initialize(DB_PATH)
        start_all_league_feeds(DB_PATH)
        start_results_refresh(DB_PATH)
        start_db_maintenance(DB_PATH)
        _workers_started = True


if __name__ == "__main__":
    import webbrowser

    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        start_background_workers()
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    app.run(port=PORT, debug=False)
