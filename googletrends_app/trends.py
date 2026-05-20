from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from .clash import ClashController
from .proxy_pool import ProxyPool
from .request_profiles import RequestProfilePool


SHORT_TIMEFRAME = "now 7-d"
MID_TIMEFRAME = "today 3-m"
LONG_TIMEFRAME = "today 12-m"
DEFAULT_GPROP = ""
AVAILABLE_GPROPS = ("", "images", "news", "youtube", "froogle")
AVAILABLE_TIMEFRAMES = (
    "now 1-d",
    SHORT_TIMEFRAME,
    "today 1-m",
    MID_TIMEFRAME,
    LONG_TIMEFRAME,
    "today 5-y",
)
NOW_TIMEFRAMES = ("now 1-d", SHORT_TIMEFRAME)
MID_TIMEFRAMES = ("today 1-m", MID_TIMEFRAME)
LONG_TIMEFRAMES = (LONG_TIMEFRAME, "today 5-y")
CONTEXT_TIMEFRAMES = MID_TIMEFRAMES + LONG_TIMEFRAMES
MONITORED_TIMEFRAMES = (SHORT_TIMEFRAME, MID_TIMEFRAME)
DEFAULT_TIMEFRAME = SHORT_TIMEFRAME
DEFAULT_GEO = ""
DEFAULT_TIMEFRAMES_TEXT = ",".join(MONITORED_TIMEFRAMES)
DEFAULT_GPROPS = (DEFAULT_GPROP,)
DEFAULT_GPROPS_TEXT = DEFAULT_GPROP


def parse_timeframes(value: str | None) -> tuple[str, ...]:
    if not value:
        return MONITORED_TIMEFRAMES
    selected: list[str] = []
    for item in value.split(","):
        timeframe = item.strip()
        if timeframe in AVAILABLE_TIMEFRAMES and timeframe not in selected:
            selected.append(timeframe)
    return tuple(selected) or MONITORED_TIMEFRAMES


def serialize_timeframes(timeframes: list[str] | tuple[str, ...]) -> str:
    selected = [item for item in timeframes if item in AVAILABLE_TIMEFRAMES]
    if not selected:
        selected = list(MONITORED_TIMEFRAMES)
    return ",".join(dict.fromkeys(selected))


