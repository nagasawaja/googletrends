from __future__ import annotations

import base64
import csv
import re
import socket
import threading
import time
from dataclasses import dataclass
from io import StringIO
from typing import Callable
from urllib.parse import quote, urlsplit, urlunsplit

import requests


SUPPORTED_PROXY_SCHEMES = {
    "http",
    "https",
    "socks4",
    "socks4a",
    "socks5",
    "socks5h",
}
SUPPORTED_CLASH_TYPES = {
    "http": "http",
    "https": "https",
    "socks5": "socks5h",
    "socks": "socks5h",
}


@dataclass(frozen=True)
class ProxyEndpoint:
    name: str
    url: str


def parse_proxy_subscription(text: str) -> list[ProxyEndpoint]:
    """Parse direct proxy URLs and Clash-style HTTP/SOCKS nodes."""
    endpoints: list[ProxyEndpoint] = []
    seen: set[str] = set()

    def add(items: list[ProxyEndpoint]) -> None:
        for item in items:
            if item.url in seen:
                continue
            seen.add(item.url)
            endpoints.append(item)

    add(parse_direct_proxy_urls(text))
    decoded = decode_base64_subscription(text)
    if decoded:
        add(parse_direct_proxy_urls(decoded))
        add(parse_clash_subscription(decoded))
    add(parse_clash_subscription(text))
    return endpoints


def parse_direct_proxy_urls(text: str) -> list[ProxyEndpoint]:
    endpoints: list[ProxyEndpoint] = []
    index = 1
    for token in re.split(r"[\s,]+", text):
        value = token.strip().strip("'\"")
        if not value:
            continue
        normalized = normalize_proxy_url(value)
        if not normalized:
            continue
        endpoints.append(ProxyEndpoint(name=f"proxy-{index}", url=normalized))
        index += 1
    return endpoints


def normalize_proxy_url(value: str) -> str | None:
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        return None
    if not parsed.hostname or parsed.port is None:
        return None
    return urlunsplit(
        (
            scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            parsed.query,
            "",
        )
    )


def decode_base64_subscription(text: str) -> str | None:
    compact = "".join(text.split())
    if not compact or not re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
        return None
    padding = "=" * (-len(compact) % 4)
    for decoder in (
        base64.b64decode,
        base64.urlsafe_b64decode,
    ):
        try:
            decoded = decoder((compact + padding).encode("ascii")).decode("utf-8")
        except Exception:
            continue
        if "://" in decoded or "proxies:" in decoded:
            return decoded
    return None


def parse_clash_subscription(text: str) -> list[ProxyEndpoint]:
    nodes = load_clash_nodes(text)
    endpoints: list[ProxyEndpoint] = []
    for index, node in enumerate(nodes, start=1):
        proxy_type = str(node.get("type", "")).lower()
        scheme = SUPPORTED_CLASH_TYPES.get(proxy_type)
        if not scheme:
            continue
        server = str(node.get("server", "")).strip()
        port = node.get("port")
        if not server or port is None:
            continue
        try:
            port_number = int(str(port))
        except ValueError:
            continue
        username = node.get("username") or node.get("user")
        password = node.get("password") or node.get("pass")
        name = str(node.get("name") or f"clash-proxy-{index}")
        url = build_proxy_url(
            scheme=scheme,
            server=server,
            port=port_number,
            username=str(username) if username is not None else None,
            password=str(password) if password is not None else None,
        )
        endpoints.append(ProxyEndpoint(name=name, url=url))
    return endpoints


def load_clash_nodes(text: str) -> list[dict[str, object]]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ModuleNotFoundError:
        return parse_clash_nodes_fallback(text)

    try:
        data = yaml.safe_load(text)
    except Exception:
        return parse_clash_nodes_fallback(text)

    if isinstance(data, dict):
        nodes = data.get("proxies")
    else:
        nodes = data
    if not isinstance(nodes, list):
        return parse_clash_nodes_fallback(text)
    return [node for node in nodes if isinstance(node, dict)]


def parse_clash_nodes_fallback(text: str) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    def finish_current() -> None:
        nonlocal current
        if current:
            nodes.append(current)
        current = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- {") and line.endswith("}"):
            finish_current()
            nodes.append(parse_inline_mapping(line[2:].strip()))
            continue
        if line.startswith("- "):
            finish_current()
            current = {}
            remainder = line[2:].strip()
            if ":" in remainder:
                key, value = remainder.split(":", 1)
                current[key.strip()] = clean_yaml_scalar(value)
            continue
        if current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key.strip()] = clean_yaml_scalar(value)

    finish_current()
    return nodes


