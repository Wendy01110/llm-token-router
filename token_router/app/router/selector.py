from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from token_router.app.config import (
    AppConfig,
    ModelInstanceConfig,
    ModelInstanceKeyConfig,
)
from token_router.app.router.quota import is_exhausted, stage_for_usage, usage_ratio
from token_router.app.router.runtime import RouteKey, route_key
from token_router.app.schemas.router import SelectedRoute
from token_router.app.usage import UsageManager


class NoAvailableModelError(Exception):
    """Raised when no configured model instance can serve a request."""


class RouteSelector:
    def __init__(self, config: AppConfig, usage_manager: UsageManager):
        self.config = config
        self.usage_manager = usage_manager

    def select(
        self,
        model: str,
        router: Mapping[str, Any] | None,
        quota_date: str,
        responses_api: str | None = None,
        excluded_routes: set[RouteKey] | None = None,
    ) -> SelectedRoute:
        router_options = dict(router or {})
        requested_model = None if model == "auto" else model
        strict_model = bool(router_options.get("strict_model", False))
        excluded_routes = excluded_routes or set()

        selected = self._select_matching(
            requested_model=requested_model,
            router_options=router_options,
            quota_date=quota_date,
            responses_api=responses_api,
            excluded_routes=excluded_routes,
        )
        if selected is not None:
            return selected

        if requested_model and not strict_model and router_options.get("fallback", True):
            selected = self._select_matching(
                requested_model=None,
                router_options=router_options,
                quota_date=quota_date,
                responses_api=responses_api,
                excluded_routes=excluded_routes,
            )
            if selected is not None:
                return selected

        raise NoAvailableModelError("no available model instance")

    def list_status(self, quota_date: str) -> list[SelectedRoute]:
        return [
            self._build_route(instance, key_config, quota_date)
            for instance in self.config.model_instances
            for key_config in instance.iter_key_configs()
        ]

    def _select_matching(
        self,
        requested_model: str | None,
        router_options: Mapping[str, Any],
        quota_date: str,
        responses_api: str | None = None,
        excluded_routes: set[RouteKey] | None = None,
    ) -> SelectedRoute | None:
        candidates = []
        levels = set(self._candidate_levels(router_options))
        provider = router_options.get("provider")
        provider = None if provider in (None, "auto") else provider
        model_group = router_options.get("model_group")
        excluded_routes = excluded_routes or set()

        for instance in self.config.model_instances:
            if instance.level not in levels:
                continue
            if provider and instance.provider != provider:
                continue
            if requested_model and instance.name != requested_model:
                continue
            if model_group and model_group not in instance.groups:
                continue
            if responses_api is not None:
                endpoint = self.config.providers[instance.provider].get_endpoint(
                    instance.endpoint
                )
                if endpoint.responses_api != responses_api:
                    continue

            for key_config in instance.iter_key_configs():
                route = self._build_route(instance, key_config, quota_date)
                if route.available and route_key(route) not in excluded_routes:
                    candidates.append(route)

        if not candidates:
            return None

        candidates.sort(
            key=lambda route: (
                route.level,
                route.stage if route.stage is not None else 99,
                route.priority,
                route.usage_ratio,
                route.provider,
                route.endpoint,
                route.key_id,
                route.model_name,
            )
        )
        return candidates[0]

    def _candidate_levels(self, router_options: Mapping[str, Any]) -> list[int]:
        start_level = int(router_options.get("level") or self.config.routing.default_level)
        fallback_enabled = bool(
            router_options.get("fallback", self.config.routing.fallback_enabled)
        )
        if not fallback_enabled:
            return [start_level]

        max_fallback_level = int(
            router_options.get(
                "max_fallback_level", self.config.routing.max_fallback_level
            )
        )
        if max_fallback_level < start_level:
            return [start_level]
        return list(range(start_level, max_fallback_level + 1))

    def _build_route(
        self,
        instance: ModelInstanceConfig,
        key_config: ModelInstanceKeyConfig,
        quota_date: str,
    ) -> SelectedRoute:
        usage = self.usage_manager.get_usage(
            instance.provider,
            key_config.key_id,
            instance.name,
            quota_date,
        )
        key_request_count = self.usage_manager.get_key_request_count(
            instance.provider,
            key_config.key_id,
            quota_date,
        )
        token_exhausted = is_exhausted(usage.total_tokens, key_config.daily_quota)
        request_exhausted = (
            key_config.daily_request_quota is not None
            and key_request_count >= key_config.daily_request_quota
        )
        exhausted = token_exhausted or request_exhausted
        return SelectedRoute(
            provider=instance.provider,
            endpoint=instance.endpoint,
            key_id=key_config.key_id,
            model_name=instance.name,
            level=instance.level,
            daily_quota=key_config.daily_quota,
            daily_request_quota=key_config.daily_request_quota,
            used_tokens=usage.total_tokens,
            used_requests=key_request_count,
            usage_ratio=usage_ratio(usage.total_tokens, key_config.daily_quota),
            stage=stage_for_usage(usage.total_tokens, key_config.daily_quota),
            priority=key_config.priority or instance.priority,
            enabled=instance.enabled and key_config.enabled,
            available=instance.enabled and key_config.enabled and not exhausted,
            groups=tuple(instance.groups),
        )
