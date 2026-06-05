import re
from pathlib import Path

from token_router.app.config import load_config, resolve_env_refs


ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


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
    level: 1
    priority: 10
    keys:
      - key_id: test_1
        daily_quota: 1000
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
    level: 1
    keys:
      - key_id: test_1
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


def test_env_example_covers_config_example_placeholders():
    config_text = Path("config.example.yaml").read_text(encoding="utf-8")
    env_text = Path(".env.example").read_text(encoding="utf-8")
    config_vars = {
        match.group(1)
        for line in config_text.splitlines()
        if not line.lstrip().startswith("#")
        for match in ENV_PATTERN.finditer(line)
    }
    env_vars = {
        line.split("=", 1)[0]
        for line in env_text.splitlines()
        if line and not line.lstrip().startswith("#") and "=" in line
    }

    assert config_vars <= env_vars


def test_config_example_uses_current_local_providers(monkeypatch):
    monkeypatch.setenv(
        "MIMO_TOKEN_PLAN_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"
    )
    monkeypatch.setenv("MIMO_TOKEN_PLAN_KEY", "tp-test")
    monkeypatch.setenv("MIMO_TOKEN_PLAN_MODEL", "mimo-v2.5-pro")
    monkeypatch.setenv(
        "ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
    )
    monkeypatch.setenv("ARK_API_KEY", "ark-test")
    monkeypatch.setenv("ARK_MODEL", "doubao-seed-2-0-lite-260215")
    monkeypatch.setenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
    monkeypatch.setenv("AGNES_API_KEY", "agnes-test")
    monkeypatch.setenv("AGNES_MODEL", "agnes-2.0-flash")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("OPENROUTER_API_KEY_2", "or-test-2")
    monkeypatch.setenv("OPENROUTER_FREE_MODEL", "openrouter/free")

    config = load_config("config.example.yaml")

    assert set(config.providers) == {
        "xiaomi_mimo",
        "volcengine_ark",
        "agnes",
        "openrouter",
    }
    assert set(config.providers["xiaomi_mimo"].endpoints) == {"token_plan"}
    assert config.model_instances[0].endpoint == "token_plan"
    assert config.providers["xiaomi_mimo"].get_endpoint("token_plan").auth_header == "api_key"
    assert config.providers["volcengine_ark"].get_endpoint("api").auth_header == "authorization_bearer"
    assert config.providers["agnes"].get_endpoint("api").base_url == "https://apihub.agnes-ai.com/v1"
    assert config.providers["agnes"].get_endpoint("api").auth_header == "authorization_bearer"
    assert config.providers["openrouter"].get_endpoint("api").auth_header == "authorization_bearer"
    assert config.providers["xiaomi_mimo"].get_endpoint("token_plan").stream_usage_mode == "no_option_usage_chunk"
    assert config.providers["volcengine_ark"].get_endpoint("api").stream_usage_mode == "ark_include_usage"
    assert config.providers["agnes"].get_endpoint("api").stream_usage_mode == "openai_include_usage"
    assert config.providers["openrouter"].get_endpoint("api").stream_usage_mode == "no_option_usage_chunk"
    assert config.model_instances[-2].name == "agnes-2.0-flash"
    assert config.model_instances[-2].provider == "agnes"
    assert config.model_instances[-2].level < config.model_instances[-1].level
    assert config.model_instances[-2].iter_key_configs()[0].priority < config.model_instances[-1].iter_key_configs()[0].priority
    assert [key.id for key in config.providers["openrouter"].get_endpoint("api").keys] == [
        "openrouter_1",
        "openrouter_2",
    ]
    assert config.model_instances[-1].name == "openrouter/free"
    assert config.model_instances[-1].provider == "openrouter"
    assert config.model_instances[-1].level == 5
    assert config.model_instances[-1].priority == 100
    openrouter_keys = config.model_instances[-1].iter_key_configs()
    assert [key.key_id for key in openrouter_keys] == ["openrouter_1", "openrouter_2"]
    assert [key.priority for key in openrouter_keys] == [100, 110]
    assert [key.daily_request_quota for key in openrouter_keys] == [50, 50]


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
    level: 1
    keys:
      - key_id: token_plan_key
        daily_quota: 1000
  - name: mimo-v2.5
    provider: xiaomi_mimo
    endpoint: api
    level: 2
    keys:
      - key_id: api_key
        daily_quota: 1000
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.providers["xiaomi_mimo"].get_endpoint("token_plan").base_url == "https://token-plan-cn.xiaomimimo.com/v1"
    assert config.providers["xiaomi_mimo"].get_endpoint("api").base_url == "https://api.xiaomimimo.com/v1"
    assert config.model_instances[0].endpoint == "token_plan"
    assert config.model_instances[1].endpoint == "api"


def test_model_instance_supports_multiple_keys_with_independent_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("KEY_ONE", "sk-one")
    monkeypatch.setenv("KEY_TWO", "sk-two")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
providers:
  test:
    type: openai_compatible
    endpoints:
      api:
        base_url: https://example.test/v1
        keys:
          - id: key_one
            value: ${KEY_ONE}
          - id: key_two
            value: ${KEY_TWO}
model_instances:
  - name: shared-model
    provider: test
    endpoint: api
    level: 1
    priority: 10
    keys:
      - key_id: key_one
        daily_quota: 1000
        priority: 20
      - key_id: key_two
        daily_quota: 2000
        priority: 30
""",
        encoding="utf-8",
    )

    config = load_config(config_file)
    key_instances = config.model_instances[0].iter_key_configs()

    assert [key.key_id for key in key_instances] == ["key_one", "key_two"]
    assert [key.daily_quota for key in key_instances] == [1000, 2000]
    assert [key.daily_request_quota for key in key_instances] == [None, None]
    assert [key.priority for key in key_instances] == [20, 30]


def test_provider_stream_usage_mode_defaults_to_endpoints(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
providers:
  test:
    type: openai_compatible
    stream_usage_mode: openai_include_usage
    endpoints:
      defaulted:
        base_url: https://defaulted.example.test/v1
        keys:
          - id: key_one
            value: sk-one
      overridden:
        base_url: https://overridden.example.test/v1
        stream_usage_mode: no_option_usage_chunk
        keys:
          - id: key_two
            value: sk-two
model_instances:
  - name: model-a
    provider: test
    endpoint: defaulted
    level: 1
    keys:
      - key_id: key_one
        daily_quota: 1000
  - name: model-b
    provider: test
    endpoint: overridden
    level: 1
    keys:
      - key_id: key_two
        daily_quota: 1000
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.providers["test"].get_endpoint("defaulted").stream_usage_mode == "openai_include_usage"
    assert config.providers["test"].get_endpoint("overridden").stream_usage_mode == "no_option_usage_chunk"
