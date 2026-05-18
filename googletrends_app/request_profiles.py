from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestProfile:
    name: str
    headers: dict[str, str]


DEFAULT_REQUEST_PROFILES = (
    RequestProfile(
        name="chrome_windows",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "accept-language": "en-US,en;q=0.9",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://trends.google.com/trends/explore",
        },
    ),
    RequestProfile(
        name="chrome_macos",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "accept-language": "en-US,en;q=0.9",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://trends.google.com/trends/explore",
        },
    ),
    RequestProfile(
        name="edge_windows",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
            ),
            "accept-language": "en-US,en;q=0.9",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://trends.google.com/trends/explore",
        },
    ),
    RequestProfile(
        name="safari_macos",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.4 Safari/605.1.15"
            ),
            "accept-language": "en-US,en;q=0.9",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://trends.google.com/trends/explore",
        },
    ),
    RequestProfile(
        name="firefox_windows",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
                "Gecko/20100101 Firefox/125.0"
            ),
            "accept-language": "en-US,en;q=0.9",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://trends.google.com/trends/explore",
        },
    ),
)


class RequestProfilePool:
    def __init__(
        self,
        profiles: tuple[RequestProfile, ...] = DEFAULT_REQUEST_PROFILES,
    ) -> None:
        if not profiles:
            raise ValueError("At least one request profile is required.")
        self._profiles = profiles
        self._assignments: dict[str, int] = {}
        self._cursor = 0
        self._lock = threading.Lock()

    @property
    def profiles(self) -> tuple[RequestProfile, ...]:
        return self._profiles

    def profile_for(self, key: str) -> RequestProfile:
        with self._lock:
            if key not in self._assignments:
                self._assignments[key] = self._cursor % len(self._profiles)
                self._cursor += 1
            return self._profiles[self._assignments[key]]

    def rotate_profile_for(self, key: str) -> RequestProfile:
        with self._lock:
            current = self._assignments.get(key, -1)
            next_index = (current + 1) % len(self._profiles)
            self._assignments[key] = next_index
            return self._profiles[next_index]

    def headers_for(self, key: str) -> dict[str, str]:
        return dict(self.profile_for(key).headers)


def build_request_profile_pool(enabled: bool = True) -> RequestProfilePool | None:
    if not enabled:
        return None
    return RequestProfilePool()
