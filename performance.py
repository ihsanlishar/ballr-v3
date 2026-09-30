"""Immutable pre-kickoff forecasts and honest, forward-looking evaluation."""
import logging
import threading
import time
from contextlib import closing
from datetime import datetime, timezone, timedelta
from config import DB_PATH
from database import connect
from leagues import LEAGUES
from operations import report
from readiness import league_status
from storage_guard import allow_optional_writes

logger = logging.getLogger(__name__)
ARCHIVE_LIMIT = 100000


def ensure_schema(db_path=DB_PATH):
    with closing(connect(db_path)) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS prediction_archive (
            match_id INTEGER PRIMARY KEY, competition TEXT NOT NULL,
            home_team TEXT NOT NULL, away_team TEXT NOT NULL,
            kickoff TEXT NOT NULL, recorded_at TEXT NOT NULL, model_version TEXT NOT NULL,
            home_prob REAL NOT NULL, draw_prob REAL NOT NULL, away_prob REAL NOT NULL,
            baseline_home REAL NOT NULL, baseline_draw REAL NOT NULL, baseline_away REAL NOT NULL
        )''')


def capture_predictions(db_path=DB_PATH, now=None):
    from predictions import model_context, cached_prediction, MODEL_VERSION
    now = now or datetime.now(timezone.utc)
    if not allow_optional_writes(db_path):
        report('prediction_archive', 'storage_paused')
        return 0
    captured = 0
    for league in LEAGUES:
        if not league_status(league, db_path)['ready']:
            continue
        with closing(connect(db_path)) as conn:
            fixtures = conn.execute("""SELECT id, home_team, away_team, utc_date FROM matches
                WHERE competition=? AND status IN ('SCHEDULED', 'TIMED')
                AND id NOT IN (SELECT match_id FROM prediction_archive)""", (league,)).fetchall()
        upcoming = []
        for row in fixtures:
            if not row[1] or not row[2]:
                continue
            try:
                kickoff = datetime.fromisoformat(row[3].replace('Z', '+00:00'))
                if kickoff.tzinfo is None:
                    continue
            except (ValueError, AttributeError):
                continue
            if now < kickoff <= now + timedelta(hours=24):
                upcoming.append(row)
        if not upcoming:
            continue
        context = model_context(league, db_path)
        for match_id, home, away, kickoff in upcoming:
            if not allow_optional_writes(db_path):
                report('prediction_archive', 'storage_paused')
                return captured
            fixture = {'home_team': home, 'away_team': away, 'utc_date': kickoff}
            prediction = cached_prediction(league, fixture, context, db_path)
            # Recheck wall time after computation: never archive after kickoff.
            recorded = max(now, datetime.now(timezone.utc))
            if recorded >= datetime.fromisoformat(kickoff.replace('Z', '+00:00')):
                continue
            probs = [prediction[key] for key in ('home_pct', 'draw_pct', 'away_pct')]
            total = sum(probs)
            with closing(connect(db_path)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                if conn.execute('SELECT COUNT(*) FROM prediction_archive').fetchone()[0] >= ARCHIVE_LIMIT:
                    report('prediction_archive', 'capacity')
                    return captured
                cursor = conn.execute('''INSERT OR IGNORE INTO prediction_archive
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (match_id, league, home, away, kickoff, recorded.isoformat(), MODEL_VERSION,
                     *(p / total for p in probs), *context[3]))
                captured += cursor.rowcount
    report('prediction_archive')
    return captured


def performance_report(league, db_path=DB_PATH):
    from predictions import MODEL_VERSION
    with closing(connect(db_path)) as conn:
        rows = conn.execute('''SELECT p.*, m.status, m.home_score, m.away_score,
            m.utc_date, m.home_team, m.away_team FROM prediction_archive p
            LEFT JOIN matches m ON p.match_id=m.id WHERE p.competition=? AND p.model_version=?
            ORDER BY p.recorded_at''', (league, MODEL_VERSION)).fetchall()
    scored, excluded = [], 0
    for row in rows:
        if row[13] != 'FINISHED' or row[14] is None or row[15] is None:
            continue
        # Postponements and changed teams are not comparable to the original forecast.
        if (row[4], row[2], row[3]) != (row[16], row[17], row[18]):
            excluded += 1
            continue
        outcome = 0 if row[14] > row[15] else 1 if row[14] == row[15] else 2
        scored.append((row, outcome))
    bins = [{'count': 0, 'predicted_sum': 0., 'observed_sum': 0} for _ in range(10)]
    model_brier = baseline_brier = correct = 0
    for row, outcome in scored:
        probs, baseline = row[7:10], row[10:13]
        model_brier += sum((p - int(i == outcome)) ** 2 for i, p in enumerate(probs))
        baseline_brier += sum((p - int(i == outcome)) ** 2 for i, p in enumerate(baseline))
        correct += int(max(range(3), key=lambda i: probs[i]) == outcome)
        for i, p in enumerate(probs):
            bucket = bins[min(9, int(p * 10))]
            bucket['count'] += 1
            bucket['predicted_sum'] += p
            bucket['observed_sum'] += int(i == outcome)
    n = len(scored)
    return {'league': league, 'model_version': MODEL_VERSION, 'recorded': len(rows),
            'evaluated': n, 'excluded': excluded, 'pending': len(rows) - n - excluded,
            'accuracy': correct / n if n else None,
            'brier': model_brier / n if n else None,
            'baseline_brier': baseline_brier / n if n else None,
            'first_recorded': rows[0][5] if rows else None,
            'last_recorded': rows[-1][5] if rows else None,
            'calibration': [{'range': f'{i*10}–{(i+1)*10}%', 'count': b['count'],
                             'predicted': b['predicted_sum']/b['count'] if b['count'] else None,
                             'observed': b['observed_sum']/b['count'] if b['count'] else None}
                            for i,b in enumerate(bins)]}


def _capture_loop(db_path):
    while True:
        try:
            capture_predictions(db_path)
        except Exception:
            report('prediction_archive', 'error')
            logger.exception('Prediction recording failed; will retry')
        time.sleep(300)


def start_prediction_archive(db_path=DB_PATH):
    thread = threading.Thread(target=_capture_loop, args=(db_path,), daemon=True,
                              name='prediction-archive')
    thread.start()
    return thread
