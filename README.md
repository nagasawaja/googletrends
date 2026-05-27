# Google Trends Monitor MVP

Low-cost MVP for monitoring fixed Google Trends keywords worldwide.

## Features

- Add, pause, resume, and delete fixed keywords.
- Collect worldwide Google Trends data across editable per-keyword timeframes.
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
GOOGLETRENDS_REQUEST_PROFILES_ENABLED=1
GOOGLETRENDS_RETRY_DELAY_SECONDS=5
GOOGLETRENDS_MAX_ATTEMPTS=5
GOOGLETRENDS_PUBLIC_BASE_URL=http://127.0.0.1:8000
GOOGLETRENDS_P1_ALERT_COOLDOWN_HOURS=6
GOOGLETRENDS_P2_ALERT_COOLDOWN_HOURS=24
GOOGLETRENDS_PROXY_URLS=http://user:pass@proxy.example:8080,socks5h://proxy.example:1080
GOOGLETRENDS_PROXY_SUBSCRIPTION_URL=https://example.com/subscription
GOOGLETRENDS_PROXY_REFRESH_SECONDS=3600
GOOGLETRENDS_PROXY_AUTO_DETECT_LOCAL_CLASH=0
GOOGLETRENDS_CLASH_ENABLED=1
GOOGLETRENDS_CLASH_PROXY_URL=http://127.0.0.1:7897
GOOGLETRENDS_CLASH_CONTROLLER_URL=http://127.0.0.1:9097
GOOGLETRENDS_CLASH_SECRET=your-clash-secret
GOOGLETRENDS_CLASH_PROXY_GROUP=Proxies
GOOGLETRENDS_CLASH_CONFIG_PATH=~/.config/clash/config.yaml
GOOGLETRENDS_CLASH_SKIP_PROXY_NAMES=DIRECT,REJECT,REJECT-DROP,PASS
GOOGLETRENDS_CLASH_ALLOWED_PROXY_NAME_KEYWORDS=香港,日本,美国,新加坡,狮城,台湾
GOOGLETRENDS_CLASH_ROTATE_ON_429=1
GOOGLETRENDS_CLASH_ROTATE_ON_ERROR=1
GOOGLETRENDS_CLASH_RETRY_AFTER_ROTATE=1
```

When `FEISHU_WEBHOOK_URL` is not set, notifications are disabled.

`GOOGLETRENDS_REQUEST_PROFILES_ENABLED=1` adds browser-like request headers to pytrends
and keeps each proxy or Clash node bound to a stable request profile. When Clash rotates
after a network failure, the immediate retry uses the new node's profile instead of
reusing the same User-Agent.

Proxy configuration is optional. `GOOGLETRENDS_PROXY_SUBSCRIPTION_URL` is treated as a
secret and should live in `.env`, not in source control. Subscriptions can contain direct
HTTP/SOCKS proxy URLs, base64 proxy URL lists, or Clash YAML with `http`, `https`, and
`socks5` nodes. Clash-native nodes such as `ss`, `vmess`, `vless`, and `trojan` are not
directly usable by Python requests; run Clash separately and point `GOOGLETRENDS_PROXY_URLS`
at its local mixed-port instead.

When no explicit proxy is configured, `GOOGLETRENDS_PROXY_AUTO_DETECT_LOCAL_CLASH=1`
lets the app use `GOOGLETRENDS_CLASH_PROXY_URL` automatically if the local Clash port is
listening. This covers the common local Clash Verge `127.0.0.1:7897` setup without enabling
the Clash controller API.

For a local Clash Verge setup, prefer the gateway mode:

```bash
GOOGLETRENDS_PROXY_URLS=http://127.0.0.1:7897
GOOGLETRENDS_PROXY_AUTO_DETECT_LOCAL_CLASH=0
GOOGLETRENDS_CLASH_ENABLED=1
GOOGLETRENDS_CLASH_PROXY_URL=http://127.0.0.1:7897
GOOGLETRENDS_CLASH_CONTROLLER_URL=http://127.0.0.1:9097
GOOGLETRENDS_CLASH_PROXY_GROUP=Proxies
```

When gateway mode is configured, the app sends pytrends traffic through the local
Clash Verge mixed-port. Clash controller rotation is optional and should only be enabled
when a reachable HTTP external-controller URL is available.
Clash Verge Rev currently exposes Mihomo's controller through
`/tmp/verge/verge-mihomo.sock`; if the HTTP external-controller is not listening,
run the local bridge before starting the Docker app:

```bash
python scripts/clash_verge_controller_bridge.py --host 127.0.0.1 --port 9097
```

In Docker, use `http://host.docker.internal:9097` for
`GOOGLETRENDS_CLASH_CONTROLLER_URL`.
When controller mode is enabled, the app sends pytrends traffic through the local Clash proxy URL.
On a Google 429 or other request failure, it calls the Clash controller API, switches the
currently effective proxy group to the next candidate, then retries the collection once.
In Clash `global` mode this means `GLOBAL`; otherwise it uses `GOOGLETRENDS_CLASH_PROXY_GROUP`.
Only candidates whose names contain one of `GOOGLETRENDS_CLASH_ALLOWED_PROXY_NAME_KEYWORDS`
are eligible for automatic or manual rotation.
If
`GOOGLETRENDS_CLASH_CONTROLLER_URL` or `GOOGLETRENDS_CLASH_SECRET` is empty, the app
tries to read them from `GOOGLETRENDS_CLASH_CONFIG_PATH`.

## Run With Docker

```bash
cp .env.example .env
docker compose up -d --build
```

Open <http://127.0.0.1:8000>. The compose service mounts `./data` into the container, so SQLite data survives container restarts.
By default the container sends outbound traffic through the host Clash Verge proxy at
`http://host.docker.internal:7897`, and it disables local auto-detection so the container
does not mistake its own loopback for the host proxy. That means the first collection
attempt in Docker goes through the proxy if the host port is reachable.

Useful commands:

```bash
docker compose logs -f
docker compose ps
docker compose down
```

## Alert Model

The radar supports editable keyword-level windows:

- Default monitored windows: `now 7-d`, `today 3-m`.
- Available windows: `now 1-d`, `now 7-d`, `today 1-m`, `today 3-m`, `today 12-m`, `today 5-y`.
- Short windows detect sudden spikes, small warm-ups, and short-term drops.
- Mid/long windows confirm breakouts, steady rises, and declines.

Alerts are stored with severity, category, timeframe, current value, baseline value, and percent change. Upward and breakout alerts are sent to Feishu; decline and cooling alerts stay in the UI/history without Feishu notifications. The same keyword, severity, category, and timeframe are cooled down before another notification is created.

中文运行流程说明见 [`docs/runtime_flow_zh.md`](docs/runtime_flow_zh.md)。

## Tests

```bash
pytest
```

Tests use a fake Trends provider and do not call Google.
