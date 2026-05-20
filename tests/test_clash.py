from __future__ import annotations

from datetime import date

import pytest
import requests
from urllib.parse import unquote

from googletrends_app.clash import (
    ClashController,
    build_clash_controller,
    read_clash_config,
)
from googletrends_app.trends import PytrendsProvider, TrendPoint


class FakeResponse:
    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.data = data or {}

    def json(self) -> dict[str, object]:
        return self.data

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, mode: str = "rule") -> None:
        self.mode = mode
        self.delay_failures: set[str] = set()
        self.proxies = {
            "Google": {
                "type": "Selector",
                "now": "香港-a",
                "all": ["DIRECT", "SG", "香港-a", "日本-b"],
            },
            "GLOBAL": {
                "type": "Selector",
                "now": "美国-a",
                "all": ["DIRECT", "Traffic: 1 GB", "Proxies", "美国-a", "台湾-b"],
            }
        }
        self.put_calls: list[dict[str, object]] = []
        self.delay_calls: list[dict[str, object]] = []

    def get(
        self,
        url: str,
        headers: dict[str, str],
        timeout: float,
        params: dict[str, object] | None = None,
    ) -> FakeResponse:
        if url.endswith("/configs"):
            return FakeResponse({"mode": self.mode})
        if url.endswith("/delay"):
            name = unquote(url.rsplit("/", 2)[-2])
            self.delay_calls.append({"name": name, "params": params})
            if name in self.delay_failures:
                raise requests.exceptions.ReadTimeout("delay test failed")
            return FakeResponse({"delay": 123})
        return FakeResponse({"proxies": self.proxies})

    def put(
        self,
        url: str,
        headers: dict[str, str],
        json: dict[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.put_calls.append({"url": url, "headers": headers, "json": json})
        group = url.rsplit("/", 1)[-1]
        self.proxies[group]["now"] = json["name"]
        return FakeResponse()


def test_clash_controller_rotates_to_next_candidate() -> None:
    session = FakeSession()
    controller = ClashController(
        controller_url="http://127.0.0.1:9090",
        secret="secret-value",
        proxy_group="Google",
        session=session,  # type: ignore[arg-type]
    )

    selected = controller.rotate_proxy()

    assert selected == "日本-b"
    assert session.proxies["Google"]["now"] == "日本-b"
    assert session.delay_calls[0]["name"] == "日本-b"
    assert session.put_calls[0]["url"] == "http://127.0.0.1:9090/proxies/Google"
    assert session.put_calls[0]["headers"]["Authorization"] == "Bearer secret-value"


def test_clash_controller_rotates_global_group_in_global_mode() -> None:
    session = FakeSession(mode="global")
    controller = ClashController(
        controller_url="http://127.0.0.1:9090",
        proxy_group="Google",
        session=session,  # type: ignore[arg-type]
    )

    selected = controller.rotate_proxy_with_group()

    assert selected == ("GLOBAL", "台湾-b")
    assert session.proxies["GLOBAL"]["now"] == "台湾-b"
    assert session.proxies["Google"]["now"] == "香港-a"
    assert session.delay_calls[0]["name"] == "台湾-b"
    assert session.put_calls[0]["url"] == "http://127.0.0.1:9090/proxies/GLOBAL"


def test_clash_controller_skips_candidates_without_allowed_keywords() -> None:
    session = FakeSession()
    controller = ClashController(
        controller_url="http://127.0.0.1:9090",
        proxy_group="Google",
        session=session,  # type: ignore[arg-type]
    )

    candidates = controller.available_candidates(session.proxies["Google"])

    assert candidates == ["香港-a", "日本-b"]


def test_clash_controller_skips_unreachable_candidates_before_selecting() -> None:
    session = FakeSession()
    session.delay_failures.add("日本-b")
    controller = ClashController(
        controller_url="http://127.0.0.1:9090",
        proxy_group="Google",
        session=session,  # type: ignore[arg-type]
    )

    selected = controller.rotate_proxy()

    assert selected == "香港-a"
    assert [call["name"] for call in session.delay_calls] == ["日本-b", "香港-a"]


def test_read_clash_config_normalizes_controller_url(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("external-controller: 0.0.0.0:9090\nsecret: local-secret\n")

    result = read_clash_config(config)

    assert result.controller_url == "http://127.0.0.1:9090"
    assert result.secret == "local-secret"


def test_build_clash_controller_reads_local_config(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("external-controller: 127.0.0.1:9090\nsecret: local-secret\n")

    controller = build_clash_controller(
        enabled=True,
        proxy_group="Google",
        config_path=config,
        probe=False,
    )

    assert controller is not None
    assert controller.controller_url == "http://127.0.0.1:9090"
    assert controller.secret == "local-secret"


class FakeClashController:
    def __init__(self) -> None:
        self.rotations = 0

    def rotate_proxy(self) -> str:
        self.rotations += 1
        return "node-b"


class FailingThenSuccessfulProvider(PytrendsProvider):
    def __init__(self, clash_controller: FakeClashController) -> None:
        super().__init__(
            clash_controller=clash_controller,  # type: ignore[arg-type]
            clash_retry_after_rotate=True,
        )
        self.calls = 0
        self.profile_keys: list[str | None] = []

    def _collect_keyword_once(
        self,
        term: str,
        timeframe: str = "now 7-d",
        geo: str = "",
        gprop: str = "",
        profile_key: str | None = None,
    ) -> list[TrendPoint]:
        self.calls += 1
        self.profile_keys.append(profile_key)
        if self.calls == 1:
            raise RuntimeError("Google returned a response with code 429")
        return [TrendPoint(date=date(2026, 5, 18), value=42)]


def test_pytrends_provider_rotates_clash_proxy_and_retries_on_429() -> None:
    clash = FakeClashController()
    provider = FailingThenSuccessfulProvider(clash)

    points = provider.collect_keyword("ChatGPT")

    assert points == [TrendPoint(date=date(2026, 5, 18), value=42)]
    assert provider.calls == 2
    assert provider.profile_keys == [None, "clash:node-b"]
    assert clash.rotations == 1


def test_pytrends_provider_keeps_original_error_when_rotation_disabled() -> None:
    clash = FakeClashController()
    provider = FailingThenSuccessfulProvider(clash)
    provider.clash_rotate_on_429 = False
    provider.clash_rotate_on_error = False

    with pytest.raises(RuntimeError, match="code 429"):
        provider.collect_keyword("ChatGPT")

    assert provider.calls == 1
    assert clash.rotations == 0
