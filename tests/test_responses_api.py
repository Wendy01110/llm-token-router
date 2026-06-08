import json

from fastapi.testclient import TestClient

from token_router.app.config import ApiKeyConfig, EndpointConfig, ModelInstanceConfig
from token_router.app.database import connect
from token_router.app.main import create_app


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


def _request_logs(usage_manager):
    with connect(usage_manager.db_path) as connection:
        return connection.execute(
            """
            SELECT provider_name, key_id, model_name, prompt_tokens,
                   completion_tokens, total_tokens, status
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
