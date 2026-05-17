from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
DISPLAY_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_beijing(value: object, timeframe: str | None = None) -> str:
    if value is None:
        return "-"

    if isinstance(value, datetime):
        return to_beijing(value).strftime(DISPLAY_FORMAT)

    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=BEIJING_TZ).strftime(
            DISPLAY_FORMAT
        )

    text = str(value).strip()
    if not text:
        return "-"

    try:
        parsed = parse_datetime_text(text, timeframe=timeframe)
    except ValueError:
        return text

    return parsed.strftime(DISPLAY_FORMAT)


def to_beijing(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING_TZ)


def parse_datetime_text(text: str, timeframe: str | None = None) -> datetime:
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        parsed_date = date.fromisoformat(text)
        tz = timezone.utc if timeframe == "now 7-d" else BEIJING_TZ
        return datetime.combine(parsed_date, time.min, tzinfo=tz).astimezone(
            BEIJING_TZ
        )

    normalized = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BEIJING_TZ)
