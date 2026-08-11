"""Wake a Workbench chat when a watched persistent shell exits.

StartShell(wake_on_exit=true) registers a watch. The agent turn can end
immediately; the shell keeps running. When the process exits, this service
starts a new Workbench chat run with the terminal tail so the agent can
continue (inspect logs, fix failures, kick off the next iteration).

While the shell is alive the user can keep chatting normally. If a wake fires
while that chat already has a live run, the wake is queued and dispatched when
the chat becomes idle again.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

DispatchResult = Literal["started", "busy", "missing", "error", "skipped"]
Dispatcher = Callable[[dict[str, Any]], Awaitable[DispatchResult]]
BusyChecker = Callable[[str], bool]

_MAX_WAKE_LINES = 120
_MAX_WAKE_CHARS = 12_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_shell_wake_prompt(
    *,
    shell_id: str,
    status: str,
    exit_code: int | None,
    title: str = "",
    cwd: str = ".",
    elapsed: str = "",
    note: str = "",
    lines: list[dict[str, Any]] | None = None,
) -> str:
    """Build the synthetic user message that resumes the agent after exit."""
    blocks = [
        "[Shell exited — automatic wake]",
        f"shell_id: {shell_id}",
        f"status: {status or 'unknown'}",
        f"exit_code: {exit_code if exit_code is not None else 'unknown'}",
        f"title: {title or 'independent shell'}",
        f"cwd: {cwd or '.'}",
    ]
    if elapsed:
        blocks.append(f"elapsed: {elapsed}")
    if note.strip():
        blocks.append(f"wake_note: {note.strip()}")
    blocks.extend(
        [
            "",
            "The long-running shell you started has exited. Inspect the output below, "
            "diagnose success/failure, and continue the prior work (fix, iterate, or "
            "report). Do not sleep or poll waiting for this process — it is already done. "
            "The user may have chatted in this conversation while the shell ran; treat "
            "this wake as a fresh turn focused on the shell result.",
            "",
            "--- last output ---",
        ]
    )
    rendered: list[str] = []
    char_budget = _MAX_WAKE_CHARS
    for item in list(lines or [])[-_MAX_WAKE_LINES:]:
        kind = str(item.get("kind") or "out")
        text = str(item.get("text") or "")
        prefix = {"err": "[err] ", "prompt": "", "meta": "[meta] "}.get(kind, "")
        line = f"{prefix}{text}"
        if len(line) + 1 > char_budget:
            rendered.append("...[truncated]...")
            break
        rendered.append(line)
        char_budget -= len(line) + 1
    if not rendered:
        rendered.append("(no captured output)")
    blocks.append("\n".join(rendered))
    return "\n".join(blocks)


class ShellWakeService:
    """In-process registry of shell-exit wakes for Workbench chats."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._wakes: dict[str, dict[str, Any]] = {}
        self._by_shell: dict[str, str] = {}
        self._pending_by_chat: dict[str, list[str]] = {}
        self._dispatcher: Dispatcher | None = None
        self._is_busy: BusyChecker | None = None

    def configure(
        self,
        *,
        dispatcher: Dispatcher | None = None,
        is_busy: BusyChecker | None = None,
    ) -> None:
        if dispatcher is not None:
            self._dispatcher = dispatcher
        if is_busy is not None:
            self._is_busy = is_busy

    async def register_wake(
        self,
        *,
        shell_id: str,
        chat_id: str,
        note: str = "",
        title: str = "",
        round_id: str = "",
    ) -> dict[str, Any]:
        shell_id = str(shell_id or "").strip()
        chat_id = str(chat_id or "").strip()
        if not shell_id:
            raise ValueError("shell_id is required")
        if not chat_id:
            raise ValueError("chat_id is required for wake_on_exit")

        wake_id = f"swake_{uuid4().hex[:12]}"
        record = {
            "wake_id": wake_id,
            "shell_id": shell_id,
            "chat_id": chat_id,
            "title": str(title or ""),
            "note": str(note or ""),
            "round_id": str(round_id or ""),
            "status": "watching",
            "created_at": _now(),
            "prompt": "",
            "exit_status": "",
            "exit_code": None,
        }
        async with self._lock:
            previous = self._by_shell.get(shell_id)
            if previous and previous in self._wakes:
                self._wakes[previous]["status"] = "replaced"
            self._wakes[wake_id] = record
            self._by_shell[shell_id] = wake_id
        return dict(record)

    async def cancel_wake(self, shell_id: str) -> bool:
        shell_id = str(shell_id or "").strip()
        async with self._lock:
            wake_id = self._by_shell.pop(shell_id, None)
            if not wake_id:
                return False
            wake = self._wakes.get(wake_id)
            if wake is None:
                return False
            if wake.get("status") == "watching":
                wake["status"] = "cancelled"
            chat_id = str(wake.get("chat_id") or "")
            queue = self._pending_by_chat.get(chat_id) or []
            self._pending_by_chat[chat_id] = [item for item in queue if item != wake_id]
            return True

    async def on_shell_exit(
        self,
        shell_id: str,
        *,
        status: str,
        exit_code: int | None,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        shell_id = str(shell_id or "").strip()
        snap = dict(snapshot or {})
        async with self._lock:
            wake_id = self._by_shell.get(shell_id)
            if not wake_id:
                return None
            wake = self._wakes.get(wake_id)
            if wake is None or wake.get("status") not in {"watching", "queued"}:
                return None
            prompt = build_shell_wake_prompt(
                shell_id=shell_id,
                status=status,
                exit_code=exit_code,
                title=str(snap.get("title") or wake.get("title") or ""),
                cwd=str(snap.get("cwd") or "."),
                elapsed=str(snap.get("elapsed") or ""),
                note=str(wake.get("note") or ""),
                lines=list(snap.get("lines") or []),
            )
            wake["status"] = "ready"
            wake["prompt"] = prompt
            wake["exit_status"] = status
            wake["exit_code"] = exit_code
            wake["ready_at"] = _now()
            chat_id = str(wake.get("chat_id") or "")
            queue = self._pending_by_chat.setdefault(chat_id, [])
            if wake_id not in queue:
                queue.append(wake_id)
            ready = dict(wake)

        await self.try_dispatch(chat_id)
        return ready

    def _chat_is_busy(self, chat_id: str) -> bool:
        if self._is_busy is None:
            return False
        try:
            return bool(self._is_busy(chat_id))
        except Exception:
            logger.exception("shell wake busy check failed for %s", chat_id)
            return True

    async def try_dispatch(self, chat_id: str = "") -> list[dict[str, Any]]:
        """Dispatch ready wakes for one chat (or every chat with a queue)."""
        results: list[dict[str, Any]] = []
        if self._dispatcher is None:
            return results

        if chat_id:
            chat_ids = [str(chat_id)]
        else:
            async with self._lock:
                chat_ids = list(self._pending_by_chat.keys())

        for target in chat_ids:
            while True:
                if self._chat_is_busy(target):
                    break
                async with self._lock:
                    queue = self._pending_by_chat.get(target) or []
                    if not queue:
                        self._pending_by_chat.pop(target, None)
                        break
                    wake_id = queue[0]
                    wake = self._wakes.get(wake_id)
                    if wake is None or wake.get("status") not in {"ready", "queued"}:
                        queue.pop(0)
                        continue
                    if not str(wake.get("prompt") or "").strip():
                        queue.pop(0)
                        wake["status"] = "skipped"
                        continue
                    wake["status"] = "dispatching"
                    payload = dict(wake)

                try:
                    outcome = await self._dispatcher(payload)
                except Exception:
                    logger.exception("shell wake dispatch failed for %s", wake_id)
                    outcome = "error"

                async with self._lock:
                    queue = self._pending_by_chat.get(target) or []
                    wake = self._wakes.get(wake_id)
                    if wake is None:
                        break
                    if outcome == "started":
                        if queue and queue[0] == wake_id:
                            queue.pop(0)
                        wake["status"] = "dispatched"
                        wake["dispatched_at"] = _now()
                        self._by_shell.pop(str(wake.get("shell_id") or ""), None)
                        results.append({"wake_id": wake_id, "result": outcome})
                        # One new run occupies the chat; stop until it finishes.
                        break
                    if outcome == "busy":
                        wake["status"] = "queued"
                        results.append({"wake_id": wake_id, "result": outcome})
                        break
                    if queue and queue[0] == wake_id:
                        queue.pop(0)
                    wake["status"] = "failed" if outcome == "error" else str(outcome)
                    wake["dispatch_error"] = outcome
                    results.append({"wake_id": wake_id, "result": outcome})
        return results

    def snapshot(self) -> dict[str, Any]:
        return {
            "wakes": [dict(item) for item in self._wakes.values()],
            "pending_by_chat": {
                chat_id: list(wake_ids)
                for chat_id, wake_ids in self._pending_by_chat.items()
            },
        }

    def clear_pending(self) -> None:
        """Discard all queued shell wake records while preserving dispatch wiring."""
        self._wakes.clear()
        self._by_shell.clear()
        self._pending_by_chat.clear()

    def reset_for_tests(self) -> None:
        """Clear in-memory wake state (test helper)."""
        self.clear_pending()
        self._dispatcher = None
        self._is_busy = None


_SERVICE = ShellWakeService()


def get_shell_wake_service() -> ShellWakeService:
    return _SERVICE


async def notify_shell_exit(
    shell_id: str,
    *,
    status: str,
    exit_code: int | None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return await _SERVICE.on_shell_exit(
        shell_id,
        status=status,
        exit_code=exit_code,
        snapshot=snapshot,
    )


__all__ = [
    "ShellWakeService",
    "build_shell_wake_prompt",
    "get_shell_wake_service",
    "notify_shell_exit",
]
