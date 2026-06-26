"""Verify the `quit(reply=...)` mechanism: the model delivers its final answer
through the quit tool argument so the common "done with an answer" turn skips the
tools=None reconstruction call (a prompt-cache prefix-root break)."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Patch missing deps before any cyrene import
sys.modules.setdefault("PIL", MagicMock())
sys.modules["PIL"].Image = MagicMock()
sys.modules.setdefault("pypdf", MagicMock())


def _quit_call(arguments: str) -> dict:
    return {"tool_calls": [{"function": {"name": "quit", "arguments": arguments}}]}


def test_quit_reply_extracted_from_arguments():
    from cyrene.agent.agent import _quit_reply_from_response

    assert _quit_reply_from_response(_quit_call(json.dumps({"reply": "最终答复"}))) == "最终答复"
    # leading/trailing whitespace is trimmed
    assert _quit_reply_from_response(_quit_call(json.dumps({"reply": "  hi  "}))) == "hi"


def test_quit_reply_absent_or_invalid_returns_empty():
    from cyrene.agent.agent import _quit_reply_from_response

    # quit without a reply argument -> falls back to reconstruction (empty here)
    assert _quit_reply_from_response(_quit_call("{}")) == ""
    # non-quit tool call
    assert _quit_reply_from_response(
        {"tool_calls": [{"function": {"name": "use_tools", "arguments": json.dumps({"task": "x"})}}]}
    ) == ""
    # malformed JSON arguments must not raise
    assert _quit_reply_from_response(_quit_call("{bad json")) == ""
    # no tool calls at all
    assert _quit_reply_from_response({"content": "hello"}) == ""
    # reply present but not a string
    assert _quit_reply_from_response(_quit_call(json.dumps({"reply": 123}))) == ""


def test_all_quit_tool_defs_expose_reply_param():
    """Every quit definition the model can see (phase1 light set, deep-research
    light set, and the full registry set used in phase2) must carry the optional
    `reply` string param — otherwise the model cannot deliver its answer through it."""
    from cyrene.agent.state import _LIGHT_TOOL_DEFS, _DEEP_RESEARCH_LIGHT_TOOL_DEFS
    from cyrene.tool_legacy import TOOL_DEFS

    def _quit_def(defs):
        return next(d for d in defs if d["function"]["name"] == "quit")

    for defs in (_LIGHT_TOOL_DEFS, _DEEP_RESEARCH_LIGHT_TOOL_DEFS, TOOL_DEFS):
        props = _quit_def(defs)["function"]["parameters"]["properties"]
        assert "reply" in props, "quit def is missing the reply param"
        assert props["reply"]["type"] == "string"
        # reply is intentionally optional: system-initiated rounds may quit silently.
        assert "required" not in _quit_def(defs)["function"]["parameters"]


def test_assistant_text_empty_for_quit_so_reply_is_used():
    """The wiring assumption: a bare quit call (no content) makes _assistant_text
    return "", which is what triggers _ensure_text_reply to fall back to the quit
    reply instead of the model's (absent) prose."""
    from cyrene.llm import _assistant_text

    assert _assistant_text(_quit_call(json.dumps({"reply": "x"}))) == ""
