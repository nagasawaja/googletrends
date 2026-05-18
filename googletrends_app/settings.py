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
    request_profiles_enabled: bool
    retry_delay_seconds: int
    max_attempts: int
    p1_alert_cooldown_hours: int
    p2_alert_cooldown_hours: int
    proxy_urls: str | None
    proxy_subscription_url: str | None
    proxy_refresh_seconds: int
    proxy_auto_detect_local_clash: bool
    clash_enabled: bool
    clash_proxy_url: str
    clash_controller_url: str | None
    clash_secret: str | None
    clash_proxy_group: str
    clash_config_path: Path
    clash_skip_proxy_names: str | None
    clash_allowed_proxy_name_keywords: str | None
    clash_rotate_on_429: bool
    clash_rotate_on_error: bool
    clash_retry_after_rotate: bool


def getenv_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    default_db_path = Path.cwd() / "data" / "googletrends.sqlite3"
    db_path = Path(os.getenv("GOOGLETRENDS_DB_PATH", str(default_db_path)))
    scheduler_enabled = os.getenv("GOOGLETRENDS_SCHEDULER", "1") != "0"
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL") or None
    public_base_url = os.getenv("GOOGLETRENDS_PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    request_delay_seconds = float(os.getenv("GOOGLETRENDS_REQUEST_DELAY_SECONDS", "2"))
    request_profiles_enabled = getenv_bool("GOOGLETRENDS_REQUEST_PROFILES_ENABLED", "1")
    retry_delay_seconds = int(os.getenv("GOOGLETRENDS_RETRY_DELAY_SECONDS", "5"))
    max_attempts = int(os.getenv("GOOGLETRENDS_MAX_ATTEMPTS", "5"))
    p1_alert_cooldown_hours = int(os.getenv("GOOGLETRENDS_P1_ALERT_COOLDOWN_HOURS", "6"))
    p2_alert_cooldown_hours = int(os.getenv("GOOGLETRENDS_P2_ALERT_COOLDOWN_HOURS", "24"))
    proxy_urls = os.getenv("GOOGLETRENDS_PROXY_URLS") or None
    proxy_subscription_url = os.getenv("GOOGLETRENDS_PROXY_SUBSCRIPTION_URL") or None
    proxy_refresh_seconds = int(os.getenv("GOOGLETRENDS_PROXY_REFRESH_SECONDS", "3600"))
    proxy_auto_detect_local_clash = getenv_bool(
        "GOOGLETRENDS_PROXY_AUTO_DETECT_LOCAL_CLASH",
        "1",
    )
    clash_controller_url = os.getenv("GOOGLETRENDS_CLASH_CONTROLLER_URL") or None
    clash_enabled = getenv_bool("GOOGLETRENDS_CLASH_ENABLED") or bool(clash_controller_url)
    clash_proxy_url = os.getenv("GOOGLETRENDS_CLASH_PROXY_URL", "http://127.0.0.1:7890")
    clash_secret = os.getenv("GOOGLETRENDS_CLASH_SECRET") or None
    clash_proxy_group = os.getenv("GOOGLETRENDS_CLASH_PROXY_GROUP", "Google")
    clash_config_path = Path(
        os.getenv(
            "GOOGLETRENDS_CLASH_CONFIG_PATH",
            str(Path.home() / ".config" / "clash" / "config.yaml"),
        )
    ).expanduser()
    clash_skip_proxy_names = os.getenv("GOOGLETRENDS_CLASH_SKIP_PROXY_NAMES") or None
    clash_allowed_proxy_name_keywords = (
        os.getenv("GOOGLETRENDS_CLASH_ALLOWED_PROXY_NAME_KEYWORDS") or None
    )
    clash_rotate_on_429 = getenv_bool("GOOGLETRENDS_CLASH_ROTATE_ON_429", "1")
    clash_rotate_on_error = getenv_bool("GOOGLETRENDS_CLASH_ROTATE_ON_ERROR", "1")
    clash_retry_after_rotate = getenv_bool("GOOGLETRENDS_CLASH_RETRY_AFTER_ROTATE", "1")
    return Settings(
        db_path=db_path,
        scheduler_enabled=scheduler_enabled,
        feishu_webhook_url=webhook_url,
        public_base_url=public_base_url,
        request_delay_seconds=request_delay_seconds,
        request_profiles_enabled=request_profiles_enabled,
        retry_delay_seconds=retry_delay_seconds,
        max_attempts=max_attempts,
        p1_alert_cooldown_hours=p1_alert_cooldown_hours,
        p2_alert_cooldown_hours=p2_alert_cooldown_hours,
        proxy_urls=proxy_urls,
        proxy_subscription_url=proxy_subscription_url,
        proxy_refresh_seconds=proxy_refresh_seconds,
        proxy_auto_detect_local_clash=proxy_auto_detect_local_clash,
        clash_enabled=clash_enabled,
        clash_proxy_url=clash_proxy_url,
        clash_controller_url=clash_controller_url,
        clash_secret=clash_secret,
        clash_proxy_group=clash_proxy_group,
        clash_config_path=clash_config_path,
        clash_skip_proxy_names=clash_skip_proxy_names,
        clash_allowed_proxy_name_keywords=clash_allowed_proxy_name_keywords,
        clash_rotate_on_429=clash_rotate_on_429,
        clash_rotate_on_error=clash_rotate_on_error,
        clash_retry_after_rotate=clash_retry_after_rotate,
    )
