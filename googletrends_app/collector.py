from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from . import repository
from .database import connect, initialize_database
from .notifier import Notifier, NullNotifier
from .time_utils import format_beijing
from .trends import (
    DEFAULT_GEO,
    DEFAULT_TIMEFRAME,
    LONG_TIMEFRAMES,
    MID_TIMEFRAMES,
    NOW_TIMEFRAMES,
    TrendsProvider,
)

ALERT_RULE_PREFIX = "trend_radar"
DEFAULT_PUBLIC_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_P1_ALERT_COOLDOWN_HOURS = 6
DEFAULT_P2_ALERT_COOLDOWN_HOURS = 24
RATE_LIMIT_RETRY_SECONDS = 30 * 60


@dataclass(frozen=True)
class AlertDecision:
    rule: str
    severity: str
    category: str
    timeframe: str
    point_date: str
    current_value: float
    baseline_value: float
    change_pct: float | None
    message: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sqlite_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def collect_keyword(
    conn: sqlite3.Connection,
    keyword_id: int,
    provider: TrendsProvider,
    timeframe: str = DEFAULT_TIMEFRAME,
    geo: str = DEFAULT_GEO,
) -> dict[str, object]:
    keyword = repository.get_keyword(conn, keyword_id)
    if keyword is None:
        raise LookupError(f"Keyword id {keyword_id} was not found.")

    started_at = utc_now()
    run = repository.create_run(conn, keyword_id, started_at)

    try:
        points = provider.collect_keyword(keyword["term"], timeframe=timeframe, geo=geo)
        collected_at = utc_now()
        records = [point.as_record() for point in points]
        count = repository.upsert_trend_points(
            conn, keyword_id, records, geo=geo, timeframe=timeframe, collected_at=collected_at
        )
        alerts = evaluate_alerts(conn, keyword_id, timeframe=timeframe)
        repository.finish_run(
            conn,
            run["id"],
            status="success",
            finished_at=utc_now(),
            points_collected=count,
        )
        return {
            "keyword_id": keyword_id,
            "term": keyword["term"],
            "status": "success",
            "points_collected": count,
            "alert_created": bool(alerts),
            "alert_count": len(alerts),
        }
    except Exception as exc:
        repository.finish_run(
            conn,
            run["id"],
            status="failed",
            finished_at=utc_now(),
            error=str(exc),
        )
        return {
            "keyword_id": keyword_id,
            "term": keyword["term"],
            "status": "failed",
            "points_collected": 0,
            "error": str(exc),
        }


