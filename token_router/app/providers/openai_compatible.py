from __future__ import annotations

from collections.abc import AsyncIterator
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

    async def chat_completion_stream(
        self,
        provider_config: EndpointConfig | ProviderConfig,
        api_key: ApiKeyConfig,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        url = f"{provider_config.base_url.rstrip('/')}/chat/completions"
        headers = self._headers(provider_config, api_key)
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk

    async def responses(
        self,
        provider_config: EndpointConfig | ProviderConfig,
        api_key: ApiKeyConfig,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{provider_config.base_url.rstrip('/')}/responses"
        headers = self._headers(provider_config, api_key)
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

    async def responses_stream(
        self,
        provider_config: EndpointConfig | ProviderConfig,
        api_key: ApiKeyConfig,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        url = f"{provider_config.base_url.rstrip('/')}/responses"
        headers = self._headers(provider_config, api_key)
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk

    def _headers(
        self, provider_config: EndpointConfig | ProviderConfig, api_key: ApiKeyConfig
    ) -> dict[str, str]:
        if provider_config.auth_header == "api_key":
            return {"api-key": api_key.value}
        return {"Authorization": f"Bearer {api_key.value}"}
