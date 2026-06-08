from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResponsesRequest(BaseModel):
    model: str = "auto"
    router: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}
