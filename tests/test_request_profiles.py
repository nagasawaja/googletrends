from __future__ import annotations

from datetime import datetime

import pandas as pd

from googletrends_app.request_profiles import RequestProfile, RequestProfilePool
from googletrends_app.trends import PytrendsProvider


def test_request_profile_pool_keeps_profiles_sticky() -> None:
    pool = RequestProfilePool(
        profiles=(
            RequestProfile(name="profile-a", headers={"User-Agent": "A"}),
            RequestProfile(name="profile-b", headers={"User-Agent": "B"}),
        )
    )

    assert pool.headers_for("proxy-a")["User-Agent"] == "A"
    assert pool.headers_for("proxy-a")["User-Agent"] == "A"
    assert pool.headers_for("proxy-b")["User-Agent"] == "B"


def test_pytrends_provider_passes_browser_profile_headers(monkeypatch) -> None:
    captured_kwargs: list[dict[str, object]] = []

    class FakeProxyPool:
        def next_proxy_url(self) -> str:
            return "http://127.0.0.1:7890"

    class FakeTrendReq:
        def __init__(self, **kwargs) -> None:
            captured_kwargs.append(kwargs)

        def build_payload(self, kw_list, timeframe: str, geo: str, gprop: str = "") -> None:
            return None

        def interest_over_time(self) -> pd.DataFrame:
            return pd.DataFrame(
                {"ChatGPT": [10], "isPartial": [False]},
                index=[datetime(2026, 5, 18, 10, 0, 0)],
            )

    import pytrends.request

    monkeypatch.setattr(pytrends.request, "TrendReq", FakeTrendReq)
    profile_pool = RequestProfilePool(
        profiles=(
            RequestProfile(name="profile-a", headers={"User-Agent": "A"}),
            RequestProfile(name="profile-b", headers={"User-Agent": "B"}),
        )
    )
    provider = PytrendsProvider(
        proxy_pool=FakeProxyPool(),  # type: ignore[arg-type]
        request_profile_pool=profile_pool,
    )

    provider.collect_keyword("ChatGPT")
    provider.collect_keyword("ChatGPT")

    first_args = captured_kwargs[0]["requests_args"]
    second_args = captured_kwargs[1]["requests_args"]
    assert first_args["headers"]["User-Agent"] == "A"
    assert second_args["headers"]["User-Agent"] == "A"
    assert captured_kwargs[0]["proxies"] == ["http://127.0.0.1:7890"]
