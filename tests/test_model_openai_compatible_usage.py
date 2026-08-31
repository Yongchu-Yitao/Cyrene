from cyrene.plugins.builtin.cyrene_model._shared import _openai_payload
from cyrene.plugins.builtin.cyrene_model.openai_compatible import (
    OPENAI_COMPATIBLE_PROVIDER,
)


def test_openai_compatible_stream_requests_usage() -> None:
    assert OPENAI_COMPATIBLE_PROVIDER.include_stream_usage is True

    payload = _openai_payload(
        {"messages": [{"role": "user", "content": "hello"}]},
        OPENAI_COMPATIBLE_PROVIDER,
        "local-model",
    )

    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