def parse_inline_mapping(value: str) -> dict[str, object]:
    inner = value.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    reader = csv.reader(StringIO(inner), skipinitialspace=True)
    result: dict[str, object] = {}
    for row in reader:
        for item in row:
            if ":" not in item:
                continue
            key, raw = item.split(":", 1)
            result[key.strip()] = clean_yaml_scalar(raw)
    return result


def clean_yaml_scalar(value: object) -> str:
    text = str(value).strip()
    if " #" in text:
        text = text.split(" #", 1)[0].strip()
    return text.strip("'\"")


def build_proxy_url(
    scheme: str,
    server: str,
    port: int,
    username: str | None = None,
    password: str | None = None,
) -> str:
    auth = ""
    if username:
        auth = quote(username, safe="")
        if password is not None:
            auth = f"{auth}:{quote(password, safe='')}"
        auth = f"{auth}@"
    host = f"[{server}]" if ":" in server and not server.startswith("[") else server
    return f"{scheme}://{auth}{host}:{port}"


class ProxyPool:
    def __init__(
        self,
        proxies: list[ProxyEndpoint] | None = None,
        subscription_url: str | None = None,
        refresh_seconds: int = 3600,
        fetch_text: Callable[[str], str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        source: str = "configured",
    ) -> None:
        self._proxies = proxies or []
        self._subscription_url = subscription_url
        self._refresh_seconds = max(0, refresh_seconds)
        self._fetch_text = fetch_text or fetch_subscription_text
        self._monotonic = monotonic
        self._last_refresh: float | None = None
        self._cursor = 0
        self._lock = threading.Lock()
        self.source = source
        self.last_proxy_name: str | None = None
        self.last_proxy_url: str | None = None

    def next_proxy_url(self) -> str | None:
        with self._lock:
            self._refresh_if_needed()
            if not self._proxies:
                self.last_proxy_name = None
                self.last_proxy_url = None
                return None
            proxy = self._proxies[self._cursor % len(self._proxies)]
            self._cursor += 1
            self.last_proxy_name = proxy.name
            self.last_proxy_url = proxy.url
            return proxy.url

    def _refresh_if_needed(self) -> None:
        if not self._subscription_url:
            return
        now = self._monotonic()
        if (
            self._proxies
            and self._last_refresh is not None
            and self._refresh_seconds
            and now - self._last_refresh < self._refresh_seconds
        ):
            return

        text = self._fetch_text(self._subscription_url)
        proxies = parse_proxy_subscription(text)
        if not proxies:
            raise RuntimeError(
                "Proxy subscription did not contain HTTP/SOCKS proxies usable by requests. "
                "For Clash-native nodes such as ss, vmess, vless, or trojan, run Clash and "
                "configure GOOGLETRENDS_PROXY_URLS with its local mixed-port."
            )
        self._proxies = proxies
        self._last_refresh = now
        self._cursor %= len(self._proxies)


def fetch_subscription_text(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": "googletrends-monitor/0.1"},
        timeout=(5, 20),
    )
    response.raise_for_status()
    return response.text


def build_proxy_pool(
    proxy_urls: str | None = None,
    subscription_url: str | None = None,
    refresh_seconds: int = 3600,
    auto_detect_proxy_url: str | None = None,
    proxy_reachable: Callable[[str], bool] | None = None,
) -> ProxyPool | None:
    static_proxies = parse_direct_proxy_urls(proxy_urls or "")
    source = "env_proxy_urls" if static_proxies else "subscription"
    resolved_proxy_reachable = proxy_reachable or is_local_proxy_reachable
    if not static_proxies and not subscription_url and auto_detect_proxy_url:
        if resolved_proxy_reachable(auto_detect_proxy_url):
            static_proxies = parse_direct_proxy_urls(auto_detect_proxy_url)
            source = "local_clash_auto"
    if not static_proxies and not subscription_url:
        return None
    return ProxyPool(
        proxies=static_proxies,
        subscription_url=subscription_url or None,
        refresh_seconds=refresh_seconds,
        source=source,
    )


def is_local_proxy_reachable(proxy_url: str, timeout: float = 0.25) -> bool:
    normalized = normalize_proxy_url(proxy_url)
    if normalized is None:
        return False

    parsed = urlsplit(normalized)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.port is None:
        return False

    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout):
            return True
    except OSError:
        return False
