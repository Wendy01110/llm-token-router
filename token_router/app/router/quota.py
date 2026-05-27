from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def quota_date_for(now: datetime, timezone_name: str, reset_hour: int) -> str:
    local_now = now.astimezone(ZoneInfo(timezone_name))
    quota_day = local_now.date()
    if local_now.hour < reset_hour:
        quota_day = quota_day - timedelta(days=1)
    return quota_day.isoformat()


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
