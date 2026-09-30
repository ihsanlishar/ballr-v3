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
from flask import Flask, jsonify, request, send_from_directory, g, send_file

from predictions import get_match_prediction, get_predictions
from fixtures import COMPETITIONS

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, static_folder="static", static_url_path="")

from overload import RequestBudget
request_budget = RequestBudget()
backup_budget = RequestBudget(capacity=1, per_second=1/3600, concurrent=1)


@app.before_request
def protect_capacity():
    if request.path in ('/api/predictions', '/api/match', '/api/performance'):
        if not request_budget.acquire():
            return jsonify({'error': 'Ballr is busy. Please retry shortly.'}), 429, {'Retry-After': '5'}
        g.budget_acquired = True


@app.teardown_request
def release_capacity(error=None):
    if getattr(g, 'budget_acquired', False):
        g.budget_acquired = False
        request_budget.release()


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
    from readiness import league_status
    status = league_status(league_key)
    status['stale'] = status['stale'] or any(p.get('fixtures_stale') for p in preds)
    return jsonify({"league": league_key, "predictions": preds, "data_status": status})


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
            conn.execute('SELECT 1 FROM market_prices LIMIT 1').fetchone()
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


@app.route('/performance')
def performance_page():
    return send_from_directory(app.static_folder, 'performance.html')


@app.route('/api/performance')
def performance():
    from performance import performance_report
    league = request.args.get('league', 'premier_league')
    if league not in COMPETITIONS:
        return jsonify({'error': 'Unknown league'}), 400
    return jsonify(performance_report(league))


@app.route('/status')
def status():
    from datetime import datetime, timezone
    from operations import snapshot
    from storage_guard import storage_status
    storage = storage_status()
    workers = snapshot()
    now = datetime.now(timezone.utc)
    for name, worker in workers.items():
        age = (now - datetime.fromisoformat(worker['last_check'])).total_seconds()
        threshold = 3600 if name.startswith(('results:', 'discovery:')) else 900
        if name.startswith(('results:', 'discovery:')) or name in ('maintenance', 'prediction_archive'):
            if age > threshold:
                worker['state'] = 'overdue'
    try:
        league_states = all_league_status()
        healthy = all(item['ready'] for item in league_states.values())
    except sqlite3.Error:
        league_states, healthy = {}, False
    expected = {'maintenance', 'prediction_archive'} | {
        f'{kind}:{league}' for kind in ('results', 'discovery') for league in COMPETITIONS}
    healthy = healthy and expected <= workers.keys()
    healthy = healthy and storage['state'] == 'ok' and all(
        worker['state'] not in ('error', 'overdue', 'capacity', 'storage_paused', 'disconnected')
        for worker in workers.values())
    return jsonify({'status': 'ok' if healthy else 'attention_required',
                    'storage': storage, 'leagues': league_states, 'workers': workers}), 200 if healthy else 503


@app.route('/api/admin/backup')
def backup():
    import hmac
    import io
    import tempfile
    from pathlib import Path
    from recover_storage import export_football_data
    token = os.environ.get('BALLR_BACKUP_TOKEN', '')
    provided = request.headers.get('Authorization', '')
    if len(token) < 32 or not hmac.compare_digest(provided.encode(), ('Bearer ' + token).encode()):
        return jsonify({'error': 'Unauthorized'}), 401
    if not backup_budget.acquire():
        return jsonify({'error': 'Backup is busy or was recently requested.'}), 429, {'Retry-After': '3600'}
    try:
        # Temporary filesystem, never the persistent database volume.
        with tempfile.TemporaryDirectory(prefix='ballr-backup-') as directory:
            output = Path(directory) / 'ballr.db'
            export_football_data(DB_PATH, output)
            if output.stat().st_size > 25 * 1024**2:
                return jsonify({'error': 'Backup exceeds download limit; operator review required.'}), 503
            payload = io.BytesIO(output.read_bytes())
        response = send_file(payload, mimetype='application/vnd.sqlite3', as_attachment=True,
                             download_name='ballr.db')
        response.headers['Cache-Control'] = 'no-store'
        return response
    except Exception:
        app.logger.exception('Essential data backup failed')
        return jsonify({'error': 'Backup failed; inspect service logs.'}), 503
    finally:
        backup_budget.release()


_workers_started = False
_workers_lock = threading.Lock()


def start_background_workers():
    global _workers_started
    from live_feed import start_all_league_feeds
    from results_refresh import start_results_refresh
    from db_maintenance import start_db_maintenance
    from performance import start_prediction_archive

    with _workers_lock:
        if _workers_started:
            return
        initialize(DB_PATH)
        start_all_league_feeds(DB_PATH)
        start_results_refresh(DB_PATH)
        start_db_maintenance(DB_PATH)
        start_prediction_archive(DB_PATH)
        _workers_started = True


if __name__ == "__main__":
    import webbrowser

    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        start_background_workers()
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    app.run(port=PORT, debug=False)
