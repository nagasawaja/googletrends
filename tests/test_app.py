from __future__ import annotations

import os
import sqlite3
import time
from datetime import date, timedelta

from fastapi.testclient import TestClient

from googletrends_app import collector, repository
from googletrends_app.database import connect, initialize_database
from googletrends_app.main import build_chart_with_size, create_app
from googletrends_app.proxy_check import ProxyCheckResult
from googletrends_app.time_utils import format_beijing
from googletrends_app.trends import DEFAULT_GPROP, SHORT_TIMEFRAME, TrendPoint


class FakeProvider:
    def __init__(
        self,
        points: list[TrendPoint] | None = None,
        fail_times: int = 0,
    ) -> None:
        self.points = points or [
            TrendPoint(date=date(2025, 1, 1), value=10),
            TrendPoint(date=date(2025, 1, 8), value=20),
            TrendPoint(date=date(2025, 1, 15), value=30),
        ]
        self.fail_times = fail_times
        self.calls: list[dict[str, str]] = []
        self.last_proxy_name: str | None = None
        self.last_proxy_url: str | None = None
        self.last_profile_key: str | None = None

    def collect_keyword(
        self,
        term: str,
        timeframe: str,
        geo: str,
        gprop: str = DEFAULT_GPROP,
    ) -> list[TrendPoint]:
        self.calls.append({"term": term, "timeframe": timeframe, "geo": geo, "gprop": gprop})
        attempt_no = len(self.calls)
        self.last_proxy_name = f"node-{attempt_no}"
        self.last_proxy_url = f"http://proxy-{attempt_no}.example:8080"
        self.last_profile_key = self.last_proxy_url
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("provider unavailable")
        return self.points


class FailingProvider:
    def __init__(self, error: str) -> None:
        self.error = error

    def collect_keyword(
        self,
        term: str,
        timeframe: str,
        geo: str,
        gprop: str = DEFAULT_GPROP,
    ) -> list[TrendPoint]:
        raise RuntimeError(self.error)


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_text(self, text: str) -> bool:
        self.messages.append(text)
        return True


class FakeUiClashController:
    controller_url = "http://127.0.0.1:49266"
    proxy_group = "Google"

    def __init__(self) -> None:
        self.rotations = 0
        self.group: dict[str, object] = {
            "now": "node-a",
            "all": ["DIRECT", "node-a", "node-b"],
        }

    def get_mode(self) -> str:
        return "rule"

    def effective_proxy_group_name(self) -> str:
        return self.proxy_group

    def get_proxy_group(self, proxy_group: str | None = None) -> dict[str, object]:
        return self.group

    def available_candidates(self, group: dict[str, object]) -> list[str]:
        values = group.get("all")
        if not isinstance(values, list):
            return []
        return [str(value) for value in values if value != "DIRECT"]

    def rotate_proxy(self) -> str:
        self.rotations += 1
        self.group["now"] = "node-b"
        return "node-b"

    def rotate_proxy_with_group(self) -> tuple[str, str]:
        selected = self.rotate_proxy()
        return self.proxy_group, selected


class FakeGlobalClashController(FakeUiClashController):
    def __init__(self) -> None:
        super().__init__()
        self.group = {
            "now": "global-node-a",
            "all": ["DIRECT", "global-node-a", "global-node-b"],
        }

    def get_mode(self) -> str:
        return "global"

    def effective_proxy_group_name(self) -> str:
        return "GLOBAL"


def make_client(
    tmp_path,
    provider: FakeProvider,
    notifier: FakeNotifier | None = None,
    max_attempts: int = 5,
) -> TestClient:
    previous_auto_detect = os.environ.get("GOOGLETRENDS_PROXY_AUTO_DETECT_LOCAL_CLASH")
    os.environ["GOOGLETRENDS_PROXY_AUTO_DETECT_LOCAL_CLASH"] = "0"
    try:
        app = create_app(
            db_path=tmp_path / "test.sqlite3",
            trends_provider=provider,
            notifier=notifier or FakeNotifier(),
            start_scheduler=False,
            request_delay_seconds=0,
            retry_delay_seconds=0,
            max_attempts=max_attempts,
        )
    finally:
        if previous_auto_detect is None:
            os.environ.pop("GOOGLETRENDS_PROXY_AUTO_DETECT_LOCAL_CLASH", None)
        else:
            os.environ["GOOGLETRENDS_PROXY_AUTO_DETECT_LOCAL_CLASH"] = previous_auto_detect
    return TestClient(app)


