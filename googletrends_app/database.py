from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL UNIQUE COLLATE NOCASE,
    enabled INTEGER NOT NULL DEFAULT 1,
    remark TEXT NOT NULL DEFAULT '',
    timeframes TEXT NOT NULL DEFAULT 'now 7-d,today 3-m',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword_id INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    points_collected INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    FOREIGN KEY (keyword_id) REFERENCES keywords(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS collection_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword_id INTEGER,
    source TEXT NOT NULL DEFAULT 'manual',
    timeframe TEXT NOT NULL DEFAULT 'today 12-m',
    geo TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_attempt_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    points_collected INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (keyword_id) REFERENCES keywords(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS collection_job_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'failed',
    proxy_name TEXT,
    proxy_url TEXT,
    profile_key TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES collection_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trend_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword_id INTEGER NOT NULL,
    point_date TEXT NOT NULL,
    value INTEGER NOT NULL,
    is_partial INTEGER NOT NULL DEFAULT 0,
    geo TEXT NOT NULL DEFAULT '',
    timeframe TEXT NOT NULL DEFAULT 'today 12-m',
    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (keyword_id) REFERENCES keywords(id) ON DELETE CASCADE,
    UNIQUE (keyword_id, point_date, geo, timeframe)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword_id INTEGER NOT NULL,
    rule TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'P2',
    category TEXT NOT NULL DEFAULT 'trend_change',
    timeframe TEXT NOT NULL DEFAULT 'today 12-m',
    point_date TEXT NOT NULL,
    current_value REAL,
    baseline_value REAL,
    change_pct REAL,
    remark TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (keyword_id) REFERENCES keywords(id) ON DELETE CASCADE,
    UNIQUE (keyword_id, rule, point_date)
);
"""

MIGRATIONS: dict[str, dict[str, str]] = {
    "keywords": {
        "remark": "ALTER TABLE keywords ADD COLUMN remark TEXT NOT NULL DEFAULT ''",
        "timeframes": "ALTER TABLE keywords ADD COLUMN timeframes TEXT NOT NULL DEFAULT 'now 7-d,today 3-m'",
    },
    "collection_jobs": {
        "max_attempts": "ALTER TABLE collection_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 5",
        "timeframe": "ALTER TABLE collection_jobs ADD COLUMN timeframe TEXT NOT NULL DEFAULT 'today 12-m'",
        "geo": "ALTER TABLE collection_jobs ADD COLUMN geo TEXT NOT NULL DEFAULT ''",
    },
    "alerts": {
        "severity": "ALTER TABLE alerts ADD COLUMN severity TEXT NOT NULL DEFAULT 'P2'",
        "category": "ALTER TABLE alerts ADD COLUMN category TEXT NOT NULL DEFAULT 'trend_change'",
        "timeframe": "ALTER TABLE alerts ADD COLUMN timeframe TEXT NOT NULL DEFAULT 'today 12-m'",
        "current_value": "ALTER TABLE alerts ADD COLUMN current_value REAL",
        "baseline_value": "ALTER TABLE alerts ADD COLUMN baseline_value REAL",
        "change_pct": "ALTER TABLE alerts ADD COLUMN change_pct REAL",
        "remark": "ALTER TABLE alerts ADD COLUMN remark TEXT NOT NULL DEFAULT ''",
    },
}


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database(db_path: str | Path) -> None:
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        run_migrations(conn)
        conn.commit()


def run_migrations(conn: sqlite3.Connection) -> None:
    for table_name, columns in MIGRATIONS.items():
        existing_columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name, statement in columns.items():
            if column_name not in existing_columns:
                conn.execute(statement)
