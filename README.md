# Ballr V3

Flask serves both the football prediction API and the static frontend. One Gunicorn worker owns the background football results refresh, Polymarket streams, and SQLite maintenance.

## Local setup

Use Python 3.14. Create a virtual environment, install `requirements.txt`, copy `.env.example` to `.env`, and set `FOOTBALL_DATA_API_KEY`.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# Edit .env with your football-data.org key.
.venv/bin/python api.py
```

The app defaults to port 5051. On startup it creates schemas locally, then loads data in background threads. Predictions return a retryable 503 until their league has finished history and a successful active-season sync. An unsuccessful refresh is retried; history is checked separately for every league. No API key is sent to the browser.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
node --check static/match.js
```

Regression tests use temporary databases and mocked providers. They do not modify `ballr.db` or use API credentials. GitHub Actions runs the same checks on pushes and pull requests. The backtest scripts are evaluation tools, not the regression suite; their results do not establish market-beating performance.

## Railway deployment

1. Create a Railway service from this GitHub repository. The app serves the frontend too; separate frontend hosting is unnecessary.
2. Attach a persistent **volume mounted at `/data`** before starting the app.
3. Set `BALLR_DB_PATH=/data/ballr.db` and `FOOTBALL_DATA_API_KEY` in Railway variables. Railway supplies `PORT`. Do not upload `.env` or the local database.
4. Keep **one replica and one Gunicorn worker**, with preloading disabled. Multiple instances would duplicate background feeds and provider requests. Keep the service always running so the feeds and results refresh continue.
5. `railway.json` specifies the start command and `/ready` deployment check with a 300-second timeout. Startup needs API access for all three competitions. If readiness fails, inspect logs and fix provider access/configuration instead of bypassing the check.
6. Generate a Railway domain, inspect `/health` and `/ready`, and test all leagues and match pages. Restart and redeploy once to verify persistent data and stream recovery. Confirm new price timestamps during a live market and current-season results after refresh.
7. Set up the small off-volume backups below, then test a restore into a separate database. This does not require Railway Pro.
8. Once staging is verified, add the Namecheap domain in Railway and copy Railway's DNS records to Namecheap. Railway manages HTTPS.

