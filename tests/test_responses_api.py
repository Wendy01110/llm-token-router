import json

import httpx
from fastapi.testclient import TestClient

from token_router.app.config import ApiKeyConfig, EndpointConfig, ModelInstanceConfig
from token_router.app.database import connect
from token_router.app.main import create_app
from token_router.app.router.selector import RouteSelector


def _status_error(status_code=429, text="rate limited"):
    request = httpx.Request("POST", "https://responses.test/v1/responses")
    response = httpx.Response(status_code, text=text, request=request)
    return httpx.HTTPStatusError(text, request=request, response=response)


class RecordingNativeResponsesProvider:
    def __init__(self):
        self.payloads = []
        self.stream_payloads = []

    async def responses(self, provider_config, api_key, payload):
        assert provider_config.base_url == "https://responses.test/v1"
        assert api_key.id == "native-key"
        self.payloads.append(payload)
        return {
            "id": "resp_test",
            "object": "response",
            "created_at": 1780000000,
            "status": "completed",
            "model": payload["model"],
            "output": [
                {
                    "id": "msg_test",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "ok",
                            "annotations": [],
                        }
                    ],
                }
            ],
            "usage": {
                "input_tokens": 7,
                "output_tokens": 5,
                "total_tokens": 12,
            },
        }

    async def responses_stream(self, provider_config, api_key, payload):
        self.stream_payloads.append(payload)
        yield b'event: response.created\ndata: {"type":"response.created"}\n\n'
        yield (
            b'event: response.output_text.delta\n'
            b'data: {"type":"response.output_text.delta","delta":"O"}\n\n'
        )
        yield (
            b'event: response.completed\n'
            b'data: {"type":"response.completed","response":'
            b'{"usage":{"input_tokens":7,"output_tokens":5,"total_tokens":12}}}\n\n'
        )


class ChatOnlyProvider:
    async def chat_completion(self, provider_config, api_key, payload):
        raise AssertionError("responses endpoint must not use chat_completion")

    async def chat_completion_stream(self, provider_config, api_key, payload):
        raise AssertionError("responses endpoint must not use chat_completion_stream")


class RuntimeFallbackNativeResponsesProvider:
    def __init__(self, first_status_code=429):
        self.first_status_code = first_status_code
        self.models = []
        self.stream_models = []

    async def responses(self, provider_config, api_key, payload):
        self.models.append(payload["model"])
        if payload["model"] == "native-a":
            raise _status_error(self.first_status_code)
        return {
            "id": "resp_fallback",
            "object": "response",
            "created_at": 1780000000,
            "status": "completed",
            "model": payload["model"],
            "output": [],
            "usage": {
                "input_tokens": 6,
                "output_tokens": 4,
                "total_tokens": 10,
            },
        }

    async def responses_stream(self, provider_config, api_key, payload):
        self.stream_models.append(payload["model"])
        if payload["model"] == "native-a":
            raise _status_error(self.first_status_code)
        yield b'event: response.created\ndata: {"type":"response.created"}\n\n'
        yield (
            b'event: response.output_text.delta\n'
            b'data: {"type":"response.output_text.delta","delta":"O"}\n\n'
        )
        yield (
            b'event: response.completed\n'
            b'data: {"type":"response.completed","response":'
            b'{"usage":{"input_tokens":6,"output_tokens":4,"total_tokens":10}}}\n\n'
        )


class EmptyThenFallbackResponsesProvider(RuntimeFallbackNativeResponsesProvider):
    async def responses_stream(self, provider_config, api_key, payload):
        self.stream_models.append(payload["model"])
        if payload["model"] == "native-a":
            return
        yield (
            b'event: response.output_text.delta\n'
            b'data: {"type":"response.output_text.delta","delta":"OK"}\n\n'
        )
        yield (
            b'event: response.completed\n'
            b'data: {"type":"response.completed","response":'
            b'{"usage":{"input_tokens":6,"output_tokens":4,"total_tokens":10}}}\n\n'
        )


class MidStreamErrorResponsesProvider(RecordingNativeResponsesProvider):
    async def responses_stream(self, provider_config, api_key, payload):
        self.stream_payloads.append(payload)
        yield b'event: response.created\ndata: {"type":"response.created"}\n\n'
        raise _status_error(400, "unsupported responses payload")


