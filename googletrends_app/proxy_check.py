from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import requests

from .clash import ClashController, build_clash_controller
from .proxy_pool import ProxyPool
from .settings import Settings
from .time_utils import BEIJING_TZ, DISPLAY_FORMAT

IP_CHECK_URL = "https://api.ipify.org?format=json"


@dataclass(frozen=True)
class ProxyRuntimeStatus:
    enabled: bool
    source: str
    proxy_hint: str
    subscription_configured: bool
    clash_enabled: bool
    clash_proxy_url: str
    clash_controller_url: str
    clash_proxy_group: str
    clash_mode: str
    clash_effective_proxy_group: str
    clash_controller_available: bool
    clash_current_proxy: str
    clash_candidate_count: int
    clash_controller_error: str
    network_error_rotation_enabled: bool
    request_profiles_enabled: bool


@dataclass(frozen=True)
class ProxyCheckResult:
    ok: bool
    checked_at: str
    proxy_url: str
    proxy_ip: str
    direct_ip: str
    elapsed_ms: int | None
    error: str
    warning: str


@dataclass(frozen=True)
class ClashRotateResult:
    ok: bool
    checked_at: str
    controller_url: str
    proxy_group: str
    selected_proxy: str
    error: str


def build_proxy_runtime_status(
    settings: Settings,
    proxy_pool: ProxyPool | None,
    clash_controller: ClashController | None = None,
) -> ProxyRuntimeStatus:
    source = "未配置"
    proxy_hint = "-"
    if settings.proxy_urls:
        source = "GOOGLETRENDS_PROXY_URLS"
        proxy_hint = summarize_proxy_urls(settings.proxy_urls)
    elif settings.proxy_subscription_url:
        source = "GOOGLETRENDS_PROXY_SUBSCRIPTION_URL"
        proxy_hint = "订阅代理"
    elif proxy_pool is not None and getattr(proxy_pool, "source", "") == "local_clash_auto":
        source = "自动发现本地 Clash"
        proxy_hint = mask_proxy_url(settings.clash_proxy_url)
    elif settings.clash_enabled:
        source = "本地 Clash"
        proxy_hint = mask_proxy_url(settings.clash_proxy_url)

    resolved_controller, controller_error = resolve_clash_controller(
        settings,
        clash_controller,
    )
    controller_available = False
    controller_url = settings.clash_controller_url or "自动发现/未配置"
    clash_mode = "-"
    effective_proxy_group = settings.clash_proxy_group
    current_proxy = "-"
    candidate_count = 0
    if resolved_controller is not None:
        controller_url = resolved_controller.controller_url
        try:
            clash_mode = resolved_controller.get_mode()
            effective_proxy_group = resolved_controller.effective_proxy_group_name()
            group = resolved_controller.get_proxy_group(effective_proxy_group)
            current_proxy = str(group.get("now") or "-")
            candidate_count = len(resolved_controller.available_candidates(group))
            controller_available = True
        except Exception as exc:
            controller_error = str(exc)

    return ProxyRuntimeStatus(
        enabled=proxy_pool is not None,
        source=source,
        proxy_hint=proxy_hint,
        subscription_configured=bool(settings.proxy_subscription_url),
        clash_enabled=settings.clash_enabled,
        clash_proxy_url=mask_proxy_url(settings.clash_proxy_url),
        clash_controller_url=controller_url,
        clash_proxy_group=settings.clash_proxy_group,
        clash_mode=clash_mode,
        clash_effective_proxy_group=effective_proxy_group,
        clash_controller_available=controller_available,
        clash_current_proxy=current_proxy,
        clash_candidate_count=candidate_count,
        clash_controller_error=controller_error,
        network_error_rotation_enabled=bool(
            clash_controller is not None and settings.clash_rotate_on_error
        ),
        request_profiles_enabled=settings.request_profiles_enabled,
    )


