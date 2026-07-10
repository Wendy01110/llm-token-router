from datetime import datetime
from zoneinfo import ZoneInfo

from token_router.app.router.quota import (
    quota_date_for,
    quota_window_for,
    stage_for_usage,
)


def test_quota_date_uses_reset_hour_boundary():
    tz = ZoneInfo("Asia/Shanghai")

    before_reset = datetime(2026, 5, 27, 10, 59, tzinfo=tz)
    after_reset = datetime(2026, 5, 27, 11, 0, tzinfo=tz)

    assert quota_date_for(before_reset, "Asia/Shanghai", 11) == "2026-05-26"
    assert quota_date_for(after_reset, "Asia/Shanghai", 11) == "2026-05-27"


def test_delayed_calendar_day_window_keeps_today_usage_after_release():
    tz = ZoneInfo("Asia/Shanghai")

    at_midnight = quota_window_for(
        datetime(2026, 5, 27, 0, 0, tzinfo=tz),
        "Asia/Shanghai",
        11,
        "delayed_calendar_day",
    )
    before_release = quota_window_for(
        datetime(2026, 5, 27, 10, 59, tzinfo=tz),
        "Asia/Shanghai",
        11,
        "delayed_calendar_day",
    )
    after_release = quota_window_for(
        datetime(2026, 5, 27, 11, 0, tzinfo=tz),
        "Asia/Shanghai",
        11,
        "delayed_calendar_day",
    )

    assert at_midnight.record_date == "2026-05-27"
    assert at_midnight.usage_dates == ("2026-05-26", "2026-05-27")
    assert before_release.record_date == "2026-05-27"
    assert before_release.usage_dates == ("2026-05-26", "2026-05-27")
    assert after_release.record_date == "2026-05-27"
    assert after_release.usage_dates == ("2026-05-27",)


def test_shifted_day_window_preserves_existing_behavior():
    tz = ZoneInfo("Asia/Shanghai")

    window = quota_window_for(
        datetime(2026, 5, 27, 10, 59, tzinfo=tz),
        "Asia/Shanghai",
        11,
        "shifted_day",
    )

    assert window.record_date == "2026-05-26"
    assert window.usage_dates == ("2026-05-26",)


def test_stage_for_usage_uses_25_percent_buckets():
    assert stage_for_usage(0, 100) == 1
    assert stage_for_usage(24, 100) == 1
    assert stage_for_usage(25, 100) == 2
    assert stage_for_usage(50, 100) == 3
    assert stage_for_usage(75, 100) == 4
    assert stage_for_usage(100, 100) is None