def collect_all_enabled(
    conn: sqlite3.Connection,
    provider: TrendsProvider,
    timeframe: str = DEFAULT_TIMEFRAME,
    geo: str = DEFAULT_GEO,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for keyword in repository.list_enabled_keywords(conn):
        results.append(collect_keyword(conn, keyword["id"], provider, timeframe, geo))
    return results


def collect_all_enabled_for_path(
    db_path: str | Path,
    provider: TrendsProvider,
    timeframe: str = DEFAULT_TIMEFRAME,
    geo: str = DEFAULT_GEO,
) -> list[dict[str, object]]:
    initialize_database(db_path)
    with connect(db_path) as conn:
        return collect_all_enabled(conn, provider, timeframe=timeframe, geo=geo)


def enqueue_keyword_job(
    conn: sqlite3.Connection,
    keyword_id: int,
    source: str = "manual",
    max_attempts: int = 3,
    timeframe: str = DEFAULT_TIMEFRAME,
    geo: str = DEFAULT_GEO,
) -> sqlite3.Row:
    return repository.create_collection_job(
        conn,
        keyword_id=keyword_id,
        source=source,
        max_attempts=max_attempts,
        timeframe=timeframe,
        geo=geo,
    )


def enqueue_keyword_jobs(
    conn: sqlite3.Connection,
    keyword_id: int,
    source: str = "manual",
    max_attempts: int = 3,
    timeframes: tuple[str, ...] | None = None,
    geo: str = DEFAULT_GEO,
) -> list[sqlite3.Row]:
    selected_timeframes = timeframes
    if selected_timeframes is None:
        keyword = repository.get_keyword(conn, keyword_id)
        if keyword is None:
            raise LookupError(f"Keyword id {keyword_id} was not found.")
        selected_timeframes = repository.keyword_timeframes(keyword)
    return [
        enqueue_keyword_job(
            conn,
            keyword_id=keyword_id,
            source=source,
            max_attempts=max_attempts,
            timeframe=timeframe,
            geo=geo,
        )
        for timeframe in selected_timeframes
    ]


def enqueue_enabled_jobs(
    conn: sqlite3.Connection,
    source: str = "manual",
    max_attempts: int = 3,
    timeframes: tuple[str, ...] | None = None,
    geo: str = DEFAULT_GEO,
) -> list[sqlite3.Row]:
    return repository.create_collection_jobs_for_enabled(
        conn,
        source=source,
        max_attempts=max_attempts,
        timeframes=timeframes,
        geo=geo,
    )


def enqueue_enabled_jobs_for_path(
    db_path: str | Path,
    source: str = "scheduled",
    max_attempts: int = 3,
    timeframes: tuple[str, ...] | None = None,
    geo: str = DEFAULT_GEO,
) -> list[int]:
    initialize_database(db_path)
    with connect(db_path) as conn:
        jobs = enqueue_enabled_jobs(
            conn,
            source=source,
            max_attempts=max_attempts,
            timeframes=timeframes,
            geo=geo,
        )
        return [job["id"] for job in jobs]


def process_due_jobs(
    conn: sqlite3.Connection,
    provider: TrendsProvider,
    notifier: Notifier | None = None,
    timeframe: str = DEFAULT_TIMEFRAME,
    geo: str = DEFAULT_GEO,
    retry_delay_seconds: int = 300,
    request_delay_seconds: float = 2,
    max_jobs: int | None = None,
    sleep_func=time.sleep,
    public_base_url: str | None = DEFAULT_PUBLIC_BASE_URL,
    p1_alert_cooldown_hours: int = DEFAULT_P1_ALERT_COOLDOWN_HOURS,
    p2_alert_cooldown_hours: int = DEFAULT_P2_ALERT_COOLDOWN_HOURS,
) -> list[dict[str, object]]:
    active_notifier = notifier or NullNotifier()
    results: list[dict[str, object]] = []

    while max_jobs is None or len(results) < max_jobs:
        job = repository.claim_next_collection_job(conn, utc_now())
        if job is None:
            break

        results.append(
            process_collection_job(
                conn=conn,
                job=job,
                provider=provider,
                notifier=active_notifier,
                timeframe=timeframe,
                geo=geo,
                retry_delay_seconds=retry_delay_seconds,
                public_base_url=public_base_url,
                p1_alert_cooldown_hours=p1_alert_cooldown_hours,
                p2_alert_cooldown_hours=p2_alert_cooldown_hours,
            )
        )

        if request_delay_seconds > 0 and (max_jobs is None or len(results) < max_jobs):
            sleep_func(request_delay_seconds)

    return results


def process_due_jobs_for_path(
    db_path: str | Path,
    provider: TrendsProvider,
    notifier: Notifier | None = None,
    timeframe: str = DEFAULT_TIMEFRAME,
    geo: str = DEFAULT_GEO,
    retry_delay_seconds: int = 300,
    request_delay_seconds: float = 2,
    max_jobs: int | None = None,
    public_base_url: str | None = DEFAULT_PUBLIC_BASE_URL,
    p1_alert_cooldown_hours: int = DEFAULT_P1_ALERT_COOLDOWN_HOURS,
    p2_alert_cooldown_hours: int = DEFAULT_P2_ALERT_COOLDOWN_HOURS,
) -> list[dict[str, object]]:
    initialize_database(db_path)
    with connect(db_path) as conn:
        return process_due_jobs(
            conn=conn,
            provider=provider,
            notifier=notifier,
            timeframe=timeframe,
            geo=geo,
            retry_delay_seconds=retry_delay_seconds,
            request_delay_seconds=request_delay_seconds,
            max_jobs=max_jobs,
            public_base_url=public_base_url,
            p1_alert_cooldown_hours=p1_alert_cooldown_hours,
            p2_alert_cooldown_hours=p2_alert_cooldown_hours,
        )


def process_collection_job(
    conn: sqlite3.Connection,
    job: sqlite3.Row,
    provider: TrendsProvider,
    notifier: Notifier,
    timeframe: str = DEFAULT_TIMEFRAME,
    geo: str = DEFAULT_GEO,
    retry_delay_seconds: int = 300,
    public_base_url: str | None = DEFAULT_PUBLIC_BASE_URL,
    p1_alert_cooldown_hours: int = DEFAULT_P1_ALERT_COOLDOWN_HOURS,
    p2_alert_cooldown_hours: int = DEFAULT_P2_ALERT_COOLDOWN_HOURS,
) -> dict[str, object]:
    keyword_id = job["keyword_id"]
    term = job["term"] or "deleted keyword"

    try:
        job_timeframe = job["timeframe"] or timeframe
        job_geo = job["geo"] if "geo" in job.keys() else geo
        points = provider.collect_keyword(term, timeframe=job_timeframe, geo=job_geo)
        collected_at = utc_now()
        records = [point.as_record() for point in points]
        count = repository.upsert_trend_points(
            conn,
            keyword_id,
            records,
            geo=job_geo,
            timeframe=job_timeframe,
            collected_at=collected_at,
        )
        alerts = evaluate_alerts(
            conn,
            keyword_id,
            timeframe=job_timeframe,
            p1_alert_cooldown_hours=p1_alert_cooldown_hours,
            p2_alert_cooldown_hours=p2_alert_cooldown_hours,
        )
        repository.finish_collection_job_success(
            conn,
            job_id=job["id"],
            finished_at=utc_now(),
            points_collected=count,
        )
        for alert in alerts:
            if not should_notify_alert(alert):
                continue
            notifier.send_text(
                format_alert_notification(
                    term,
                    alert,
                    keyword_id=keyword_id,
                    public_base_url=public_base_url,
                )
            )
        return {
            "job_id": job["id"],
            "keyword_id": keyword_id,
            "term": term,
            "timeframe": job_timeframe,
            "status": "success",
            "points_collected": count,
            "alert_created": bool(alerts),
            "alert_count": len(alerts),
        }
    except Exception as exc:
        raw_error = str(exc)
        error = format_collection_error(raw_error)
        if job["attempts"] < job["max_attempts"]:
            retry_seconds = next_retry_delay_seconds(
                raw_error=raw_error,
                attempts=job["attempts"],
                retry_delay_seconds=retry_delay_seconds,
            )
            next_attempt_at = (
                datetime.now(timezone.utc) + timedelta(seconds=retry_seconds)
            ).isoformat(timespec="seconds")
            repository.requeue_collection_job(
                conn,
                job_id=job["id"],
                next_attempt_at=next_attempt_at,
                error=error,
            )
            return {
                "job_id": job["id"],
                "keyword_id": keyword_id,
                "term": term,
                "status": "queued",
                "error": error,
                "next_attempt_at": next_attempt_at,
                "retry_delay_seconds": retry_seconds,
            }

        repository.finish_collection_job_failure(
            conn,
            job_id=job["id"],
            finished_at=utc_now(),
            error=error,
        )
        notifier.send_text(
            f"Google Trends collection failed\nKeyword: {term}\nError: {error}"
        )
        return {
            "job_id": job["id"],
            "keyword_id": keyword_id,
            "term": term,
            "status": "failed",
            "error": error,
        }


def is_google_rate_limit_error(error: str) -> bool:
    text = error.lower()
    return "code 429" in text or "response with code 429" in text


def format_collection_error(error: str) -> str:
    if is_google_rate_limit_error(error):
        return "Google Trends 限流 429：请求过于频繁，已自动延后重试。"
    return error


def next_retry_delay_seconds(
    raw_error: str,
    attempts: int,
    retry_delay_seconds: int,
) -> int:
    if not is_google_rate_limit_error(raw_error):
        return retry_delay_seconds
    multiplier = max(1, 2 ** max(attempts - 1, 0))
    return RATE_LIMIT_RETRY_SECONDS * multiplier


def evaluate_alerts(
    conn: sqlite3.Connection,
    keyword_id: int,
    timeframe: str = DEFAULT_TIMEFRAME,
    p1_alert_cooldown_hours: int = DEFAULT_P1_ALERT_COOLDOWN_HOURS,
    p2_alert_cooldown_hours: int = DEFAULT_P2_ALERT_COOLDOWN_HOURS,
) -> list[AlertDecision]:
    raw_points = repository.list_trend_points(conn, keyword_id, timeframe=timeframe)
    points = normalized_alert_points(raw_points, timeframe)
    candidates = build_alert_candidates(points, timeframe)
    created_alerts: list[AlertDecision] = []

    for alert in candidates:
        if is_alert_in_cooldown(
            conn,
            keyword_id,
            alert,
            p1_alert_cooldown_hours=p1_alert_cooldown_hours,
            p2_alert_cooldown_hours=p2_alert_cooldown_hours,
        ):
            continue
        created = repository.insert_alert(
            conn,
            keyword_id=keyword_id,
            rule=alert.rule,
            severity=alert.severity,
            category=alert.category,
            timeframe=alert.timeframe,
            point_date=alert.point_date,
            current_value=alert.current_value,
            baseline_value=alert.baseline_value,
            change_pct=alert.change_pct,
            message=alert.message,
        )
        if created:
            created_alerts.append(alert)

    return created_alerts


def is_alert_in_cooldown(
    conn: sqlite3.Connection,
    keyword_id: int,
    alert: AlertDecision,
    p1_alert_cooldown_hours: int = DEFAULT_P1_ALERT_COOLDOWN_HOURS,
    p2_alert_cooldown_hours: int = DEFAULT_P2_ALERT_COOLDOWN_HOURS,
) -> bool:
    cooldown_hours = alert_cooldown_hours(
        alert.severity,
        p1_alert_cooldown_hours=p1_alert_cooldown_hours,
        p2_alert_cooldown_hours=p2_alert_cooldown_hours,
    )
    if cooldown_hours <= 0:
        return False

    created_after = sqlite_utc_timestamp(
        datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)
    )
    return repository.has_recent_alert(
        conn,
        keyword_id=keyword_id,
        severity=alert.severity,
        category=alert.category,
        timeframe=alert.timeframe,
        created_after=created_after,
    )


