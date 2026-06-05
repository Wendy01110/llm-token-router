from fastapi.testclient import TestClient

from token_router.app.config import ModelInstanceConfig
from token_router.app.main import create_app


class FakeProvider:
    async def chat_completion(self, provider_config, api_key, payload):
        assert provider_config.base_url == "https://example.test/v1"
        assert api_key.id == "k1"
        assert payload["model"] == "model-a"
        assert "router" not in payload
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": "model-a",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
            },
        }


class FakeProviderWithoutUsage:
    async def chat_completion(self, provider_config, api_key, payload):
        return {
            "id": "chatcmpl-no-usage",
            "object": "chat.completion",
            "model": "model-a",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        }


class FakeStreamingProvider:
    async def chat_completion_stream(self, provider_config, api_key, payload):
        assert payload["stream"] is True
        assert payload["model"] == "model-a"
        assert "router" not in payload
        yield b'data: {"choices":[{"delta":{"content":"O"}}],"usage":null}\n\n'
        yield b'data: {"choices":[{"delta":{"content":"K"}}],"usage":null}\n\n'
        yield (
            b'data: {"choices":[],"usage":'
            b'{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
        )
        yield b"data: [DONE]\n\n"


class FakeStreamingProviderWithoutUsage:
    async def chat_completion_stream(self, provider_config, api_key, payload):
        yield b'data: {"choices":[{"delta":{"content":"OK"}}],"usage":null}\n\n'
        yield b"data: [DONE]\n\n"


class FakeStreamingProviderWithUsageOption:
    async def chat_completion_stream(self, provider_config, api_key, payload):
        assert payload["stream_options"] == {"include_usage": True}
        yield (
            b'data: {"choices":[],"usage":'
            b'{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
        )
        yield b"data: [DONE]\n\n"


class RecordingStreamingProvider:
    def __init__(self):
        self.payloads = []

    async def chat_completion_stream(self, provider_config, api_key, payload):
        self.payloads.append(payload)
        yield (
            b'data: {"choices":[],"usage":'
            b'{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
        )
        yield b"data: [DONE]\n\n"


class RecordingProvider:
    def __init__(self):
        self.payloads = []

    async def chat_completion(self, provider_config, api_key, payload):
        self.payloads.append(payload)
        return {
            "id": "chatcmpl-recorded",
            "object": "chat.completion",
            "model": payload["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
            },
        }


def test_chat_endpoint_routes_and_records_usage(
    app_config, usage_manager, fixed_now
):
    app = create_app(
        app_config,
        usage_manager,
        provider=FakeProvider(),
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "router": {"level": 1},
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == "model-a"
    usage = usage_manager.get_usage("test", "k1", "model-a", "2026-05-27")
    assert usage.total_tokens == 5


def test_chat_endpoint_counts_successful_request_without_usage(
    app_config, usage_manager, fixed_now
):
    app = create_app(
        app_config,
        usage_manager,
        provider=FakeProviderWithoutUsage(),
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "router": {"level": 1},
        },
    )

    assert response.status_code == 200
    usage = usage_manager.get_usage("test", "k1", "model-a", "2026-05-27")
    assert usage.total_tokens == 0
    assert usage.request_count == 1


def test_chat_endpoint_streams_sse_and_records_usage(
    app_config, usage_manager, fixed_now
):
    app = create_app(
        app_config,
        usage_manager,
        provider=FakeStreamingProvider(),
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "router": {"level": 1, "debug": True},
        },
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["X-Router-Model"] == "model-a"
    assert body.endswith(b"data: [DONE]\n\n")
    usage = usage_manager.get_usage("test", "k1", "model-a", "2026-05-27")
    assert usage.prompt_tokens == 3
    assert usage.completion_tokens == 2
    assert usage.total_tokens == 5
    assert usage.request_count == 1


def test_chat_endpoint_stream_counts_request_when_usage_missing(
    app_config, usage_manager, fixed_now
):
    app = create_app(
        app_config,
        usage_manager,
        provider=FakeStreamingProviderWithoutUsage(),
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "router": {"level": 1},
        },
    )

    assert response.status_code == 200
    usage = usage_manager.get_usage("test", "k1", "model-a", "2026-05-27")
    assert usage.total_tokens == 0
    assert usage.request_count == 1


def test_chat_endpoint_stream_applies_provider_usage_mode(
    app_config, usage_manager, fixed_now
):
    app_config.providers["test"].stream_usage_mode = "openai_include_usage"
    app = create_app(
        app_config,
        usage_manager,
        provider=FakeStreamingProviderWithUsageOption(),
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "router": {"level": 1},
        },
    )

    assert response.status_code == 200
    usage = usage_manager.get_usage("test", "k1", "model-a", "2026-05-27")
    assert usage.total_tokens == 5


def test_chat_endpoint_stream_false_uses_json_path(
    app_config, usage_manager, fixed_now
):
    app = create_app(
        app_config,
        usage_manager,
        provider=FakeProvider(),
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
            "router": {"level": 1},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["model"] == "model-a"


def test_chat_endpoint_translates_router_thinking_for_ark_auto_model(
    app_config, usage_manager, fixed_now
):
    app_config.providers["volcengine_ark"] = app_config.providers.pop("test")
    app_config.model_instances[0] = ModelInstanceConfig(
        name="doubao-seed-2-0-pro-260215",
        provider="volcengine_ark",
        endpoint="api",
        level=1,
        keys=[{"key_id": "k1", "daily_quota": 100}],
    )
    provider = RecordingProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "disabled"},
            "router": {
                "level": 1,
                "thinking": True,
                "thinking_effort": "high",
            },
        },
    )

    assert response.status_code == 200
    assert provider.payloads[0]["thinking"] == {"type": "enabled"}
    assert provider.payloads[0]["reasoning_effort"] == "high"
    assert "router" not in provider.payloads[0]


