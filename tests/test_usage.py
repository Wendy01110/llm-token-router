from token_router.app.database import init_db
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
