import asyncio

import httpx

from token_router.app.config import ApiKeyConfig, ProviderConfig
from token_router.app.providers.openai_compatible import OpenAICompatibleProvider


def test_provider_uses_api_key_header_when_configured():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    provider = OpenAICompatibleProvider(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        type="openai_compatible",
        base_url="https://example.test/v1",
        auth_header="api_key",
        keys=[ApiKeyConfig(id="mimo", value="tp-test")],
    )

    result = asyncio.run(
        provider.chat_completion(
            config,
            config.keys[0],
            {"model": "mimo-v2.5-pro", "messages": []},
        )
    )

    assert result == {"ok": True}
    assert captured["headers"]["api-key"] == "tp-test"
    assert "authorization" not in captured["headers"]


def test_provider_uses_authorization_bearer_by_default():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    provider = OpenAICompatibleProvider(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        type="openai_compatible",
        base_url="https://example.test/v1",
        keys=[ApiKeyConfig(id="ark", value="ark-test")],
    )

    asyncio.run(
        provider.chat_completion(
            config,
            config.keys[0],
            {"model": "doubao-seed-2-0-lite-260215", "messages": []},
        )
    )

    assert captured["headers"]["authorization"] == "Bearer ark-test"


async def _collect_stream(stream):
    return [chunk async for chunk in stream]


def test_provider_streams_raw_sse_bytes():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.read()
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"OK"}}],"usage":null}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    provider = OpenAICompatibleProvider(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        type="openai_compatible",
        base_url="https://example.test/v1",
        keys=[ApiKeyConfig(id="test", value="sk-test")],
    )

    chunks = asyncio.run(
        _collect_stream(
            provider.chat_completion_stream(
                config,
                config.keys[0],
                {"model": "model-a", "stream": True},
            )
        )
    )

    assert b"".join(chunks).endswith(b"data: [DONE]\n\n")
    assert captured["headers"]["authorization"] == "Bearer sk-test"


def test_provider_posts_native_responses_endpoint():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "total_tokens": 5,
                },
            },
        )

    provider = OpenAICompatibleProvider(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        type="openai_compatible",
        base_url="https://example.test/v1",
        keys=[ApiKeyConfig(id="test", value="sk-test")],
    )

    result = asyncio.run(
        provider.responses(
            config,
            config.keys[0],
            {"model": "model-a", "input": "hello"},
        )
    )

    assert captured["url"] == "https://example.test/v1/responses"
    assert result["id"] == "resp_test"


def test_provider_streams_native_responses_sse_bytes():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'event: response.completed\ndata: {"type":"response.completed"}\n\n',
        )

    provider = OpenAICompatibleProvider(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        type="openai_compatible",
        base_url="https://example.test/v1",
        keys=[ApiKeyConfig(id="test", value="sk-test")],
    )

    chunks = asyncio.run(
        _collect_stream(
            provider.responses_stream(
                config,
                config.keys[0],
                {"model": "model-a", "input": "hello", "stream": True},
            )
        )
    )

    assert captured["url"] == "https://example.test/v1/responses"
    assert b"".join(chunks).startswith(b"event: response.completed")
