from __future__ import annotations


def test_startup_recovers_crashed_running_chat_and_clears_stale_question(monkeypatch):
    from webui import routes_workbench_chat as chat_mod
    from webui.workbench_chat_runs import ChatRunManager

    payload = {
        "chats": [
            {
                "id": "chat_crashed",
                "status": "running",
                "pendingQuestion": {"id": "stale", "text": "旧问题"},
            },
            {
                "id": "chat_waiting",
                "status": "idle",
                "pendingQuestion": {"id": "valid", "text": "仍待回答"},
            },
        ]
    }
    written = []
    monkeypatch.setattr(chat_mod, "_read_chats_store", lambda: payload)
    monkeypatch.setattr(chat_mod, "_write_chats_store", lambda value: written.append(value))

    ChatRunManager().startup()

    assert payload["chats"][0]["status"] == "idle"
    assert "pendingQuestion" not in payload["chats"][0]
    assert payload["chats"][1]["pendingQuestion"]["id"] == "valid"
    assert written == [payload]