def alert_cooldown_hours(
    severity: str,
    p1_alert_cooldown_hours: int = DEFAULT_P1_ALERT_COOLDOWN_HOURS,
    p2_alert_cooldown_hours: int = DEFAULT_P2_ALERT_COOLDOWN_HOURS,
) -> int:
    if severity == "P1":
        return p1_alert_cooldown_hours
    return p2_alert_cooldown_hours


def normalized_alert_points(
    points: list[sqlite3.Row],
    timeframe: str,
) -> list[sqlite3.Row]:
    if timeframe in NOW_TIMEFRAMES:
        return points
    return [point for point in points if not point["is_partial"]]


def build_alert_candidates(
    points: list[sqlite3.Row],
    timeframe: str,
) -> list[AlertDecision]:
    if timeframe in NOW_TIMEFRAMES:
        return build_short_window_alerts(points, timeframe)
    if timeframe in MID_TIMEFRAMES:
        return build_mid_window_alerts(points, timeframe)
    if timeframe in LONG_TIMEFRAMES:
        return build_long_window_alerts(points, timeframe)
    return []


def build_short_window_alerts(
    points: list[sqlite3.Row],
    timeframe: str,
) -> list[AlertDecision]:
    if len(points) < 12:
        return []

    latest = points[-1]
    recent = points[-3:] if len(points) >= 27 else points[-2:]
    previous = points[-27:-3] if len(points) >= 27 else points[:-2]
    if not previous:
        return []

    recent_avg = average_value(recent)
    baseline = average_value(previous)
    alerts: list[AlertDecision] = []

    if is_spike(recent_avg, baseline, multiplier=2.5, delta=15, floor=15):
        alerts.append(
            make_alert(
                timeframe=timeframe,
                category="sudden_spike",
                severity="P1",
                point_date=latest["point_date"],
                current_value=recent_avg,
                baseline_value=baseline,
                message="短线搜索热度暴增，可能进入快速传播阶段。",
            )
        )
    elif is_spike(recent_avg, baseline, multiplier=1.35, delta=6, floor=10):
        alerts.append(
            make_alert(
                timeframe=timeframe,
                category="warming_up",
                severity="P2",
                point_date=latest["point_date"],
                current_value=recent_avg,
                baseline_value=baseline,
                message="短线搜索热度小幅升温，建议观察是否持续放量。",
            )
        )

    if is_drop(recent_avg, baseline, multiplier=0.45, delta=15, floor=15):
        alerts.append(
            make_alert(
                timeframe=timeframe,
                category="sudden_drop",
                severity="P1",
                point_date=latest["point_date"],
                current_value=recent_avg,
                baseline_value=baseline,
                message="短线搜索热度快速下跌，可能出现热度暴毙。",
            )
        )
    elif is_drop(recent_avg, baseline, multiplier=0.70, delta=8, floor=12):
        alerts.append(
            make_alert(
                timeframe=timeframe,
                category="cooling_down",
                severity="P2",
                point_date=latest["point_date"],
                current_value=recent_avg,
                baseline_value=baseline,
                message="短线搜索热度明显回落，建议确认是否失去动能。",
            )
        )

    return alerts


