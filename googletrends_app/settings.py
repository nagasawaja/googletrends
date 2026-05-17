from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path
    scheduler_enabled: bool
    feishu_webhook_url: str | None
    public_base_url: str
    request_delay_seconds: float
    retry_delay_seconds: int
    max_attempts: int
    p1_alert_cooldown_hours: int
    p2_alert_cooldown_hours: int


def load_settings() -> Settings:
    default_db_path = Path.cwd() / "data" / "googletrends.sqlite3"
    db_path = Path(os.getenv("GOOGLETRENDS_DB_PATH", str(default_db_path)))
    scheduler_enabled = os.getenv("GOOGLETRENDS_SCHEDULER", "1") != "0"
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL") or None
    public_base_url = os.getenv("GOOGLETRENDS_PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    request_delay_seconds = float(os.getenv("GOOGLETRENDS_REQUEST_DELAY_SECONDS", "2"))
    retry_delay_seconds = int(os.getenv("GOOGLETRENDS_RETRY_DELAY_SECONDS", "300"))
    max_attempts = int(os.getenv("GOOGLETRENDS_MAX_ATTEMPTS", "3"))
    p1_alert_cooldown_hours = int(os.getenv("GOOGLETRENDS_P1_ALERT_COOLDOWN_HOURS", "6"))
    p2_alert_cooldown_hours = int(os.getenv("GOOGLETRENDS_P2_ALERT_COOLDOWN_HOURS", "24"))
    return Settings(
        db_path=db_path,
        scheduler_enabled=scheduler_enabled,
        feishu_webhook_url=webhook_url,
        public_base_url=public_base_url,
        request_delay_seconds=request_delay_seconds,
        retry_delay_seconds=retry_delay_seconds,
        max_attempts=max_attempts,
        p1_alert_cooldown_hours=p1_alert_cooldown_hours,
        p2_alert_cooldown_hours=p2_alert_cooldown_hours,
    )
