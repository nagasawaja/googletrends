from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import collector, repository
from .trends import DEFAULT_TIMEFRAME, LONG_TIMEFRAMES, MID_TIMEFRAMES, NOW_TIMEFRAMES


@dataclass(frozen=True)
class BacktestEvent:
    rule: str
    severity: str
    category: str
    timeframe: str
    point_date: str
    current_value: float
    baseline_value: float
    change_pct: float | None
    message: str
    observed_points: int


@dataclass(frozen=True)
class BacktestResult:
    keyword: sqlite3.Row
    timeframe: str
    points_count: int
    events: list[BacktestEvent]
    category_counts: dict[str, int]


def run_keyword_backtest(
    conn: sqlite3.Connection,
    keyword_id: int,
    timeframe: str = DEFAULT_TIMEFRAME,
    p1_alert_cooldown_hours: int = collector.DEFAULT_P1_ALERT_COOLDOWN_HOURS,
    p2_alert_cooldown_hours: int = collector.DEFAULT_P2_ALERT_COOLDOWN_HOURS,
) -> BacktestResult:
    keyword = repository.get_keyword(conn, keyword_id)
    if keyword is None:
        raise LookupError(f"Keyword id {keyword_id} was not found.")

    raw_points = repository.list_trend_points(conn, keyword_id, timeframe=timeframe)
    points = collector.normalized_alert_points(raw_points, timeframe)
    events = simulate_events(
        points,
        timeframe=timeframe,
        p1_alert_cooldown_hours=p1_alert_cooldown_hours,
        p2_alert_cooldown_hours=p2_alert_cooldown_hours,
    )
    category_counts: dict[str, int] = {}
    for event in events:
        category_counts[event.category] = category_counts.get(event.category, 0) + 1

    return BacktestResult(
        keyword=keyword,
        timeframe=timeframe,
        points_count=len(points),
        events=events,
        category_counts=category_counts,
    )


def simulate_events(
    points: list[sqlite3.Row],
    timeframe: str,
    p1_alert_cooldown_hours: int = collector.DEFAULT_P1_ALERT_COOLDOWN_HOURS,
    p2_alert_cooldown_hours: int = collector.DEFAULT_P2_ALERT_COOLDOWN_HOURS,
) -> list[BacktestEvent]:
    events: list[BacktestEvent] = []
    seen_rules: set[tuple[str, str]] = set()
    last_event_at: dict[tuple[str, str, str], datetime] = {}
    min_points = minimum_points_for_timeframe(timeframe)

    for end_index in range(min_points, len(points) + 1):
        candidates = collector.build_alert_candidates(points[:end_index], timeframe)
        for alert in candidates:
            exact_key = (alert.rule, alert.point_date)
            if exact_key in seen_rules:
                continue

            cooldown_key = (alert.severity, alert.category, alert.timeframe)
            point_time = parse_point_time(alert.point_date)
            previous_time = last_event_at.get(cooldown_key)
            cooldown_hours = collector.alert_cooldown_hours(
                alert.severity,
                p1_alert_cooldown_hours=p1_alert_cooldown_hours,
                p2_alert_cooldown_hours=p2_alert_cooldown_hours,
            )
            if (
                previous_time is not None
                and point_time is not None
                and point_time - previous_time < timedelta(hours=cooldown_hours)
            ):
                continue

            events.append(
                BacktestEvent(
                    rule=alert.rule,
                    severity=alert.severity,
                    category=alert.category,
                    timeframe=alert.timeframe,
                    point_date=alert.point_date,
                    current_value=alert.current_value,
                    baseline_value=alert.baseline_value,
                    change_pct=alert.change_pct,
                    message=alert.message,
                    observed_points=end_index,
                )
            )
            seen_rules.add(exact_key)
            if point_time is not None:
                last_event_at[cooldown_key] = point_time

    return events


def minimum_points_for_timeframe(timeframe: str) -> int:
    if timeframe in NOW_TIMEFRAMES:
        return 12
    if timeframe in MID_TIMEFRAMES:
        return 17
    if timeframe in LONG_TIMEFRAMES:
        return 10
    return 1


def parse_point_time(value: str) -> datetime | None:
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
