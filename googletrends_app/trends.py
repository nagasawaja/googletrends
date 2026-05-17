from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


SHORT_TIMEFRAME = "now 7-d"
MID_TIMEFRAME = "today 3-m"
LONG_TIMEFRAME = "today 12-m"
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
    ) -> list[TrendPoint]:
        ...


class PytrendsProvider:
    def __init__(self, hl: str = "en-US", tz: int = 480) -> None:
        self.hl = hl
        self.tz = tz

    def collect_keyword(
        self,
        term: str,
        timeframe: str = DEFAULT_TIMEFRAME,
        geo: str = DEFAULT_GEO,
    ) -> list[TrendPoint]:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl=self.hl, tz=self.tz)
        pytrends.build_payload([term], timeframe=timeframe, geo=geo)
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
