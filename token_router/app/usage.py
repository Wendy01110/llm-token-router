from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from token_router.app.database import connect
from token_router.app.router.quota import quota_storage_date


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
        self,
        provider: str,
        key_id: str,
        model_name: str,
        quota_date: str,
        quota_refresh_mode: str = "shifted_day",
    ) -> UsageRecord:
        return self.get_usage_for_dates(
            provider,
            key_id,
            model_name,
            (quota_date,),
            quota_refresh_mode,
        )

    def get_usage_for_dates(
        self,
        provider: str,
        key_id: str,
        model_name: str,
        quota_dates: tuple[str, ...],
        quota_refresh_mode: str = "shifted_day",
    ) -> UsageRecord:
        if not quota_dates:
            return UsageRecord()
        storage_dates = tuple(
            quota_storage_date(quota_date, quota_refresh_mode)
            for quota_date in quota_dates
        )
        placeholders = ", ".join("?" for _ in storage_dates)
        with connect(self.db_path) as connection:
            row = connection.execute(
                f"""
                SELECT
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(request_count), 0) AS request_count
                FROM model_usage_daily
                WHERE provider_name = ?
                  AND key_id = ?
                  AND model_name = ?
                  AND quota_date IN ({placeholders})
                """,
                (provider, key_id, model_name, *storage_dates),
            ).fetchone()

        return UsageRecord(
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
            total_tokens=row["total_tokens"],
            request_count=row["request_count"],
        )

    def get_key_request_count(
        self,
        provider: str,
        key_id: str,
        quota_date: str,
        quota_refresh_mode: str = "shifted_day",
    ) -> int:
        return self.get_key_request_count_for_dates(
            provider,
            key_id,
            (quota_date,),
            quota_refresh_mode,
        )

    def get_key_request_count_for_dates(
        self,
        provider: str,
        key_id: str,
        quota_dates: tuple[str, ...],
        quota_refresh_mode: str = "shifted_day",
    ) -> int:
        if not quota_dates:
            return 0
        storage_dates = tuple(
            quota_storage_date(quota_date, quota_refresh_mode)
            for quota_date in quota_dates
        )
        placeholders = ", ".join("?" for _ in storage_dates)
        with connect(self.db_path) as connection:
            row = connection.execute(
                f"""
                SELECT COALESCE(SUM(request_count), 0) AS request_count
                FROM model_usage_daily
                WHERE provider_name = ?
                  AND key_id = ?
                  AND quota_date IN ({placeholders})
                """,
                (provider, key_id, *storage_dates),
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
        quota_refresh_mode: str = "shifted_day",
    ) -> None:
        total_tokens = prompt_tokens + completion_tokens
        storage_date = quota_storage_date(quota_date, quota_refresh_mode)
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
                    storage_date,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                ),
            )

    def bootstrap_calendar_usage(
        self,
        timezone_name: str,
        routes: set[tuple[str, str, str]],
    ) -> None:
        if not routes:
            return

        with connect(self.db_path) as connection:
            migrated = {
                (row["provider_name"], row["key_id"], row["model_name"])
                for row in connection.execute(
                    """
                    SELECT provider_name, key_id, model_name
                    FROM calendar_usage_migrations
                    WHERE timezone_name = ?
                    """,
                    (timezone_name,),
                )
            }
            pending = routes - migrated
            if not pending:
                return

            aggregate: dict[tuple[str, str, str, str], list[int]] = {}
            local_timezone = ZoneInfo(timezone_name)
            route_clauses = " OR ".join(
                "(provider_name = ? AND key_id = ? AND model_name = ?)"
                for _ in pending
            )
            route_params = tuple(value for route in sorted(pending) for value in route)
            rows = connection.execute(
                f"""
                SELECT provider_name, key_id, model_name, prompt_tokens,
                       completion_tokens, total_tokens, status, latency_ms,
                       created_at
                FROM request_logs
                WHERE {route_clauses}
                """,
                route_params,
            )
            for row in rows:
                route = (row["provider_name"], row["key_id"], row["model_name"])
                if route not in pending:
                    continue
                if row["status"] != "ok" and int(row["total_tokens"]) <= 0:
                    continue
                accounted_at = datetime.fromisoformat(row["created_at"])
                if accounted_at.tzinfo is None:
                    accounted_at = accounted_at.replace(tzinfo=timezone.utc)
                else:
                    accounted_at = accounted_at.astimezone(timezone.utc)
                if row["latency_ms"] is not None:
                    accounted_at -= timedelta(milliseconds=max(0, row["latency_ms"]))
                usage_date = (
                    accounted_at.astimezone(local_timezone).date().isoformat()
                )
                aggregate_key = (*route, usage_date)
                totals = aggregate.setdefault(aggregate_key, [0, 0, 0, 0])
                totals[0] += int(row["prompt_tokens"])
                totals[1] += int(row["completion_tokens"])
                totals[2] += int(row["total_tokens"])
                totals[3] += 1

            for (provider, key_id, model_name, usage_date), totals in aggregate.items():
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
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(provider_name, key_id, model_name, quota_date)
                    DO NOTHING
                    """,
                    (
                        provider,
                        key_id,
                        model_name,
                        quota_storage_date(usage_date, "delayed_calendar_day"),
                        *totals,
                    ),
                )

            connection.executemany(
                """
                INSERT INTO calendar_usage_migrations (
                    provider_name,
                    key_id,
                    model_name,
                    timezone_name
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(provider_name, key_id, model_name, timezone_name)
                DO NOTHING
                """,
                [(*route, timezone_name) for route in sorted(pending)],
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
