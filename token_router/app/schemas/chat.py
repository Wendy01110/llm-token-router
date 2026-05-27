from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatCompletionRequest(BaseModel):
    model: str = "auto"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    router: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}
