from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests


DEFAULT_CLASH_CONFIG_PATH = Path.home() / ".config" / "clash" / "config.yaml"
DEFAULT_SKIP_PROXY_NAMES = ("DIRECT", "REJECT", "REJECT-DROP", "PASS")
DEFAULT_ALLOWED_PROXY_NAME_KEYWORDS = ("香港", "日本", "美国", "新加坡", "狮城", "台湾")
SKIP_PROXY_PREFIXES = ("Traffic:", "Expire:")
DEFAULT_CONNECTIVITY_TEST_URL = "https://www.google.com/generate_204"
DEFAULT_CONNECTIVITY_TEST_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class ClashConfig:
    controller_url: str | None = None
    secret: str | None = None


def read_clash_config(path: str | Path = DEFAULT_CLASH_CONFIG_PATH) -> ClashConfig:
    config_path = Path(path).expanduser()
    if not config_path.exists():
        return ClashConfig()

    text = config_path.read_text()
    controller_url = None
    secret = None

    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(text) or {}
        if isinstance(data, dict):
            controller_url = data.get("external-controller") or None
            secret = data.get("secret") or None
    except Exception:
        controller_url = find_yaml_scalar(text, "external-controller")
        secret = find_yaml_scalar(text, "secret")

    return ClashConfig(
        controller_url=(
            normalize_controller_url(str(controller_url)) if controller_url else None
        ),
        secret=str(secret) if secret else None,
    )


def find_yaml_scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*['\"]?([^'\"\n#]+)", text, re.M)
    if not match:
        return None
    return match.group(1).strip()


def normalize_controller_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url.replace("://0.0.0.0:", "://127.0.0.1:")


def parse_skip_proxy_names(value: str | None) -> tuple[str, ...]:
    if not value:
        return DEFAULT_SKIP_PROXY_NAMES
    names = tuple(item.strip() for item in value.split(",") if item.strip())
    return names or DEFAULT_SKIP_PROXY_NAMES