def build_mid_window_alerts(
    points: list[sqlite3.Row],
    timeframe: str,
) -> list[AlertDecision]:
    if len(points) < 17:
        return []

    latest = points[-1]
    recent = points[-3:]
    previous = points[-17:-3]
    recent_avg = average_value(recent)
    baseline = average_value(previous)
    previous_peak = max(point["value"] for point in previous)
    alerts: list[AlertDecision] = []

    if recent_avg >= 85 and recent_avg >= previous_peak * 0.95:
        alerts.append(
            make_alert(
                timeframe=timeframe,
                category="window_breakout",
                severity="P1",
                point_date=latest["point_date"],
                current_value=recent_avg,
                baseline_value=max(float(previous_peak), 1.0),
                message="中期热度接近当前周期高位，可能从升温进入爆发确认。",
            )
        )
    elif is_spike(recent_avg, baseline, multiplier=1.30, delta=8, floor=15):
        alerts.append(
            make_alert(
                timeframe=timeframe,
                category="steady_rise",
                severity="P2",
                point_date=latest["point_date"],
                current_value=recent_avg,
                baseline_value=baseline,
                message="中期热度连续走强，趋势正在形成。",
            )
        )

    if is_drop(recent_avg, baseline, multiplier=0.65, delta=10, floor=20):
        alerts.append(
            make_alert(
                timeframe=timeframe,
                category="steady_decline",
                severity="P2",
                point_date=latest["point_date"],
                current_value=recent_avg,
                baseline_value=baseline,
                message="中期热度连续回落，可能进入衰退段。",
            )
        )

    return alerts


