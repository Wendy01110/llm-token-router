import httpx

from token_router.app.router.runtime import (
    RuntimeRouteState,
    is_retryable_runtime_error,
)
from token_router.app.schemas.router import SelectedRoute


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://upstream.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=response,
    )


def _selected_route(
    key_id="k1",
    model_name="model-a",
    max_concurrency=2,
) -> SelectedRoute:
    return SelectedRoute(
        provider="test",
        endpoint="api",
        key_id=key_id,
        model_name=model_name,
        level=1,
        daily_quota=100,
        daily_request_quota=None,
        used_tokens=0,
        used_requests=0,
        usage_ratio=0.0,
        stage=0,
        priority=10,
        max_concurrency=max_concurrency,
        enabled=True,
        available=True,
        groups=("general",),
    )


def test_runtime_concurrency_limit_is_shared_across_keys_for_same_model():
    state = RuntimeRouteState()
    key_one_route = _selected_route(key_id="k1", max_concurrency=2)
    key_two_route = _selected_route(key_id="k2", max_concurrency=2)

    assert state.try_acquire_concurrency(key_one_route) is True
    assert state.try_acquire_concurrency(key_two_route) is True
    assert state.try_acquire_concurrency(key_one_route) is False
    assert state.concurrency_count(key_one_route) == 2

    state.release_concurrency(key_two_route)

    assert state.try_acquire_concurrency(key_one_route) is True


def test_runtime_fallback_treats_selected_4xx_statuses_as_retryable():
    for status_code in (400, 401, 403):
        assert is_retryable_runtime_error(_status_error(status_code)) is True


def test_runtime_fallback_does_not_treat_other_4xx_statuses_as_retryable():
    assert is_retryable_runtime_error(_status_error(404)) is False
