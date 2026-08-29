"""Session-local policy for durable mid-run user guidance."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cyrene.localization import localized


class GuidanceService:
    """Translate Workbench inbox events into ContextTree user input.

    The Workbench adapter owns persistence, admission, and cross-loop wakeups.
    This Plugin owns the Agent-facing representation, acknowledgement point,
    and propagation to active child Agents.
    """

    def __init__(self, owner: Any, channel: Any = None) -> None:
        self.owner = owner
        self.channel = channel

    @property
    def enabled(self) -> bool:
        return self.channel is not None

    @property
    def has_pending(self) -> bool:
        return bool(self.channel is not None and self.channel.has_pending)

    async def wait(self) -> bool:
        if self.channel is None:
            return False
        return bool(await self.channel.wait())

    async def collect(self) -> list[dict[str, Any]]:
        if self.channel is None:
            return []
        return list(await self.channel.collect())

    async def collect_or_seal(self) -> list[dict[str, Any]]:
        if self.channel is None:
            return []
        return list(await self.channel.collect_or_seal())

    def requeue(self, events: list[dict[str, Any]]) -> None:
        if self.channel is not None:
            self.channel.requeue(events)

    async def acknowledge(self, events: list[dict[str, Any]]) -> None:
        if self.channel is not None:
            await self.channel.acknowledge(events)

    @staticmethod
    def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = event.get("payload")
        return payload if isinstance(payload, Mapping) else {}

    def node_value(
        self,
        events: list[dict[str, Any]],
        *,
        run_id: str,
    ) -> dict[str, Any]:
        payloads = [self._payload(event) for event in events]
        texts = [str(payload.get("text") or "").strip() for payload in payloads]
        texts = [text for text in texts if text]
        raw_text = "\n\n".join(texts)
        content = localized(
            "[Runtime guidance]\nThe user sent this while the current task was "
            "running. Treat the latest guidance as authoritative for all work "
            "not already completed.\n\n{guidance}",
            "[运行中引导]\n用户在当前任务运行期间发送了以下要求。对于尚未完成的工作，"
            "应以最新引导为准。\n\n{guidance}",
            guidance=raw_text,
        )
        local_authorization = "\n\n".join(
            str(payload.get("text") or "").strip()
            for payload in payloads
            if not bool(payload.get("agent_originated"))
            and str(payload.get("text") or "").strip()
        )
        return {
            "role": "user",
            "content": content,
            "trigger_model": True,
            "run_id": str(run_id),
            "runtime_guidance": True,
            "guidance_count": len(events),
            "guidance_event_ids": [
                str(event.get("event_id") or "") for event in events
            ],
            "authorization_request": local_authorization,
            "metadata": {
                "source": "cyrene_guidance",
                "raw_guidance": raw_text,
                "agent_originated": all(
                    bool(payload.get("agent_originated")) for payload in payloads
                ),
                "origin_session_ids": [
                    str(payload.get("origin_session_id") or "")
                    for payload in payloads
                    if str(payload.get("origin_session_id") or "")
                ],
            },
        }

    async def fan_out(self, events: list[dict[str, Any]]) -> None:
        payloads = [self._payload(event) for event in events]
        text = "\n\n".join(
            str(payload.get("text") or "").strip()
            for payload in payloads
            if str(payload.get("text") or "").strip()
        )
        if not text:
            return
        manager = self.owner.plugin_services.get("subagents")
        deliver = getattr(manager, "broadcast_user_guidance", None)
        if not callable(deliver):
            return
        event_ids = ":".join(
            str(event.get("event_id") or "") for event in events
        )
        await deliver(text, effect_key=f"runtime-guidance:{event_ids}")


__all__ = ["GuidanceService"]
