from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import TypeAlias

import httpx

from token_router.app.schemas.router import SelectedRoute


RouteKey: TypeAlias = tuple[str, str, str, str]
ConcurrencyKey: TypeAlias = tuple[str, str, str, str]


def route_key(selected: SelectedRoute) -> RouteKey:
    return (
        selected.provider,
        selected.endpoint,
        selected.key_id,
        selected.model_name,
    )


def concurrency_key(selected: SelectedRoute) -> ConcurrencyKey:
    return (
        selected.provider,
        selected.endpoint,
        selected.key_id,
        selected.model_name,
    )


class RuntimeRouteState:
    def __init__(self, clock: Callable[[], float] = monotonic):
        self._clock = clock
        self._cooldowns: dict[RouteKey, float] = {}
        self._in_flight: dict[ConcurrencyKey, int] = {}
        self._lock = Lock()

    def active_route_keys(self) -> set[RouteKey]:
        with self._lock:
            now = self._clock()
            expired = [
                key
                for key, cooldown_until in self._cooldowns.items()
                if cooldown_until <= now
            ]
            for key in expired:
                self._cooldowns.pop(key, None)
            return set(self._cooldowns)

    def mark_cooldown(self, selected: SelectedRoute, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._lock:
            self._cooldowns[route_key(selected)] = self._clock() + seconds

    def try_acquire_concurrency(self, selected: SelectedRoute) -> bool:
        key = concurrency_key(selected)
        with self._lock:
            count = self._in_flight.get(key, 0)
            if count >= selected.max_concurrency:
                return False
            self._in_flight[key] = count + 1
            return True

    def release_concurrency(self, selected: SelectedRoute) -> None:
        key = concurrency_key(selected)
        with self._lock:
            count = self._in_flight.get(key, 0)
            if count <= 1:
                self._in_flight.pop(key, None)
            else:
                self._in_flight[key] = count - 1

    def concurrency_count(self, selected: SelectedRoute) -> int:
        with self._lock:
            return self._in_flight.get(concurrency_key(selected), 0)


def is_retryable_runtime_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code in {400, 401, 403, 429} or 500 <= status_code <= 599
    return isinstance(exc, httpx.RequestError)
