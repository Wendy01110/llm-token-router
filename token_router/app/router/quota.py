from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


CALENDAR_QUOTA_PREFIX = "calendar:"


@dataclass(frozen=True)
class QuotaWindow:
    record_date: str
    usage_dates: tuple[str, ...]


def _local_now(now: datetime, timezone_name: str) -> datetime:
    timezone = ZoneInfo(timezone_name)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone)
    return now.astimezone(timezone)


def quota_date_for(now: datetime, timezone_name: str, reset_hour: int) -> str:
    local_now = _local_now(now, timezone_name)
    quota_day = local_now.date()
    if local_now.hour < reset_hour:
        quota_day = quota_day - timedelta(days=1)
    return quota_day.isoformat()


def quota_window_for(
    now: datetime,
    timezone_name: str,
    reset_hour: int,
    refresh_mode: str,
) -> QuotaWindow:
    if refresh_mode == "shifted_day":
        quota_date = quota_date_for(now, timezone_name, reset_hour)
        return QuotaWindow(record_date=quota_date, usage_dates=(quota_date,))
    if refresh_mode != "delayed_calendar_day":
        raise ValueError(f"unknown quota refresh mode {refresh_mode!r}")

    local_now = _local_now(now, timezone_name)
    today = local_now.date()
    usage_days = (today,)
    if local_now.hour < reset_hour:
        usage_days = (today - timedelta(days=1), today)
    return QuotaWindow(
        record_date=today.isoformat(),
        usage_dates=tuple(day.isoformat() for day in usage_days),
    )


def quota_storage_date(quota_date: str, refresh_mode: str) -> str:
    if refresh_mode == "delayed_calendar_day":
        return f"{CALENDAR_QUOTA_PREFIX}{quota_date}"
    return quota_date


def usage_ratio(used_tokens: int, daily_quota: int) -> float:
    return used_tokens / daily_quota


def is_exhausted(used_tokens: int, daily_quota: int) -> bool:
    return used_tokens >= daily_quota


def stage_for_usage(used_tokens: int, daily_quota: int) -> int | None:
    ratio = usage_ratio(used_tokens, daily_quota)
    if ratio >= 1.0:
        return None
    if ratio >= 0.75:
        return 4
    if ratio >= 0.50:
        return 3
    if ratio >= 0.25:
        return 2
    return 1
