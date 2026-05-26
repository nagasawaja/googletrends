from __future__ import annotations

from googletrends_app.settings import load_settings


def test_clash_enabled_explicit_false_overrides_controller_url(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLETRENDS_CLASH_ENABLED", "0")
    monkeypatch.setenv("GOOGLETRENDS_CLASH_CONTROLLER_URL", "http://127.0.0.1:49266")

    settings = load_settings()

    assert settings.clash_enabled is False
    assert settings.clash_controller_url == "http://127.0.0.1:49266"


def test_clash_controller_url_enables_controller_when_flag_unset(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLETRENDS_CLASH_ENABLED", raising=False)
    monkeypatch.setenv("GOOGLETRENDS_CLASH_CONTROLLER_URL", "http://127.0.0.1:33331")

    settings = load_settings()

    assert settings.clash_enabled is True


def test_default_clash_proxy_url_prefers_clash_verge_port(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLETRENDS_CLASH_CONTROLLER_URL", raising=False)
    monkeypatch.delenv("GOOGLETRENDS_CLASH_ENABLED", raising=False)
    monkeypatch.delenv("GOOGLETRENDS_CLASH_PROXY_URL", raising=False)

    settings = load_settings()

    assert settings.clash_proxy_url == "http://127.0.0.1:7897"
