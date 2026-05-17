from __future__ import annotations

import logging
from typing import Protocol

import requests

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    def send_text(self, text: str) -> bool:
        ...


class NullNotifier:
    def send_text(self, text: str) -> bool:
        return False


class FeishuNotifier:
    def __init__(self, webhook_url: str, timeout_seconds: float = 5.0) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds

    def send_text(self, text: str) -> bool:
        try:
            response = requests.post(
                self.webhook_url,
                json={"msg_type": "text", "content": {"text": text}},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException:
            logger.exception("Failed to send Feishu notification.")
            return False
        return True


def build_notifier(webhook_url: str | None) -> Notifier:
    if webhook_url:
        return FeishuNotifier(webhook_url)
    return NullNotifier()

