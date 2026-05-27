from fastapi.testclient import TestClient

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