def build_long_window_alerts(
    points: list[sqlite3.Row],
    timeframe: str,
) -> list[AlertDecision]:
    if len(points) < 10:
        return []

    latest = points[-1]
    recent = points[-2:]
    previous = points[-10:-2]
    latest_value = float(latest["value"])
    recent_avg = average_value(recent)
    baseline = average_value(previous)
    alerts: list[AlertDecision] = []

    if latest_value >= 85:
        alerts.append(
            make_alert(
                timeframe=timeframe,
                category="historical_hot",
                severity="P1",
                point_date=latest["point_date"],
                current_value=latest_value,
                baseline_value=baseline,
                message="长期窗口已接近当前周期高位，属于历史级热度。",
            )
        )
    elif is_spike(recent_avg, baseline, multiplier=1.35, delta=8, floor=15):
        alerts.append(
            make_alert(
                timeframe=timeframe,
                category="long_rise",
                severity="P2",
                point_date=latest["point_date"],
                current_value=recent_avg,
                baseline_value=baseline,
                message="长期窗口明显上行，当前变化具有历史参照意义。",
            )
        )

    if is_drop(recent_avg, baseline, multiplier=0.65, delta=10, floor=20):
        alerts.append(
            make_alert(
                timeframe=timeframe,
                category="long_decline",
                severity="P2",
                point_date=latest["point_date"],
                current_value=recent_avg,
                baseline_value=baseline,
                message="长期窗口明显下行，热度可能进入衰退。",
            )
        )

    return alerts


