"""One-shot LLM naming for visible Workbench sessions."""

from __future__ import annotations

import re
from typing import Any


async def generate_session_title(
    user_message: str,
    *,
    limit: int = 60,
    candidate: dict[str, Any] | None = None,
) -> str:
    """Generate one compact UI title from a session's opening user message."""
    from agent.plugin import active_plugin_service
    from cyrene.model_runtime.messages import assistant_text

    prompt = str(user_message or "").strip()
    if not prompt:
        return ""
    length_instruction = (
        f"Use no more than {max(1, int(limit))} characters. "
        "Prefer no more than 12 words or 24 Chinese characters."
    )
    gateway = active_plugin_service("model")
    if gateway is None:
        raise RuntimeError("Model Provider Plugins are not available")
    response = await gateway.complete(
        [
            {
                "role": "system",
                "content": (
                    "Generate a concise title for a chat session from its opening user "
                    "message. Use the user's language. Return only the title as plain text "
                    "on one line. " + length_instruction + " Do not use quotes, markdown, "
                    "generic labels, or trailing punctuation in the title."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        route="secondary",
        caller="workbench_session_namer",
        session_id="workbench-session-naming",
        model_identity=candidate,
    )
    raw = assistant_text(response).strip()
    title = re.sub(r"\s+", " ", raw).strip()
    title = title.strip("\"'`#*_ ").rstrip("。！？!?；;，,").strip()
    return title


__all__ = ["generate_session_title"]