def parse_allowed_proxy_name_keywords(value: str | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_ALLOWED_PROXY_NAME_KEYWORDS
    keywords = tuple(item.strip() for item in value.split(",") if item.strip())
    return keywords


def discover_clash_controller_urls() -> list[str]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return []

    urls: list[str] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        if not re.search(r"clash|mihomo", line, re.I):
            continue
        match = re.search(r"\b(?:127\.0\.0\.1|localhost):(\d+)\b", line)
        if not match:
            continue
        port = match.group(1)
        if port in {"7890", "7891", "7892", "7893"}:
            continue
        url = f"http://127.0.0.1:{port}"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


class ClashController:
    def __init__(
        self,
        controller_url: str,
        proxy_group: str,
        secret: str | None = None,
        skip_proxy_names: tuple[str, ...] = DEFAULT_SKIP_PROXY_NAMES,
        allowed_proxy_name_keywords: tuple[str, ...] = DEFAULT_ALLOWED_PROXY_NAME_KEYWORDS,
        timeout_seconds: float = 5,
        session: requests.Session | None = None,
    ) -> None:
        self.controller_url = normalize_controller_url(controller_url)
        self.proxy_group = proxy_group
        self.secret = secret
        self.skip_proxy_names = set(skip_proxy_names)
        self.allowed_proxy_name_keywords = allowed_proxy_name_keywords
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self._lock = threading.Lock()

    def rotate_proxy(self) -> str | None:
        selected = self.rotate_proxy_with_group()
        return selected[1] if selected else None

    def rotate_proxy_with_group(self) -> tuple[str, str] | None:
        with self._lock:
            proxy_group = self.effective_proxy_group_name()
            group = self.get_proxy_group(proxy_group)
            current = str(group.get("now") or "")
            candidates = self.available_candidates(group)
            if not candidates:
                return None

            try:
                start_index = candidates.index(current) + 1
            except ValueError:
                start_index = 0
            ordered_candidates = candidates[start_index:] + candidates[:start_index]
            last_error: Exception | None = None
            for selected in ordered_candidates:
                try:
                    if not self.test_proxy_connectivity(selected):
                        continue
                except Exception as exc:
                    last_error = exc
                    continue
                self.select_proxy(selected, proxy_group=proxy_group)
                return proxy_group, selected
            if last_error is not None:
                raise RuntimeError(
                    f"No reachable proxy candidate was found for group {proxy_group!r}. "
                    f"Last connectivity check failed: {last_error}"
                ) from last_error
            raise RuntimeError(
                f"No reachable proxy candidate was found for group {proxy_group!r}."
            )

    def is_available(self) -> bool:
        try:
            response = self.session.get(
                f"{self.controller_url}/configs",
                headers=self.headers(),
                timeout=min(self.timeout_seconds, 2),
            )
            response.raise_for_status()
        except Exception:
            return False
        return True

    def get_mode(self) -> str:
        response = self.session.get(
            f"{self.controller_url}/configs",
            headers=self.headers(),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        mode = data.get("mode") if isinstance(data, dict) else None
        return str(mode or "rule").lower()

    def effective_proxy_group_name(self) -> str:
        if self.get_mode() == "global":
            return "GLOBAL"
        return self.proxy_group

    def get_proxy_group(self, proxy_group: str | None = None) -> dict[str, object]:
        resolved_proxy_group = proxy_group or self.proxy_group
        proxies = self.get_proxies()
        group = proxies.get(resolved_proxy_group)
        if not isinstance(group, dict):
            raise RuntimeError(f"Clash proxy group {resolved_proxy_group!r} was not found.")
        if not isinstance(group.get("all"), list):
            raise RuntimeError(f"Clash proxy group {resolved_proxy_group!r} is not selectable.")
        return group

    def get_proxies(self) -> dict[str, dict[str, object]]:
        response = self.session.get(
            f"{self.controller_url}/proxies",
            headers=self.headers(),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        proxies = data.get("proxies") if isinstance(data, dict) else None
        if not isinstance(proxies, dict):
            raise RuntimeError("Clash controller returned an invalid proxies response.")
        return proxies

    def available_candidates(self, group: dict[str, object]) -> list[str]:
        values = group.get("all")
        if not isinstance(values, list):
            return []
        return [
            str(value)
            for value in values
            if not self.is_skipped_proxy_name(str(value))
        ]

    def is_skipped_proxy_name(self, name: str) -> bool:
        if name in self.skip_proxy_names or name.startswith(SKIP_PROXY_PREFIXES):
            return True
        return not self.is_allowed_proxy_name(name)

    def is_allowed_proxy_name(self, name: str) -> bool:
        if not self.allowed_proxy_name_keywords:
            return True
        return any(keyword in name for keyword in self.allowed_proxy_name_keywords)

    def select_proxy(self, name: str, proxy_group: str | None = None) -> None:
        resolved_proxy_group = proxy_group or self.proxy_group
        response = self.session.put(
            f"{self.controller_url}/proxies/{quote(resolved_proxy_group, safe='')}",
            headers=self.headers(),
            json={"name": name},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()

    def test_proxy_connectivity(
        self,
        name: str,
        url: str = DEFAULT_CONNECTIVITY_TEST_URL,
        timeout_ms: int = DEFAULT_CONNECTIVITY_TEST_TIMEOUT_MS,
    ) -> bool:
        response = self.session.get(
            f"{self.controller_url}/proxies/{quote(name, safe='')}/delay",
            headers=self.headers(),
            params={"url": url, "timeout": timeout_ms},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Clash controller returned an invalid delay response.")
        delay = data.get("delay")
        return delay is not None

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        return headers


def build_clash_controller(
    enabled: bool = False,
    controller_url: str | None = None,
    secret: str | None = None,
    proxy_group: str | None = None,
    config_path: str | Path | None = DEFAULT_CLASH_CONFIG_PATH,
    skip_proxy_names: str | None = None,
    allowed_proxy_name_keywords: str | None = None,
    probe: bool = True,
) -> ClashController | None:
    if not enabled and not controller_url:
        return None

    candidate_urls: list[str] = []
    if controller_url:
        candidate_urls.append(normalize_controller_url(controller_url))
    resolved_secret = secret
    if (not candidate_urls or resolved_secret is None) and config_path:
        local_config = read_clash_config(config_path)
        if local_config.controller_url and local_config.controller_url not in candidate_urls:
            candidate_urls.append(local_config.controller_url)
        resolved_secret = resolved_secret if resolved_secret is not None else local_config.secret

    for discovered_url in discover_clash_controller_urls():
        if discovered_url not in candidate_urls:
            candidate_urls.append(discovered_url)

    if not candidate_urls:
        raise RuntimeError(
            "Clash controller is enabled, but no controller URL was configured or found."
        )

    for candidate_url in candidate_urls:
        controller = ClashController(
            controller_url=candidate_url,
            secret=resolved_secret,
            proxy_group=proxy_group or "Google",
            skip_proxy_names=parse_skip_proxy_names(skip_proxy_names),
            allowed_proxy_name_keywords=parse_allowed_proxy_name_keywords(
                allowed_proxy_name_keywords
            ),
        )
        if not probe or controller.is_available():
            return controller

    raise RuntimeError(
        "Clash controller is enabled, but no reachable controller was found. "
        "Set GOOGLETRENDS_CLASH_CONTROLLER_URL to the current Clash API address."
    )
