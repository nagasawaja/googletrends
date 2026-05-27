from __future__ import annotations

from googletrends_app.proxy_check import run_proxy_check


class FakeProxyPool:
    def next_proxy_url(self) -> str:
        return "http://127.0.0.1:7897"


def test_proxy_check_explains_matching_direct_and_proxy_ip(monkeypatch) -> None:
    def fake_fetch_ip(url, proxies=None):
        return "203.0.113.10", ""

    monkeypatch.setattr("googletrends_app.proxy_check.fetch_ip", fake_fetch_ip)

    result = run_proxy_check(
        settings=object(),  # type: ignore[arg-type]
        proxy_pool=FakeProxyPool(),  # type: ignore[arg-type]
    )

    assert result.ok is True
    assert "代理请求成功" in result.warning
    assert "Clash Verge TUN" in result.warning
