from fastapi.testclient import TestClient

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
