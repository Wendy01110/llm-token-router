from token_router.app.database import connect, init_db
from token_router.app.usage import UsageManager


def test_usage_manager_records_and_reads_daily_usage(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    manager = UsageManager(db_path)

    manager.record_usage(
        provider="test",
        key_id="key1",
        model_name="model-a",
        quota_date="2026-05-27",
        prompt_tokens=12,
        completion_tokens=8,
    )

    usage = manager.get_usage("test", "key1", "model-a", "2026-05-27")

    assert usage.prompt_tokens == 12
    assert usage.completion_tokens == 8
    assert usage.total_tokens == 20
    assert usage.request_count == 1


def test_usage_manager_sums_request_count_for_key(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    manager = UsageManager(db_path)

    manager.record_usage(
        provider="test",
        key_id="key1",
        model_name="model-a",
        quota_date="2026-05-27",
        prompt_tokens=1,
        completion_tokens=1,
    )
    manager.record_usage(
        provider="test",
        key_id="key1",
        model_name="model-b",
        quota_date="2026-05-27",
        prompt_tokens=1,
        completion_tokens=1,
    )

    assert manager.get_key_request_count("test", "key1", "2026-05-27") == 2


def test_usage_manager_sums_model_usage_across_dates(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    manager = UsageManager(db_path)

    manager.record_usage(
        provider="test",
        key_id="key1",
        model_name="model-a",
        quota_date="2026-05-26",
        prompt_tokens=90,
        completion_tokens=60,
        quota_refresh_mode="delayed_calendar_day",
    )
    manager.record_usage(
        provider="test",
        key_id="key1",
        model_name="model-a",
        quota_date="2026-05-27",
        prompt_tokens=12,
        completion_tokens=8,
        quota_refresh_mode="delayed_calendar_day",
    )

    usage = manager.get_usage_for_dates(
        "test",
        "key1",
        "model-a",
        ("2026-05-26", "2026-05-27"),
        "delayed_calendar_day",
    )

    assert usage.prompt_tokens == 102
    assert usage.completion_tokens == 68
    assert usage.total_tokens == 170
    assert usage.request_count == 2


def test_usage_manager_bootstraps_calendar_usage_once_from_request_logs(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    manager = UsageManager(db_path)
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO request_logs (
                request_id,
                provider_name,
                key_id,
                model_name,
                level,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                status,
                error_message,
                latency_ms,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "request-1",
                "test",
                "key1",
                "model-a",
                1,
                12,
                8,
                20,
                "ok",
                None,
                10,
                "2026-05-26 17:00:00",
            ),
        )

    routes = {("test", "key1", "model-a")}
    manager.bootstrap_calendar_usage("Asia/Shanghai", routes)
    manager.bootstrap_calendar_usage("Asia/Shanghai", routes)

    usage = manager.get_usage(
        "test",
        "key1",
        "model-a",
        "2026-05-27",
        "delayed_calendar_day",
    )
    assert usage.total_tokens == 20
    assert usage.request_count == 1


def test_usage_manager_bootstrap_keeps_token_bearing_stream_errors(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    manager = UsageManager(db_path)
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO request_logs (
                request_id,
                provider_name,
                key_id,
                model_name,
                level,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                status,
                error_message,
                latency_ms,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "request-stream-error",
                "test",
                "key1",
                "model-a",
                1,
                4,
                1,
                5,
                "error",
                "stream interrupted",
                10,
                "2026-05-26 17:00:00",
            ),
        )

    manager.bootstrap_calendar_usage(
        "Asia/Shanghai", {("test", "key1", "model-a")}
    )

    usage = manager.get_usage(
        "test",
        "key1",
        "model-a",
        "2026-05-27",
        "delayed_calendar_day",
    )
    assert usage.total_tokens == 5
    assert usage.request_count == 1


def test_usage_manager_bootstrap_uses_request_start_time(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    manager = UsageManager(db_path)
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO request_logs (
                request_id,
                provider_name,
                key_id,
                model_name,
                level,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                status,
                error_message,
                latency_ms,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "request-cross-midnight",
                "test",
                "key1",
                "model-a",
                1,
                12,
                8,
                20,
                "ok",
                None,
                2000,
                "2026-05-26 16:00:01",
            ),
        )

    manager.bootstrap_calendar_usage(
        "Asia/Shanghai", {("test", "key1", "model-a")}
    )

    previous_day = manager.get_usage(
        "test",
        "key1",
        "model-a",
        "2026-05-26",
        "delayed_calendar_day",
    )
    next_day = manager.get_usage(
        "test",
        "key1",
        "model-a",
        "2026-05-27",
        "delayed_calendar_day",
    )
    assert previous_day.total_tokens == 20
    assert next_day.total_tokens == 0
