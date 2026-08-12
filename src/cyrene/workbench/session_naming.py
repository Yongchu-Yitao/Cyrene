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
    from cyrene.agent.model_service import call_agent_model
    from cyrene.model_runtime.messages import assistant_text

    prompt = str(user_message or "").strip()
    if not prompt:
        return ""
    length_instruction = (
        f"Use no more than {max(1, int(limit))} characters. "
        "Prefer no more than 12 words or 24 Chinese characters."
    )
    response = await call_agent_model(
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
        tools=None,
        max_tokens=None,
        caller="workbench_session_namer",
        candidates=[candidate] if candidate is not None else None,
        thinking="low",
    )
    raw = assistant_text(response).strip()
    title = re.sub(r"\s+", " ", raw).strip()
    title = title.strip("\"'`#*_ ").rstrip("。！？!?；;，,").strip()
    return title


__all__ = ["generate_session_title"]
