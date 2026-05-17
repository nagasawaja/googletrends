# Google Trends Monitor MVP

Low-cost MVP for monitoring fixed Google Trends keywords worldwide.

## Features

- Add, pause, resume, and delete fixed keywords.
- Collect worldwide Google Trends data across `now 7-d`, `today 3-m`, and `today 12-m`.
- Run collection manually from the web UI.
- Schedule hourly short-window job creation and daily context-window job creation.
- Process queued jobs in a background worker with retry and request spacing.
- Send Feishu bot notifications with cooldown, keyword page links, and suggested actions.
- Store trend points, collection runs, and simple alerts in SQLite.
- View keyword trend charts in a simple server-rendered web UI.
- Run historical backtests against stored trend points.

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn googletrends_app.main:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>.

The default database is `data/googletrends.sqlite3`. Useful environment variables:

```bash
GOOGLETRENDS_DB_PATH=/path/to/app.sqlite3
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
GOOGLETRENDS_REQUEST_DELAY_SECONDS=2
GOOGLETRENDS_RETRY_DELAY_SECONDS=300
GOOGLETRENDS_MAX_ATTEMPTS=3
GOOGLETRENDS_PUBLIC_BASE_URL=http://127.0.0.1:8000
GOOGLETRENDS_P1_ALERT_COOLDOWN_HOURS=6
GOOGLETRENDS_P2_ALERT_COOLDOWN_HOURS=24
```

When `FEISHU_WEBHOOK_URL` is not set, notifications are disabled.

## Run With Docker

```bash
cp .env.example .env
docker compose up -d --build
```

Open <http://127.0.0.1:8000>. The compose service mounts `./data` into the container, so SQLite data survives container restarts.

Useful commands:

```bash
docker compose logs -f
docker compose ps
docker compose down
```

## Alert Model

The radar uses three windows:

- `now 7-d`: detects sudden spikes, small warm-ups, and short-term drops.
- `today 3-m`: confirms medium-term breakouts, steady rises, and steady declines.
- `today 12-m`: checks whether a keyword is near a 12-month high or has meaningful long-term movement.

Alerts are stored with severity, category, timeframe, current value, baseline value, and percent change. New alerts are sent to Feishu. The same keyword, severity, category, and timeframe are cooled down before another notification is created.

中文运行流程说明见 [`docs/runtime_flow_zh.md`](docs/runtime_flow_zh.md)。

## Tests

```bash
pytest
```

Tests use a fake Trends provider and do not call Google.
