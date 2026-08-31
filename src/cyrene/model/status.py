"""One publication seam for live and durable model status updates."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any


ModelStatusPersister = Callable[..., Awaitable[None]]
MODEL_STATUS_NAMES = frozenset({
    "retry",
    "recovered",
    "switching",
    "switched",
    "failed",
})
_persister: ModelStatusPersister | None = None
logger = logging.getLogger(__name__)


def register_model_status_persister(persister: ModelStatusPersister) -> None:
    global _persister
    _persister = persister


def model_status_message(
    chat_id: str,
    round_id: str,
    *,
    status: str,
    model: str,
    retry_count: int = 0,
    retry_limit: int = 0,
) -> dict[str, Any]:
    """Build the canonical, stable message for one round's status card."""

    session = str(chat_id or "").strip()
    round_key = str(round_id or "").strip()
    target_model = str(model or "").strip()
    normalized_status = str(status or "").strip().lower()
    if not session or not round_key or not target_model:
        raise ValueError("model status requires chat, round, and model")
    if normalized_status not in MODEL_STATUS_NAMES:
        raise ValueError("unsupported model status")
    identity = hashlib.sha256(
        f"{session}\0{round_key}\0model-status".encode("utf-8")
    ).hexdigest()[:20]
    value: dict[str, Any] = {
        "status": normalized_status,
        "model": target_model,
    }
    if normalized_status == "retry":
        value.update({
            "retryCount": max(0, int(retry_count or 0)),
            "retryLimit": max(0, int(retry_limit or 0)),
        })
    return {
        "id": f"msg_model_status_{identity}",
        "role": "assistant",
        "content": "",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "roundId": round_key,
        "modelStatusCard": True,
        "modelStatus": value,
    }


async def persist_model_status(
    chat_id: str,
    round_id: str,
    *,
    status: str,
    model: str,
    retry_count: int = 0,
    retry_limit: int = 0,
) -> None:
    persister = _persister
    if persister is None:
        return
    await persister(
        chat_id,
        round_id,
        status=status,
        model=model,
        retry_count=retry_count,
        retry_limit=retry_limit,
    )


async def publish_context_model_status(
    context: Any,
    *,
    status: str,
    model: str,
    retry_count: int = 0,
    retry_limit: int = 0,
) -> bool:
    """Resolve one PluginContext and publish a best-effort status update."""

    data = getattr(context, "data", None)
    data = data if isinstance(data, Mapping) else {}
    run_context = data.get("run_context")
    run_context = run_context if isinstance(run_context, Mapping) else {}
    session_id = str(
        data.get("session_id")
        or run_context.get("session_id")
        or ""
    )
    round_id = str(
        data.get("run_id")
        or run_context.get("round_id")
        or run_context.get("run_id")
        or ""
    )
    target_model = str(model or "").strip()
    if not session_id or not round_id or not target_model:
        return False
    try:
        from cyrene.core.plugin.context import publish_runtime_event

        message = model_status_message(
            session_id,
            round_id,
            status=status,
            model=target_model,
            retry_count=retry_count,
            retry_limit=retry_limit,
        )
        if await publish_runtime_event(
            context,
            {"type": "intermediate_message", "message": message},
        ):
            return True
        await persist_model_status(
            session_id,
            round_id,
            status=status,
            model=target_model,
            retry_count=retry_count,
            retry_limit=retry_limit,
        )
    except Exception:
        logger.exception(
            "Failed to persist model status %s [session=%s round=%s]",
            status,
            session_id,
            round_id,
        )
        return False
    return True