def _request_logs(usage_manager):
    with connect(usage_manager.db_path) as connection:
        return connection.execute(
            """
                SELECT request_id, provider_name, key_id, model_name, prompt_tokens,
                   completion_tokens, total_tokens, status, error_message
            FROM request_logs
            ORDER BY id
            """
        ).fetchall()


def _enable_native_responses_endpoint(app_config):
    app_config.providers["native"] = app_config.providers.pop("test")
    app_config.providers["native"].endpoints["api"] = EndpointConfig(
        base_url="https://responses.test/v1",
        responses_api="native",
        keys=[ApiKeyConfig(id="native-key", value="sk-native")],
    )
    app_config.model_instances[0] = ModelInstanceConfig(
        name="native-model",
        provider="native",
        endpoint="api",
        level=1,
        keys=[{"key_id": "native-key", "daily_quota": 100}],
        groups=["general"],
    )


def _enable_two_native_responses_models(app_config):
    _enable_native_responses_endpoint(app_config)
    app_config.model_instances = [
        ModelInstanceConfig(
            name="native-a",
            provider="native",
            endpoint="api",
            level=1,
            keys=[{"key_id": "native-key", "daily_quota": 100}],
            groups=["general"],
        ),
        ModelInstanceConfig(
            name="native-b",
            provider="native",
            endpoint="api",
            level=2,
            keys=[{"key_id": "native-key", "daily_quota": 100}],
            groups=["general"],
        ),
    ]


def _enable_ordered_native_responses_models(app_config):
    _enable_two_native_responses_models(app_config)
    app_config.model_instances.append(
        ModelInstanceConfig(
            name="native-c",
            provider="native",
            endpoint="api",
            level=2,
            priority=50,
            keys=[{"key_id": "native-key", "daily_quota": 100}],
            groups=["general"],
        )
    )


def _enable_native_responses_custom_tool_fallback(app_config):
    app_config.providers["native_a"] = app_config.providers.pop("test")
    app_config.providers["native_a"].endpoints["api"] = EndpointConfig(
        base_url="https://responses.test/v1",
        responses_api="native",
        responses_unsupported_tool_types=["custom"],
        keys=[ApiKeyConfig(id="native-key", value="sk-native-a")],
    )
    app_config.providers["native_b"] = app_config.providers["native_a"].model_copy(
        deep=True
    )
    app_config.providers["native_b"].endpoints["api"] = EndpointConfig(
        base_url="https://responses.test/v1",
        responses_api="native",
        keys=[ApiKeyConfig(id="native-key", value="sk-native-b")],
    )
    app_config.model_instances = [
        ModelInstanceConfig(
            name="native-a",
            provider="native_a",
            endpoint="api",
            level=1,
            keys=[{"key_id": "native-key", "daily_quota": 100}],
            groups=["general"],
        ),
        ModelInstanceConfig(
            name="native-b",
            provider="native_b",
            endpoint="api",
            level=2,
            keys=[{"key_id": "native-key", "daily_quota": 100}],
            groups=["general"],
        ),
    ]


def test_responses_endpoint_proxies_native_response_and_records_usage(
    app_config, usage_manager, fixed_now
):
    _enable_native_responses_endpoint(app_config)
    provider = RecordingNativeResponsesProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        json={
            "model": "auto",
            "input": "hello",
            "previous_response_id": "resp_previous",
            "router": {"level": 1, "debug": True},
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Router-Provider"] == "native"
    assert response.json()["id"] == "resp_test"
    assert response.json()["usage"]["total_tokens"] == 12
    assert provider.payloads == [
        {
            "model": "native-model",
            "input": "hello",
            "previous_response_id": "resp_previous",
        }
    ]

    usage = usage_manager.get_usage("native", "native-key", "native-model", "2026-05-27")
    assert usage.prompt_tokens == 7
    assert usage.completion_tokens == 5
    assert usage.total_tokens == 12
    assert usage.request_count == 1
    logs = _request_logs(usage_manager)
    assert len(logs) == 1
    assert logs[0]["status"] == "ok"
    assert logs[0]["total_tokens"] == 12


def test_responses_endpoint_falls_back_on_retryable_runtime_error(
    app_config, usage_manager, fixed_now
):
    _enable_two_native_responses_models(app_config)
    provider = RuntimeFallbackNativeResponsesProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        json={"model": "auto", "input": "hello", "router": {"level": 1}},
    )

    assert response.status_code == 200
    assert response.json()["model"] == "native-b"
    assert provider.models == ["native-a", "native-b"]
    usage_a = usage_manager.get_usage(
        "native", "native-key", "native-a", "2026-05-27"
    )
    usage_b = usage_manager.get_usage(
        "native", "native-key", "native-b", "2026-05-27"
    )
    assert usage_a.request_count == 0
    assert usage_b.request_count == 1
    logs = _request_logs(usage_manager)
    assert [log["status"] for log in logs] == ["error", "ok"]
    assert logs[0]["model_name"] == "native-a"
    assert logs[0]["request_id"] == logs[1]["request_id"]