References: [Railway volumes](https://docs.railway.com/volumes), [health checks](https://docs.railway.com/deployments/healthchecks), [custom domains](https://docs.railway.com/networking/domains).

## Data behavior and limits

- `FOOTBALL_SEASON` optionally overrides the season's start year. By default the three configured European competitions roll over in July. Current-season requests are cached for two minutes and paced at least 6.2 seconds apart within this process; other apps sharing the key can still consume its quota.
- Historical ingestion targets the preceding season. Current results refresh every 30 minutes. `/ready` requires finished history and a successful current-season sync within two hours for each league; `/health` checks schema access and key configuration. Railway deployment checks are not continuous monitoring: monitor `/ready` separately after launch.
- Polymarket discovery is cached for one minute, stream subscriptions are rediscovered every 15 minutes, and failures retry. Connections restart on subscription changes to receive fresh book snapshots. A market comparison requires home, draw, and away quotes no more than five minutes old. Quiet markets can therefore show no recent odds. Market matching still requires matching provider team names.
- SQLite uses WAL, a busy timeout, and indexes for match and latest-price reads. Each market outcome has one current-price row that is replaced on updates. Quotes older than one hour are deleted every minute. Freed pages are reused; no disruptive hourly `VACUUM` runs. For an existing oversized database, compact a backup offline during maintenance if necessary.
- Ratings and goal profiles include available historical and current-season results. Win/draw/win and scoreline projections are separate models. The `/performance` page measures forward predictions; see its methodology below.

A successful local check is not a verified Railway deployment. Complete the staging, persistence, backup restore, and live-provider checks above before opening the service publicly.


## Recovering an existing full volume

The current release writes only `market_prices` (one row per outcome), never
appending to the legacy `price_updates` table. Startup does not scan, copy, or
delete the potentially multi-gigabyte legacy table. Existing history therefore
continues occupying space until maintenance is completed. A full volume may
need recovery before the new app can even create its small schema.

`recover_storage.py` exports `matches`, `season_sync`, and `prediction_archive` (if present) to a new database,
verifies every copied row and database integrity, and leaves the source alone.
It refuses existing output files or unexpected source tables. Market quotes
are disposable and refill from the feed after restart.

Example inside the Railway container during maintenance, with application
writers stopped and sufficient temporary disk space:

```sh
python recover_storage.py --source /data/ballr.db --output /tmp/ballr-recovered.db
```

This is an export, **not an automatic replacement**. Download the verified
export to durable storage before deleting anything or restarting the container:
`/tmp` is temporary and can disappear on redeployment. Then arrange an offline
replacement of `/data/ballr.db` using the verified export, with all application
processes stopped and SQLite journal files handled together. Do not delete WAL
or SHM files from a running database or blindly overwrite a live SQLite file.
A stopped Railway service may not allow terminal access; use a dedicated
maintenance deployment rather than trying to run maintenance alongside feeds.

After recovery, deploy the new code, verify `/ready` and current market quotes,
and monitor volume usage across a busy match day. Persistent storage remains
necessary for football history; raw market tick history is no longer retained.

## Reliability in this release

- Quotes are capped at **10,000 outcomes**, with one row per outcome. New IDs are refused at the cap; existing IDs continue updating. Old quotes expire after an hour, and comparisons still require quotes newer than five minutes.
- Optional quote and prediction-recording writes pause when the database plus journals reaches **256 MB**, or free disk drops below **128 MB or 10%**, whichever threshold is reached first. These are early-warning guards, not a hard filesystem quota: essential football results still write, and old database files are not automatically shrunk. `/status` reports pressure; recover the old full volume first.
- Successful result refreshes update only changed match rows. Model work is cached against the actual completed results, including corrections. A fixed simulation seed keeps unchanged inputs stable across refreshes and restarts. Market quotes are read fresh separately.
- During provider outages, saved fixtures/results remain usable for at most **24 hours** after the last successful results sync, with a visible stale-data label. Prediction recording requires fresher data (two hours). `/ready` remains stricter than the browsing fallback.
- Prediction, match and performance endpoints share a **120 requests/minute** refill budget with a burst of 60 and at most **two simultaneous requests**. Excess requests get 429 with `Retry-After`. This protects a small single-process app, but is not full DDoS protection; one busy client can use the shared budget.
- `/status` reports storage, league freshness and background-worker health. No telemetry history is written to the volume. `/health` remains a lightweight liveness check, while `/ready` is the deployment readiness check.

## Forward performance tracking

`/performance` shows the model version, sample size, recording date range, accuracy, Brier score, league-frequency baseline, and calibration bins. A background worker records the first available forecast within 24 hours of kickoff, once per match, every five minutes. Records are immutable. No historical results are backfilled as supposed pre-match predictions. Finished matches are evaluated against their recorded probabilities; changed kickoff times or teams are excluded.

The archive stops accepting new entries at 100,000 records rather than deleting the track record. Storage pressure also pauses capture. Existing records stay readable and are included in backups. Increment `MODEL_VERSION` in `predictions.py` when changing model behavior; the page reports the current version only. This page measures the win/draw/win forecast, not the separate scoreline model or profitability. Results start empty and take real completed matches to become useful.

## Off-volume backups and status checks (opt-in)

1. Add a random `BALLR_BACKUP_TOKEN` of at least 32 characters to the **web** service's Railway variables. Keep it private; do not send it in a URL. Generate one locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. In GitHub repository **Settings → Secrets and variables → Actions**, add that same value as the secret `BALLR_BACKUP_TOKEN`. Add the variable `BALLR_URL` with your HTTPS Railway origin (no trailing route), and `BALLR_OPERATIONS_ENABLED` = `true` after this code is deployed and the volume recovered.
3. Run **Production checks and backup** manually in the Actions tab, with backup selected. Download the resulting artifact and verify it. Scheduled runs check status hourly; a separate daily schedule at 06:17 UTC creates a backup. GitHub schedules can be delayed or skipped, so verify the first scheduled backup appears.
4. Each backup contains only essential football data and the prediction archive, is verified before upload, is limited to 25 MB, and expires from GitHub after seven days. The endpoint permits one authenticated backup attempt per hour per process. Failed status checks still allow the backup step to run. Artifacts inherit repository access; do not add private/user data to these exports without revisiting access controls.
5. Check your GitHub Actions usage allowance and set a spending budget if needed. This uses no additional hosting service, but GitHub Actions storage/minutes have account-specific allowances. Enable notifications for failed workflow runs in GitHub if you want alerts; the code does not itself send email.
6. Before restoring, run `python -c "from scripts.operations_check import verify_backup; print(verify_backup('path/to/ballr.db'))"` locally. Test the downloaded file with a separate `BALLR_DB_PATH` and `/ready`, `/performance`, and a prediction request. Never overwrite the running production database. Retain a downloaded copy outside the container before offline replacement.

The regression suite exports, verifies and reopens a database with saved predictions, checking readiness and the archived records. This is a local restore drill, not proof that the live backup job has run.

Enable **Wait for CI** in Railway's GitHub source settings so failing checks block deployment. This is a dashboard setting; adding the workflow alone does not activate it.

References: [Railway Wait for CI](https://docs.railway.com/deployments/github-autodeploys), [GitHub artifact retention](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-what-your-workflow-does/storing-and-sharing-data-from-a-workflow), [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
