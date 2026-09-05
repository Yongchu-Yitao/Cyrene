async def test_run_event_timeline_keeps_intermediate_attachment_at_terminal_boundary():
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
    await run.publish({
        "_seq": 2,
        "runId": run.run_id,
        "type": "intermediate_message",
        "message": attachment_message,
    })

    messages = run.terminal_timeline_messages([])

    assert len(messages) == 1
    assert all(messages[0][key] == value for key, value in attachment_message.items())
    assert messages == run.events[-1]["timeline"]["messages"]
    assert messages[0] is not attachment_message
    assert messages[0]["attachments"] is not attachment_message["attachments"]


async def test_terminal_timeline_interleaves_messages_and_activities_by_run_events():
    from cyrene.workbench.chat.chat_runs import ChatRun

    run = ChatRun("chat_ordered", {"type": "ack"})
    events = [
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
    ]
    for event in events:
        await run.publish(event)
    live_records = run.timeline.messages()
    await run.publish({"type": "run_finalizing"})

    messages = run.terminal_timeline_messages([])

    assert [message["id"] for message in messages] == [record["id"] for record in live_records]
    assert len(messages) == 4
    assert messages[0]["id"] == "message_before_first_tool"
    assert messages[1]["activityCard"] is True
    assert messages[1]["trace"][0]["toolCallId"] == "call_read"
    assert messages[2]["id"] == "message_before_second_tool"
    assert messages[3]["activityCard"] is True
    assert messages[3]["trace"][0]["toolCallId"] == "call_test"
    assert all(message["status"] == "completed" for message in messages)
    assert messages == run.timeline.messages()
