from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from token_router.app.database import connect


@dataclass(frozen=True)
class UsageRecord:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0


class UsageManager:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def get_usage(
        self, provider: str, key_id: str, model_name: str, quota_date: str
    ) -> UsageRecord:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT prompt_tokens, completion_tokens, total_tokens, request_count
                FROM model_usage_daily
                WHERE provider_name = ?
                  AND key_id = ?
                  AND model_name = ?
                  AND quota_date = ?
                """,
                (provider, key_id, model_name, quota_date),
            ).fetchone()

        if row is None:
            return UsageRecord()
        return UsageRecord(
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
            total_tokens=row["total_tokens"],
            request_count=row["request_count"],
        )

    def get_key_request_count(
        self, provider: str, key_id: str, quota_date: str
    ) -> int:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(request_count), 0) AS request_count
                FROM model_usage_daily
                WHERE provider_name = ?
                  AND key_id = ?
                  AND quota_date = ?
                """,
                (provider, key_id, quota_date),
            ).fetchone()
        return int(row["request_count"])

    def record_usage(
        self,
        provider: str,
        key_id: str,
        model_name: str,
        quota_date: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        total_tokens = prompt_tokens + completion_tokens
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO model_usage_daily (
                    provider_name,
                    key_id,
                    model_name,
                    quota_date,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    request_count,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(provider_name, key_id, model_name, quota_date)
                DO UPDATE SET
                    prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens,
                    total_tokens = total_tokens + excluded.total_tokens,
                    request_count = request_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    provider,
                    key_id,
                    model_name,
                    quota_date,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                ),
            )

    def log_request(
        self,
        request_id: str,
        provider: str | None,
        key_id: str | None,
        model_name: str | None,
        level: int | None,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        status: str,
        error_message: str | None,
        latency_ms: int | None,
    ) -> None:
        with connect(self.db_path) as connection:
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
                    latency_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    provider,
                    key_id,
                    model_name,
                    level,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    status,
                    error_message,
                    latency_ms,
                ),
            )