def test_chat_endpoint_translates_router_thinking_for_openrouter_auto_model(
    app_config, usage_manager, fixed_now
):
    app_config.providers["openrouter"] = app_config.providers.pop("test")
    app_config.model_instances[0] = ModelInstanceConfig(
        name="openrouter/free",
        provider="openrouter",
        endpoint="api",
        level=1,
        keys=[{"key_id": "k1", "daily_quota": 100}],
    )
    provider = RecordingProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning": {"effort": "none"},
            "router": {
                "level": 1,
                "thinking": True,
                "thinking_effort": "medium",
            },
        },
    )

    assert response.status_code == 200
    assert provider.payloads[0]["reasoning"] == {"effort": "medium"}
    assert "router" not in provider.payloads[0]


def test_chat_endpoint_translates_router_thinking_false_for_openrouter_auto_model(
    app_config, usage_manager, fixed_now
):
    app_config.providers["openrouter"] = app_config.providers.pop("test")
    app_config.model_instances[0] = ModelInstanceConfig(
        name="openrouter/free",
        provider="openrouter",
        endpoint="api",
        level=1,
        keys=[{"key_id": "k1", "daily_quota": 100}],
    )
    provider = RecordingProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "router": {
                "level": 1,
                "thinking": False,
            },
        },
    )

    assert response.status_code == 200
    assert provider.payloads[0]["reasoning"] == {"effort": "none"}


def test_chat_endpoint_adapts_standard_openai_params_for_auto_openrouter(
    app_config, usage_manager, fixed_now
):
    app_config.providers["openrouter"] = app_config.providers.pop("test")
    app_config.model_instances[0] = ModelInstanceConfig(
        name="openrouter/free",
        provider="openrouter",
        endpoint="api",
        level=1,
        keys=[{"key_id": "k1", "daily_quota": 100}],
    )
    provider = RecordingProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "store": False,
            "max_tokens": 128,
            "reasoning_effort": "medium",
            "stream_options": {"include_usage": True},
            "router": {"level": 1, "provider": "auto"},
        },
    )

    assert response.status_code == 200
    payload = provider.payloads[0]
    assert payload["store"] is False
    assert payload["max_completion_tokens"] == 128
    assert payload["reasoning"] == {"effort": "medium"}
    assert "max_tokens" not in payload
    assert "reasoning_effort" not in payload
    assert "stream_options" not in payload


def test_chat_endpoint_preserves_standard_params_when_provider_is_explicit(
    app_config, usage_manager, fixed_now
):
    app_config.providers["openrouter"] = app_config.providers.pop("test")
    app_config.model_instances[0] = ModelInstanceConfig(
        name="openrouter/free",
        provider="openrouter",
        endpoint="api",
        level=1,
        keys=[{"key_id": "k1", "daily_quota": 100}],
    )
    provider = RecordingProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openrouter/free",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 128,
            "reasoning_effort": "medium",
            "stream_options": {"include_usage": True},
            "router": {
                "provider": "openrouter",
                "level": 1,
                "fallback": False,
                "strict_model": True,
            },
        },
    )

    assert response.status_code == 200
    payload = provider.payloads[0]
    assert payload["max_tokens"] == 128
    assert payload["reasoning_effort"] == "medium"
    assert payload["stream_options"] == {"include_usage": True}
    assert "max_completion_tokens" not in payload
    assert "reasoning" not in payload


def test_chat_endpoint_adapts_reasoning_effort_for_mimo_auto_model(
    app_config, usage_manager, fixed_now
):
    app_config.providers["xiaomi_mimo"] = app_config.providers.pop("test")
    app_config.model_instances[0] = ModelInstanceConfig(
        name="mimo-v2.5-pro",
        provider="xiaomi_mimo",
        endpoint="api",
        level=1,
        keys=[{"key_id": "k1", "daily_quota": 100}],
    )
    provider = RecordingProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning_effort": "medium",
            "router": {"level": 1},
        },
    )

    assert response.status_code == 200
    payload = provider.payloads[0]
    assert payload["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in payload


def test_chat_endpoint_removes_stream_options_for_auto_no_option_stream_provider(
    app_config, usage_manager, fixed_now
):
    app_config.providers["openrouter"] = app_config.providers.pop("test")
    app_config.providers["openrouter"].stream_usage_mode = "no_option_usage_chunk"
    app_config.model_instances[0] = ModelInstanceConfig(
        name="openrouter/free",
        provider="openrouter",
        endpoint="api",
        level=1,
        keys=[{"key_id": "k1", "daily_quota": 100}],
    )
    provider = RecordingStreamingProvider()
    app = create_app(
        app_config,
        usage_manager,
        provider=provider,
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "stream_options": {"include_usage": True},
            "router": {"level": 1, "provider": "auto"},
        },
    )

    assert response.status_code == 200
    assert "stream_options" not in provider.payloads[0]
