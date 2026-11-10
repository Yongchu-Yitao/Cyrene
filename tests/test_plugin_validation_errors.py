from copy import deepcopy

import pytest

from cyrene.core.plugin.validation import (
    PluginInputValidationError,
    normalize_plugin_arguments,
    validate_plugin_arguments,
)
from cyrene.plugins.builtin.cyrene_cards.definitions import definition


@pytest.mark.parametrize("operation,container", [("card.create", "spec"), ("card.update", "changes")])
@pytest.mark.parametrize("block,fragments", [
    ({"block_id": "app", "type": "embed"}, ["blocks[0].html", "缺少必填参数"]),
    ({"block_id": "app"}, ["blocks[0].html", "缺少必填参数"]),
    ({"block_id": "app", "html": ""}, ["blocks[0].html", "至少需要 1 个字符"]),
    ({"block_id": "app", "type": "video", "html": "<video></video>"}, ["blocks[0].type", "可选值", "embed"]),
    ({"block_id": "app", "type": "embed", "html": "<p>hi</p>", "autoplay": True},
     ["blocks[0]", "不支持的字段", "autoplay"]),
    ({"block_id": "items", "type": "timeline", "items": [{"item_id": "a"}]},
     ["blocks[0].items[0].label", "缺少必填参数"]),
])
def test_card_errors_identify_the_relevant_field(operation, container, block, fragments):
    args = {container: {"title": "Check", "blocks": [block]}}
    args.update({"template": "custom"} if operation == "card.create" else {"card_id": "card", "expected_revision": 1})
    original = deepcopy(args)
    schema = definition(operation)[1]
    normalized = normalize_plugin_arguments(args, schema).arguments
    with pytest.raises(PluginInputValidationError) as raised:
        validate_plugin_arguments(operation, normalized, schema)
    message = raised.value.localized_message("zh")
    assert container + ".blocks[0]" in message
    for fragment in fragments:
        assert fragment in message
    assert args == original


def test_explicit_type_takes_precedence_over_default_branch():
    args = {"template": "custom", "spec": {"title": "Check", "blocks": [{"block_id": "intro", "type": "text"}]}}
    with pytest.raises(PluginInputValidationError) as raised:
        validate_plugin_arguments("card.create", args, definition("card.create")[1])
    assert raised.value.localized_message("zh") == "缺少必填参数：spec.blocks[0].text。"
    assert "html" not in str(raised.value)


def test_ambiguous_union_does_not_invent_a_selected_branch():
    schema = {"oneOf": [
        {"type": "object", "properties": {"type": {"const": "same"}}, "required": ["left"]},
        {"type": "object", "properties": {"type": {"const": "same"}}, "required": ["right"]},
    ]}
    with pytest.raises(PluginInputValidationError) as raised:
        validate_plugin_arguments("ambiguous", {"type": "same"}, schema)
    assert raised.value.validator == "oneOf"


def test_other_constraints_keep_the_reason_and_path():
    schema = {"type": "object", "properties": {"count": {"type": "integer", "minimum": 1}}}
    with pytest.raises(PluginInputValidationError) as raised:
        validate_plugin_arguments("count", {"count": 0}, schema)
    error = raised.value
    assert "count" in error.localized_message("zh")
    assert error.reason in error.localized_message("zh")
