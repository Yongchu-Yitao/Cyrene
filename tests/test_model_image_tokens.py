from __future__ import annotations

import base64
import copy
import json
from io import BytesIO

import pytest
from PIL import Image

from cyrene.model.image_tokens import estimate_image_tokens
from cyrene.observability.context_trace import approx_token_count
from cyrene.plugins.model_router import _eligible_candidates, request_token_estimate


def image_block(compress_level=6):
    output = BytesIO()
    Image.new("RGB", (3840, 1920)).save(output, format="PNG", compress_level=compress_level)
    return {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode(),
    }}


def test_image_budget_is_independent_of_png_compression():
    compressed, uncompressed = image_block(), image_block(0)
    assert len(uncompressed["image_url"]["url"]) > 200_000
    assert estimate_image_tokens(compressed, model="qwen3.8-flash-next") == 7202
    assert estimate_image_tokens(uncompressed, model="qwen3.8-flash-next") == 7202


def test_screenshot_passes_context_gate_without_mutating_request():
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "请识别截图中的报错"}, image_block(),
    ]}]
    original = copy.deepcopy(messages)
    candidate = {"model": "qwen3.8-flash-next", "context_limit": 200_000}
    assert 7202 < request_token_estimate(messages, None, model=candidate["model"]) < 7400
    assert _eligible_candidates([candidate], messages, None, 4096) == [candidate]
    assert messages == original


def test_context_gate_retains_larger_fallback_and_output_budget():
    messages = [{"role": "user", "content": [image_block()]}]
    small = {"model": "qwen3.8-small", "context_limit": 8000}
    large = {"model": "qwen3.8-large", "context_limit": 200_000}
    assert _eligible_candidates([small, large], messages, None, 4096) == [large]
    with pytest.raises(ValueError, match="exceeds all configured context windows"):
        _eligible_candidates([small], messages, None, 4096)


@pytest.mark.parametrize("url", ["https://example.test/image.png", "data:image/png;base64,!!!"])
def test_unknown_image_uses_bounded_visual_budget(url):
    block = {"type": "input_image", "image_url": url}
    assert estimate_image_tokens(block) == 16_386
    messages = [{"role": "user", "content": [block]}]
    assert 16_386 < request_token_estimate(messages, None) < 16_500


def test_text_and_tool_budgets_still_enforce_context_limits():
    messages = [{"role": "user", "content": "长文本。" * 2000}]
    tools = [{"type": "function", "function": {"name": "lookup", "description": "Find data"}}]
    expected = 4 + approx_token_count(json.dumps(messages[0], ensure_ascii=False, sort_keys=True))
    expected += approx_token_count(json.dumps(tools, ensure_ascii=False, sort_keys=True))
    assert request_token_estimate(messages, tools) == expected
    candidate = {"model": "text-model", "context_limit": 1000}
    with pytest.raises(ValueError, match="exceeds all configured context windows"):
        _eligible_candidates([candidate], messages, tools, 10)
    assert _eligible_candidates([candidate], messages, tools, 10, estimated_input_tokens=100) == [candidate]