def parse_gprops(value: str | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_GPROPS
    selected: list[str] = []
    for item in value.split(","):
        gprop = item.strip()
        if gprop in AVAILABLE_GPROPS and gprop not in selected:
            selected.append(gprop)
    return tuple(selected) or DEFAULT_GPROPS


def serialize_gprops(gprops: list[str] | tuple[str, ...]) -> str:
    selected = [item for item in gprops if item in AVAILABLE_GPROPS]
    if not selected:
        selected = list(DEFAULT_GPROPS)
    return ",".join(dict.fromkeys(selected))


def parse_gprop(value: str | None) -> str:
    return parse_gprops(value)[0]


@dataclass(frozen=True)
class TrendPoint:
    date: date | datetime
    value: int
    is_partial: bool = False

    def as_record(self) -> dict[str, object]:
        if isinstance(self.date, datetime):
            point_date = self.date.isoformat(timespec="seconds")
        else:
            point_date = self.date.isoformat()
        return {
            "date": point_date,
            "value": self.value,
            "is_partial": self.is_partial,
        }


class TrendsProvider(Protocol):
    def collect_keyword(
        self,
        term: str,
        timeframe: str = DEFAULT_TIMEFRAME,
        geo: str = DEFAULT_GEO,
        gprop: str = DEFAULT_GPROP,
    ) -> list[TrendPoint]:
        ...


class PytrendsProvider:
    def __init__(
        self,
        hl: str = "en-US",
        tz: int = 480,
        proxy_pool: ProxyPool | None = None,
        request_profile_pool: RequestProfilePool | None = None,
        clash_controller: ClashController | None = None,
        clash_rotate_on_429: bool = True,
        clash_rotate_on_error: bool = True,
        clash_retry_after_rotate: bool = True,
    ) -> None:
        self.hl = hl
        self.tz = tz
        self.proxy_pool = proxy_pool
        self.request_profile_pool = request_profile_pool
        self.clash_controller = clash_controller
        self.clash_rotate_on_429 = clash_rotate_on_429
        self.clash_rotate_on_error = clash_rotate_on_error
        self.clash_retry_after_rotate = clash_retry_after_rotate
        self.last_proxy_name: str | None = None
        self.last_proxy_url: str | None = None
        self.last_profile_key: str | None = None

    def collect_keyword(
        self,
        term: str,
        timeframe: str = DEFAULT_TIMEFRAME,
        geo: str = DEFAULT_GEO,
        gprop: str = DEFAULT_GPROP,
    ) -> list[TrendPoint]:
        try:
            return self._collect_keyword_once(
                term,
                timeframe=timeframe,
                geo=geo,
                gprop=gprop,
            )
        except Exception as exc:
            if not self._should_rotate_clash_proxy(exc):
                raise
            try:
                selected_proxy = (
                    self.clash_controller.rotate_proxy()
                    if self.clash_controller
                    else None
                )
            except Exception as rotate_exc:
                raise RuntimeError(
                    f"{exc} (Clash proxy rotation failed: {rotate_exc})"
                ) from exc
            if selected_proxy and self.clash_retry_after_rotate:
                return self._collect_keyword_once(
                    term,
                    timeframe=timeframe,
                    geo=geo,
                    gprop=gprop,
                    profile_key=f"clash:{selected_proxy}",
                )
            raise

    def _collect_keyword_once(
        self,
        term: str,
        timeframe: str = DEFAULT_TIMEFRAME,
        geo: str = DEFAULT_GEO,
        gprop: str = DEFAULT_GPROP,
        profile_key: str | None = None,
    ) -> list[TrendPoint]:
        pytrends = self._build_trend_req(profile_key=profile_key)
        pytrends.build_payload([term], timeframe=timeframe, geo=geo, gprop=gprop)
        data = pytrends.interest_over_time()
        if data.empty:
            return []

        points: list[TrendPoint] = []
        for index, row in data.iterrows():
            value = row.get(term)
            if value is None:
                continue
            partial_value = bool(row.get("isPartial", False))
            timestamp = index.to_pydatetime()
            if (
                timeframe not in NOW_TIMEFRAMES
                and timestamp.hour == 0
                and timestamp.minute == 0
                and timestamp.second == 0
                and timestamp.microsecond == 0
            ):
                point_date = timestamp.date()
            else:
                point_date = timestamp.replace(microsecond=0)
            points.append(
                TrendPoint(
                    date=point_date,
                    value=int(value),
                    is_partial=partial_value,
                )
            )
        return points

    def _build_trend_req(self, profile_key: str | None = None):
        from pytrends.request import TrendReq

        proxy_url = self.proxy_pool.next_proxy_url() if self.proxy_pool else None
        proxy_name = getattr(self.proxy_pool, "last_proxy_name", None) if self.proxy_pool else None
        if proxy_name is None:
            proxy_name = "direct" if proxy_url is None else proxy_url
        self.last_proxy_name = proxy_name
        self.last_proxy_url = proxy_url
        identity_key = profile_key or proxy_name or "direct"
        self.last_profile_key = identity_key
        trend_kwargs: dict[str, object] = {"hl": self.hl, "tz": self.tz}
        if proxy_url:
            trend_kwargs["proxies"] = [proxy_url]
        if self.request_profile_pool is not None:
            trend_kwargs["requests_args"] = {
                "headers": self.request_profile_pool.headers_for(identity_key)
            }
        return TrendReq(**trend_kwargs)

    def _should_rotate_clash_proxy(self, exc: Exception) -> bool:
        if self.clash_controller is None:
            return False
        if self.clash_rotate_on_429 and is_rate_limit_error(str(exc)):
            return True
        return self.clash_rotate_on_error


def is_rate_limit_error(error: str) -> bool:
    text = error.lower()
    return (
        "429" in text
        or "too many requests" in text
        or "rate limit" in text
        or "ratelimit" in text
    )
