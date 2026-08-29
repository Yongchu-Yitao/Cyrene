def test_run_event_timeline_keeps_intermediate_attachment_at_terminal_boundary():
    from cyrene.workbench.chat.chat_runs import ChatRun

    attachment_message = {
        "id": "assistant_attachment",
        "role": "assistant",
        "content": "file ready",
        "intermediate": True,
        "roundId": "run_current",
        "attachments": [{"name": "snake.html", "contentType": "text/html"}],
    }
    run = ChatRun("chat_current", {"type": "ack"})
    attachment_message["roundId"] = run.run_id
    run.events.append({
        "_seq": 2,
        "runId": run.run_id,
        "type": "intermediate_message",
        "message": attachment_message,
    })

    messages = run.terminal_timeline_messages([])

    assert messages == [attachment_message]
    assert messages[0] is not attachment_message
    assert messages[0]["attachments"] is not attachment_message["attachments"]


def test_terminal_timeline_interleaves_messages_and_activities_by_run_events():
    from cyrene.workbench.chat.chat_runs import ChatRun

    run = ChatRun("chat_ordered", {"type": "ack"})
    run.events.extend([
        {
            "_seq": 2,
            "type": "intermediate_message",
            "message": {
                "id": "message_before_first_tool",
                "role": "assistant",
                "content": "先检查文件。",
                "createdAt": "2026-08-29T08:00:00+00:00",
                "intermediate": True,
                "roundId": run.run_id,
                "opensActivity": True,
            },
        },
        {
            "_seq": 3,
            "type": "tool.started",
            "payload": {"toolCallId": "call_read", "name": "Read"},
        },
        {
            "_seq": 4,
            "type": "intermediate_message",
            "message": {
                "id": "message_before_second_tool",
                "role": "assistant",
                "content": "再检查测试。",
                "createdAt": "2026-08-29T08:00:02+00:00",
                "intermediate": True,
                "roundId": run.run_id,
            },
        },
        {
            "_seq": 5,
            "type": "tool_call_started",
            "tool_call_id": "call_test",
            "tool": "Bash",
        },
    ])
    activities = [
        {
            "id": "activity_read",
            "role": "assistant",
            "content": "",
            "createdAt": "2026-08-29T08:00:01+00:00",
            "activityCard": True,
            "intermediate": True,
            "trace": [{"toolCallId": "call_read", "kind": "tool"}],
        },
        {
            "id": "activity_test",
            "role": "assistant",
            "content": "",
            "createdAt": "2026-08-29T08:00:03+00:00",
            "activityCard": True,
            "intermediate": True,
            "trace": [{"toolCallId": "call_test", "kind": "tool"}],
        },
    ]

    messages = run.terminal_timeline_messages(activities)

    assert [message["id"] for message in messages] == [
        "message_before_first_tool",
        "activity_read",
        "message_before_second_tool",
        "activity_test",
    ]
    assert "opensActivity" not in messages[0]
