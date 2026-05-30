from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SelectedRoute:
    provider: str
    endpoint: str
    key_id: str
    model_name: str
    level: int
    daily_quota: int
    used_tokens: int
    usage_ratio: float
    stage: int | None
    priority: int
    enabled: bool
    available: bool
    groups: tuple[str, ...]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["groups"] = list(self.groups)
        return data
