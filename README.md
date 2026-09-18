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
7. Enable Railway volume backups, then perform a restore drill in a separate staging service before public launch.
8. Once staging is verified, add the Namecheap domain in Railway and copy Railway's DNS records to Namecheap. Railway manages HTTPS.

References: [Railway volumes](https://docs.railway.com/volumes), [health checks](https://docs.railway.com/deployments/healthchecks), [custom domains](https://docs.railway.com/networking/domains).

## Data behavior and limits

- `FOOTBALL_SEASON` optionally overrides the season's start year. By default the three configured European competitions roll over in July. Current-season requests are cached for two minutes and paced at least 6.2 seconds apart within this process; other apps sharing the key can still consume its quota.
- Historical ingestion targets the preceding season. Current results refresh every 30 minutes. `/ready` requires finished history and a successful current-season sync within two hours for each league; `/health` checks schema access and key configuration. Railway deployment checks are not continuous monitoring: monitor `/ready` separately after launch.
- Polymarket discovery is cached for one minute, stream subscriptions are rediscovered every 15 minutes, and failures retry. Connections restart on subscription changes to receive fresh book snapshots. A market comparison requires home, draw, and away quotes no more than five minutes old. Quiet markets can therefore show no recent odds. Market matching still requires matching provider team names.
- SQLite uses WAL, a busy timeout, and indexes for match and latest-price reads. Ticks older than 48 hours are deleted hourly. Freed pages are reused; no disruptive hourly `VACUUM` runs. For an existing oversized database, compact a backup offline during maintenance if necessary.
- Ratings and goal profiles include available historical and current-season results. Win/draw/win and scoreline projections are separate models. Calibration and broader model validation remain separate work.

A successful local check is not a verified Railway deployment. Complete the staging, persistence, backup restore, and live-provider checks above before opening the service publicly.
