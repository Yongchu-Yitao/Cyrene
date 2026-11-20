import asyncio
import shutil
from pathlib import Path

from cyrene.plugins.builtin.cyrene_response_style.context import COMMON, DETAIL, TONE
from test_task_contexts import make_session


def test_reply_preferences_refresh_and_project_once_per_request(tmp_path, monkeypatch):
    source = Path(__file__).parents[1] / "src/cyrene/plugins/builtin/cyrene_response_style"
    shutil.copytree(source, tmp_path / "plugins" / source.name)
    settings = {}
    monkeypatch.setattr("cyrene.platform.settings_store.get", settings.get)
    inputs = []

    async def model(args, _context):
        inputs.append(args["messages"])
        return {"content": "OK", "tool_calls": []}

    session = make_session(tmp_path, model)
    try:
        for detail, tone in [
            ("inherit", "inherit"), ("concise", "warm"), ("concise", "warm"),
            ("balanced", "direct"), ("detailed", "natural"), ("inherit", "warm"),
            ("detailed", "inherit"), ("inherit", "inherit"),
        ]:
            settings.update(reply_detail=detail, reply_tone=tone)
            session.submit("Hello")
            asyncio.run(session.drain())
            messages = inputs[-1]
            assert str(messages).count("<response_style>") == 1
            system = next(m["content"] for m in messages if "<response_style>" in str(m)
                          and m["role"] == "system")
            assert COMMON in system
            for choices, selected in [(DETAIL, detail), (TONE, tone)]:
                for value, instruction in choices.items():
                    assert (instruction in system) == (value == selected)
        assert len(inputs) == 8
    finally:
        session.close()
