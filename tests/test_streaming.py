from token_router.app.providers.streaming import (
    SSEUsageTracker,
    apply_stream_usage_policy,
    extract_usage_from_sse_bytes,
)


def test_openai_policy_adds_include_usage_when_absent():
    payload = {"model": "m", "stream": True}

    result = apply_stream_usage_policy(payload, "openai_include_usage")

    assert result["stream_options"] == {"include_usage": True}
    assert payload == {"model": "m", "stream": True}


def test_policy_preserves_client_stream_options():
    payload = {"model": "m", "stream": True, "stream_options": {"include_usage": False}}

    result = apply_stream_usage_policy(payload, "openai_include_usage")

    assert result["stream_options"] == {"include_usage": False}


def test_no_option_policy_does_not_add_stream_options():
    payload = {"model": "m", "stream": True}

    result = apply_stream_usage_policy(payload, "no_option_usage_chunk")

    assert "stream_options" not in result


def test_extract_usage_from_final_empty_choices_chunk():
    frame = (
        b'data: {"choices":[],"usage":'
        b'{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
    )

    usage = extract_usage_from_sse_bytes(frame)

    assert usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def test_extract_usage_ignores_done_and_malformed_json():
    assert extract_usage_from_sse_bytes(b"data: [DONE]\n\n") is None
    assert extract_usage_from_sse_bytes(b"data: {bad-json}\n\n") is None


def test_extract_usage_from_bigmodel_style_final_chunk():
    frame = (
        b'data: {"choices":[{"finish_reason":"stop"}],"usage":'
        b'{"prompt_tokens":4,"completion_tokens":6,"total_tokens":10}}\n\n'
    )

    usage = extract_usage_from_sse_bytes(frame)

    assert usage == {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}


def test_usage_tracker_handles_split_sse_frame():
    tracker = SSEUsageTracker()

    first = tracker.feed(b'data: {"choices":[],"usage":{"prompt_tokens":3,')
    second = tracker.feed(b'"completion_tokens":2,"total_tokens":5}}\n\n')

    assert first is None
    assert second == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
