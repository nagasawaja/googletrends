from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from .trends import DEFAULT_TIMEFRAMES_TEXT, parse_timeframes, serialize_timeframes


def create_keyword(conn: sqlite3.Connection, term: str) -> sqlite3.Row:
    cleaned = term.strip()
    if not cleaned:
        raise ValueError("Keyword cannot be empty.")

    conn.execute(
        """
        INSERT INTO keywords (term, enabled, timeframes, updated_at)
        VALUES (?, 1, ?, CURRENT_TIMESTAMP)
        """,
        (cleaned, DEFAULT_TIMEFRAMES_TEXT),
    )
    conn.commit()
    return get_keyword_by_term(conn, cleaned)


def get_keyword(conn: sqlite3.Connection, keyword_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM keywords WHERE id = ?",
        (keyword_id,),
    ).fetchone()


def get_keyword_by_term(conn: sqlite3.Connection, term: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM keywords WHERE term = ? COLLATE NOCASE",
        (term,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Keyword not found: {term}")
    return row


def list_keywords(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
                k.*,
                COUNT(tp.id) AS points_count,
                MAX(tp.collected_at) AS last_collected_at,
                (
                    SELECT cj.status
                    FROM collection_jobs cj
                    WHERE cj.keyword_id = k.id
                    ORDER BY cj.updated_at DESC, cj.id DESC
                    LIMIT 1
                ) AS last_status,
                (
                    SELECT cj.error
                    FROM collection_jobs cj
                    WHERE cj.keyword_id = k.id
                    ORDER BY cj.updated_at DESC, cj.id DESC
                    LIMIT 1
                ) AS last_error,
                (
                    SELECT MAX(cj.finished_at)
                    FROM collection_jobs cj
                    WHERE cj.keyword_id = k.id AND cj.status = 'success'
                ) AS last_success_at,
                (
                    SELECT MAX(cj.finished_at)
                    FROM collection_jobs cj
                    WHERE cj.keyword_id = k.id AND cj.status = 'failed'
                ) AS last_failed_at,
                (
                    SELECT COUNT(*)
                    FROM collection_jobs cj
                    WHERE cj.keyword_id = k.id
                      AND cj.status IN ('queued', 'running')
                ) AS active_jobs_count,
                (
                    SELECT COUNT(*)
                    FROM collection_jobs cj
                    WHERE cj.keyword_id = k.id
                      AND cj.status = 'failed'
                      AND cj.finished_at > COALESCE((
                          SELECT MAX(cj2.finished_at)
                          FROM collection_jobs cj2
                          WHERE cj2.keyword_id = k.id AND cj2.status = 'success'
                      ), '')
                ) AS consecutive_failures
            FROM keywords k
            LEFT JOIN trend_points tp ON tp.keyword_id = k.id
            GROUP BY k.id
            ORDER BY k.created_at DESC, k.id DESC
            """
        )
    )


def list_enabled_keywords(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM keywords WHERE enabled = 1 ORDER BY id"))


def keyword_timeframes(keyword: sqlite3.Row) -> tuple[str, ...]:
    return parse_timeframes(keyword["timeframes"] if "timeframes" in keyword.keys() else None)


def set_keyword_enabled(
    conn: sqlite3.Connection, keyword_id: int, enabled: bool
) -> None:
    conn.execute(
        """
        UPDATE keywords
        SET enabled = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (1 if enabled else 0, keyword_id),
    )
    conn.commit()


def update_keyword_remark(
    conn: sqlite3.Connection,
    keyword_id: int,
    remark: str,
) -> sqlite3.Row | None:
    conn.execute(
        """
        UPDATE keywords
        SET remark = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (remark.strip(), keyword_id),
    )
    conn.commit()
    return get_keyword(conn, keyword_id)


def update_keyword_timeframes(
    conn: sqlite3.Connection,
    keyword_id: int,
    timeframes: Sequence[str],
) -> sqlite3.Row | None:
    selected = serialize_timeframes(tuple(timeframes))
    conn.execute(
        """
        UPDATE keywords
        SET timeframes = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (selected, keyword_id),
    )
    conn.execute(
        """
        DELETE FROM collection_jobs
        WHERE keyword_id = ?
          AND status = 'queued'
          AND instr(',' || ? || ',', ',' || timeframe || ',') = 0
        """,
        (keyword_id, selected),
    )
    conn.commit()
    return get_keyword(conn, keyword_id)


def toggle_keyword(conn: sqlite3.Connection, keyword_id: int) -> None:
    conn.execute(
        """
        UPDATE keywords
        SET enabled = CASE WHEN enabled = 1 THEN 0 ELSE 1 END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (keyword_id,),
    )
    conn.commit()


def delete_keyword(conn: sqlite3.Connection, keyword_id: int) -> None:
    conn.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
    conn.commit()


def create_collection_job(
    conn: sqlite3.Connection,
    keyword_id: int,
    source: str = "manual",
    max_attempts: int = 3,
    next_attempt_at: str | None = None,
    timeframe: str = "today 12-m",
    geo: str = "",
) -> sqlite3.Row:
    existing = conn.execute(
        """
        SELECT cj.*, k.term
        FROM collection_jobs cj
        LEFT JOIN keywords k ON k.id = cj.keyword_id
        WHERE cj.keyword_id = ?
          AND cj.timeframe = ?
          AND cj.geo = ?
          AND cj.status IN ('queued', 'running')
        ORDER BY cj.created_at ASC, cj.id ASC
        LIMIT 1
        """,
        (keyword_id, timeframe, geo),
    ).fetchone()
    if existing is not None:
        return existing

    cursor = conn.execute(
        """
        INSERT INTO collection_jobs
            (keyword_id, source, timeframe, geo, status, max_attempts, next_attempt_at)
        VALUES (?, ?, ?, ?, 'queued', ?, ?)
        """,
        (keyword_id, source, timeframe, geo, max_attempts, next_attempt_at),
    )
    conn.commit()
    return get_collection_job(conn, cursor.lastrowid)


def create_collection_jobs_for_enabled(
    conn: sqlite3.Connection,
    source: str = "manual",
    max_attempts: int = 3,
    timeframes: tuple[str, ...] | None = None,
    geo: str = "",
) -> list[sqlite3.Row]:
    jobs: list[sqlite3.Row] = []
    for keyword in list_enabled_keywords(conn):
        selected_timeframes = keyword_timeframes(keyword)
        requested_timeframes = timeframes or selected_timeframes
        for timeframe in requested_timeframes:
            if timeframe not in selected_timeframes:
                continue
            jobs.append(
                create_collection_job(
                    conn,
                    keyword["id"],
                    source,
                    max_attempts,
                    timeframe=timeframe,
                    geo=geo,
                )
            )
    return jobs


def get_collection_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT cj.*, k.term
        FROM collection_jobs cj
        LEFT JOIN keywords k ON k.id = cj.keyword_id
        WHERE cj.id = ?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Collection job not found: {job_id}")
    return row


def claim_next_collection_job(
    conn: sqlite3.Connection,
    now: str,
) -> sqlite3.Row | None:
    job = conn.execute(
        """
        SELECT cj.*, k.term, k.enabled
        FROM collection_jobs cj
        JOIN keywords k ON k.id = cj.keyword_id
        WHERE cj.status = 'queued'
          AND k.enabled = 1
          AND instr(',' || k.timeframes || ',', ',' || cj.timeframe || ',') > 0
          AND (cj.next_attempt_at IS NULL OR cj.next_attempt_at <= ?)
        ORDER BY cj.created_at ASC, cj.id ASC
        LIMIT 1
        """,
        (now,),
    ).fetchone()
    if job is None:
        return None

    conn.execute(
        """
        UPDATE collection_jobs
        SET status = 'running',
            attempts = attempts + 1,
            started_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'queued'
        """,
        (now, job["id"]),
    )
    conn.commit()
    return get_collection_job(conn, job["id"])


def delete_unmonitored_queued_jobs(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        """
        DELETE FROM collection_jobs
        WHERE status = 'queued'
          AND EXISTS (
              SELECT 1
              FROM keywords k
              WHERE k.id = collection_jobs.keyword_id
                AND instr(',' || k.timeframes || ',', ',' || collection_jobs.timeframe || ',') = 0
          )
        """
    )
    conn.commit()
    return cursor.rowcount


def finish_collection_job_success(
    conn: sqlite3.Connection,
    job_id: int,
    finished_at: str,
    points_collected: int,
) -> None:
    conn.execute(
        """
        UPDATE collection_jobs
        SET status = 'success',
            finished_at = ?,
            points_collected = ?,
            error = NULL,
            next_attempt_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (finished_at, points_collected, job_id),
    )
    conn.commit()


def finish_collection_job_failure(
    conn: sqlite3.Connection,
    job_id: int,
    finished_at: str,
    error: str,
) -> None:
    conn.execute(
        """
        UPDATE collection_jobs
        SET status = 'failed',
            finished_at = ?,
            error = ?,
            next_attempt_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (finished_at, error, job_id),
    )
    conn.commit()


def requeue_collection_job(
    conn: sqlite3.Connection,
    job_id: int,
    next_attempt_at: str,
    error: str,
) -> None:
    conn.execute(
        """
        UPDATE collection_jobs
        SET status = 'queued',
            next_attempt_at = ?,
            error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (next_attempt_at, error, job_id),
    )
    conn.commit()


def list_jobs(
    conn: sqlite3.Connection,
    limit: int = 100,
    status: str | None = None,
) -> list[sqlite3.Row]:
    where = ""
    params: list[object] = []
    if status:
        where = "WHERE cj.status = ?"
        params.append(status)
    params.append(limit)
    return list(
        conn.execute(
            f"""
            SELECT cj.*, k.term
            FROM collection_jobs cj
            LEFT JOIN keywords k ON k.id = cj.keyword_id
            {where}
            ORDER BY cj.created_at DESC, cj.id DESC
            LIMIT ?
            """,
            params,
        )
    )


def upsert_trend_points(
    conn: sqlite3.Connection,
    keyword_id: int,
    points: Sequence[dict[str, object]],
    geo: str,
    timeframe: str,
    collected_at: str,
) -> int:
    count = 0
    for point in points:
        conn.execute(
            """
            INSERT INTO trend_points
                (keyword_id, point_date, value, is_partial, geo, timeframe, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(keyword_id, point_date, geo, timeframe)
            DO UPDATE SET
                value = excluded.value,
                is_partial = excluded.is_partial,
                collected_at = excluded.collected_at
            """,
            (
                keyword_id,
                str(point["date"]),
                int(point["value"]),
                1 if point.get("is_partial") else 0,
                geo,
                timeframe,
                collected_at,
            ),
        )
        count += 1
    conn.commit()
    return count


def list_trend_points(
    conn: sqlite3.Connection,
    keyword_id: int,
    limit: int | None = None,
    timeframe: str | None = None,
) -> list[sqlite3.Row]:
    timeframe_filter = ""
    base_params: list[object] = [keyword_id]
    if timeframe is not None:
        timeframe_filter = "AND timeframe = ?"
        base_params.append(timeframe)

    sql = """
        SELECT *
        FROM trend_points
        WHERE keyword_id = ?
        {timeframe_filter}
        ORDER BY point_date ASC
    """.format(timeframe_filter=timeframe_filter)
    params: tuple[object, ...] = tuple(base_params)
    if limit is not None:
        sql = """
            SELECT *
            FROM (
                SELECT *
                FROM trend_points
                WHERE keyword_id = ?
                {timeframe_filter}
                ORDER BY point_date DESC
                LIMIT ?
            )
            ORDER BY point_date ASC
        """.format(timeframe_filter=timeframe_filter)
        params = tuple([*base_params, limit])
    return list(conn.execute(sql, params))


def create_run(
    conn: sqlite3.Connection, keyword_id: int, started_at: str
) -> sqlite3.Row:
    cursor = conn.execute(
        """
        INSERT INTO collection_runs (keyword_id, started_at, status)
        VALUES (?, ?, 'running')
        """,
        (keyword_id, started_at),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM collection_runs WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    finished_at: str,
    points_collected: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE collection_runs
        SET status = ?, finished_at = ?, points_collected = ?, error = ?
        WHERE id = ?
        """,
        (status, finished_at, points_collected, error, run_id),
    )
    conn.commit()


def list_runs(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return list_jobs(conn, limit=limit)


def insert_alert(
    conn: sqlite3.Connection,
    keyword_id: int,
    rule: str,
    point_date: str,
    message: str,
    severity: str = "P2",
    category: str = "trend_change",
    timeframe: str = "today 12-m",
    current_value: float | None = None,
    baseline_value: float | None = None,
    change_pct: float | None = None,
) -> bool:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO alerts (
            keyword_id, rule, severity, category, timeframe, point_date,
            current_value, baseline_value, change_pct, message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            keyword_id,
            rule,
            severity,
            category,
            timeframe,
            point_date,
            current_value,
            baseline_value,
            change_pct,
            message,
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


def has_recent_alert(
    conn: sqlite3.Connection,
    keyword_id: int,
    severity: str,
    category: str,
    timeframe: str,
    created_after: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM alerts
        WHERE keyword_id = ?
          AND severity = ?
          AND category = ?
          AND timeframe = ?
          AND created_at >= ?
        LIMIT 1
        """,
        (keyword_id, severity, category, timeframe, created_after),
    ).fetchone()
    return row is not None


def get_alert(conn: sqlite3.Connection, alert_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT a.*, k.term
        FROM alerts a
        JOIN keywords k ON k.id = a.keyword_id
        WHERE a.id = ?
        """,
        (alert_id,),
    ).fetchone()


def update_alert_remark(
    conn: sqlite3.Connection,
    alert_id: int,
    remark: str,
) -> sqlite3.Row | None:
    conn.execute(
        """
        UPDATE alerts
        SET remark = ?
        WHERE id = ?
        """,
        (remark.strip(), alert_id),
    )
    conn.commit()
    return get_alert(conn, alert_id)


def list_alerts(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT a.*, k.term
            FROM alerts a
            JOIN keywords k ON k.id = a.keyword_id
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            (limit,),
        )
    )
