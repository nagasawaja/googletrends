from __future__ import annotations

import threading
from typing import Any

from fastapi import FastAPI

from . import collector


def start_worker(app: FastAPI) -> bool:
    lock = app.state.worker_lock
    if not lock.acquire(blocking=False):
        return False

    def run() -> None:
        try:
            collector.process_due_jobs_for_path(
                db_path=app.state.db_path,
                provider=app.state.trends_provider,
                notifier=app.state.notifier,
                retry_delay_seconds=app.state.retry_delay_seconds,
                request_delay_seconds=app.state.request_delay_seconds,
                public_base_url=app.state.public_base_url,
                p1_alert_cooldown_hours=app.state.p1_alert_cooldown_hours,
                p2_alert_cooldown_hours=app.state.p2_alert_cooldown_hours,
            )
        finally:
            lock.release()

    thread = threading.Thread(target=run, name="collection-worker", daemon=True)
    thread.start()
    return True


def run_worker_once(app: FastAPI) -> list[dict[str, Any]]:
    return collector.process_due_jobs_for_path(
        db_path=app.state.db_path,
        provider=app.state.trends_provider,
        notifier=app.state.notifier,
        retry_delay_seconds=app.state.retry_delay_seconds,
        request_delay_seconds=app.state.request_delay_seconds,
        public_base_url=app.state.public_base_url,
        p1_alert_cooldown_hours=app.state.p1_alert_cooldown_hours,
        p2_alert_cooldown_hours=app.state.p2_alert_cooldown_hours,
    )
