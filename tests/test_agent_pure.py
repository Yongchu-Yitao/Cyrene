"""Pure-function tests for the agent/ subpackage — zero mocking needed.

All functions tested here are pure data-transformation helpers.  They must
remain stable after the agent.py → agent/ split.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ===========================================================================
# Main-agent operating contract  (agent/prompts.py)
# ===========================================================================

def test_main_agent_prompt_requires_final_deliverable_verification():
    from cyrene.agent.prompts import _MAIN_AGENT_PROMPT

    assert "compare the result with the original request" in _MAIN_AGENT_PROMPT
    assert "inspect the final deliverables yourself" in _MAIN_AGENT_PROMPT
    assert "report any failed or unavailable checks" in _MAIN_AGENT_PROMPT


def test_agent_generated_external_skills_require_successful_registration():
    from cyrene.agent.prompts import _EXECUTION_SYSTEM_PROMPT, _MAIN_AGENT_PROMPT

    assert "invoke `skill.install`" in _MAIN_AGENT_PROMPT
    assert "only a draft until `skill.install` succeeds" in _MAIN_AGENT_PROMPT
    assert "writing `SKILL.md` alone does not register or enable it" in _EXECUTION_SYSTEM_PROMPT


def test_user_facing_prompts_hide_internal_runtime_details():
    from cyrene.agent.prompts import _EXECUTION_SYSTEM_PROMPT, _MAIN_AGENT_PROMPT

    for prompt in (_MAIN_AGENT_PROMPT, _EXECUTION_SYSTEM_PROMPT):
        assert "Never expose internal tool, function, gateway" in prompt
        assert "runtime risk tiers and codes (including `R0`–`R4`)" in prompt
        assert 'labels such as "R2 operation" as internal-only' in prompt
        assert "Never include them in user-visible text" in prompt
        assert "state only the concrete action that needs their confirmation" in prompt
        assert "Describe work in natural, outcome-oriented language" in prompt
        assert "omit implementation details about the agent runtime itself" in prompt
        assert "do not paste raw internal errors or identifiers" in prompt


def test_main_agent_prompt_proactively_consults_knowledge_base():
    from cyrene.agent.prompts import (
        _EXECUTION_SYSTEM_PROMPT,
        _MAIN_AGENT_PROMPT,
        _PHASE1_DECISION_PROMPT,
    )

    assert "`knowledge_tools`" in _MAIN_AGENT_PROMPT
    assert "`knowledge.search`" in _MAIN_AGENT_PROMPT
    assert "`knowledge.list_documents`" in _MAIN_AGENT_PROMPT
    assert "`knowledge.library.search`" in _MAIN_AGENT_PROMPT
    assert "`knowledge.library.update_metadata`" in _MAIN_AGENT_PROMPT
    assert "user-specific, or project-specific" in _PHASE1_DECISION_PROMPT
    assert "`knowledge.search`" in _EXECUTION_SYSTEM_PROMPT
    assert "`knowledge.library.search`" in _EXECUTION_SYSTEM_PROMPT


def test_phase1_prompt_requires_bounded_plan_before_execution():
    from cyrene.agent.prompts import _PHASE1_DECISION_PROMPT
    from cyrene.agent.state import _LIGHT_TOOL_DEFS
    from cyrene.tooling.wire import _USE_TOOLS_DEF

    assert "bounded execution-planning pass" in _PHASE1_DECISION_PROMPT
    assert "observable completion evidence" in _PHASE1_DECISION_PROMPT
    assert "material risks or fallbacks" in _PHASE1_DECISION_PROMPT
    assert "ordered initial steps/tools" in _PHASE1_DECISION_PROMPT
    assert "Do not expose private chain-of-thought" in _PHASE1_DECISION_PROMPT
    for tool_def in (_LIGHT_TOOL_DEFS[0], _USE_TOOLS_DEF):
        parameters = tool_def["function"]["parameters"]
        assert parameters["required"] == ["task", "execution_brief"]
        assert "execution_brief" in parameters["properties"]


def test_browser_prompts_prefer_visible_clicks_over_direct_url_navigation():
    from cyrene.agent.prompts import _EXECUTION_SYSTEM_PROMPT, _MAIN_AGENT_PROMPT

    for prompt in (_MAIN_AGENT_PROMPT, _EXECUTION_SYSTEM_PROMPT):
        assert "prefer" in prompt.lower()
        assert "browser.snapshot" in prompt
        assert "browser.click_ref" in prompt
        assert "browser.click_text" not in prompt
        assert "exact URL requested by the user" in prompt

    assert "Prefer clicking visible page UI over navigating by URL" in _MAIN_AGENT_PROMPT
    assert "reconstructed URLs" in _MAIN_AGENT_PROMPT
    assert "call it repeatedly with different URLs" not in _MAIN_AGENT_PROMPT


# ===========================================================================
# report_export_filename  (modules/deep_research.py)
# ===========================================================================

def test_report_export_filename_basic():
    from cyrene.agent.research import report_export_filename
    assert report_export_filename("round_12345") == "round_12345.pdf"


def test_report_export_filename_sanitized():
    from cyrene.agent.research import report_export_filename
    result = report_export_filename("round_abc/def:ghi")
    assert "/" not in result
    assert result.endswith(".pdf")


def test_report_export_filename_fallback():
    from cyrene.agent.research import report_export_filename
    result = report_export_filename("", "my-report")
    assert result == "my-report.pdf"


# ===========================================================================
# report_title_from_text  (modules/deep_research.py)
# ===========================================================================

def test_report_title_from_heading():
    from cyrene.agent.research import report_title_from_text
    text = "# My Research Report\n\nSome content."
    assert report_title_from_text(text) == "My Research Report"


def test_report_title_from_first_line():
    from cyrene.agent.research import report_title_from_text
    text = "Just a plain line\nSecond line"
    title = report_title_from_text(text)
    assert title == "Just a plain line"


def test_report_title_fallback():
    from cyrene.agent.research import report_title_from_text
    assert report_title_from_text("") == "Deep Research Report"
    assert report_title_from_text(None) == "Deep Research Report"


# ===========================================================================
# _fallback_label  (agent/message.py)
# ===========================================================================

def test_fallback_label_truncates():
    from cyrene.agent.message import _fallback_label
    long_text = "a" * 100
    assert len(_fallback_label(long_text)) == 48


def test_fallback_label_strips_punctuation():
    from cyrene.agent.message import _fallback_label
    assert _fallback_label("  [Hello] ", limit=10) == "Hello"


def test_fallback_label_empty():
    from cyrene.agent.message import _fallback_label
    assert _fallback_label("", limit=10) == "Untitled"
    assert _fallback_label(None, limit=10) == "Untitled"


# ===========================================================================
# Round timestamp helpers  (agent/message.py)
# ===========================================================================

def test_round_epoch_ms_valid():
    from cyrene.agent.message import _round_epoch_ms
    assert _round_epoch_ms("round_1700000000000") == 1700000000000


def test_round_epoch_ms_invalid():
    from cyrene.agent.message import _round_epoch_ms
    assert _round_epoch_ms("round_abc") is None
    assert _round_epoch_ms("") is None
    assert _round_epoch_ms(None) is None


def test_round_started_iso_valid():
    from cyrene.agent.message import _round_started_iso
    result = _round_started_iso("round_1700000000000")
    assert result is not None
    assert "2023-11-14" in result


def test_round_started_iso_invalid():
    from cyrene.agent.message import _round_started_iso
    assert _round_started_iso("") is None
    assert _round_started_iso("bad") is None


def test_round_title_prefers_entry_title():
    from cyrene.agent.message import _round_title_from_entry
    entry = {"title": "My Custom Title", "last_user": "ignored"}
    assert _round_title_from_entry(entry) == "My Custom Title"


def test_round_title_falls_back():
    from cyrene.agent.message import _round_title_from_entry
    entry = {"last_user": "User said something"}
    title = _round_title_from_entry(entry)
    assert "User said" in title


# ===========================================================================
# Message identity  (agent/message.py)
# ===========================================================================

def test_ensure_message_identity_adds_ids():
    from cyrene.agent.message import _ensure_message_identity
    messages = [{"role": "user", "content": "hi"}]
    result = _ensure_message_identity(messages)
    assert len(result) == 1
    assert result[0]["message_id"].startswith("msg_")


def test_ensure_message_identity_preserves_existing():
    from cyrene.agent.message import _ensure_message_identity
    messages = [{"role": "user", "content": "hi", "message_id": "msg_existing"}]
    result = _ensure_message_identity(messages)
    assert result[0]["message_id"] == "msg_existing"


# ===========================================================================
# Dedup / merge  (agent/message.py)
# ===========================================================================

def test_dedupe_keeps_latest_at_original_position():
    from cyrene.agent.message import _dedupe_messages_by_id
    messages = [
        {"message_id": "m1", "content": "first"},
        {"message_id": "m2", "content": "second"},
        {"message_id": "m1", "content": "updated"},
    ]
    result = _dedupe_messages_by_id(messages)
    assert len(result) == 2
    assert result[0]["message_id"] == "m1"
    assert result[0]["content"] == "updated"
    assert result[1]["message_id"] == "m2"


def test_merge_incoming_replaces_existing():
    from cyrene.agent.message import _merge_message_sequence
    existing = [
        {"message_id": "m1", "content": "old", "round_id": "r1"},
        {"message_id": "m2", "content": "keep", "round_id": "r1"},
    ]
    incoming = [
        {"message_id": "m1", "content": "new", "round_id": "r1"},
    ]
    result = _merge_message_sequence(existing, incoming)
    assert len(result) == 2
    assert result[0]["content"] == "new"
    assert result[1]["content"] == "keep"


def test_merge_appends_new_messages():
    from cyrene.agent.message import _merge_message_sequence
    existing = [{"message_id": "m1", "content": "a"}]
    incoming = [{"message_id": "m2", "content": "b"}]
    result = _merge_message_sequence(existing, incoming)
    assert len(result) == 2
    assert result[1]["message_id"] == "m2"


def test_live_message_equivalence_uses_tool_call_identity():
    from cyrene.agent.session import _messages_equivalent

    left = {
        "role": "assistant",
        "message_id": "m1",
        "round_id": "round_1",
        "content": "checking",
        "tool_calls": [{"id": "call_1", "function": {"name": "WebSearch"}}],
    }
    right = {
        "role": "assistant",
        "message_id": "m2",
        "round_id": "round_1",
        "content": "checking",
        "tool_calls": [{"id": "call_1", "function": {"name": "WebSearch"}}],
    }

    assert _messages_equivalent(left, right)


def test_merge_live_block_dedupes_repeated_tool_call_batches():
    from cyrene.agent.session import _merge_live_block

    existing = [
        {
            "role": "assistant",
            "message_id": "m1",
            "round_id": "round_1",
            "content": "checking",
            "tool_calls": [{"id": "call_1", "function": {"name": "WebSearch"}}],
        },
        {"role": "tool", "message_id": "t1", "round_id": "round_1", "tool_call_id": "call_1", "content": "old"},
    ]
    incoming = [
        {
            "role": "assistant",
            "message_id": "m2",
            "round_id": "round_1",
            "content": "checking",
            "tool_calls": [{"id": "call_1", "function": {"name": "WebSearch"}}],
        },
        {"role": "tool", "message_id": "t2", "round_id": "round_1", "tool_call_id": "call_1", "content": "new"},
    ]

    result = _merge_live_block(existing, incoming)

    assert len(result) == 2
    assert result[0]["message_id"] == "m2"
    assert result[1]["message_id"] == "t2"
    assert result[1]["content"] == "new"


# ===========================================================================
# JSON extraction  (agent/message.py)
# ===========================================================================

def test_extract_json_object_plain():
    from cyrene.agent.message import _extract_json_object
    assert _extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_fenced():
    from cyrene.agent.message import _extract_json_object
    result = _extract_json_object('```json\n{"a": 1}\n```')
    assert result == {"a": 1}


def test_extract_json_object_invalid():
    from cyrene.agent.message import _extract_json_object
    assert _extract_json_object("not json") == {}


# ===========================================================================
# Tool result helpers  (agent/message.py)
# ===========================================================================

def test_tool_result_requests_input():
    from cyrene.agent.message import _tool_result_requests_user_input
    assert _tool_result_requests_user_input('{"status": "awaiting_user"}')


def test_tool_result_not_requesting():
    from cyrene.agent.message import _tool_result_requests_user_input
    assert not _tool_result_requests_user_input('{"status": "ok"}')
    assert not _tool_result_requests_user_input("")


# ===========================================================================
# Replaceable live message  (agent/message.py)
# ===========================================================================

def test_is_replaceable_matches_round():
    from cyrene.agent.message import _is_replaceable_live_message
    entry = {"round_id": "round_123", "content": "hi"}
    assert _is_replaceable_live_message(entry, "round_123")


def test_is_replaceable_wrong_round():
    from cyrene.agent.message import _is_replaceable_live_message
    entry = {"round_id": "round_123", "content": "hi"}
    assert not _is_replaceable_live_message(entry, "round_456")


def test_is_replaceable_guidance_not_replaced():
    from cyrene.agent.message import _is_replaceable_live_message
    entry = {"round_id": "round_123", "content": "hi", "queued_guidance_id": "guidance_1"}
    assert not _is_replaceable_live_message(entry, "round_123")


# ===========================================================================
# Message suffix after persisted prefix  (agent/message.py)
# ===========================================================================

def test_suffix_by_message_id():
    from cyrene.agent.message import _message_suffix_after_persisted_prefix
    base = [{"message_id": "m1"}, {"message_id": "m2"}]
    messages = [{"message_id": "m1"}, {"message_id": "m2"}, {"message_id": "m3", "content": "new"}]
    suffix = _message_suffix_after_persisted_prefix(messages, base, 0)
    assert len(suffix) == 1
    assert suffix[0]["message_id"] == "m3"


def test_suffix_fallback_prefix_len():
    from cyrene.agent.message import _message_suffix_after_persisted_prefix
    base = [{"role": "user"}, {"role": "assistant"}]
    messages = [{"role": "user"}, {"role": "assistant"}, {"role": "user", "content": "new"}]
    suffix = _message_suffix_after_persisted_prefix(messages, base, 2)
    assert len(suffix) == 1
    assert suffix[0]["content"] == "new"


# ===========================================================================
# extract_new_references  (modules/deep_research.py)
# ===========================================================================

def test_extract_new_references_with_heading():
    from cyrene.agent.research import extract_new_references
    text = "Some body text.\n\n## New References\n[1] https://example.com/a\n[2] https://example.com/b"
    body, refs = extract_new_references(text)
    assert "Some body" in body
    assert len(refs) == 2
    assert "[1]" in refs[0]
    assert "[2]" in refs[1]


def test_extract_new_references_chinese_heading():
    from cyrene.agent.research import extract_new_references
    text = "正文内容。\n\n## 参考文献\n[1] https://example.com/c"
    body, refs = extract_new_references(text)
    assert "正文" in body
    assert len(refs) == 1


def test_extract_new_references_orphan_fallback():
    from cyrene.agent.research import extract_new_references
    text = "Some body text.\n[1] https://example.com/x\n[2] https://example.com/y"
    body, refs = extract_new_references(text)
    assert len(refs) >= 1
    assert "[1]" in refs[0]


def test_extract_new_references_no_refs():
    from cyrene.agent.research import extract_new_references
    text = "Just body text, no references."
    body, refs = extract_new_references(text)
    assert body == "Just body text, no references."
    assert refs == []


# ===========================================================================
# strip_stray_references  (modules/deep_research.py)
# ===========================================================================

def test_strip_stray_references_removes_ref_block():
    from cyrene.agent.research import strip_stray_references
    text = "Some content.\n## References\n[1] example.com\nMore content."
    result = strip_stray_references(text)
    assert "Some content." in result
    assert "## References" not in result
    assert "[1]" not in result
    assert "More content." in result


def test_strip_stray_references_no_ref_block():
    from cyrene.agent.research import strip_stray_references
    text = "Clean content without references."
    result = strip_stray_references(text)
    assert result == "Clean content without references."


# ===========================================================================
# deduplicate_references  (modules/deep_research.py)
# ===========================================================================

def test_deduplicate_references_by_url():
    from cyrene.agent.research import deduplicate_references
    entries = [
        "[1] https://example.com/a",
        "[2] https://example.com/b",
        "[3] https://example.com/a",
    ]
    deduped, mapping = deduplicate_references(entries)
    assert len(deduped) == 2
    assert mapping[3] == 1


def test_deduplicate_references_no_duplicates():
    from cyrene.agent.research import deduplicate_references
    entries = [
        "[1] https://example.com/a",
        "[2] https://example.com/b",
    ]
    deduped, mapping = deduplicate_references(entries)
    assert len(deduped) == 2
    assert mapping == {1: 1, 2: 2}


# ===========================================================================
# fill_missing_references  (modules/deep_research.py)
# ===========================================================================

def test_fill_missing_references_adds_placeholder():
    from cyrene.agent.research import fill_missing_references
    body = "See [1] and [3] for details."
    refs = ["[1] Source A"]
    result = fill_missing_references(body, refs)
    assert len(result) == 2


def test_fill_missing_references_all_present():
    from cyrene.agent.research import fill_missing_references
    body = "See [1] for details."
    refs = ["[1] Source A"]
    result = fill_missing_references(body, refs)
    assert len(result) == 1
    assert result == refs


# ===========================================================================
# renumber_citations  (modules/deep_research.py)
# ===========================================================================

def test_renumber_citations():
    from cyrene.agent.research import renumber_citations
    text = "See [1] and [3] for details."
    mapping = {1: 1, 3: 2}
    result = renumber_citations(text, mapping)
    assert "[1]" in result
    assert "[2]" in result
    assert "[3]" not in result


# ===========================================================================
# assemble_report  (modules/deep_research.py)
# ===========================================================================

def test_assemble_report_basic():
    from cyrene.agent.research import assemble_report
    sections = ["## Intro\nContent here.", "## Analysis\nMore content."]
    refs = ["[1] https://example.com"]
    outline = {"title": "Test Report"}
    report = assemble_report(sections, refs, outline)
    assert "# Test Report" in report
    assert "## Intro" in report
    assert "## 参考文献" in report
    assert "[1]" in report


def test_assemble_report_with_dedup_mapping():
    from cyrene.agent.research import assemble_report
    sections = ["## Intro\nSee [2] for details."]
    refs = ["[1] Source A"]
    outline = {"title": "Report"}
    mapping = {2: 1}
    report = assemble_report(sections, refs, outline, dedup_mapping=mapping)
    assert "[2]" not in report
    assert "[1]" in report


# ===========================================================================
# parse_length_preference  (modules/deep_research.py)
# ===========================================================================

def test_parse_length_short():
    from cyrene.agent.research import parse_length_preference
    msgs = [{"role": "user", "content": "给我一个短报告，10页左右"}]
    assert parse_length_preference(msgs) == "short"


def test_parse_length_long():
    from cyrene.agent.research import parse_length_preference
    msgs = [{"role": "user", "content": "写一个长报告，30页"}]
    assert parse_length_preference(msgs) == "long"


def test_parse_length_medium_default():
    from cyrene.agent.research import parse_length_preference
    msgs = [{"role": "user", "content": "Just a normal question"}]
    assert parse_length_preference(msgs) == "medium"


def test_parse_length_prefers_latest():
    from cyrene.agent.research import parse_length_preference
    msgs = [
        {"role": "user", "content": "一个短报告"},
        {"role": "assistant", "content": "OK"},
        {"role": "user", "content": "算了写长一点，30页"},
    ]
    assert parse_length_preference(msgs) == "long"
