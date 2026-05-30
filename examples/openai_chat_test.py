from __future__ import annotations

import json
import os
from typing import Any

try:
    from openai import OpenAI
except ImportError as exc:
    raise SystemExit(
        'Missing dependency. Run: python -m pip install -e ".[dev]"'
    ) from exc


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def dump_model(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def main() -> None:
    base_url = os.getenv("ROUTER_BASE_URL", "http://127.0.0.1:8000/v1")
    api_key = os.getenv("ROUTER_API_KEY", "local-router-test")
    model = os.getenv("ROUTER_MODEL", "doubao-seed-2-0-mini-260428")
    provider = os.getenv("ROUTER_PROVIDER", "volcengine_ark")
    level = int(os.getenv("ROUTER_LEVEL", "3"))
    prompt = os.getenv(
        "ROUTER_TEST_PROMPT",
        "Explain why a local LLM token router records usage. "
        "Answer in one concise Chinese sentence.",
    )

    router_options: dict[str, Any] = {
        "provider": provider,
        "level": level,
        "strict_model": env_bool("ROUTER_STRICT_MODEL", model != "auto"),
        "fallback": env_bool("ROUTER_FALLBACK", model == "auto"),
        "debug": True,
    }

    client = OpenAI(base_url=base_url, api_key=api_key)
    raw_response = client.chat.completions.with_raw_response.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": os.getenv(
                    "ROUTER_SYSTEM_PROMPT",
                    "You are a concise Chinese technical assistant.",
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=float(os.getenv("ROUTER_TEMPERATURE", "0.2")),
        max_tokens=int(os.getenv("ROUTER_MAX_TOKENS", "160")),
        extra_body={"router": router_options},
    )
    completion = raw_response.parse()
    message = completion.choices[0].message if completion.choices else None
    router_headers = {
        key: value
        for key, value in raw_response.headers.items()
        if key.lower().startswith("x-router")
    }

    print(
        json.dumps(
            {
                "request": {
                    "base_url": base_url,
                    "model": model,
                    "router": router_options,
                },
                "router_headers": router_headers,
                "response": {
                    "model": completion.model,
                    "message": dump_model(message),
                    "usage": dump_model(completion.usage),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