def run_proxy_check(
    settings: Settings,
    proxy_pool: ProxyPool | None,
    *,
    ip_check_url: str = IP_CHECK_URL,
) -> ProxyCheckResult:
    checked_at = datetime.now(BEIJING_TZ).strftime(DISPLAY_FORMAT)
    if proxy_pool is None:
        return ProxyCheckResult(
            ok=False,
            checked_at=checked_at,
            proxy_url="-",
            proxy_ip="-",
            direct_ip="-",
            elapsed_ms=None,
            error="当前没有可用代理池。请配置 GOOGLETRENDS_PROXY_URLS、GOOGLETRENDS_PROXY_SUBSCRIPTION_URL，或启用 Clash。",
            warning="",
        )

    try:
        proxy_url = proxy_pool.next_proxy_url()
    except Exception as exc:  # pragma: no cover - defensive path for live subscriptions
        return ProxyCheckResult(
            ok=False,
            checked_at=checked_at,
            proxy_url="-",
            proxy_ip="-",
            direct_ip="-",
            elapsed_ms=None,
            error=f"读取代理失败：{exc}",
            warning="",
        )

    if not proxy_url:
        return ProxyCheckResult(
            ok=False,
            checked_at=checked_at,
            proxy_url="-",
            proxy_ip="-",
            direct_ip="-",
            elapsed_ms=None,
            error="代理池为空，当前不会通过代理访问 Google Trends。",
            warning="",
        )

    direct_ip, direct_error = fetch_ip(ip_check_url)
    start = datetime.now()
    proxy_ip, proxy_error = fetch_ip(
        ip_check_url,
        proxies={"http": proxy_url, "https": proxy_url},
    )
    elapsed_ms = int((datetime.now() - start).total_seconds() * 1000)

    warning = ""
    if direct_error:
        warning = f"直连检测失败：{direct_error}"
    elif direct_ip and proxy_ip and direct_ip == proxy_ip:
        warning = "代理请求成功，但代理出口 IP 和直连 IP 一致，请确认 Clash 规则没有走 DIRECT。"

    return ProxyCheckResult(
        ok=not proxy_error,
        checked_at=checked_at,
        proxy_url=mask_proxy_url(proxy_url),
        proxy_ip=proxy_ip or "-",
        direct_ip=direct_ip or "-",
        elapsed_ms=elapsed_ms,
        error=proxy_error,
        warning=warning,
    )


def rotate_clash_proxy(
    settings: Settings,
    clash_controller: ClashController | None = None,
) -> ClashRotateResult:
    checked_at = datetime.now(BEIJING_TZ).strftime(DISPLAY_FORMAT)
    controller, error = resolve_clash_controller(settings, clash_controller)
    if controller is None:
        return ClashRotateResult(
            ok=False,
            checked_at=checked_at,
            controller_url=settings.clash_controller_url or "自动发现/未配置",
            proxy_group=settings.clash_proxy_group,
            selected_proxy="-",
            error=error or "Clash controller 不可用，无法切换代理组。",
        )

    try:
        selected = controller.rotate_proxy_with_group()
    except Exception as exc:
        return ClashRotateResult(
            ok=False,
            checked_at=checked_at,
            controller_url=controller.controller_url,
            proxy_group=safe_effective_proxy_group(controller),
            selected_proxy="-",
            error=str(exc),
        )

    if not selected:
        return ClashRotateResult(
            ok=False,
            checked_at=checked_at,
            controller_url=controller.controller_url,
            proxy_group=safe_effective_proxy_group(controller),
            selected_proxy="-",
            error="代理组没有可切换的候选节点。",
        )

    proxy_group, selected_proxy = selected
    return ClashRotateResult(
        ok=True,
        checked_at=checked_at,
        controller_url=controller.controller_url,
        proxy_group=proxy_group,
        selected_proxy=selected_proxy,
        error="",
    )


def resolve_clash_controller(
    settings: Settings,
    clash_controller: ClashController | None = None,
) -> tuple[ClashController | None, str]:
    if clash_controller is not None:
        return clash_controller, ""
    if not (
        settings.clash_enabled
        or settings.clash_controller_url
        or settings.proxy_auto_detect_local_clash
    ):
        return None, "Clash controller 未启用。"

    try:
        controller = build_clash_controller(
            enabled=True,
            controller_url=settings.clash_controller_url,
            secret=settings.clash_secret,
            proxy_group=settings.clash_proxy_group,
            config_path=settings.clash_config_path,
            skip_proxy_names=settings.clash_skip_proxy_names,
            allowed_proxy_name_keywords=settings.clash_allowed_proxy_name_keywords,
        )
    except Exception as exc:
        return None, str(exc)
    return controller, ""


def safe_effective_proxy_group(controller: ClashController) -> str:
    try:
        return controller.effective_proxy_group_name()
    except Exception:
        return controller.proxy_group


def fetch_ip(
    url: str,
    proxies: dict[str, str] | None = None,
) -> tuple[str, str]:
    try:
        response = requests.get(url, proxies=proxies, timeout=(5, 15))
        response.raise_for_status()
    except Exception as exc:
        return "", str(exc)

    try:
        payload = response.json()
    except ValueError:
        return response.text.strip(), ""

    ip = payload.get("ip") if isinstance(payload, dict) else None
    return str(ip or "").strip(), ""


def summarize_proxy_urls(value: str) -> str:
    urls = [line.strip() for line in value.replace(",", "\n").splitlines() if line.strip()]
    if not urls:
        return "-"
    if len(urls) == 1:
        return mask_proxy_url(urls[0])
    return f"已配置 {len(urls)} 个代理"


def mask_proxy_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.username:
        return value

    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = f"{parsed.username}:***@{hostname}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
