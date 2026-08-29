"""Service-level tests for the :::button block_actions protocol helpers."""

from cyrene.workbench.chat.chat_application import (
    _button_blocks as _iter_button_blocks,
    disable_button_block,
    has_button_block,
)

BUTTON_CONTENT = """说明

:::button
label: 开始翻译
action_id: translate_start
style: primary
mode: model
value: zh->en
:::

:::button
label: 清空输入
action_id: input_clear
mode: local
:::
"""

NESTED_CONTENT = """:::actions
  :::button
  label: A
  action_id: act_a
  mode: model
  :::
  :::button
  label: B
  action_id: act_b
  disabled: true
  :::
:::
"""


def test_iter_button_blocks_finds_standalone_and_nested():
    blocks = _iter_button_blocks(BUTTON_CONTENT)
    assert [(action, label) for _, action, label in blocks] == [
        ("translate_start", "开始翻译"),
        ("input_clear", "清空输入"),
    ]
    nested = _iter_button_blocks(NESTED_CONTENT)
    assert [action for _, action, _ in nested] == ["act_a", "act_b"]


def test_has_button_block():
    assert has_button_block(BUTTON_CONTENT, "translate_start")
    assert has_button_block(NESTED_CONTENT, "act_b")
    assert not has_button_block(BUTTON_CONTENT, "missing")


def test_disable_button_block_flips_disabled_and_keeps_other_blocks():
    updated, label = disable_button_block(BUTTON_CONTENT, "translate_start")
    assert updated is not None
    assert label == "开始翻译"
    assert "disabled: true" in updated
    # The other button stays untouched.
    other = _iter_button_blocks(updated)
    assert len(other) == 2
    input_clear_block = next(raw for raw, action, _ in other if action == "input_clear")
    assert "disabled: true" not in input_clear_block


def test_disable_button_block_is_idempotent():
    updated, _ = disable_button_block(BUTTON_CONTENT, "translate_start")
    assert updated is not None
    again, _ = disable_button_block(updated, "translate_start")
    assert again is None  # duplicate click


def test_disable_button_block_unknown_action():
    updated, label = disable_button_block(BUTTON_CONTENT, "nope")
    assert updated is None
    assert label == ""


def test_disable_button_block_already_disabled_block():
    updated, label = disable_button_block(NESTED_CONTENT, "act_b")
    assert updated is None
    assert label == "B"