def test_responses_endpoint_falls_back_on_403_runtime_error(
    app_config, usage_manager, fixed_now
):
    _enable_two_native_responses_models(app_config)
    provider = RuntimeFallbackNativeResponsesProvider(first_status_code=403)
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        json={"model": "auto", "input": "hello", "router": {"level": 1}},
    )

    assert response.status_code == 200
    assert response.json()["model"] == "native-b"
    assert provider.models == ["native-a", "native-b"]


def test_responses_endpoint_uses_fallback_models_order_on_runtime_error(
    app_config, usage_manager, fixed_now
):
    _enable_ordered_native_responses_models(app_config)
    provider = RuntimeFallbackNativeResponsesProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        json={
            "model": "auto",
            "input": "hello",
            "router": {
                "level": 1,
                "fallback_models": ["native-c", "native-b"],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == "native-c"
    assert provider.models == ["native-a", "native-c"]


def test_responses_endpoint_skips_saturated_model(
    app_config, usage_manager, fixed_now
):
    _enable_two_native_responses_models(app_config)
    app_config.model_instances[0].max_concurrency = 1
    provider = RecordingNativeResponsesProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    selected = RouteSelector(app_config, usage_manager).select(
        model="auto",
        router={"level": 1},
        quota_date="2026-05-27",
        responses_api="native",
    )
    assert app.state.runtime_state.try_acquire_concurrency(selected) is True
    client = TestClient(app)

    try:
        response = client.post(
            "/v1/responses",
            json={"model": "auto", "input": "hello", "router": {"level": 1}},
        )
    finally:
        app.state.runtime_state.release_concurrency(selected)

    assert response.status_code == 200
    assert provider.payloads[0]["model"] == "native-b"


def test_responses_endpoint_skips_unsupported_lower_level_model(
    app_config, usage_manager, fixed_now
):
    app_config.providers["native"] = app_config.providers["test"].model_copy(deep=True)
    app_config.providers["native"].endpoints["api"] = EndpointConfig(
        base_url="https://responses.test/v1",
        responses_api="native",
        keys=[ApiKeyConfig(id="native-key", value="sk-native")],
    )
    app_config.model_instances = [
        ModelInstanceConfig(
            name="chat-only-model",
            provider="test",
            endpoint="api",
            level=1,
            keys=[{"key_id": "k1", "daily_quota": 100}],
            groups=["general"],
        ),
        ModelInstanceConfig(
            name="native-model",
            provider="native",
            endpoint="api",
            level=2,
            keys=[{"key_id": "native-key", "daily_quota": 100}],
            groups=["general"],
        ),
    ]
    provider = RecordingNativeResponsesProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        json={"model": "auto", "input": "hello", "router": {"level": 1}},
    )

    assert response.status_code == 200
    assert provider.payloads[0]["model"] == "native-model"


def test_responses_endpoint_returns_429_when_no_native_response_model(
    app_config, usage_manager, fixed_now
):
    app = create_app(
        app_config,
        usage_manager,
        provider=ChatOnlyProvider(),
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        json={"model": "auto", "input": "hello", "router": {"level": 1}},
    )

    assert response.status_code == 429
    assert response.json()["detail"] == "no available model instance"


def test_responses_endpoint_streams_native_events_and_records_usage(
    app_config, usage_manager, fixed_now
):
    _enable_native_responses_endpoint(app_config)
    provider = RecordingNativeResponsesProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "auto",
            "input": "hello",
            "stream": True,
            "router": {"level": 1},
        },
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert provider.stream_payloads == [
        {"model": "native-model", "input": "hello", "stream": True}
    ]
    assert b"event: response.output_text.delta" in body

    completed = [
        block
        for block in body.decode().split("\n\n")
        if block.startswith("event: response.completed")
    ][0]
    data = completed.split("data: ", 1)[1]
    assert json.loads(data)["response"]["usage"]["total_tokens"] == 12
    usage = usage_manager.get_usage("native", "native-key", "native-model", "2026-05-27")
    assert usage.prompt_tokens == 7
    assert usage.completion_tokens == 5
    assert usage.request_count == 1


