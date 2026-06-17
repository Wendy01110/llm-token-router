from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from token_router.app.config import ApiKeyConfig, EndpointConfig, load_config


@dataclass(frozen=True)
class ProbeResult:
    provider: str
    endpoint: str
    key_id: str
    model_name: str
    response_format_type: str
    status: str
    http_status: int | None
    error_param: str | None
    message: str | None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe upstream chat response_format.type support."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--type", default="json_object", dest="response_format_type")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--model", help="Only probe one upstream model name.")
    parser.add_argument("--provider", help="Only probe one provider.")
    args = parser.parse_args()

    config = load_config(args.config)
    for instance in config.model_instances:
        if args.model and instance.name != args.model:
            continue
        if args.provider and instance.provider != args.provider:
            continue
        provider_config = config.providers[instance.provider]
        endpoint_config = provider_config.get_endpoint(instance.endpoint)
        for key_config in instance.iter_key_configs():
            api_key = _find_api_key(endpoint_config, key_config.key_id)
            result = _probe(
                provider=instance.provider,
                endpoint=instance.endpoint,
                endpoint_config=endpoint_config,
                api_key=api_key,
                model_name=instance.name,
                response_format_type=args.response_format_type,
                timeout=args.timeout,
            )
            print(json.dumps(asdict(result), ensure_ascii=False))


def _find_api_key(endpoint_config: EndpointConfig, key_id: str) -> ApiKeyConfig:
    for api_key in endpoint_config.keys:
        if api_key.id == key_id:
            return api_key
    raise RuntimeError(f"missing API key {key_id!r}")


def _probe(
    provider: str,
    endpoint: str,
    endpoint_config: EndpointConfig,
    api_key: ApiKeyConfig,
    model_name: str,
    response_format_type: str,
    timeout: float,
) -> ProbeResult:
    url = f"{endpoint_config.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": 'Return a JSON object exactly like {"ok": true}.',
            }
        ],
        "response_format": {"type": response_format_type},
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                url,
                headers=_headers(endpoint_config, api_key),
                json=payload,
            )
    except httpx.HTTPError as exc:
        return ProbeResult(
            provider=provider,
            endpoint=endpoint,
            key_id=api_key.id,
            model_name=model_name,
            response_format_type=response_format_type,
            status="transport_error",
            http_status=None,
            error_param=None,
            message=str(exc),
        )

    if response.is_success:
        return ProbeResult(
            provider=provider,
            endpoint=endpoint,
            key_id=api_key.id,
            model_name=model_name,
            response_format_type=response_format_type,
            status="supported",
            http_status=response.status_code,
            error_param=None,
            message=None,
        )

    error = _parse_error(response)
    return ProbeResult(
        provider=provider,
        endpoint=endpoint,
        key_id=api_key.id,
        model_name=model_name,
        response_format_type=response_format_type,
        status="unsupported_or_failed",
        http_status=response.status_code,
        error_param=error.get("param"),
        message=error.get("message") or response.text.strip(),
    )


def _headers(endpoint_config: EndpointConfig, api_key: ApiKeyConfig) -> dict[str, str]:
    if endpoint_config.auth_header == "api_key":
        return {"api-key": api_key.value}
    return {"Authorization": f"Bearer {api_key.value}"}


def _parse_error(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return {}
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


if __name__ == "__main__":
    main()
