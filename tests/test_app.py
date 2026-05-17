from __future__ import annotations

import time
from datetime import date, timedelta

from fastapi.testclient import TestClient

from googletrends_app import collector, repository
from googletrends_app.database import connect, initialize_database
from googletrends_app.main import create_app
from googletrends_app.time_utils import format_beijing
from googletrends_app.trends import SHORT_TIMEFRAME, TrendPoint


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

    def collect_keyword(self, term: str, timeframe: str, geo: str) -> list[TrendPoint]:
        self.calls.append({"term": term, "timeframe": timeframe, "geo": geo})
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("provider unavailable")
        return self.points


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_text(self, text: str) -> bool:
        self.messages.append(text)
        return True


def make_client(
    tmp_path,
    provider: FakeProvider,
    notifier: FakeNotifier | None = None,
    max_attempts: int = 3,
) -> TestClient:
    app = create_app(
        db_path=tmp_path / "test.sqlite3",
        trends_provider=provider,
        notifier=notifier or FakeNotifier(),
        start_scheduler=False,
        request_delay_seconds=0,
        retry_delay_seconds=0,
        max_attempts=max_attempts,
    )
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


def test_beijing_time_formatting() -> None:
    assert format_beijing("2026-05-17T03:00:00+00:00") == "2026-05-17 11:00:00"
    assert format_beijing("2026-05-17 03:00:00") == "2026-05-17 11:00:00"
    assert format_beijing("2026-05-17") == "2026-05-17 00:00:00"
    assert format_beijing("2026-05-17", timeframe="now 7-d") == "2026-05-17 08:00:00"


def test_manual_collection_queues_job_and_saves_points(tmp_path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        client.post("/keywords", data={"term": "ChatGPT"})

        response = client.post("/collect/run-now", follow_redirects=True)

        assert response.status_code == 200
        assert "Queued 3 collection jobs" in response.text
        wait_until(lambda: len(provider.calls) == 3 and "成功" in client.get("/runs").text)
        assert provider.calls == [
            {"term": "ChatGPT", "timeframe": "now 7-d", "geo": ""},
            {"term": "ChatGPT", "timeframe": "today 3-m", "geo": ""},
            {"term": "ChatGPT", "timeframe": "today 12-m", "geo": ""},
        ]

        detail = client.get("/keywords/1")
        assert detail.status_code == 200
        assert "2025-01-15" in detail.text
        assert ">30<" in detail.text


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
        assert any(
            "页面: http://127.0.0.1:8000/keywords/1?timeframe=now%207-d" in message
            for message in notifier.messages
        )


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
