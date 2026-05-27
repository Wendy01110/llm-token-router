from __future__ import annotations

from typing import Any

import httpx

from token_router.app.config import ApiKeyConfig, ProviderConfig


class OpenAICompatibleProvider:
    async def chat_completion(
        self,
        provider_config: ProviderConfig,
        api_key: ApiKeyConfig,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{provider_config.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key.value}"}
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
