from __future__ import annotations

from typing import Any

import httpx

from token_router.app.config import ApiKeyConfig, EndpointConfig, ProviderConfig


class OpenAICompatibleProvider:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def chat_completion(
        self,
        provider_config: EndpointConfig | ProviderConfig,
        api_key: ApiKeyConfig,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{provider_config.base_url.rstrip('/')}/chat/completions"
        headers = self._headers(provider_config, api_key)
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

    def _headers(
        self, provider_config: EndpointConfig | ProviderConfig, api_key: ApiKeyConfig
    ) -> dict[str, str]:
        if provider_config.auth_header == "api_key":
            return {"api-key": api_key.value}
        return {"Authorization": f"Bearer {api_key.value}"}
