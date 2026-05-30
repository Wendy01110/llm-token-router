from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class RefreshConfig(BaseModel):
    timezone: str = "Asia/Shanghai"
    daily_reset_hour: int = Field(default=11, ge=0, le=23)


class RoutingConfig(BaseModel):
    default_level: int = Field(default=1, ge=1)
    fallback_enabled: bool = True
    max_fallback_level: int = Field(default=5, ge=1)


class ApiKeyConfig(BaseModel):
    id: str
    value: str


class EndpointConfig(BaseModel):
    base_url: str
    auth_header: Literal["authorization_bearer", "api_key"] = "authorization_bearer"
    keys: list[ApiKeyConfig]


class ProviderConfig(BaseModel):
    type: str = "openai_compatible"
    base_url: str | None = None
    auth_header: Literal["authorization_bearer", "api_key"] = "authorization_bearer"
    keys: list[ApiKeyConfig] = Field(default_factory=list)
    endpoints: dict[str, EndpointConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_endpoint_shape(self) -> ProviderConfig:
        if self.endpoints:
            return self
        if self.base_url and self.keys:
            return self
        raise ValueError("provider must define endpoints or base_url with keys")

    def get_endpoint(self, endpoint: str) -> EndpointConfig:
        if self.endpoints:
            try:
                return self.endpoints[endpoint]
            except KeyError as exc:
                raise KeyError(f"unknown endpoint {endpoint!r}") from exc
        if endpoint != "default":
            raise KeyError(f"unknown endpoint {endpoint!r}")
        if self.base_url is None:
            raise KeyError("default endpoint is not configured")
        return EndpointConfig(
            base_url=self.base_url,
            auth_header=self.auth_header,
            keys=self.keys,
        )


class ModelInstanceConfig(BaseModel):
    name: str
    provider: str
    endpoint: str = "default"
    key_id: str
    level: int = Field(ge=1)
    daily_quota: int = Field(ge=1)
    priority: int = Field(default=100, ge=1)
    groups: list[str] = Field(default_factory=list)
    enabled: bool = True


class AppConfig(BaseModel):
    refresh: RefreshConfig = Field(default_factory=RefreshConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    providers: dict[str, ProviderConfig]
    model_instances: list[ModelInstanceConfig]

    @model_validator(mode="after")
    def validate_model_references(self) -> AppConfig:
        for instance in self.model_instances:
            provider = self.providers.get(instance.provider)
            if provider is None:
                raise ValueError(
                    f"model {instance.name!r} references unknown provider {instance.provider!r}"
                )
            try:
                endpoint = provider.get_endpoint(instance.endpoint)
            except KeyError as exc:
                raise ValueError(
                    f"model {instance.name!r} references unknown endpoint {instance.endpoint!r}"
                ) from exc
            key_ids = {key.id for key in endpoint.keys}
            if instance.key_id not in key_ids:
                raise ValueError(
                    f"model {instance.name!r} references unknown key {instance.key_id!r}"
                )
        return self


def resolve_env_refs(raw_text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return os.environ[name]

    resolved_lines = []
    for line in raw_text.splitlines(keepends=True):
        content, comment_marker, comment = line.partition("#")
        resolved_lines.append(_ENV_PATTERN.sub(replace, content))
        resolved_lines.append(comment_marker)
        resolved_lines.append(comment)
    return "".join(resolved_lines)


def load_env_file(config_path: Path) -> None:
    env_path = config_path.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path)
    load_env_file(config_path)
    raw_text = config_path.read_text(encoding="utf-8")
    resolved_text = resolve_env_refs(raw_text)
    data = yaml.safe_load(resolved_text)
    return AppConfig.model_validate(data)