def test_responses_endpoint_stream_falls_back_before_first_chunk(
    app_config, usage_manager, fixed_now
):
    _enable_two_native_responses_models(app_config)
    provider = RuntimeFallbackNativeResponsesProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "auto",
            "input": "hello",
            "stream": True,
            "router": {"level": 1},
        },
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert provider.stream_models == ["native-a", "native-b"]
    assert b"event: response.output_text.delta" in body
    usage_a = usage_manager.get_usage(
        "native", "native-key", "native-a", "2026-05-27"
    )
    usage_b = usage_manager.get_usage(
        "native", "native-key", "native-b", "2026-05-27"
    )
    assert usage_a.request_count == 0
    assert usage_b.request_count == 1
    logs = _request_logs(usage_manager)
    assert [log["status"] for log in logs] == ["error", "ok"]


def test_responses_endpoint_stream_falls_back_when_upstream_ends_before_first_chunk(
    app_config, usage_manager, fixed_now
):
    _enable_two_native_responses_models(app_config)
    provider = EmptyThenFallbackResponsesProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "auto",
            "input": "hello",
            "stream": True,
            "router": {"level": 1},
        },
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert provider.stream_models == ["native-a", "native-b"]
    assert b"event: response.output_text.delta" in body
    usage_a = usage_manager.get_usage(
        "native", "native-key", "native-a", "2026-05-27"
    )
    usage_b = usage_manager.get_usage(
        "native", "native-key", "native-b", "2026-05-27"
    )
    assert usage_a.request_count == 0
    assert usage_b.request_count == 1
    logs = _request_logs(usage_manager)
    assert [log["status"] for log in logs] == ["error", "ok"]
    assert "stream ended before first chunk" in logs[0]["error_message"]


def test_responses_endpoint_stream_skips_saturated_model_before_first_chunk(
    app_config, usage_manager, fixed_now
):
    _enable_two_native_responses_models(app_config)
    app_config.model_instances[0].max_concurrency = 1
    provider = RecordingNativeResponsesProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    selected = RouteSelector(app_config, usage_manager).select(
        model="auto",
        router={"level": 1},
        quota_date="2026-05-27",
        responses_api="native",
    )
    assert app.state.runtime_state.try_acquire_concurrency(selected) is True
    client = TestClient(app)

    try:
        with client.stream(
            "POST",
            "/v1/responses",
            json={
                "model": "auto",
                "input": "hello",
                "stream": True,
                "router": {"level": 1},
            },
        ) as response:
            body = b"".join(response.iter_bytes())
    finally:
        app.state.runtime_state.release_concurrency(selected)

    assert response.status_code == 200
    assert provider.stream_payloads[0]["model"] == "native-b"
    assert b"event: response.output_text.delta" in body


def test_responses_endpoint_skips_routes_with_unsupported_custom_tools(
    app_config, usage_manager, fixed_now
):
    _enable_native_responses_custom_tool_fallback(app_config)
    provider = RecordingNativeResponsesProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        json={
            "model": "auto",
            "input": "hello",
            "tools": [{"type": "custom", "name": "shell"}],
            "router": {"level": 1},
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == "native-b"
    assert provider.payloads == [
        {
            "model": "native-b",
            "input": "hello",
            "tools": [{"type": "custom", "name": "shell"}],
        }
    ]


def test_responses_endpoint_stream_reports_midstream_upstream_error(
    app_config, usage_manager, fixed_now
):
    _enable_native_responses_endpoint(app_config)
    provider = MidStreamErrorResponsesProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "auto",
            "input": "hello",
            "stream": True,
            "router": {"level": 1},
        },
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b"event: response.created" in body
    assert b"event: error" in body
    assert b"unsupported responses payload" in body
    logs = _request_logs(usage_manager)
    assert logs[-1]["status"] == "error"
    assert "unsupported responses payload" in logs[-1]["error_message"]