def average_value(points: list[sqlite3.Row]) -> float:
    return sum(float(point["value"]) for point in points) / len(points)


def is_spike(
    current: float,
    baseline: float,
    multiplier: float,
    delta: float,
    floor: float,
) -> bool:
    if current < floor:
        return False
    if baseline <= 0:
        return current >= floor
    return current >= baseline * multiplier and (current - baseline) >= delta


def is_drop(
    current: float,
    baseline: float,
    multiplier: float,
    delta: float,
    floor: float,
) -> bool:
    if baseline < floor:
        return False
    return current <= baseline * multiplier and (baseline - current) >= delta


def make_alert(
    timeframe: str,
    category: str,
    severity: str,
    point_date: str,
    current_value: float,
    baseline_value: float,
    message: str,
) -> AlertDecision:
    change_pct = percent_change(current_value, baseline_value)
    return AlertDecision(
        rule=f"{ALERT_RULE_PREFIX}:{timeframe}:{category}",
        severity=severity,
        category=category,
        timeframe=timeframe,
        point_date=point_date,
        current_value=round(current_value, 2),
        baseline_value=round(baseline_value, 2),
        change_pct=round(change_pct, 2) if change_pct is not None else None,
        message=message,
    )


def percent_change(current: float, baseline: float) -> float | None:
    if baseline <= 0:
        return None
    return ((current - baseline) / baseline) * 100


def format_alert_notification(
    term: str,
    alert: AlertDecision,
    keyword_id: int | None = None,
    public_base_url: str | None = DEFAULT_PUBLIC_BASE_URL,
) -> str:
    change = "N/A" if alert.change_pct is None else f"{alert.change_pct:+.1f}%"
    point_date = format_beijing(alert.point_date, timeframe=alert.timeframe)
    lines = [
        "Google Trends 告警",
        f"级别: {alert.severity}",
        f"关键词: {term}",
        f"类型: {alert.category}",
        f"窗口: {alert.timeframe}",
        f"触发点: {point_date}",
        f"当前: {alert.current_value:.1f}",
        f"基线: {alert.baseline_value:.1f}",
        f"变化: {change}",
        f"说明: {alert.message}",
        f"建议动作: {suggested_action(alert)}",
    ]
    keyword_url = build_keyword_url(public_base_url, keyword_id, alert.timeframe)
    if keyword_url:
        lines.append(f"页面: {keyword_url}")
    return "\n".join(lines)


def should_notify_alert(alert: AlertDecision) -> bool:
    return alert.category not in {
        "sudden_drop",
        "cooling_down",
        "steady_decline",
        "long_decline",
    }


def build_keyword_url(
    public_base_url: str | None,
    keyword_id: int | None,
    timeframe: str,
) -> str | None:
    if not public_base_url or keyword_id is None:
        return None
    return (
        f"{public_base_url.rstrip('/')}/keywords/{keyword_id}"
        f"?timeframe={quote(timeframe, safe='')}"
    )


def suggested_action(alert: AlertDecision) -> str:
    if alert.category in {
        "sudden_spike",
        "window_breakout",
        "three_month_breakout",
        "historical_hot",
    }:
        return "立即查看关键词详情，优先评估选题、投放、内容或库存机会。"
    if alert.category in {"warming_up", "steady_rise", "long_rise"}:
        return "继续观察下一轮采集，提前准备素材；若持续上涨则升级处理。"
    if alert.category in {"sudden_drop", "cooling_down", "steady_decline", "long_decline"}:
        return "确认是否进入衰退，降低追涨优先级并复盘已投入动作。"
    return "打开关键词详情页，结合历史曲线判断是否需要人工介入。"
