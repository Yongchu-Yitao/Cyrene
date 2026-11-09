from dataclasses import FrozenInstanceError

import pytest

from cyrene.workbench.http.workbench.chat_routes.send_request import SendOptions, SendOrigin


@pytest.mark.parametrize("timestamp,expected", [(None, 0.0), ("123.5", 123.5), ("invalid", 0.0), ({}, 0.0)])
def test_origin_retains_request_normalization_and_strict_origin_flag(timestamp, expected):
    origin = SendOrigin.parse({"clientRequestId": " req ", "clientSendEpochMs": timestamp,
                               "uiInstanceId": " ui ", "agentOriginated": "true",
                               "sourceSessionId": " session ", "conversationSource": " API "})
    assert origin.client_request_id == "req"
    assert origin.client_send_epoch_ms == expected
    assert origin.agent_originated is False
    assert origin.conversation_source == "API"
    assert origin.origin_session_id == "session"
    with pytest.raises(FrozenInstanceError):
        origin.client_request_id = "changed"


def test_options_preserve_false_missing_truthiness_and_payload_identity():
    activations = {"skills": []}
    options = SendOptions.parse({"contextActivations": activations, "stream": "false", "retry": False,
                                 "mode": " FULL ", "model": " Model-ID ", "reasoningEffort": " HIGH ",
                                 "lang": " ZH ", "voiceCommand": 1})
    assert options.requested_context_activations is activations
    assert options.wants_stream is True  # Existing bool coercion, not a new parser.
    assert options.retry is False
    assert options.requested_mode == "full"
    assert options.requested_model == "Model-ID"
    assert options.requested_effort == "high"
    assert options.lang == "zh"
    assert options.voice_command is False
    assert SendOptions.parse({}).requested_context_activations is None
    assert SendOptions.parse({"contextActivations": False}).requested_context_activations is False


@pytest.mark.parametrize("agent,kind,expected", [
    (True, "task_notification", "task_notification"),
    (True, "welcome_refresh", "welcome_refresh"),
    (False, "welcome_refresh", ""),
    (False, "task_notification", ""),
    (True, "unknown", ""),
])
def test_notification_type_survives_send_and_replay(agent, kind, expected):
    from cyrene.workbench.http.workbench.chat_routes.send_input import SendInput, append_user_message
    from cyrene.workbench.chat.chat_application import public_message
    origin = SendOrigin.parse({"agentOriginated": agent, "agentMessageKind": kind, "clientRequestId": "notice"})
    assert origin.agent_message_kind == expected
    chat = {"messages": []}
    source = SendInput("status data", "status data", "", [], [])
    turn = append_user_message(chat, chat["messages"], source, origin, "now", lambda _: "msg")
    assert public_message(turn.user_entry).get("agentMessageKind", "") == expected
    if agent:
        replay = append_user_message(chat, chat["messages"], source, origin, "later", lambda _: "other")
        assert replay.user_entry is turn.user_entry
        assert len(chat["messages"]) == 1
