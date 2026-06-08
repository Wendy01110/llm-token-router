from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import TypeAlias

import httpx

from token_router.app.schemas.router import SelectedRoute


RouteKey: TypeAlias = tuple[str, str, str, str]


def route_key(selected: SelectedRoute) -> RouteKey:
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

    def active_route_keys(self) -> set[RouteKey]:
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
        self._cooldowns[route_key(selected)] = self._clock() + seconds


def is_retryable_runtime_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or 500 <= status_code <= 599
    return isinstance(exc, httpx.RequestError)
