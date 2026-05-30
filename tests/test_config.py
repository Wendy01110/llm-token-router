from token_router.app.config import load_config, resolve_env_refs


def test_load_config_resolves_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ROUTER_KEY", "sk-test")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
refresh:
  timezone: Asia/Shanghai
  daily_reset_hour: 11
routing:
  default_level: 1
  fallback_enabled: true
  max_fallback_level: 5
providers:
  test:
    type: openai_compatible
    base_url: https://example.test/v1
    keys:
      - id: test_1
        value: ${TEST_ROUTER_KEY}
model_instances:
  - name: test-model
    provider: test
    key_id: test_1
    level: 1
    daily_quota: 1000
    priority: 10
    groups: [general]
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.providers["test"].keys[0].value == "sk-test"
    assert config.model_instances[0].provider == "test"


def test_load_config_reads_env_file_next_to_config(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_ROUTER_KEY", raising=False)
    config_file = tmp_path / "config.yaml"
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_ROUTER_KEY=sk-from-dotenv\n", encoding="utf-8")
    config_file.write_text(
        """
providers:
  test:
    type: openai_compatible
    base_url: https://example.test/v1
    keys:
      - id: test_1
        value: ${TEST_ROUTER_KEY}
model_instances:
  - name: test-model
    provider: test
    key_id: test_1
    level: 1
    daily_quota: 1000
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.providers["test"].keys[0].value == "sk-from-dotenv"


def test_env_resolution_ignores_commented_placeholders(monkeypatch):
    monkeypatch.setenv("PRESENT_KEY", "sk-present")

    resolved = resolve_env_refs(
        "# value: ${MISSING_KEY}\nvalue: ${PRESENT_KEY}\n"
    )

    assert "# value: ${MISSING_KEY}" in resolved
    assert "value: sk-present" in resolved


def test_config_example_uses_current_local_providers(monkeypatch):
    monkeypatch.setenv(
        "MIMO_TOKEN_PLAN_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"
    )
    monkeypatch.setenv("MIMO_TOKEN_PLAN_KEY", "tp-test")
    monkeypatch.setenv("MIMO_TOKEN_PLAN_MODEL", "mimo-v2.5-pro")
    monkeypatch.setenv(
        "VOLCENGINE_ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
    )
    monkeypatch.setenv("VOLCENGINE_ARK_API_KEY", "ark-test")
    monkeypatch.setenv("VOLCENGINE_ARK_MODEL", "doubao-seed-2-0-lite-260215")

    config = load_config("config.example.yaml")

    assert set(config.providers) == {"xiaomi_mimo", "volcengine_ark"}
    assert set(config.providers["xiaomi_mimo"].endpoints) == {"token_plan"}
    assert config.model_instances[0].endpoint == "token_plan"
    assert config.providers["xiaomi_mimo"].get_endpoint("token_plan").auth_header == "api_key"
    assert config.providers["volcengine_ark"].get_endpoint("api").auth_header == "authorization_bearer"


def test_provider_can_separate_api_and_token_plan_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMO_TOKEN_PLAN_KEY", "tp-test")
    monkeypatch.setenv("MIMO_API_KEY", "sk-test")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
providers:
  xiaomi_mimo:
    type: openai_compatible
    endpoints:
      token_plan:
        base_url: https://token-plan-cn.xiaomimimo.com/v1
        auth_header: api_key
        keys:
          - id: token_plan_key
            value: ${MIMO_TOKEN_PLAN_KEY}
      api:
        base_url: https://api.xiaomimimo.com/v1
        auth_header: api_key
        keys:
          - id: api_key
            value: ${MIMO_API_KEY}
model_instances:
  - name: mimo-v2.5-pro
    provider: xiaomi_mimo
    endpoint: token_plan
    key_id: token_plan_key
    level: 1
    daily_quota: 1000
  - name: mimo-v2.5
    provider: xiaomi_mimo
    endpoint: api
    key_id: api_key
    level: 2
    daily_quota: 1000
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.providers["xiaomi_mimo"].get_endpoint("token_plan").base_url == "https://token-plan-cn.xiaomimimo.com/v1"
    assert config.providers["xiaomi_mimo"].get_endpoint("api").base_url == "https://api.xiaomimimo.com/v1"
    assert config.model_instances[0].endpoint == "token_plan"
    assert config.model_instances[1].endpoint == "api"
