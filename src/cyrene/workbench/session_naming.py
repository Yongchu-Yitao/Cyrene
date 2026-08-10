"""One-shot LLM naming for visible Workbench sessions."""

from __future__ import annotations

import json
import re


async def generate_session_title(user_message: str, *, limit: int = 60) -> str:
    """Generate one compact UI title from a session's opening user message."""
    from cyrene.agent.model_service import call_agent_model
    from cyrene.model_runtime.messages import assistant_text

    prompt = str(user_message or "").strip()
    if not prompt:
        return ""
    response = await call_agent_model(
        [
            {
                "role": "system",
                "content": (
                    "Generate a concise title for a chat session from its opening user "
                    "message. Use the user's language. Return one JSON object with only "
                    "a string field named title. Use no more than 12 words or 24 Chinese "
                    "characters. Do not use quotes, markdown, generic labels, or trailing "
                    "punctuation in the title."
                ),
            },
            {"role": "user", "content": prompt[:4000]},
        ],
        tools=None,
        max_tokens=120,
        caller="workbench_session_namer",
        secondary=True,
        thinking="low",
        response_format={"type": "json_object"},
    )
    raw = assistant_text(response).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        payload = json.loads(match.group(0)) if match else {}
    if not isinstance(payload, dict):
        return ""
    title = re.sub(r"\s+", " ", str(payload.get("title") or "")).strip()
    title = title.strip("\"'`#*_ ").rstrip("。！？!?；;，,").strip()
    return title[: max(1, int(limit))]


__all__ = ["generate_session_title"]
