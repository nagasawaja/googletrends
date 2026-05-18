from __future__ import annotations

import base64

import pytest

from googletrends_app.proxy_pool import (
    ProxyPool,
    build_proxy_pool,
    parse_proxy_subscription,
)


def test_parse_direct_proxy_urls() -> None:
    proxies = parse_proxy_subscription(
        "http://user:pass@proxy-a.example:8080\nsocks5h://proxy-b.example:1080"
    )

    assert [proxy.url for proxy in proxies] == [
        "http://user:pass@proxy-a.example:8080",
        "socks5h://proxy-b.example:1080",
    ]


def test_parse_base64_proxy_subscription() -> None:
    encoded = base64.b64encode(
        b"http://proxy-a.example:8080\nsocks5h://proxy-b.example:1080"
    ).decode("ascii")

    proxies = parse_proxy_subscription(encoded)

    assert [proxy.url for proxy in proxies] == [
        "http://proxy-a.example:8080",
        "socks5h://proxy-b.example:1080",
    ]


def test_parse_clash_http_and_socks_nodes() -> None:
    proxies = parse_proxy_subscription(
        """
proxies:
  - name: http-a
    type: http
    server: proxy-a.example
    port: 8080
    username: user@example.com
    password: pa:ss
  - {name: socks-b, type: socks5, server: proxy-b.example, port: 1080}
  - name: native-node
    type: vmess
    server: proxy-c.example
    port: 443
"""
    )

    assert [proxy.name for proxy in proxies] == ["http-a", "socks-b"]
    assert [proxy.url for proxy in proxies] == [
        "http://user%40example.com:pa%3Ass@proxy-a.example:8080",
        "socks5h://proxy-b.example:1080",
    ]


def test_proxy_pool_rotates_and_refreshes_subscription() -> None:
    now = 1000.0
    subscriptions = [
        "http://proxy-a.example:8080\nhttp://proxy-b.example:8080",
        "http://proxy-c.example:8080",
    ]

    def fetch_text(_url: str) -> str:
        return subscriptions.pop(0)

    pool = ProxyPool(
        subscription_url="https://subscription.example/sub",
        refresh_seconds=10,
        fetch_text=fetch_text,
        monotonic=lambda: now,
    )

    assert pool.next_proxy_url() == "http://proxy-a.example:8080"
    assert pool.next_proxy_url() == "http://proxy-b.example:8080"

    now = 1011.0
    assert pool.next_proxy_url() == "http://proxy-c.example:8080"


def test_subscription_without_usable_nodes_fails() -> None:
    pool = ProxyPool(
        subscription_url="https://subscription.example/sub",
        fetch_text=lambda _url: "proxies:\n  - {name: native, type: trojan, server: x, port: 443}",
    )

    with pytest.raises(RuntimeError, match="HTTP/SOCKS proxies"):
        pool.next_proxy_url()


def test_build_proxy_pool_returns_none_without_proxy_config() -> None:
    assert build_proxy_pool() is None


def test_build_proxy_pool_auto_detects_local_clash() -> None:
    pool = build_proxy_pool(
        auto_detect_proxy_url="http://127.0.0.1:7890",
        proxy_reachable=lambda _url: True,
    )

    assert pool is not None
    assert pool.source == "local_clash_auto"
    assert pool.next_proxy_url() == "http://127.0.0.1:7890"


def test_build_proxy_pool_skips_unreachable_auto_detected_proxy() -> None:
    pool = build_proxy_pool(
        auto_detect_proxy_url="http://127.0.0.1:7890",
        proxy_reachable=lambda _url: False,
    )

    assert pool is None
