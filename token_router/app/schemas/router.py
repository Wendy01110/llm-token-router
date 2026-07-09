from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SelectedRoute:
    provider: str
    endpoint: str
    key_id: str
    model_name: str
    upstream_model_name: str
    level: int
    daily_quota: int
    daily_request_quota: int | None
    used_tokens: int
    used_requests: int
    usage_ratio: float
    stage: int | None
    priority: int
    max_concurrency: int
    enabled: bool
    available: bool
    groups: tuple[str, ...]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["groups"] = list(self.groups)
        return data
