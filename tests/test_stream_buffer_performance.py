"""Fragment accumulation and line framing preserve protocol boundaries."""
import asyncio
import codecs
import itertools
import random

import pytest

from cyrene.model.stream_buffer import TextBuffer, append_argument
from cyrene.model.stream_decoder import StreamDecoder
from cyrene.model.stream_encoding import ModelStreamError, strict_utf8_lines


def test_buffer_coalesces_small_fragments_and_reuses_materialization():
    value = TextBuffer()
    for _ in range(20000):
        value.append('中')
    assert len(value._parts) < 256
    assert len(value._blocks) <= 20000 // 256
    assert str(value) == '中' * 20000
    assert str(value) is str(value)
    assert not value._parts
    value.append('tail')
    assert str(value) == '中' * 20000 + 'tail'


def test_replacement_discards_previous_fragments_and_can_append_again():
    call = {'arguments': ''}
    append_argument(call, 'discard')
    call['arguments'] = '{"x":'
    append_argument(call, '1}')
    assert str(call['arguments']) == '{"x":1}'
    call['arguments'] = '{}'
    assert str(call['arguments']) == '{}'


def test_responses_all_final_replacements_preserve_existing_behavior():
    decoder = StreamDecoder('openai_responses', None, {}, usage_for=None,
                            parse_response=None, update_diagnostics=None,
                            stream_error=None, openai_event=None, openai_adapters=set())
    asyncio.run(decoder.responses({'type': 'response.function_call_arguments.delta', 'output_index': 0, 'delta': 'old'}))
    decoder.responses_arguments_done({'output_index': 0, 'arguments': '{"done":1}'})
    assert str(decoder.tool_calls['0']['arguments']) == '{"done":1}'
    decoder.responses_item_done({'output_index': 0, 'item': {'type': 'function_call', 'call_id': 'a', 'arguments': '{"item":2}'}})
    assert str(decoder.tool_calls['0']['arguments']) == '{"item":2}'
    decoder.merge_completed_call({'id': 'a', 'function': {'arguments': '{"final":3}'}})
    assert str(decoder.tool_calls['0']['arguments']) == '{"final":3}'


async def legacy_lines(chunks):
    """Frozen pre-optimization framing algorithm, including delayed CR."""
    decoder = codecs.getincrementaldecoder('utf-8')(errors='strict')
    buffered = ''
    async for chunk in chunks:
        buffered += decoder.decode(chunk, final=False)
        while True:
            indexes = [i for i in (buffered.find('\r'), buffered.find('\n')) if i >= 0]
            if not indexes:
                break
            index = min(indexes)
            if buffered[index] == '\r' and index + 1 == len(buffered):
                break
            width = 2 if buffered[index:index + 2] == '\r\n' else 1
            yield buffered[:index]
            buffered = buffered[index + width:]
    buffered += decoder.decode(b'', final=True)
    while True:
        indexes = [i for i in (buffered.find('\r'), buffered.find('\n')) if i >= 0]
        if not indexes:
            break
        index = min(indexes)
        width = 2 if buffered[index:index + 2] == '\r\n' else 1
        yield buffered[:index]
        buffered = buffered[index + width:]
    if buffered:
        yield buffered


async def observe(parts, modern, failure):
    consumed = 0
    output = []
    async def chunks():
        nonlocal consumed
        for part in parts:
            consumed += 1
            yield part
        consumed += 1
        if failure:
            raise OSError('transport')
    stream = strict_utf8_lines(None, {}, chunks()) if modern else legacy_lines(chunks())
    error = ''
    try:
        async for line in stream:
            output.append((consumed, line))
    except UnicodeDecodeError:
        error = 'invalid_utf8'
    except ModelStreamError as exc:
        assert exc.kind == 'protocol_invalid_utf8'
        error = 'invalid_utf8'
    except OSError:
        error = 'transport'
    return output, error


@pytest.mark.asyncio
async def test_framing_matches_legacy_for_partitions_errors_and_yield_timing():
    samples = [b'', b'a', b'\r', b'\n', b'\r\n', b'a\r\nb\r', b'\r\r\n\n',
               '中\r\n文'.encode(), b'a\n\xff', b'a\r\xe4', b'a\n\xe4\xb8']
    for raw in samples:
        for mask in range(1 << max(0, len(raw) - 1)):
            cuts = [0] + [i for i in range(1, len(raw)) if mask & (1 << (i - 1))] + [len(raw)]
            parts = [raw[a:b] for a, b in zip(cuts, cuts[1:])]
            for failure in (False, True):
                for variant in (parts, [b'', *itertools.chain.from_iterable((p, b'') for p in parts)]):
                    assert await observe(variant, True, failure) == await observe(variant, False, failure)
    rng = random.Random(20261026)
    for _ in range(2000):
        raw = ''.join(rng.choices('ab中😀\r\n\u2028\v', k=rng.randrange(60))).encode()
        if rng.randrange(4) == 0:
            offset = rng.randrange(len(raw) + 1)
            raw = raw[:offset] + b'\xff' + raw[offset:]
        parts = []
        while raw:
            width = rng.randrange(1, 8)
            parts.append(raw[:width])
            raw = raw[width:]
        failure = bool(rng.randrange(2))
        assert await observe(parts, True, failure) == await observe(parts, False, failure)


@pytest.mark.asyncio
async def test_long_line_scans_only_new_text(monkeypatch):
    from cyrene.model import stream_encoding
    original = stream_encoding._LINE_ENDINGS
    scanned = 0
    class Observe:
        def finditer(self, text, cursor):
            nonlocal scanned
            scanned += len(text)
            return original.finditer(text, cursor)
    monkeypatch.setattr(stream_encoding, '_LINE_ENDINGS', Observe())
    parts = [b'x' * 128] * 8192 + [b'\n']
    result, error = await observe(parts, True, False)
    assert error == ''
    assert result[0][1] == 'x' * 1048576
    assert scanned == 1048577