def wait_until(predicate, timeout_seconds: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


def test_keyword_can_be_added_and_listed(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        response = client.post("/keywords", data={"term": "ChatGPT"}, follow_redirects=True)

        assert response.status_code == 200
        assert "ChatGPT" in response.text
        assert "启用" in response.text


def test_keyword_remark_can_be_saved_and_displayed(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        client.post("/keywords", data={"term": "ChatGPT"})

        response = client.post(
            "/keywords/1/remark",
            data={"remark": "重点观察国内外热度差异"},
        )
        assert response.status_code == 200
        assert response.json() == {"remark": "重点观察国内外热度差异"}

        index = client.get("/")
        assert index.status_code == 200
        assert "重点观察国内外热度差异" in index.text
        assert "remark-editor.js" in index.text


def test_beijing_time_formatting() -> None:
    assert format_beijing("2026-05-17T03:00:00+00:00") == "2026-05-17 11:00:00"
    assert format_beijing("2026-05-17 03:00:00") == "2026-05-17 11:00:00"
    assert format_beijing("2026-05-17") == "2026-05-17 00:00:00"
    assert format_beijing("2026-05-17", timeframe="now 7-d") == "2026-05-17 08:00:00"


def test_proxy_check_page_shows_current_status(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        response = client.get("/proxy-check")

        assert response.status_code == 200
        assert "代理验证" in response.text
        assert "当前配置" in response.text
        assert "未启用" in response.text


def test_proxy_check_can_be_run_without_real_network(tmp_path, monkeypatch) -> None:
    def fake_run_proxy_check(settings, proxy_pool):
        return ProxyCheckResult(
            ok=True,
            checked_at="2026-05-18 10:00:00",
            proxy_url="http://127.0.0.1:7890",
            proxy_ip="203.0.113.10",
            direct_ip="198.51.100.20",
            elapsed_ms=123,
            error="",
            warning="",
        )

    monkeypatch.setattr("googletrends_app.main.run_proxy_check", fake_run_proxy_check)
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        response = client.post("/proxy-check")

        assert response.status_code == 200
        assert "验证结果" in response.text
        assert "203.0.113.10" in response.text
        assert "2026-05-18 10:00:00" in response.text


def test_proxy_check_page_shows_clash_controller_status(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        client.app.state.clash_controller = FakeUiClashController()

        response = client.get("/proxy-check")

        assert response.status_code == 200
        assert "Clash 控制器可用" in response.text
        assert "node-a" in response.text
        assert "网络失败自动切换" in response.text


def test_proxy_check_page_focuses_on_effective_group_in_global_mode(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        client.app.state.clash_controller = FakeGlobalClashController()

        response = client.get("/proxy-check")

        assert response.status_code == 200
        assert "代理入口" in response.text
        assert "订阅代理" not in response.text
        assert "配置代理组" not in response.text
        assert "规则模式代理组" not in response.text
        assert "生效代理组" in response.text
        assert "GLOBAL" in response.text
        assert "global-node-a" in response.text


def test_proxy_check_can_rotate_clash_proxy_group(tmp_path) -> None:
    provider = FakeProvider()
    clash = FakeUiClashController()
    with make_client(tmp_path, provider) as client:
        client.app.state.clash_controller = clash

        response = client.post("/proxy-check/rotate-clash")

        assert response.status_code == 200
        assert "节点切换结果" in response.text
        assert "node-b" in response.text
        assert clash.rotations == 1


def test_manual_collection_queues_job_and_saves_points(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        client.post("/keywords", data={"term": "ChatGPT"})

        response = client.post("/collect/run-now", follow_redirects=True)

        assert response.status_code == 200
        assert "Queued 2 collection jobs" in response.text
        wait_until(lambda: len(provider.calls) == 2 and "成功" in client.get("/runs").text)
        assert provider.calls == [
            {"term": "ChatGPT", "timeframe": "now 7-d", "geo": "", "gprop": ""},
            {"term": "ChatGPT", "timeframe": "today 3-m", "geo": "", "gprop": ""},
        ]

        detail = client.get("/keywords/1")
        assert detail.status_code == 200
        assert "2025-01-15" in detail.text
        assert ">30<" in detail.text

        index = client.get("/")
        assert "mini-chart" in index.text
        assert "now 7-d" in index.text
        assert "最新 30" in index.text


def test_chart_building_includes_axis_ticks() -> None:
    points = [
        {
            "point_date": date(2025, 1, day).isoformat(),
            "value": day,
            "timeframe": "today 3-m",
        }
        for day in range(1, 10)
    ]

    chart = build_chart_with_size(points, width=900, height=280, padding_x=32, padding_y=20)

    assert [tick["label"] for tick in chart["y_ticks"]] == ["100", "75", "50", "25", "0"]
    assert [tick["label"] for tick in chart["x_ticks"]] == [
        "01-01",
        "01-03",
        "01-05",
        "01-07",
        "01-09",
    ]
    assert [tick["anchor"] for tick in chart["x_ticks"]] == [
        "start",
        "middle",
        "middle",
        "middle",
        "end",
    ]


def test_keyword_timeframes_can_be_changed_and_used_for_collection(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        client.post("/keywords", data={"term": "ChatGPT"})
        response = client.post(
            "/keywords/1/timeframes",
            data={"timeframes": ["now 1-d", "today 12-m"]},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert "now 1-d" in response.text
        assert "today 12-m" in response.text

        response = client.post("/collect/run-now", follow_redirects=True)
        assert "Queued 2 collection jobs" in response.text
        wait_until(lambda: len(provider.calls) == 2 and "成功" in client.get("/runs").text)
        assert provider.calls == [
            {"term": "ChatGPT", "timeframe": "now 1-d", "geo": "", "gprop": ""},
            {"term": "ChatGPT", "timeframe": "today 12-m", "geo": "", "gprop": ""},
        ]

        index = client.get("/")
        assert 'href="/keywords/1?timeframe=now 1-d&gprop="' in index.text
        assert "ChatGPT now 1-d trend chart" in index.text


def test_keyword_timeframes_can_be_bulk_updated(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        client.post("/keywords", data={"term": "ChatGPT"})
        client.post("/keywords", data={"term": "Gemini"})

        response = client.post(
            "/keywords/timeframes/bulk",
            data={
                "keyword_ids": ["1", "2"],
                "timeframes": ["now 1-d", "today 12-m"],
                "action": "add",
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert "now 1-d, today 12-m added to 2 keyword(s)." in response.text

        response = client.post(
            "/keywords/timeframes/bulk",
            data={
                "keyword_ids": ["1", "2"],
                "timeframes": ["now 7-d", "today 3-m"],
                "action": "remove",
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert "now 7-d, today 3-m removed from 2 keyword(s)." in response.text

        with connect(client.app.state.db_path) as conn:
            keyword1 = repository.get_keyword(conn, 1)
            keyword2 = repository.get_keyword(conn, 2)
            assert repository.keyword_timeframes(keyword1) == ("now 1-d", "today 12-m")
            assert repository.keyword_timeframes(keyword2) == ("now 1-d", "today 12-m")


def test_index_edit_mode_controls_bulk_timeframes(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        client.post("/keywords", data={"term": "ChatGPT"})

        default_index = client.get("/")
        assert "批量监控周期" not in default_index.text
        assert "进入编辑模式" in default_index.text
        assert "全选" not in default_index.text

        edit_index = client.get("/?edit=1")
        assert edit_index.status_code == 200
        assert "批量监控周期" in edit_index.text
        assert "监控周期" in edit_index.text
        assert "now 7-d，today 3-m" in edit_index.text
        assert "全选" in edit_index.text
        assert "取消全选" in edit_index.text
        assert "全选周期" in edit_index.text
        assert "清空周期" in edit_index.text
        assert 'type="checkbox" name="keyword_ids"' in edit_index.text
        assert "退出编辑模式" in edit_index.text


def test_bulk_timeframe_action_keeps_edit_mode_query(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        client.post("/keywords", data={"term": "ChatGPT"})

        response = client.post(
            "/keywords/timeframes/bulk",
            data={
                "keyword_ids": ["1"],
                "timeframes": ["today 12-m", "now 1-d"],
                "action": "add",
                "next_url": "/?edit=1",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?edit=1&message=today+12-m%2C+now+1-d+added+to+1+keyword%28s%29."


def test_keyword_gprops_can_be_changed_and_used_for_collection(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        client.post("/keywords", data={"term": "ChatGPT"})
        response = client.post(
            "/keywords/1/gprops",
            data={"gprops": ["", "youtube"]},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert "搜索来源：网页搜索" in response.text
        assert "YouTube" in response.text

        response = client.post("/keywords/1/collect", follow_redirects=True)

        assert response.status_code == 200
        wait_until(lambda: len(provider.calls) == 4 and "成功" in client.get("/runs").text)
        runs_page = client.get("/runs")
        assert "搜索来源" in runs_page.text
        assert "网页搜索" in runs_page.text
        assert "YouTube" in runs_page.text
        assert provider.calls == [
            {"term": "ChatGPT", "timeframe": "now 7-d", "geo": "", "gprop": ""},
            {"term": "ChatGPT", "timeframe": "now 7-d", "geo": "", "gprop": "youtube"},
            {"term": "ChatGPT", "timeframe": "today 3-m", "geo": "", "gprop": ""},
            {"term": "ChatGPT", "timeframe": "today 3-m", "geo": "", "gprop": "youtube"},
        ]
        detail = client.get("/keywords/1?gprop=youtube")
        assert detail.status_code == 200
        assert "Related Queries" not in detail.text
        assert "Related Topics" not in detail.text


def test_failed_collection_records_job_error_and_notifies(tmp_path) -> None:
    provider = FakeProvider(fail_times=1)
    notifier = FakeNotifier()
    with make_client(tmp_path, provider, notifier=notifier, max_attempts=1) as client:
        client.post("/keywords", data={"term": "ChatGPT"})

        client.post("/collect/run-now", follow_redirects=True)

        wait_until(lambda: "provider unavailable" in client.get("/runs").text)
        response = client.get("/runs")

        assert response.status_code == 200
        assert "失败" in response.text
        assert "provider unavailable" in response.text
        assert notifier.messages == [
            "Google Trends collection failed\nKeyword: ChatGPT\nError: provider unavailable"
        ]


def test_collection_runs_show_failed_attempt_details_for_retries(tmp_path) -> None:
    provider = FakeProvider(fail_times=2)
    with make_client(tmp_path, provider, max_attempts=5) as client:
        with connect(client.app.state.db_path) as conn:
            keyword = repository.create_keyword(conn, "ChatGPT")
            collector.enqueue_keyword_job(
                conn,
                keyword_id=keyword["id"],
                max_attempts=5,
                timeframe=SHORT_TIMEFRAME,
            )

        collector.process_due_jobs_for_path(
            db_path=client.app.state.db_path,
            provider=provider,
            retry_delay_seconds=0,
            request_delay_seconds=0,
            max_jobs=3,
        )

        response = client.get("/runs")

        assert response.status_code == 200
        assert "失败明细" in response.text
        assert "第 1 次" in response.text
        assert "第 2 次" in response.text
        assert "node-1" in response.text
        assert "node-2" in response.text

        with connect(client.app.state.db_path) as conn:
            jobs = repository.list_jobs(conn, limit=10)
            job = next(run for run in jobs if run["term"] == "ChatGPT")
            assert job["attempts"] == 3
            attempts = repository.list_collection_job_attempts(conn, job["id"])
            assert [row["attempt_no"] for row in attempts] == [1, 2]
            assert [row["proxy_name"] for row in attempts] == [
                "node-1",
                "node-2",
            ]
            assert [row["proxy_url"] for row in attempts] == [
                "http://proxy-1.example:8080",
                "http://proxy-2.example:8080",
            ]


def test_collection_runs_show_clash_profile_name_in_attempt_details(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        with connect(client.app.state.db_path) as conn:
            keyword = repository.create_keyword(conn, "Rapidus")
            job = repository.create_collection_job(
                conn,
                keyword["id"],
                timeframe="today 3-m",
            )
            conn.execute(
                """
                INSERT INTO collection_job_attempts
                    (job_id, attempt_no, status, proxy_name, proxy_url, profile_key, error, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"],
                    1,
                    "failed",
                    "proxy-1",
                    "http://host.docker.internal:7890",
                    "clash:🇯🇵 高级 专线 日本 03",
                    "Google Trends 限流 429：请求过于频繁，已自动延后重试。",
                    "2026-05-19T17:52:55+00:00",
                    "2026-05-19T17:52:59+00:00",
                ),
            )
            conn.execute(
                """
                UPDATE collection_jobs
                SET attempts = 2,
                    status = 'success',
                    started_at = '2026-05-19T17:53:22+00:00',
                    finished_at = '2026-05-19T17:53:26+00:00'
                WHERE id = ?
                """,
                (job["id"],),
            )
            conn.commit()

        response = client.get("/runs")

        assert response.status_code == 200
        assert "🇯🇵 高级 专线 日本 03" in response.text
        assert "proxy-1" not in response.text


def test_startup_requeues_stale_running_jobs(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    initialize_database(db_path)
    with connect(db_path) as conn:
        keyword = repository.create_keyword(conn, "ChatGPT")
        job = repository.create_collection_job(conn, keyword["id"], timeframe=SHORT_TIMEFRAME)
        conn.execute(
            """
            UPDATE collection_jobs
            SET status = 'running',
                started_at = '2026-05-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (job["id"],),
        )
        conn.commit()

    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        with connect(client.app.state.db_path) as conn:
            refreshed = repository.get_collection_job(conn, job["id"])
            assert refreshed["status"] == "queued"
            assert refreshed["started_at"] is None


def test_migration_adds_collection_job_attempt_metadata_columns(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE collection_job_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                attempt_no INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'failed',
                error TEXT,
                finished_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    initialize_database(db_path)

    with connect(db_path) as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(collection_job_attempts)")
        }

    assert {"proxy_name", "proxy_url", "profile_key", "started_at"} <= columns


def test_failed_job_is_requeued_before_max_attempts(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    initialize_database(db_path)
    provider = FakeProvider(fail_times=1)
    notifier = FakeNotifier()

    with connect(db_path) as conn:
        keyword = repository.create_keyword(conn, "ChatGPT")
        job = collector.enqueue_keyword_job(
            conn,
            keyword_id=keyword["id"],
            max_attempts=2,
        )

        results = collector.process_due_jobs(
            conn,
            provider,
            notifier=notifier,
            retry_delay_seconds=60,
            request_delay_seconds=0,
            max_jobs=1,
        )
        updated = repository.get_collection_job(conn, job["id"])

        assert results[0]["status"] == "queued"
        assert updated["status"] == "queued"
        assert updated["attempts"] == 1
        assert updated["next_attempt_at"] is not None
        assert notifier.messages == []


def test_failed_job_requeues_if_attempt_recording_fails(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "test.sqlite3"
    initialize_database(db_path)
    provider = FakeProvider(fail_times=1)

    def fail_record_attempt(*args, **kwargs) -> None:
        raise sqlite3.OperationalError("old attempt table")

    monkeypatch.setattr(repository, "record_collection_job_attempt", fail_record_attempt)

    with connect(db_path) as conn:
        keyword = repository.create_keyword(conn, "ChatGPT")
        job = collector.enqueue_keyword_job(
            conn,
            keyword_id=keyword["id"],
            max_attempts=2,
        )

        results = collector.process_due_jobs(
            conn,
            provider,
            retry_delay_seconds=60,
            request_delay_seconds=0,
            max_jobs=1,
        )
        updated = repository.get_collection_job(conn, job["id"])

    assert results[0]["status"] == "queued"
    assert updated["status"] == "queued"
    assert "failed to record attempt: old attempt table" in updated["error"]


def test_google_rate_limit_uses_short_retry_delay_with_proxy_rotation(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    initialize_database(db_path)
    provider = FailingProvider("The request failed: Google returned a response with code 429")
    notifier = FakeNotifier()

    with connect(db_path) as conn:
        keyword = repository.create_keyword(conn, "ChatGPT")
        job = collector.enqueue_keyword_job(
            conn,
            keyword_id=keyword["id"],
            max_attempts=2,
        )

        results = collector.process_due_jobs(
            conn,
            provider,
            notifier=notifier,
            retry_delay_seconds=60,
            request_delay_seconds=0,
            max_jobs=1,
        )
        updated = repository.get_collection_job(conn, job["id"])

        assert results[0]["status"] == "queued"
        assert results[0]["retry_delay_seconds"] == 5
        assert updated["status"] == "queued"
        assert updated["error"] == "Google Trends 限流 429：请求过于频繁，已自动延后重试。"
        assert updated["next_attempt_at"] is not None
        assert notifier.messages == []


def test_alert_is_created_and_notified_for_recent_spike(tmp_path) -> None:
    start = date(2025, 1, 1)
    points: list[TrendPoint] = []
    for index in range(30):
        points.append(TrendPoint(date=start + timedelta(days=index * 7), value=10))
    for index in range(7):
        points.append(TrendPoint(date=start + timedelta(days=(30 + index) * 7), value=20))
    provider = FakeProvider(points=points)
    notifier = FakeNotifier()

    with make_client(tmp_path, provider, notifier=notifier) as client:
        client.post("/keywords", data={"term": "AI"})
        client.post("/collect/run-now", follow_redirects=True)

        wait_until(lambda: "trend_radar:now 7-d:warming_up" in client.get("/alerts").text)
        response = client.get("/alerts")

        assert response.status_code == 200
        assert "AI" in response.text
        assert "trend_radar:now 7-d:warming_up" in response.text
        assert "短线搜索热度小幅升温" in response.text
        assert any("Google Trends 告警" in message for message in notifier.messages)
        assert any("关键词: AI" in message for message in notifier.messages)
        assert any("类型: warming_up" in message for message in notifier.messages)
        assert any("建议动作:" in message for message in notifier.messages)
        assert all("触发点: 2025-" in message for message in notifier.messages)
        assert any(
            "页面: http://127.0.0.1:8000/keywords/1?timeframe=now%207-d" in message
            for message in notifier.messages
        )


def test_decline_alert_is_recorded_but_not_notified(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    initialize_database(db_path)
    start = date(2025, 1, 1)
    points: list[TrendPoint] = []
    for index in range(30):
        points.append(TrendPoint(date=start + timedelta(days=index * 7), value=50))
    for index in range(3):
        points.append(TrendPoint(date=start + timedelta(days=(30 + index) * 7), value=10))
    provider = FakeProvider(points=points)
    notifier = FakeNotifier()

    with connect(db_path) as conn:
        keyword = repository.create_keyword(conn, "AI")
        collector.enqueue_keyword_job(
            conn,
            keyword_id=keyword["id"],
            timeframe=SHORT_TIMEFRAME,
        )
        results = collector.process_due_jobs(
            conn,
            provider,
            notifier=notifier,
            request_delay_seconds=0,
            max_jobs=1,
            public_base_url="http://monitor.test",
        )
        alerts = repository.list_alerts(conn)

    assert results[0]["alert_count"] == 1
    assert alerts[0]["category"] == "sudden_drop"
    assert notifier.messages == []


def test_alert_notification_formats_point_date_as_beijing_time() -> None:
    alert = collector.AlertDecision(
        rule="trend_radar:now 7-d:warming_up",
        severity="P2",
        category="warming_up",
        timeframe=SHORT_TIMEFRAME,
        point_date="2026-05-17T03:00:00+00:00",
        current_value=40,
        baseline_value=20,
        change_pct=100,
        message="短线搜索热度小幅升温，建议观察是否持续放量。",
    )

    message = collector.format_alert_notification(
        "AI",
        alert,
        keyword_id=1,
        public_base_url="http://monitor.test",
    )

    assert "触发点: 2026-05-17 11:00:00" in message


def test_alert_cooldown_suppresses_repeated_same_category(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    initialize_database(db_path)
    start = date(2025, 1, 1)
    first_points: list[TrendPoint] = []
    for index in range(30):
        first_points.append(TrendPoint(date=start + timedelta(days=index * 7), value=10))
    for index in range(7):
        first_points.append(
            TrendPoint(date=start + timedelta(days=(30 + index) * 7), value=20)
        )
    second_points = [
        *first_points,
        TrendPoint(date=start + timedelta(days=37 * 7), value=20),
    ]
    provider = FakeProvider(points=first_points)
    notifier = FakeNotifier()

    with connect(db_path) as conn:
        keyword = repository.create_keyword(conn, "AI")
        collector.enqueue_keyword_job(
            conn,
            keyword_id=keyword["id"],
            timeframe=SHORT_TIMEFRAME,
        )
        first_result = collector.process_due_jobs(
            conn,
            provider,
            notifier=notifier,
            request_delay_seconds=0,
            max_jobs=1,
            public_base_url="http://monitor.test",
        )

        provider.points = second_points
        collector.enqueue_keyword_job(
            conn,
            keyword_id=keyword["id"],
            timeframe=SHORT_TIMEFRAME,
        )
        second_result = collector.process_due_jobs(
            conn,
            provider,
            notifier=notifier,
            request_delay_seconds=0,
            max_jobs=1,
            public_base_url="http://monitor.test",
        )

        alerts = repository.list_alerts(conn)

    assert first_result[0]["alert_count"] == 1
    assert second_result[0]["alert_count"] == 0
    assert len(alerts) == 1
    assert len(notifier.messages) == 1


def test_alert_remark_can_be_saved_and_displayed(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    provider = FakeProvider()
    app = create_app(
        db_path=db_path,
        trends_provider=provider,
        notifier=FakeNotifier(),
        start_scheduler=False,
        request_delay_seconds=0,
    )

    with TestClient(app) as client:
        with connect(db_path) as conn:
            keyword = repository.create_keyword(conn, "AI")
            created = repository.insert_alert(
                conn,
                keyword_id=keyword["id"],
                rule="trend_radar:now 7-d:warming_up",
                severity="P2",
                category="warming_up",
                timeframe=SHORT_TIMEFRAME,
                gprop="",
                point_date="2026-05-17T11:00:00+08:00",
                current_value=40,
                baseline_value=20,
                change_pct=100,
                message="短线搜索热度小幅升温，建议观察是否持续放量。",
            )
            alert_id = repository.list_alerts(conn)[0]["id"]

        assert created is True
        response = client.post(
            f"/alerts/{alert_id}/remark",
            data={"remark": "已确认，有价值"},
        )
        assert response.status_code == 200
        assert response.json() == {"remark": "已确认，有价值"}

        alerts_page = client.get("/alerts")
        assert alerts_page.status_code == 200
        assert "已确认，有价值" in alerts_page.text


def test_backtest_page_lists_simulated_alerts(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    provider = FakeProvider()
    app = create_app(
        db_path=db_path,
        trends_provider=provider,
        notifier=FakeNotifier(),
        start_scheduler=False,
        request_delay_seconds=0,
    )

    with TestClient(app) as client:
        start = date(2025, 1, 1)
        points: list[TrendPoint] = []
        for index in range(30):
            points.append(TrendPoint(date=start + timedelta(days=index * 7), value=10))
        for index in range(7):
            points.append(TrendPoint(date=start + timedelta(days=(30 + index) * 7), value=20))

        with connect(db_path) as conn:
            keyword = repository.create_keyword(conn, "AI")
            repository.upsert_trend_points(
                conn,
                keyword_id=keyword["id"],
                points=[point.as_record() for point in points],
                geo="",
                gprop="",
                timeframe=SHORT_TIMEFRAME,
                collected_at=collector.utc_now(),
            )

        response = client.get(
            "/backtest",
            params={"keyword_id": 1, "timeframe": SHORT_TIMEFRAME},
        )

    assert response.status_code == 200
    assert "历史回测" in response.text
    assert "trend_radar:now 7-d:warming_up" in response.text
    assert "短线搜索热度小幅升温" in response.text
