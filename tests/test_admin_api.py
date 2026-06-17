from fastapi.testclient import TestClient

from token_router.app.config import ModelInstanceConfig
from token_router.app.main import create_app


def test_health_endpoint_returns_ok(app_config, usage_manager, fixed_now):
    app = create_app(app_config, usage_manager, now_fn=lambda: fixed_now)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_route_preview_returns_selected_route(app_config, usage_manager, fixed_now):
    app = create_app(app_config, usage_manager, now_fn=lambda: fixed_now)
    client = TestClient(app)

    response = client.post(
        "/admin/route/preview", json={"model": "auto", "router": {"level": 1}}
    )

    assert response.status_code == 200
    assert response.json()["selected"]["level"] == 1
    assert response.json()["selected"]["daily_request_quota"] is None
    assert response.json()["selected"]["used_requests"] == 0


def test_route_preview_skips_unsupported_response_format(
    app_config, usage_manager, fixed_now
):
    app_config.model_instances[0] = ModelInstanceConfig(
        name="json-unsupported-model",
        provider="test",
        endpoint="api",
        level=1,
        priority=10,
        keys=[{"key_id": "k1", "daily_quota": 100}],
        unsupported_response_format_types=["json_object"],
    )
    app_config.model_instances.append(
        ModelInstanceConfig(
            name="json-supported-model",
            provider="test",
            endpoint="api",
            level=2,
            priority=20,
            keys=[{"key_id": "k1", "daily_quota": 100}],
        )
    )
    app = create_app(app_config, usage_manager, now_fn=lambda: fixed_now)
    client = TestClient(app)

    response = client.post(
        "/admin/route/preview",
        json={
            "model": "auto",
            "response_format": {"type": "json_object"},
            "router": {"level": 1},
        },
    )

    assert response.status_code == 200
    assert response.json()["selected"]["model_name"] == "json-supported-model"


def test_usage_page_shows_key_summary_and_model_usage(
    app_config, usage_manager, fixed_now
):
    usage_manager.record_usage(
        provider="test",
        key_id="k1",
        model_name="model-a",
        quota_date="2026-05-27",
        prompt_tokens=12,
        completion_tokens=8,
    )
    app = create_app(app_config, usage_manager, now_fn=lambda: fixed_now)
    client = TestClient(app)

    response = client.get("/admin/usage")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "API Key Usage" in response.text
    assert "test" in response.text
    assert "k1" in response.text
    assert "model-a" in response.text
    assert "20" in response.text
    assert "20.0%" in response.text
    assert "Priority" in response.text
    assert "Requests / Quota" in response.text
