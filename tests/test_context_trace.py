from cyrene.observability.context_trace import (
    attach_context,
    context_block,
    strip_context_metadata,
    summarize_context_trace,
)


def test_context_metadata_is_stripped_before_payload() -> None:
    message = attach_context(
        {"role": "user", "content": "hello"},
        context_block("user.current.raw", "user", source="test", reason="unit test", content="hello"),
    )

    stripped = strip_context_metadata(message)

    assert "_ctx" not in stripped
    assert stripped == {"role": "user", "content": "hello"}


def test_context_trace_uses_explicit_blocks() -> None:
    message = attach_context(
        {"role": "user", "content": "hello"},
        context_block("user.current.raw", "user", source="test", reason="unit test", content="hello"),
    )

    trace = summarize_context_trace([message])

    assert trace["included"][0]["id"] == "user.current.raw"
    assert trace["included"][0]["type"] == "user"
    assert trace["message_map"][0]["block_ids"] == ["user.current.raw"]


def test_context_trace_infers_blocks_for_plain_messages() -> None:
    trace = summarize_context_trace([{"role": "tool", "tool_call_id": "call_123", "content": "result"}])

    assert trace["included"][0]["id"] == "tool.result.call_123"
    assert trace["included"][0]["type"] == "tool_result"


def test_token_runs_match_legacy_estimate_and_bound_cache():
    import random
    import re
    from cyrene.observability.context_trace import approx_token_count, _cached_token_count
    def legacy(source):
        source = str(source or '')
        return sum(max(1, (len(unit) + 3) // 4) if re.fullmatch(r'[A-Za-z0-9_]+', unit) else 1
                   for unit in re.findall(r'[一-鿿]|[A-Za-z0-9_]+|[^\s]', source))
    rng = random.Random(20261026)
    alphabet = 'abcXYZ019_中文。，éß😀\n\r\t \v\f\u00a0\u2003\u2028\u200b'
    for value in (None, False, 0, 123, '', 'a' * 16385, '中' * 65536):
        assert approx_token_count(value) == legacy(value)
    for _ in range(3000):
        value = ''.join(rng.choices(alphabet, k=rng.randrange(300)))
        assert approx_token_count(value) == legacy(value)
    _cached_token_count.cache_clear()
    approx_token_count('cached')
    approx_token_count('cached')
    assert _cached_token_count.cache_info().hits == 1
    before = _cached_token_count.cache_info()
    approx_token_count('中' * 16385)
    assert _cached_token_count.cache_info() == before
    for i in range(100):
        approx_token_count(str(i))
    assert _cached_token_count.cache_info().currsize <= 64


def test_same_length_message_edit_recomputes_content_statistics():
    message = {'role': 'user', 'content': 'abcd'}
    first = summarize_context_trace([message])
    message['content'] = '中文测试'
    second = summarize_context_trace([message])
    assert first['total_tokens_est'] == 1
    assert second['total_tokens_est'] == 4
    assert first['included'][0]['content_sha256_16'] != second['included'][0]['content_sha256_16']
