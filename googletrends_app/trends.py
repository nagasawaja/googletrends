from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


SHORT_TIMEFRAME = "now 7-d"
MID_TIMEFRAME = "today 3-m"
LONG_TIMEFRAME = "today 12-m"
MONITORED_TIMEFRAMES = (SHORT_TIMEFRAME, MID_TIMEFRAME, LONG_TIMEFRAME)
DEFAULT_TIMEFRAME = LONG_TIMEFRAME
DEFAULT_GEO = ""


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
                timeframe != SHORT_TIMEFRAME
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
