from __future__ import annotations

import json
from typing import Any

from token_router.app.config import StreamUsageMode


def apply_stream_usage_policy(
    payload: dict[str, Any], stream_usage_mode: StreamUsageMode | None
) -> dict[str, Any]:
    outgoing = dict(payload)
    if stream_usage_mode in {"openai_include_usage", "ark_include_usage"}:
        stream_options = dict(outgoing.get("stream_options") or {})
        stream_options.setdefault("include_usage", True)
        outgoing["stream_options"] = stream_options
    return outgoing


def extract_usage_from_sse_bytes(chunk: bytes) -> dict[str, Any] | None:
    latest_usage = None
    for raw_line in chunk.splitlines():
        usage = _extract_usage_from_sse_line(raw_line)
        if usage is not None:
            latest_usage = usage
    return latest_usage


class SSEUsageTracker:
    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, chunk: bytes) -> dict[str, Any] | None:
        self._buffer += chunk
        latest_usage = None
        while b"\n" in self._buffer:
            raw_line, self._buffer = self._buffer.split(b"\n", 1)
            usage = _extract_usage_from_sse_line(raw_line)
            if usage is not None:
                latest_usage = usage
        return latest_usage


def _extract_usage_from_sse_line(raw_line: bytes) -> dict[str, Any] | None:
    line = raw_line.strip()
    if not line.startswith(b"data:"):
        return None
    data = line[5:].strip()
    if not data or data == b"[DONE]":
        return None
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return None
    usage = event.get("usage")
    if isinstance(usage, dict):
        return usage
    return None
