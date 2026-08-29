def test_checkpointed_attachment_message_is_returned_for_its_run():
    from cyrene.workbench.http.workbench.chat_routes.run_send_routes import _SendOperation

    attachment_message = {
        "id": "assistant_attachment",
        "role": "assistant",
        "content": "file ready",
        "intermediate": True,
        "roundId": "run_current",
        "attachments": [{"name": "snake.html", "contentType": "text/html"}],
    }
    chat = {
        "messages": [
            attachment_message,
            {
                "id": "assistant_previous",
                "role": "assistant",
                "intermediate": True,
                "roundId": "run_previous",
                "attachments": [{"name": "old.html"}],
            },
            {
                "id": "user_current",
                "role": "user",
                "roundId": "run_current",
                "content": "make a game",
            },
        ]
    }

    messages = _SendOperation._checkpointed_run_messages(chat, "run_current")

    assert messages == [attachment_message]
    assert messages[0] is not attachment_message
    assert messages[0]["attachments"] is not attachment_message["attachments"]

