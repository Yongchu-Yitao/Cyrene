"""
Subagent registry, lifecycle management, and sub-agent execution loop.

每个子 agent 在注册表中有一条记录：
  agent_id -> {"task": str, "status": "running" | "waiting" | "resumed" | "done" | "timeout", "result": str}

状态机：
  RUNNING → WAITING → (收到新消息 → RESUMED → RUNNING) | (全部 done → DONE) | (超时 → TIMEOUT)

注册表用于：
1. 发送 inbox 消息前检查对方是否还活着
2. 注入到每个 agent 的 context 中，让大家知道谁在干什么

_run_subagent 原本在 agent.py 中，移到此处避免 tools.py 与 agent.py 之间的循环依赖。
"""

import asyncio
import hashlib
import inspect
import json
import logging
from contextvars import ContextVar
import random
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from cyrene import debug
from cyrene.config import DATA_DIR

logger = logging.getLogger(__name__)

# 状态常量
RUNNING = "running"          # 正在干活
WAITING = "waiting"          # 活干完了，等别人消息
RESUMED = "resumed"          # 等待期间收到新消息，继续干活
DONE = "done"                # 真正完成
TIMEOUT = "timeout"          # 超时退出
INCOMPLETE = "incomplete"    # 安全熔断或无进展，保留部分结果
EXECUTION_MODE = "execution"
DISCUSSION_MODE = "discussion"
_TERMINAL_STATUSES = frozenset({DONE, TIMEOUT, INCOMPLETE})
_MAX_WAITING_RESULT_CHARS = 6000
_MAX_FINAL_RESULT_CHARS = 16000
_MAX_COLLECT_RESULT_CHARS = 12000
_MAX_SUMMARY_MESSAGE_CHARS = 2400
_MAX_SUMMARY_TOTAL_CHARS = 48000

_NO_LIMIT = 1_000_000_000
_SUMMARY_AGENT_PREFIX = "agent_summary_"

def _is_deep_research() -> bool:
    try:
        from cyrene.agent.state import _deep_research_mode
        return _deep_research_mode.get()
    except Exception:
        return False

def _limit(val: int) -> int:
    return _NO_LIMIT if _is_deep_research() else val

# 全局注册表
_registry: dict[str, dict] = {}
_lock = asyncio.Lock()
_direct_message_mode: ContextVar[bool] = ContextVar("_direct_message_mode", default=False)
_discussion_states: dict[str, dict[str, Any]] = {}

# 已生成子 agent 的 asyncio 任务，用于中断时取消
_subagent_tasks: dict[str, asyncio.Task] = {}


def _matches_round(entry: dict[str, Any], round_id: str = "", session_id: str = "") -> bool:
    """Return True when *entry* belongs to the requested round / session filter."""
    if session_id and str(entry.get("session_id", "")) != session_id:
        return False
    if not round_id:
        return True
    return str(entry.get("round_id", "")) == round_id


def _discussion_key(entry: dict[str, Any]) -> str:
    session_id = str(entry.get("session_id") or "")
    discussion_id = str(
        entry.get("discussion_id")
        or entry.get("round_id")
        or ""
    )
    return f"{session_id}\x1f{discussion_id}"


def _matches_discussion(
    entry: dict[str, Any],
    *,
    discussion_id: str = "",
    session_id: str = "",
) -> bool:
    if session_id and str(entry.get("session_id") or "") != session_id:
        return False
    if not discussion_id:
        return True
    return str(
        entry.get("discussion_id")
        or entry.get("round_id")
        or ""
    ) == discussion_id


async def _publish_registry_event(agent_id: str, *, message: str = "") -> None:
    """Publish the latest subagent snapshot for live UI updates.

    Keep the session id on the SSE envelope.  Workbench filters runtime events
    by session, so omitting it makes otherwise valid subagent events disappear
    from the task's live activity/log view.
    """
    async with _lock:
        entry = dict(_registry.get(agent_id, {}))
    if not entry:
        return
    event = {
        "type": "subagent_update",
        "agent_id": agent_id,
        "caller": f"subagent_{agent_id}",
        "task": entry.get("task", ""),
        "status": entry.get("status", ""),
        "mode": entry.get("mode", EXECUTION_MODE),
        "outcome": entry.get("outcome", ""),
        "stop_reason": entry.get("stop_reason", ""),
        "metrics": dict(entry.get("metrics") or {}),
        "result_preview": str(entry.get("result", "") or "")[:200],
        "message_count": len(entry.get("messages", [])),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
        "round_id": entry.get("round_id", ""),
    }
    if message:
        event["message"] = str(message)[:240]
    session_id = str(entry.get("session_id") or "")
    if session_id:
        await debug.publish_event(event, session_id=session_id)
    else:
        await debug.publish_event(event)


def _normalize_mode(mode: str = "", role: str = "") -> str:
    """Return the effective worker mode.

    Moderator/participant roles are intrinsically conversational, so they
    always use discussion limits even if an older caller omitted ``mode``.
    """
    if str(role or "").strip().lower() in {"moderator", "participant"}:
        return DISCUSSION_MODE
    normalized = str(mode or "").strip().lower()
    return DISCUSSION_MODE if normalized == DISCUSSION_MODE else EXECUTION_MODE


def _normalize_success_criteria(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    criteria: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in criteria:
            criteria.append(text[:500])
    return criteria[:20]


async def register(
    agent_id: str,
    task: str,
    round_id: str = "",
    role: str = "",
    session_id: str = "",
    mode: str = "",
    success_criteria: list[str] | None = None,
    discussion_max_messages: int | None = None,
    discussion_id: str = "",
) -> bool:
    """注册一个子 agent。

    *role* 可选，目前支持 "moderator"（主持人）/ "participant"（参与者），
    用于多 agent 讨论时区分谁负责开场、谁负责等待发言。
    """
    from cyrene.inbox import clear_inbox

    async with _lock:
        existing = _registry.get(agent_id)
        active_task = _subagent_tasks.get(agent_id)
        if (
            existing is not None
            and str(existing.get("status") or "") not in _TERMINAL_STATUSES
        ) or (
            active_task is not None
            and not active_task.done()
        ):
            return False
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "task": task,
            "status": RUNNING,
            "mode": _normalize_mode(mode, role),
            "success_criteria": _normalize_success_criteria(success_criteria),
            "outcome": "",
            "stop_reason": "",
            "result": "",
            "messages": [],
            "delivered_communications": [],
            "metrics": {
                "model_turns": 0,
                "tool_calls": 0,
                "lease_tool_calls": 0,
                "no_progress_turns": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "lease_estimated_cost_usd": 0.0,
                "context_compactions": 0,
                "discussion_rounds": 0,
                "discussion_messages": 0,
            },
            "created_at": now,
            "updated_at": now,
        }
        if discussion_max_messages is not None:
            try:
                entry["discussion_max_messages"] = max(
                    1,
                    min(50, int(discussion_max_messages)),
                )
            except (TypeError, ValueError):
                pass
        if round_id:
            entry["round_id"] = round_id
        if _normalize_mode(mode, role) == DISCUSSION_MODE:
            entry["discussion_id"] = str(discussion_id or round_id or "").strip()
        if role:
            entry["role"] = role
        if session_id:
            entry["session_id"] = session_id
        _registry[agent_id] = entry
        if entry["mode"] == DISCUSSION_MODE:
            state = _discussion_states.setdefault(_discussion_key(entry), {
                "rounds": 0,
                "messages_total": 0,
                "no_new_info_rounds": 0,
                "current_round_has_new_information": False,
                "seen_message_fingerprints": [],
                "participants": [],
                "moderator_id": "",
            })
            participants = state.setdefault("participants", [])
            if agent_id not in participants:
                participants.append(agent_id)
            if role == "moderator":
                state["moderator_id"] = agent_id
    await clear_inbox(agent_id, session_id=session_id)
    await _publish_registry_event(agent_id)
    return True


async def save_messages(agent_id: str, messages: list) -> None:
    """Save subagent conversation messages to the registry."""
    async with _lock:
        if agent_id in _registry:
            _registry[agent_id]["messages"] = messages
            _registry[agent_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
    await _publish_registry_event(agent_id)


async def mark_done(agent_id: str, result: str = "", reason: str = "completed") -> None:
    """标记 agent 已完成。

    Result 会累加而非覆盖 —— 这样被唤醒的 agent 跑完第二轮再次 mark_done 时，
    新的内容会被追加在已有结果之后，不会丢掉初次执行的结论。
    """
    async with _lock:
        if agent_id in _registry:
            current = _registry[agent_id]
            if (
                current.get("status") == TIMEOUT
                or str(current.get("outcome") or "") == "cancelled"
            ):
                return
            _registry[agent_id]["status"] = DONE
            _registry[agent_id]["outcome"] = "completed"
            _registry[agent_id]["stop_reason"] = str(reason or "completed")[:120]
            _registry[agent_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            existing = _registry[agent_id].get("result", "") or ""
            if result and result != existing:
                if existing:
                    # 如果 existing 是 result 的前缀（说明是 set_waiting 截断的版本），
                    # 直接用完整 result，避免重复拼接。
                    if result.startswith(existing):
                        _registry[agent_id]["result"] = result[:_limit(_MAX_FINAL_RESULT_CHARS)]
                    else:
                        _registry[agent_id]["result"] = (existing + "\n---\n" + result)[:_limit(_MAX_FINAL_RESULT_CHARS)]
                else:
                    _registry[agent_id]["result"] = result[:_limit(_MAX_FINAL_RESULT_CHARS)]
    await _publish_registry_event(agent_id)


async def mark_timeout(
    agent_id: str,
    result: str = "",
    reason: str = "timeout",
) -> None:
    """Mark an active subagent as settled after a timeout or infrastructure failure."""
    async with _lock:
        if agent_id not in _registry:
            return
        if str(_registry[agent_id].get("outcome") or "") == "cancelled":
            return
        _registry[agent_id]["status"] = TIMEOUT
        _registry[agent_id]["outcome"] = "resource_exhausted"
        _registry[agent_id]["stop_reason"] = str(reason or "timeout")[:120]
        _registry[agent_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        if result:
            _registry[agent_id]["result"] = str(result)[:_limit(_MAX_FINAL_RESULT_CHARS)]
    await _publish_registry_event(agent_id)


async def mark_incomplete(
    agent_id: str,
    result: str = "",
    reason: str = "incomplete",
    outcome: str = "partial",
) -> None:
    """Settle a worker with an explicit non-success outcome."""
    async with _lock:
        if agent_id not in _registry:
            return
        current = _registry[agent_id]
        if (
            current.get("status") == TIMEOUT
            or str(current.get("outcome") or "") == "cancelled"
        ):
            return
        _registry[agent_id]["status"] = INCOMPLETE
        normalized_outcome = str(outcome or "partial").strip().lower()
        _registry[agent_id]["outcome"] = (
            normalized_outcome
            if normalized_outcome in {"partial", "blocked", "resource_exhausted"}
            else "partial"
        )
        _registry[agent_id]["stop_reason"] = str(reason or "incomplete")[:120]
        _registry[agent_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        if result:
            _registry[agent_id]["result"] = str(result)[:_limit(_MAX_FINAL_RESULT_CHARS)]
    await _publish_registry_event(agent_id, message=str(reason or "incomplete"))


async def reactivate(agent_id: str) -> bool:
    """把 DONE/TIMEOUT 的 agent 状态改回 RESUMED，准备被重新启动。

    返回 True 表示成功改了状态；如果 agent 不存在或已经在跑，返回 False。
    """
    async with _lock:
        entry = _registry.get(agent_id)
        if entry is None:
            return False
        if entry["status"] in _TERMINAL_STATUSES:
            entry["status"] = RESUMED
            entry["outcome"] = ""
            entry["stop_reason"] = ""
            if _normalize_mode(
                str(entry.get("mode") or ""),
                str(entry.get("role") or ""),
            ) == EXECUTION_MODE:
                metrics = entry.setdefault("metrics", {})
                metrics["lease_tool_calls"] = 0
                metrics["lease_estimated_cost_usd"] = 0.0
                metrics["no_progress_turns"] = 0
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            should_publish = True
        else:
            should_publish = False
    if should_publish:
        await _publish_registry_event(agent_id)
        return True
    return False


async def get_raw_messages(agent_id: str) -> list:
    """获取 agent 的完整消息历史（含 system prompt、tool_calls 原始参数）。

    与 get_snapshot 不同 —— snapshot 是给 WebUI 用的，会精简内容；
    这里返回的是可以直接喂给 LLM 续跑的原始 messages 列表。
    """
    async with _lock:
        entry = _registry.get(agent_id)
        if entry is None:
            return []
        return list(entry.get("messages", []))


async def get_task(agent_id: str) -> str:
    """获取 agent 的原始任务（被唤醒时用于恢复 context）。"""
    async with _lock:
        entry = _registry.get(agent_id)
        return entry["task"] if entry else ""


async def get_round_id(agent_id: str) -> str:
    """获取 agent 所属轮次 ID。"""
    async with _lock:
        entry = _registry.get(agent_id)
        return str(entry.get("round_id", "")) if entry else ""


async def get_session_id(agent_id: str) -> str:
    """Return the persisted parent session for message-scope isolation."""
    async with _lock:
        entry = _registry.get(agent_id)
        return str(entry.get("session_id", "")) if entry else ""


async def get_role(agent_id: str) -> str:
    """获取 agent 的讨论角色（moderator / participant / 空）。"""
    async with _lock:
        entry = _registry.get(agent_id)
        return str(entry.get("role", "")) if entry else ""


async def get_mode(agent_id: str) -> str:
    """Return the persisted execution/discussion mode for a worker."""
    async with _lock:
        entry = _registry.get(agent_id)
        if not entry:
            return EXECUTION_MODE
        return _normalize_mode(str(entry.get("mode") or ""), str(entry.get("role") or ""))


async def get_success_criteria(agent_id: str) -> list[str]:
    async with _lock:
        entry = _registry.get(agent_id)
        return _normalize_success_criteria(entry.get("success_criteria") if entry else [])


async def get_discussion_max_messages(agent_id: str) -> int | None:
    async with _lock:
        entry = _registry.get(agent_id)
        if not entry or entry.get("discussion_max_messages") is None:
            return None
        try:
            return max(1, min(50, int(entry["discussion_max_messages"])))
        except (TypeError, ValueError):
            return None


async def get_discussion_id(agent_id: str) -> str:
    async with _lock:
        entry = _registry.get(agent_id)
        if not entry:
            return ""
        return str(entry.get("discussion_id") or entry.get("round_id") or "")


async def _get_discussion_state(agent_id: str) -> dict[str, Any]:
    async with _lock:
        entry = _registry.get(agent_id)
        if not entry:
            return {
                "rounds": 0,
                "messages_total": 0,
                "no_new_info_rounds": 0,
            }
        state = _discussion_states.get(_discussion_key(entry), {})
        return {
            "rounds": int(state.get("rounds") or 0),
            "messages_total": int(state.get("messages_total") or 0),
            "no_new_info_rounds": int(state.get("no_new_info_rounds") or 0),
        }


async def _update_metrics(agent_id: str, **updates: Any) -> None:
    async with _lock:
        entry = _registry.get(agent_id)
        if not entry:
            return
        metrics = entry.setdefault("metrics", {})
        for key, value in updates.items():
            metric_key = str(key)
            if metric_key.endswith("cost_usd"):
                try:
                    metrics[metric_key] = round(max(0.0, float(value)), 8)
                except (TypeError, ValueError):
                    continue
            else:
                try:
                    metrics[metric_key] = max(0, int(value))
                except (TypeError, ValueError):
                    continue
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()


async def _claim_discussion_message_slot(
    agent_id: str,
    *,
    max_per_agent: int,
    max_total: int,
) -> tuple[bool, str]:
    """Atomically enforce per-agent and round-wide discussion message caps."""
    async with _lock:
        entry = _registry.get(agent_id)
        if not entry:
            return False, "agent_not_registered"
        metrics = entry.setdefault("metrics", {})
        per_agent = int(metrics.get("discussion_messages") or 0)
        state = _discussion_states.setdefault(_discussion_key(entry), {
            "rounds": 0,
            "messages_total": 0,
            "no_new_info_rounds": 0,
            "current_round_has_new_information": False,
            "seen_message_fingerprints": [],
            "participants": [agent_id],
            "moderator_id": "",
        })
        total = int(state.get("messages_total") or 0)
        if per_agent >= max_per_agent:
            return False, "discussion_message_limit_per_agent"
        if total >= max_total:
            return False, "discussion_message_limit_total"
        metrics["discussion_messages"] = per_agent + 1
        state["messages_total"] = total + 1
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        return True, ""


async def _release_discussion_message_slot(agent_id: str) -> None:
    async with _lock:
        entry = _registry.get(agent_id)
        if not entry:
            return
        metrics = entry.setdefault("metrics", {})
        metrics["discussion_messages"] = max(
            0,
            int(metrics.get("discussion_messages") or 0) - 1,
        )
        state = _discussion_states.get(_discussion_key(entry))
        if state is not None:
            state["messages_total"] = max(
                0,
                int(state.get("messages_total") or 0) - 1,
            )


async def _record_discussion_delivery(
    agent_id: str,
    content: str,
) -> dict[str, int]:
    fingerprint = _message_fingerprint(content)
    async with _lock:
        entry = _registry.get(agent_id)
        if not entry:
            return {"rounds": 0, "no_new_info_rounds": 0}
        state = _discussion_states.setdefault(_discussion_key(entry), {
            "rounds": 0,
            "messages_total": 0,
            "no_new_info_rounds": 0,
            "current_round_has_new_information": False,
            "seen_message_fingerprints": [],
            "participants": [agent_id],
            "moderator_id": "",
        })
        seen = state.setdefault("seen_message_fingerprints", [])
        adds_information = fingerprint not in seen
        if adds_information:
            seen.append(fingerprint)
            if len(seen) > 500:
                del seen[:-500]

        moderator_id = str(state.get("moderator_id") or "")
        starts_round = (
            str(entry.get("role") or "") == "moderator"
            or not moderator_id
        )
        if starts_round:
            if int(state.get("rounds") or 0) > 0:
                if bool(state.get("current_round_has_new_information")):
                    state["no_new_info_rounds"] = 0
                else:
                    state["no_new_info_rounds"] = (
                        int(state.get("no_new_info_rounds") or 0) + 1
                    )
            state["rounds"] = int(state.get("rounds") or 0) + 1
            state["current_round_has_new_information"] = adds_information
        elif adds_information:
            state["current_round_has_new_information"] = True
        return {
            "rounds": int(state.get("rounds") or 0),
            "no_new_info_rounds": int(state.get("no_new_info_rounds") or 0),
        }


async def _discussion_message_total(agent_id: str) -> int:
    return int((await _get_discussion_state(agent_id)).get("messages_total") or 0)


async def round_has_moderator(
    round_id: str = "",
    exclude: str = "",
    discussion_id: str = "",
    session_id: str = "",
) -> bool:
    """本轮是否存在主持人（除 *exclude* 之外）。"""
    async with _lock:
        return any(
            info.get("role") == "moderator" and aid != exclude
            for aid, info in _registry.items()
            if _matches_round(info, round_id)
            and _matches_discussion(
                info,
                discussion_id=discussion_id,
                session_id=session_id,
            )
        )


async def set_waiting(agent_id: str, result: str = "") -> None:
    """标记 agent 活干完了，等待其他人。

    可选地把当前阶段的 result 写入 registry —— 这样主 agent 即便提前 collect，
    也能拿到真实内容，而不是空字符串。
    """
    async with _lock:
        if agent_id in _registry and _registry[agent_id]["status"] in (RUNNING, RESUMED):
            _registry[agent_id]["status"] = WAITING
            _registry[agent_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            if result:
                _registry[agent_id]["result"] = result[:_limit(_MAX_WAITING_RESULT_CHARS)]
    await _publish_registry_event(agent_id)


async def set_resumed(agent_id: str) -> None:
    """标记 agent 在等待期间收到新消息，恢复工作。"""
    async with _lock:
        if agent_id in _registry and _registry[agent_id]["status"] == WAITING:
            _registry[agent_id]["status"] = RESUMED
            _registry[agent_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
    await _publish_registry_event(agent_id)


async def set_running(agent_id: str) -> None:
    """标记 agent 已进入活跃执行态。"""
    async with _lock:
        if agent_id in _registry and _registry[agent_id]["status"] != RUNNING:
            _registry[agent_id]["status"] = RUNNING
            _registry[agent_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
    await _publish_registry_event(agent_id)


async def can_receive(
    agent_id: str,
    round_id: str = "",
    discussion_id: str = "",
    session_id: str = "",
    strict_session: bool = False,
) -> bool:
    """检查 agent 是否能接收消息。

    任何已注册的 agent 都能收 —— 即使是 DONE/TIMEOUT 的也可以，
    主 agent 监控循环会负责唤醒它们处理新消息。
    """
    async with _lock:
        entry = _registry.get(agent_id)
        return (
            entry is not None
            and _matches_round(entry, round_id, session_id)
            and (
                not strict_session
                or str(entry.get("session_id") or "") == session_id
            )
            and _matches_discussion(
                entry,
                discussion_id=discussion_id,
                session_id=session_id,
            )
        )


async def list_discussion_peer_ids(agent_id: str) -> list[str]:
    async with _lock:
        source = _registry.get(agent_id)
        if not source:
            return []
        discussion_id = str(
            source.get("discussion_id")
            or source.get("round_id")
            or ""
        )
        session_id = str(source.get("session_id") or "")
        return [
            peer_id
            for peer_id, info in _registry.items()
            if peer_id != agent_id
            and _normalize_mode(
                str(info.get("mode") or ""),
                str(info.get("role") or ""),
            ) == DISCUSSION_MODE
            and str(info.get("session_id") or "") == session_id
            and _matches_discussion(
                info,
                discussion_id=discussion_id,
                session_id=session_id,
            )
        ]


async def all_quiescent(round_id: str = "") -> bool:
    """所有 agent 都进入了 waiting/done（没有 running 的）。"""
    async with _lock:
        infos = [info for info in _registry.values() if _matches_round(info, round_id)]
        return not any(info["status"] == RUNNING for info in infos)


async def all_done(round_id: str = "") -> bool:
    """所有 agent 都真正完成了（没有 running/waiting/resumed 的）。"""
    async with _lock:
        infos = [info for info in _registry.values() if _matches_round(info, round_id)]
        return not any(info["status"] in (RUNNING, WAITING, RESUMED) for info in infos)


async def all_willing_to_quit(round_id: str = "") -> bool:
    """没有 agent 还在主动干活 —— 全部都在 WAITING/DONE/TIMEOUT。

    用于 wait_for_others 的解锁判断：当所有人都进入 WAITING（想退出但还在等别人）时，
    应该让大家一起退出，而不是互相等待。
    """
    async with _lock:
        infos = [info for info in _registry.values() if _matches_round(info, round_id)]
        return not any(info["status"] in (RUNNING, RESUMED) for info in infos)


async def wait_for_others(agent_id: str, inbox_check_func, mark_read_func=None, max_wait: int = 600, result: str = "") -> str:
    """Subagent 干完活后调用：标记 waiting（带 result），等其他人。

    每 2 秒检查一次（加随机抖动避免惊群效应）：
    - inbox 有新消息 → 短暂等待 0.5s 允许批量投递，然后返回消息内容（回去继续干活）
    - 所有 agent 都不在干活 (RUNNING/RESUMED) → 返回 ""（一起退出）
    - 超时 → 返回 "timeout"

    先检查 inbox 再检查全局退出条件，避免在有人发来消息时直接退出。
    """
    round_id = await get_round_id(agent_id)
    await set_waiting(agent_id, result=result)
    waited = 0
    while waited < max_wait:
        new_msgs = inbox_check_func(agent_id)
        if new_msgs:
            # 短暂等待让批量消息有机会全部到达
            await asyncio.sleep(0.5)
            if mark_read_func:
                maybe_awaitable = mark_read_func(agent_id)
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable
            return new_msgs
        if await all_willing_to_quit(round_id=round_id):
            return ""
        interval = 2 + random.uniform(-0.3, 0.3)
        await asyncio.sleep(interval)
        waited += interval
    return "timeout"


async def get_status(agent_id: str) -> str | None:
    """获取 agent 状态：running / waiting / resumed / done / timeout / None。"""
    async with _lock:
        entry = _registry.get(agent_id)
        if entry is None:
            return None
        return entry["status"]


async def get_context(
    exclude: str = "",
    round_id: str = "",
    discussion_id: str = "",
    session_id: str = "",
    strict_session: bool = False,
) -> str:
    """格式化注册表为文本，注入 agent context。"""
    async with _lock:
        entries = [
            (aid, info)
            for aid, info in _registry.items()
            if _matches_round(info, round_id)
            and _normalize_mode(
                str(info.get("mode") or ""),
                str(info.get("role") or ""),
            ) == DISCUSSION_MODE
            and (
                not strict_session
                or str(info.get("session_id") or "") == session_id
            )
            and _matches_discussion(
                info,
                discussion_id=discussion_id,
                session_id=session_id,
            )
        ]
        if not entries:
            return ""
        lines = ["[活跃子 agent]"]
        for aid, info in entries:
            marker = "-> " if aid == exclude else "  "
            st = {
                "running": "工作中",
                "waiting": "活干完了等大家",
                "resumed": "恢复工作",
                "done": "已完成",
                "timeout": "超时",
                "incomplete": "部分完成",
            }.get(info["status"], info["status"])
            role_tag = {"moderator": "（主持人）", "participant": "（参与者）"}.get(info.get("role", ""), "")
            mode_tag = "（讨论）" if info.get("mode") == DISCUSSION_MODE and not role_tag else ""
            lines.append(f"  {marker}{aid}{role_tag}{mode_tag}: {info['task'][:50]} [{st}]")
        return "\n".join(lines)


async def clear(round_id: str | None = None, session_id: str = "") -> None:
    """清除注册表（新 session 时调用）。

    当提供 *round_id* 或 *session_id* 时，只删除匹配的 subagent。
    """
    async with _lock:
        if not round_id and not session_id:
            _registry.clear()
            _discussion_states.clear()
            return
        doomed = [
            aid
            for aid, info in _registry.items()
            if _matches_round(info, round_id, session_id)
        ]
        for aid in doomed:
            _registry.pop(aid, None)
        active_discussion_keys = {
            _discussion_key(info)
            for info in _registry.values()
            if _normalize_mode(
                str(info.get("mode") or ""),
                str(info.get("role") or ""),
            ) == DISCUSSION_MODE
        }
        for key in list(_discussion_states):
            if key not in active_discussion_keys:
                _discussion_states.pop(key, None)


async def collect_results(round_id: str = "") -> str:
    """收集所有 subagent 的结果，格式化为文本。"""
    async with _lock:
        lines = []
        for aid, info in _registry.items():
            if not _matches_round(info, round_id):
                continue
            task = str(info.get("task", "") or "").strip()
            status = str(info.get("status", "") or "").strip()
            outcome = str(info.get("outcome", "") or "").strip()
            stop_reason = str(info.get("stop_reason", "") or "").strip()
            result = info.get("result", "")
            if result:
                lines.append(
                    f"[{aid}] task: {task or '—'}\n"
                    f"status: {status or 'unknown'}\n"
                    f"outcome: {outcome or 'unknown'}\n"
                    f"stop_reason: {stop_reason or '—'}\n"
                    f"result:\n{str(result)[:_limit(_MAX_COLLECT_RESULT_CHARS)]}"
                )
            else:
                lines.append(
                    f"[{aid}] task: {task or '—'}\n"
                    f"status: {status or 'unknown'}\n"
                    f"outcome: {outcome or 'unknown'}\n"
                    f"stop_reason: {stop_reason or '—'}\n"
                    "result:\n无结果"
                )
        return "\n\n".join(lines) if lines else "无 subagent 结果。"


async def build_deep_research_source(round_id: str = "") -> str:
    """Collect only subagent research RESULTS for the final Phase 3 report.

    Unlike build_round_summary_transcript, this does NOT include:
    - Subagent internal transcripts (tool calls, reasoning, messages)
    - Inter-agent communication messages
    - Agent IDs, status labels, or process metadata

    Output is pure research material, formatted as clean sections the main
    agent can directly incorporate into the final report.
    """
    entries = await _registry_entries_for_round(round_id=round_id)
    if not entries:
        return "No research material available."

    # Sort by creation time for consistent ordering
    entries.sort(key=lambda item: str(item[1].get("created_at") or ""))

    sections: list[str] = []
    for index, (agent_id, info) in enumerate(entries, start=1):
        task = str(info.get("task") or "").strip()
        result = str(info.get("result") or "").strip()
        if not result:
            continue

        section = (
            f"## Research Topic {index}: {task or 'Untitled'}\n\n"
            f"{result}"
        )
        sections.append(section)

    if not sections:
        return "No research material available."

    return "\n\n---\n\n".join(sections)


async def get_snapshot(round_id: str = "", session_id: str = "") -> dict:
    """Return a JSON-safe snapshot of all subagents for the WebUI."""
    async with _lock:
        snapshot = {}
        for aid, info in _registry.items():
            if not _matches_round(info, round_id, session_id):
                continue
            msgs = []
            for m in info.get("messages", []):
                role = m.get("role", "")
                content = m.get("content", "")
                if role == "system":
                    content = content[:200]  # trim system prompts
                entry = {"role": role, "content": content}
                if m.get("tool_calls"):
                    entry["tool_calls"] = [
                        {"name": tc["function"]["name"]}
                        for tc in m["tool_calls"]
                    ]
                msgs.append(entry)
            snapshot[aid] = {
                "task": info.get("task", ""),
                "status": info.get("status", ""),
                "mode": info.get("mode", EXECUTION_MODE),
                "success_criteria": list(info.get("success_criteria") or []),
                "discussion_id": info.get("discussion_id", ""),
                "discussion_max_messages": info.get("discussion_max_messages"),
                "outcome": info.get("outcome", ""),
                "stop_reason": info.get("stop_reason", ""),
                "metrics": dict(info.get("metrics") or {}),
                "result": info.get("result", ""),
                "messages": msgs,
            }
            if info.get("mode") == DISCUSSION_MODE:
                state = _discussion_states.get(_discussion_key(info), {})
                snapshot[aid]["discussion_state"] = {
                    "rounds": int(state.get("rounds") or 0),
                    "messages_total": int(state.get("messages_total") or 0),
                    "no_new_info_rounds": int(
                        state.get("no_new_info_rounds") or 0
                    ),
                }
        return snapshot


def _flow_message_copy(message: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, JSON-safe message copy for flow persistence."""
    role = str(message.get("role", "") or "").strip()
    entry: dict[str, Any] = {"role": role}

    if "content" in message:
        content = message.get("content")
        if role == "system":
            entry["content"] = str(content or "")[:200]
        else:
            entry["content"] = content
    if message.get("reasoning_content"):
        entry["reasoning_content"] = message.get("reasoning_content")
    if message.get("tool_call_id"):
        entry["tool_call_id"] = str(message.get("tool_call_id") or "")
    if message.get("usage"):
        entry["usage"] = message.get("usage")

    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        compact_calls: list[dict[str, Any]] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
            compact_fn = {
                "name": str(fn.get("name") or "").strip(),
                "arguments": fn.get("arguments", ""),
            }
            compact_calls.append({
                "id": str(tc.get("id") or ""),
                "type": tc.get("type", "function"),
                "function": compact_fn,
            })
        if compact_calls:
            entry["tool_calls"] = compact_calls

    return entry


async def build_flow_snapshot(round_id: str) -> dict[str, Any]:
    """Persist the minimum completed-round subagent data needed by the WebUI."""
    entries = await _registry_entries_for_round(round_id=round_id)
    agent_ids = {agent_id for agent_id, _ in entries}
    comm_messages = _round_comm_messages(agent_ids, round_id=round_id)

    snapshot_agents: dict[str, dict[str, Any]] = {}
    for agent_id, info in entries:
        snapshot_agents[agent_id] = {
            "task": info.get("task", ""),
            "status": info.get("status", ""),
            "mode": info.get("mode", EXECUTION_MODE),
            "success_criteria": list(info.get("success_criteria") or []),
            "discussion_id": info.get("discussion_id", ""),
            "discussion_max_messages": info.get("discussion_max_messages"),
            "outcome": info.get("outcome", ""),
            "stop_reason": info.get("stop_reason", ""),
            "metrics": dict(info.get("metrics") or {}),
            "result": info.get("result", ""),
            "messages": [
                _flow_message_copy(message)
                for message in (info.get("messages") or [])
                if isinstance(message, dict)
            ],
            "created_at": info.get("created_at"),
            "updated_at": info.get("updated_at"),
            "round_id": str(info.get("round_id", "") or round_id),
        }

    compact_comm_messages = [
        {
            "message_id": str(item.get("message_id") or ""),
            "from": str(item.get("from") or ""),
            "to": str(item.get("to") or ""),
            "type": str(item.get("type") or "chat"),
            "content": str(item.get("content") or ""),
            "timestamp": item.get("timestamp"),
            "round_id": str(item.get("round_id") or round_id),
        }
        for item in comm_messages
        if isinstance(item, dict)
    ]

    return {
        "round_id": round_id,
        "summary_agent_id": _summary_agent_id(round_id),
        "agents": snapshot_agents,
        "comm_messages": compact_comm_messages,
    }


def _summary_agent_id(round_id: str) -> str:
    suffix = str(round_id or "").removeprefix("round_").strip() or "adhoc"
    suffix = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in suffix)
    return f"{_SUMMARY_AGENT_PREFIX}{suffix[:32]}"


def _truncate_summary_text(text: str, limit: int | None = None) -> str:
    if limit is None:
        limit = _limit(_MAX_SUMMARY_MESSAGE_CHARS)
    source = str(text or "")
    if len(source) <= limit:
        return source
    return source[:limit] + "\n...[truncated]..."


def _render_summary_message(message: dict[str, Any]) -> str:
    role = str(message.get("role", "") or "").strip() or "unknown"
    if role == "system":
        return ""

    chunks: list[str] = []
    content = str(message.get("content") or "").strip()
    reasoning = str(message.get("reasoning_content") or "").strip()
    if role == "assistant" and reasoning:
        chunks.append(f"[reasoning]\n{_truncate_summary_text(reasoning)}")
    if content:
        chunks.append(_truncate_summary_text(content))

    tool_calls = message.get("tool_calls") or []
    for tc in tool_calls:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        name = str(fn.get("name") or "tool").strip()
        args = str(fn.get("arguments") or "").strip()
        rendered = f"[tool_call] {name}"
        if args:
            rendered += f"\nargs: {_truncate_summary_text(args, 600)}"
        chunks.append(rendered)

    if role == "tool" and not chunks:
        chunks.append(_truncate_summary_text(str(message.get("content") or "")))

    if not chunks:
        return ""
    return f"{role}:\n" + "\n".join(chunks)


async def _registry_entries_for_round(round_id: str = "", exclude_ids: set[str] | None = None, session_id: str = "") -> list[tuple[str, dict[str, Any]]]:
    blocked = exclude_ids or set()
    async with _lock:
        entries = [
            (aid, dict(info))
            for aid, info in _registry.items()
            if aid not in blocked and _matches_round(info, round_id, session_id)
        ]
    entries.sort(key=lambda item: str(item[1].get("created_at") or ""))
    return entries


def _round_comm_messages(agent_ids: set[str], round_id: str = "") -> list[dict[str, Any]]:
    inbox_root = DATA_DIR / "inbox"
    if not agent_ids or not inbox_root.exists():
        return []

    messages: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for msg_file in inbox_root.rglob("*.json"):
        try:
            payload = json.loads(msg_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if round_id and str(payload.get("round_id", "")).strip() != round_id:
            continue
        from_agent = str(payload.get("from", "")).strip()
        to_agent = str(payload.get("to", "")).strip()
        if from_agent not in agent_ids or to_agent not in agent_ids:
            continue
        message_id = str(payload.get("message_id") or msg_file.stem).strip()
        if message_id in seen_ids:
            continue
        seen_ids.add(message_id)
        messages.append(payload)
    messages.sort(key=lambda item: str(item.get("timestamp") or ""))
    return messages


async def build_group_chat_messages(round_id: str) -> dict[str, Any]:
    """Build group-chat-formatted messages for a given round.

    Extracts:
    - ``subagent.send_message`` / ``subagent.broadcast`` module invocations from
      each subagent's message history, formatted as chat entries with
      ``@recipient`` / ``@所有人`` prepended to the body.
    - Each subagent's final ``result`` (when non-trivial).
    - User messages from inbox files (``from == "user"``).

    Falls back to ``subagent_flow_snapshot`` in saved session messages when
    the live ``_registry`` has no entries for *round_id* (e.g. after the
    round has completed and the registry was cleared).

    Returns ``{"messages": [...], "agents": [...]}`` sorted chronologically.
    """
    async with _lock:
        entries = [
            (aid, dict(info))
            for aid, info in _registry.items()
            if _matches_round(info, round_id) and aid != "main"
        ]

    # Fallback: live registry may have been cleared — reconstruct from
    # subagent_flow_snapshot embedded in the saved session messages.
    if not entries:
        from cyrene.agent.state import STATE_FILE

        if STATE_FILE and STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                raw_msgs = data.get("messages", []) if isinstance(data, dict) else []
                for msg in reversed(raw_msgs):
                    snapshot = msg.get("subagent_flow_snapshot")
                    if not isinstance(snapshot, dict):
                        continue
                    if str(snapshot.get("round_id", "")).strip() != round_id:
                        continue
                    agents_data = snapshot.get("agents") or {}
                    comm_msgs = snapshot.get("comm_messages") or []
                    agents_list = []
                    msg_list = []
                    for agent_id, info in agents_data.items():
                        if not isinstance(info, dict):
                            continue
                        agents_list.append({
                            "id": agent_id,
                            "task": str(info.get("task", "") or "").strip(),
                            "status": str(info.get("status", "") or "done").strip(),
                        })
                        result = str(info.get("result", "") or "").strip()
                        if result and result not in ("Done.", "", "无结果"):
                            msg_list.append({
                                "id": f"{agent_id}_result",
                                "type": "agent_result",
                                "from": agent_id,
                                "to": "",
                                "content": result,
                                "timestamp": str(info.get("updated_at") or info.get("created_at") or ""),
                                "round_id": round_id,
                            })
                    for comm in comm_msgs:
                        if not isinstance(comm, dict):
                            continue
                        content = str(comm.get("content", "") or "").strip()
                        if not content:
                            continue
                        target = str(comm.get("to", "") or "").strip()
                        is_broadcast = str(comm.get("type", "") or "").strip() == "broadcast"
                        display = f"@{'所有人' if is_broadcast or not target else target} {content}"
                        from_agent = str(comm.get("from", "") or "").strip()
                        msg_list.append({
                            "id": str(comm.get("message_id", "") or f"{from_agent}_{comm.get('timestamp', '')}"),
                            "type": "agent_broadcast" if is_broadcast else "agent_send",
                            "from": from_agent,
                            "to": target or "all",
                            "content": display,
                            "timestamp": str(comm.get("timestamp", "") or ""),
                            "round_id": round_id,
                        })
                    msg_list.sort(key=lambda m: str(m.get("timestamp") or ""))
                    return {"messages": msg_list, "agents": agents_list}
            except Exception:
                logger.warning("Failed to load flow snapshot for round %s", round_id, exc_info=True)

    agents_list: list[dict[str, str]] = []
    messages: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    for agent_id, info in entries:
        task = str(info.get("task", "") or "").strip()
        status = str(info.get("status", "") or "unknown").strip()
        agents_list.append({"id": agent_id, "task": task, "status": status})

        agent_created = str(info.get("created_at") or now)
        agent_msgs = info.get("messages") or []

        # 1. Extract subagent communication module invocations.
        for msg_idx, msg in enumerate(agent_msgs):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue
            for tc in (msg.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function", {})
                if not isinstance(fn, dict):
                    continue
                name = str(fn.get("name", "") or "").strip()
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    continue
                try:
                    from cyrene.tooling import resolve_wire_call

                    resolution = resolve_wire_call(
                        name,
                        args,
                        actor="subagent",
                    )
                    name = resolution.concrete_name
                    args = resolution.concrete_arguments
                except Exception:
                    continue
                if name not in (
                    "send_agent_message",
                    "broadcast_agent_message",
                    "send_message_to_user",
                ):
                    continue
                # delivery.send_message_to_user uses "text"; peer messages use "content"
                content = str(args.get("content", "") or args.get("text", "") or "").strip()
                if not content:
                    continue

                is_broadcast = name == "broadcast_agent_message"
                is_user_reply = name == "send_message_to_user"
                if is_user_reply:
                    target = "user"
                elif is_broadcast:
                    target = "all"
                else:
                    target = str(args.get("to", "") or "").strip()
                display_content = f"@{target} {content}" if target else content

                # Generate a per-message timestamp from agent_created + offset
                ts = agent_created  # same-timestamp batch; sort stable within agent

                messages.append({
                    "id": f"{agent_id}_{tc.get('id', f'msg_{msg_idx}')}",
                    "type": "agent_broadcast" if is_broadcast else "agent_send",
                    "from": agent_id,
                    "to": target,
                    "content": display_content,
                    "timestamp": ts,
                    "round_id": round_id,
                })

        # 2. Extract subagent result (non-trivial only)
        result = str(info.get("result", "") or "").strip()
        result_ts = str(info.get("updated_at") or agent_created)
        if result and result not in ("Done.", "", "无结果"):
            messages.append({
                "id": f"{agent_id}_result",
                "type": "agent_result",
                "from": agent_id,
                "to": "",
                "content": result,
                "timestamp": result_ts,
                "round_id": round_id,
            })

    # 3. Read inbox messages from user
    inbox_root = DATA_DIR / "inbox"
    agent_ids = {aid for aid, _ in entries}
    if inbox_root.exists():
        for inbox_dir in sorted(inbox_root.iterdir()):
            if not inbox_dir.is_dir():
                continue
            agent_id = inbox_dir.name
            if agent_id not in agent_ids:
                continue
            for msg_file in sorted(inbox_dir.glob("msg_*.json")):
                try:
                    payload = json.loads(msg_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("from", "") or "").strip() != "user":
                    continue
                if round_id and str(payload.get("round_id", "") or "").strip() != round_id:
                    continue
                content = str(payload.get("content", "") or "").strip()
                if not content:
                    continue
                messages.append({
                    "id": str(payload.get("message_id", msg_file.stem)),
                    "type": "user_message",
                    "from": "user",
                    "to": agent_id,
                    "content": content,
                    "timestamp": str(payload.get("timestamp", "") or ""),
                    "round_id": round_id,
                })

    # 4. Dedup by (from + content) — avoids duplicate user messages from broadcast to multiple agents
    seen_keys: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for m in messages:
        key = (str(m.get("from", "") or "").strip(), str(m.get("content", "") or "").strip())
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(m)

    # 5. Sort by timestamp (empty timestamps sort last within their group)
    deduped.sort(key=lambda m: str(m.get("timestamp") or ""))

    return {"messages": deduped, "agents": agents_list}


async def build_round_summary_transcript(round_id: str, exclude_ids: set[str] | None = None) -> str:
    entries = await _registry_entries_for_round(round_id=round_id, exclude_ids=exclude_ids)
    if not entries:
        return "No peer subagent transcript was captured for this round."

    sections: list[str] = []
    total_chars = 0
    agent_ids = {agent_id for agent_id, _ in entries}
    for agent_id, info in entries:
        rendered_messages = [
            block
            for block in (_render_summary_message(message) for message in (info.get("messages") or []))
            if block
        ]
        section = (
            f"## {agent_id}\n"
            f"task: {str(info.get('task') or '').strip() or '—'}\n"
            f"status: {str(info.get('status') or '').strip() or 'unknown'}\n"
            f"result:\n{_truncate_summary_text(str(info.get('result') or ''), _limit(5000)) or '—'}\n\n"
            f"transcript:\n" + ("\n\n".join(rendered_messages) if rendered_messages else "—")
        )
        summary_total_limit = _limit(_MAX_SUMMARY_TOTAL_CHARS)
        if total_chars + len(section) > summary_total_limit:
            remaining = summary_total_limit - total_chars
            if remaining <= 0:
                sections.append("[older peer transcript omitted]")
                break
            sections.append(_truncate_summary_text(section, remaining))
            sections.append("[older peer transcript omitted]")
            break
        sections.append(section)
        total_chars += len(section)

    comms = _round_comm_messages(agent_ids, round_id=round_id)
    if comms and total_chars < summary_total_limit:
        lines = ["## Inter-agent messages"]
        for item in comms:
            lines.append(
                f"[{item.get('timestamp', '—')}] {item.get('from', '?')} -> {item.get('to', '?')} ({item.get('type', 'chat')})\n"
                f"{_truncate_summary_text(str(item.get('content') or ''))}"
            )
        comms_block = "\n\n".join(lines)
        remaining = summary_total_limit - total_chars
        if len(comms_block) > remaining:
            comms_block = _truncate_summary_text(comms_block, remaining)
        sections.append(comms_block)

    return "\n\n".join(sections).strip() or "No peer subagent transcript was captured for this round."


async def run_summary_subagent(
    round_id: str,
    parent_task: str,
    guidance: str = "",
    round_history: list[dict[str, Any]] | None = None,
) -> str:
    """Run a dedicated summary subagent after peer subagents finish.

    In deep research mode, skip the LLM summariser and just concatenate all
    subagent transcripts directly — the main agent will synthesise from the
    full raw material without any intermediate compression.
    """
    from cyrene.agent.state import _call_llm, _current_session_id
    from cyrene.llm import _assistant_text

    summary_agent_id = _summary_agent_id(round_id)
    transcript = await build_round_summary_transcript(round_id=round_id, exclude_ids={summary_agent_id})
    summary_session_id = _current_session_id.get()

    # Deep research: return raw concatenated transcript, no LLM compression
    if _is_deep_research():
        header = f"## Deep Research Raw Transcript\nParent task: {parent_task or '—'}\n\n"
        final_text = header + transcript
        await register(summary_agent_id, "Concatenate all subagent transcripts (deep research)", round_id=round_id, session_id=summary_session_id)
        await mark_done(summary_agent_id, final_text)
        return final_text

    summary_task = "Summarize every peer subagent transcript and their communication for the main agent."

    history_lines: list[str] = []
    for msg in (round_history or [])[-12:]:
        role = str(msg.get("role", "") or "").strip()
        if role == "system":
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        history_lines.append(f"[{role}] {_truncate_summary_text(content, 800)}")
    history_block = "\n".join(history_lines) if history_lines else "—"

    await register(summary_agent_id, summary_task, round_id=round_id, session_id=summary_session_id)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a synthesis agent. Your job is to read the materials below "
                "and produce a clear, well-organised answer that directly addresses the user's question.\n\n"
                "### How to Synthesise\n"
                "1. Read ALL the materials thoroughly. Identify: what was the user asking, "
                "what are the key findings, where do different sources agree or conflict.\n"
                "2. Write a direct answer to the user. Do NOT describe the research process or mention "
                "how the information was gathered. Write as if you personally found everything.\n"
                "3. Organise your answer for clarity:\n"
                "   - Start by directly addressing the user's question.\n"
                "   - Present findings in a logical order — group related information together.\n"
                "   - Use headings to separate major topics if the answer is long.\n"
                "   - Use bullet points or numbered lists when comparing items or listing options.\n"
                "   - End with a brief conclusion or recommendation when appropriate.\n"
                "4. Preserve ALL important data: specific numbers, concrete facts, key quotes, "
                "and important nuances from the materials. Do not over-summarise — "
                "a detailed answer is better than a vague one.\n"
                "5. When sources disagree, present both sides rather than arbitrarily picking one.\n"
                "6. Be honest about uncertainty. If information is incomplete, say so.\n\n"
                "### Forbidden\n"
                "- Do NOT reference 'subagents', 'research tracks', 'transcripts', or the process.\n"
                "- Do NOT preface your answer with meta-commentary like 'Based on the research...'.\n"
                "- Do NOT end with 'I hope this helps' or similar filler.\n"
                "- Do NOT ask the user questions.\n"
                "- Do NOT invent facts not in the materials.\n\n"
                "### Language\n"
                "Match the user's language. If the user wrote in Chinese, reply in Chinese. "
                "If in English, reply in English."
            ),
        },
        {
            "role": "user",
            "content": (
                f"User's question:\n{parent_task or '—'}\n\n"
                f"Additional guidance:\n{guidance or '—'}\n\n"
                f"Context from the conversation:\n{history_block}\n\n"
                f"Research materials:\n{transcript}"
            ),
        },
    ]
    await save_messages(summary_agent_id, messages)

    try:
        response = await _call_llm(messages, tools=None, max_tokens=None)
        assistant_entry: dict[str, Any] = {"role": "assistant", "content": response.get("content") or ""}
        if response.get("reasoning_content"):
            assistant_entry["reasoning_content"] = response["reasoning_content"]
        if response.get("usage"):
            assistant_entry["usage"] = response["usage"]
        messages.append(assistant_entry)
        await save_messages(summary_agent_id, messages)
        final_text = _assistant_text(response).strip() or "No summary was produced."
    except Exception as exc:
        logger.exception("Summary sub-agent %s crashed", summary_agent_id)
        final_text = f"Summary sub-agent crashed: {exc}"

    await mark_done(summary_agent_id, final_text)
    return final_text


# ---------------------------------------------------------------------------
# Sub-agent execution loop (moved from agent.py)
# ---------------------------------------------------------------------------


def _spawn_subagent_task(coro, agent_id: str) -> asyncio.Task:
    """Create a fire-and-forget asyncio task with error logging.

    If the coroutine raises before its internal try/except, the exception
    would otherwise be silently lost.
    """
    existing = _subagent_tasks.get(agent_id)
    if existing is not None and not existing.done():
        if inspect.iscoroutine(coro):
            coro.close()
        raise RuntimeError(f"Sub-agent '{agent_id}' is already running.")
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda t: _log_task_exception(t, agent_id))
    task.add_done_callback(
        lambda t: (
            _subagent_tasks.pop(agent_id, None)
            if _subagent_tasks.get(agent_id) is t
            else None
        )
    )
    _subagent_tasks[agent_id] = task
    return task


def _bounded_int_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    from cyrene.settings_store import get as get_setting

    try:
        value = int(get_setting(name, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bounded_float_setting(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    from cyrene.settings_store import get as get_setting

    try:
        raw = get_setting(name, default)
        value = float(default if raw is None else raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _optional_int_setting(name: str, default: int, maximum: int) -> int:
    """Read an integer setting where zero explicitly disables the override."""
    from cyrene.settings_store import get as get_setting

    try:
        raw = get_setting(name, default)
        value = int(default if raw is None else raw)
    except (TypeError, ValueError):
        value = default
    return max(0, min(maximum, value))


def _subagent_limits(mode: str) -> dict[str, Any]:
    if mode == DISCUSSION_MODE:
        return {
            "max_rounds": _bounded_int_setting("subagent_discussion_max_rounds", 5, 1, 50),
            "max_messages_per_agent": _bounded_int_setting(
                "subagent_discussion_max_messages_per_agent", 4, 1, 50
            ),
            "max_total_messages": _bounded_int_setting(
                "subagent_discussion_max_total_messages", 20, 1, 500
            ),
            "max_message_chars": _bounded_int_setting(
                "subagent_discussion_max_message_chars", 2000, 100, 20000
            ),
            "max_wall_seconds": _bounded_int_setting(
                "subagent_discussion_max_wall_seconds", 600, 30, 86400
            ),
            "max_tool_calls": _bounded_int_setting(
                "subagent_discussion_max_tool_calls", 50, 1, 1000
            ),
            "no_new_info_rounds": _bounded_int_setting(
                "subagent_discussion_no_new_info_rounds", 2, 1, 20
            ),
        }
    return {
        "max_tool_calls": _bounded_int_setting(
            "subagent_execution_max_tool_calls", 200, 1, 5000
        ),
        "max_wall_seconds": _bounded_int_setting(
            "subagent_execution_max_wall_seconds", 1800, 30, 86400
        ),
        "no_progress_turns": _bounded_int_setting(
            "subagent_execution_no_progress_turns", 3, 1, 20
        ),
        "checkpoint_calls": _bounded_int_setting(
            "subagent_execution_checkpoint_calls", 20, 1, 500
        ),
        "max_cost_usd": _bounded_float_setting(
            "subagent_execution_max_cost_usd", 5.0, 0.0, 1000.0
        ),
        "max_context_tokens": _optional_int_setting(
            "subagent_execution_max_context_tokens", 0, 4_000_000
        ),
    }


def _effective_subagent_context_limit(
    configured_limit: int,
    *,
    use_secondary: bool = False,
) -> int:
    from cyrene.config_store import get_current_ctx_limit, get_secondary_model

    if use_secondary:
        secondary = get_secondary_model() or {}
        model_limit = max(0, int(secondary.get("ctx_limit") or 0))
    else:
        model_limit = max(0, int(get_current_ctx_limit() or 0))
    configured = max(0, int(configured_limit or 0))
    if configured and model_limit:
        return min(configured, model_limit)
    return configured or model_limit


def _compact_subagent_context(
    messages: list[dict[str, Any]],
    *,
    max_context_tokens: int,
    reserved_tokens: int = 0,
) -> tuple[list[dict[str, Any]], int, int, bool]:
    """Mechanically compact a worker history while preserving its task contract."""
    from cyrene.agent.session import _compact_messages_for_storage
    from cyrene.call_llm import _message_token_estimate

    reserved = max(0, int(reserved_tokens or 0))
    before = (
        sum(_message_token_estimate(message) for message in messages)
        + reserved
    )
    if max_context_tokens <= 0 or before <= int(max_context_tokens * 0.60):
        return messages, before, before, False
    if not messages:
        return messages, before, before, False

    system_message = messages[0]
    system_tokens = _message_token_estimate(system_message)
    tail_limit = max(200, max_context_tokens - reserved - system_tokens)
    tail = _compact_messages_for_storage(
        list(messages[1:]),
        ctx_limit=tail_limit,
    )
    compacted = [system_message, *tail]
    after = (
        sum(_message_token_estimate(message) for message in compacted)
        + reserved
    )
    if after > int(max_context_tokens * 0.90):
        tail = _compact_messages_for_storage(
            list(messages[1:]),
            ctx_limit=tail_limit,
            force=True,
        )
        compacted = [system_message, *tail]
        after = (
            sum(_message_token_estimate(message) for message in compacted)
            + reserved
        )
    return compacted, before, after, compacted != messages


def _response_usage_cost_usd(response: dict[str, Any]) -> tuple[dict[str, int], float]:
    from cyrene.model_prices import effective_price, estimate_cost, to_usd

    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    normalized = {
        "prompt_tokens": max(0, int(usage.get("prompt_tokens") or 0)),
        "completion_tokens": max(0, int(usage.get("completion_tokens") or 0)),
        "total_tokens": max(0, int(usage.get("total_tokens") or 0)),
        "cache_hit_tokens": max(
            0,
            int(usage.get("prompt_cache_hit_tokens") or 0),
        ),
        "cache_miss_tokens": max(
            0,
            int(usage.get("prompt_cache_miss_tokens") or 0),
        ),
    }
    if not normalized["total_tokens"]:
        normalized["total_tokens"] = (
            normalized["prompt_tokens"] + normalized["completion_tokens"]
        )
    model = str(response.get("model") or "").strip()
    price = to_usd(effective_price(model))
    cost = estimate_cost(
        price,
        normalized["prompt_tokens"],
        normalized["completion_tokens"],
        cache_hit_tokens=normalized["cache_hit_tokens"],
        cache_miss_tokens=normalized["cache_miss_tokens"],
    )
    return normalized, max(0.0, float(cost))


def _tool_signature(capability_id: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{capability_id}:{payload}".encode("utf-8")).hexdigest()


def _result_fingerprint(result: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(result or "")).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _tool_result_is_progress(
    capability_id: str,
    signature: str,
    result_hash: str,
    *,
    seen_signatures: set[str],
    seen_results: set[str],
) -> bool:
    """Treat observation tools as progress only when they add new evidence."""
    normalized = str(capability_id or "").casefold()
    observation_markers = (
        "read", "search", "fetch", "list", "get", "query", "recall",
        "snapshot", "status", "check", "inspect", "analyze",
    )
    if any(marker in normalized for marker in observation_markers):
        return result_hash not in seen_results
    return signature not in seen_signatures or result_hash not in seen_results


def _message_fingerprint(content: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(content or "")).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _quit_reply(response: dict[str, Any]) -> str:
    for call in response.get("tool_calls") or []:
        if str((call.get("function") or {}).get("name") or "") != "quit":
            continue
        try:
            args = json.loads((call.get("function") or {}).get("arguments") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return ""
        reply = args.get("reply") if isinstance(args, dict) else ""
        return str(reply or "").strip()
    return ""


def _quit_arguments(response: dict[str, Any]) -> dict[str, Any]:
    for call in response.get("tool_calls") or []:
        if str((call.get("function") or {}).get("name") or "") != "quit":
            continue
        try:
            arguments = json.loads((call.get("function") or {}).get("arguments") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return arguments if isinstance(arguments, dict) else {}
    return {}


def _completion_evidence_missing(
    response: dict[str, Any],
    success_criteria: list[str],
) -> list[str]:
    """Return criteria without a non-empty completion evidence entry."""
    if not success_criteria:
        return []
    arguments = _quit_arguments(response)
    if str(arguments.get("completion_status") or "").strip().lower() != "completed":
        return []
    evidence = arguments.get("criteria_evidence")
    if not isinstance(evidence, list):
        return list(success_criteria)
    covered = {
        str(item.get("criterion") or "").strip()
        for item in evidence
        if isinstance(item, dict)
        and str(item.get("criterion") or "").strip()
        and str(item.get("evidence") or "").strip()
    }
    return [criterion for criterion in success_criteria if criterion not in covered]


def _communication_delivery_succeeded(capability_id: str, result: Any) -> bool:
    text = str(result or "").strip()
    if capability_id == "subagent.send_message":
        return text.startswith("Message sent to ")
    if capability_id == "subagent.broadcast":
        match = re.match(r"Broadcast sent to\s+(\d+)/(\d+)\s+peers", text)
        return bool(match and int(match.group(1)) > 0)
    return False


async def _record_delivered_communication(
    agent_id: str,
    *,
    tool_call_id: str,
    capability_id: str,
    arguments: dict[str, Any],
) -> None:
    content = str(arguments.get("content") or "").strip()
    if not content:
        return
    target = (
        "all"
        if capability_id == "subagent.broadcast"
        else str(arguments.get("to") or "").strip()
    )
    async with _lock:
        entry = _registry.get(agent_id)
        if not entry:
            return
        deliveries = entry.setdefault("delivered_communications", [])
        if any(str(item.get("tool_call_id") or "") == tool_call_id for item in deliveries):
            return
        deliveries.append({
            "tool_call_id": tool_call_id,
            "capability_id": capability_id,
            "to": target,
            "content": content,
        })
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()


async def cancel_subagent_tasks(round_id: str, session_id: str = "") -> None:
    """Cancel all running subagent tasks for *round_id* and settle them immediately.

    This is called when the user hits "stop" — subagents stop whatever they are
    doing (the asyncio task is cancelled) and their registry entry flips to a
    terminal incomplete state so the UI and summary phase see a
    consistent snapshot.
    """
    cancelled_ids: list[str] = []
    async with _lock:
        for agent_id, info in list(_registry.items()):
            if not _matches_round(info, round_id, session_id):
                continue
            if agent_id.startswith(_SUMMARY_AGENT_PREFIX):
                continue
            if info.get("status") in _TERMINAL_STATUSES:
                continue
            _registry[agent_id]["status"] = INCOMPLETE
            _registry[agent_id]["outcome"] = "cancelled"
            _registry[agent_id]["stop_reason"] = "user_cancelled"
            _registry[agent_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            cancelled_ids.append(agent_id)

    for agent_id in cancelled_ids:
        await _publish_registry_event(agent_id)
        task = _subagent_tasks.get(agent_id)
        if task is not None and not task.done():
            task.cancel()

    if cancelled_ids:
        await asyncio.sleep(0.1)  # brief yield so CancelledError can propagate


async def timeout_subagents(agent_ids: list[str], reason: str = "子代理执行超时。") -> None:
    """Settle and cancel a bounded set of still-active subagents."""
    ids: list[str] = []
    async with _lock:
        for agent_id in agent_ids:
            info = _registry.get(agent_id)
            if not info or agent_id.startswith(_SUMMARY_AGENT_PREFIX):
                continue
            if str(info.get("status") or "") in _TERMINAL_STATUSES:
                continue
            info["status"] = TIMEOUT
            info["outcome"] = "resource_exhausted"
            info["stop_reason"] = "parent_monitor_deadline"
            info["updated_at"] = datetime.now(timezone.utc).isoformat()
            info["result"] = str(reason)[:_limit(_MAX_FINAL_RESULT_CHARS)]
            ids.append(agent_id)
    cancelled_tasks: list[asyncio.Task[Any]] = []
    for agent_id in ids:
        await _publish_registry_event(agent_id, message=reason)
        task = _subagent_tasks.get(agent_id)
        if task is not None and not task.done():
            task.cancel()
            cancelled_tasks.append(task)
    if cancelled_tasks:
        await asyncio.gather(*cancelled_tasks, return_exceptions=True)


async def timeout_all_subagent_tasks(reason: str = "服务关闭，子代理已停止。") -> None:
    """Settle all active subagents before the web server tears down its loop."""
    async with _lock:
        ids = [
            agent_id for agent_id, info in _registry.items()
            if not agent_id.startswith(_SUMMARY_AGENT_PREFIX)
            and str(info.get("status") or "") in (RUNNING, RESUMED)
        ]
    await timeout_subagents(ids, reason=reason)


async def publish_active_heartbeat(
    *,
    session_id: str = "",
    round_id: str = "",
    message: str = "仍在执行。",
) -> list[str]:
    """Publish a lightweight progress pulse for active subagents."""
    async with _lock:
        ids = [
            agent_id for agent_id, info in _registry.items()
            if not agent_id.startswith(_SUMMARY_AGENT_PREFIX)
            and _matches_round(info, round_id=round_id, session_id=session_id)
            and str(info.get("status") or "") in (RUNNING, RESUMED)
        ]
    for agent_id in ids:
        await _publish_registry_event(agent_id, message=message)
    return ids


async def wait_until_settled(
    *,
    session_id: str = "",
    round_id: str = "",
    timeout: float = 300.0,
    poll_interval: float = 2.0,
    on_poll: Callable[[], Awaitable[bool]] | None = None,
) -> list[str]:
    """Block until every in-scope subagent stops actively working.

    A subagent counts as *settled* once it leaves the active states (``RUNNING``
    / ``RESUMED``) — i.e. it reached ``DONE`` / ``TIMEOUT``, or went idle in
    ``WAITING`` with no live orchestrator left to reactivate it. Summary
    subagents are ignored; the orchestrator spawns and awaits those itself.
    Scope is the ``round_id`` / ``session_id`` filter (same semantics as
    :func:`get_snapshot`); at least one is required or the call no-ops, so a
    caller can never accidentally block on every run's subagents at once.

    This exists because ``run_agent`` can return while subagents it spawned are
    still fire-and-forget tasks: its own monitoring loop caps at ~60s, and a
    spawn+quit in a single turn skips monitoring entirely. Callers that treat
    "run_agent returned" as "the work is finished" — e.g. the goal loop marking
    a step complete — use this to actually wait for that work to land.

    Returns the agent_ids still active when the wait ends: empty once everything
    settles, non-empty when ``timeout`` elapsed or ``on_poll`` aborted.
    ``on_poll`` is awaited once per cycle before sleeping; return ``False`` to
    stop early (e.g. the surrounding run was paused or cancelled).
    """
    if not session_id and not round_id:
        return []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)
    while True:
        async with _lock:
            active = [
                aid
                for aid, info in _registry.items()
                if not aid.startswith(_SUMMARY_AGENT_PREFIX)
                and _matches_round(info, round_id, session_id)
                and str(info.get("status") or "") in (RUNNING, RESUMED)
            ]
        if not active:
            return []
        if loop.time() >= deadline:
            return active
        if on_poll is not None and not await on_poll():
            return active
        await asyncio.sleep(poll_interval)


def _log_task_exception(task: asyncio.Task, agent_id: str) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Sub-agent %s task crashed before internal try/except", agent_id)


async def _run_subagent(
    agent_id: str,
    task: str,
    bot: Any,
    chat_id: int,
    db_path: str,
    resume_messages: list | None = None,
    use_secondary: bool = False,
    role: str = "",
    mode: str = "",
    success_criteria: list[str] | None = None,
) -> str:
    """Run a completion-driven execution worker or a bounded discussion peer."""
    from cyrene.agent.prompts import (
        _DEEP_RESEARCH_SUBAGENT_PROMPT,
        _DECISION_SUBAGENT_PROMPT, _LEARNING_SUBAGENT_PROMPT, _COMPARE_SUBAGENT_PROMPT,
        prompt_for_enabled_tool_packs,
        workspace_scope_block,
    )
    from cyrene.agent.state import (
        _deep_research_mode, _current_command,
        _call_llm, _caller_type, _current_agent_id, _current_round_id,
        _current_session_id, active_workspace_dir,
    )
    from cyrene.llm import _assistant_text, _truncate
    from cyrene.tooling import (
        execute_wire_tool,
        get_subagent_wire_tool_defs,
        resolve_wire_call,
    )
    from cyrene.tooling.gateway import (
        activate_catalog_snapshot,
        reset_catalog_snapshot,
    )

    caller_token = _caller_type.set(f"subagent_{agent_id}")
    catalog_snapshot_token = activate_catalog_snapshot("subagent")
    round_id = await get_round_id(agent_id)
    persisted_role = await get_role(agent_id)
    if not role:
        role = persisted_role
    persisted_mode = await get_mode(agent_id)
    effective_mode = _normalize_mode(mode or persisted_mode, role)
    discussion_id = await get_discussion_id(agent_id)
    criteria = _normalize_success_criteria(
        success_criteria if success_criteria is not None else await get_success_criteria(agent_id)
    )
    limits = _subagent_limits(effective_mode)
    if effective_mode == DISCUSSION_MODE:
        per_agent_override = await get_discussion_max_messages(agent_id)
        if per_agent_override is not None:
            limits["max_messages_per_agent"] = min(
                limits["max_messages_per_agent"],
                per_agent_override,
            )
    round_token = _current_round_id.set(round_id) if round_id else None
    dm_token = _direct_message_mode.set(False)
    _subagent_session_id = (
        await get_session_id(agent_id)
        or _current_session_id.get()
    )
    from cyrene.inbox import get_inbox_context as _get_inbox_base, mark_all_read as _mark_inbox_read_base

    def _get_inbox(agent_id: str) -> str:
        return _get_inbox_base(agent_id, session_id=_subagent_session_id)

    async def _mark_inbox_read(agent_id: str) -> None:
        await _mark_inbox_read_base(agent_id, session_id=_subagent_session_id)

    cmd = _current_command.get()
    if cmd == "help-me-decide":
        extra_prompt = _DECISION_SUBAGENT_PROMPT
    elif cmd == "learning-plan":
        extra_prompt = _LEARNING_SUBAGENT_PROMPT
    elif cmd == "deep-compare":
        extra_prompt = _COMPARE_SUBAGENT_PROMPT
    elif _deep_research_mode.get():
        extra_prompt = _DEEP_RESEARCH_SUBAGENT_PROMPT
    else:
        extra_prompt = ""
    now = datetime.now(timezone.utc).astimezone()
    temporal_context = (
        "## Current Date\n"
        f"- Current local date: {now:%Y-%m-%d} ({now:%A}).\n"
        "- Interpret relative phrases such as today, recently, this week, last week, 最近, 最近一周, 今天, 本周 relative to this date.\n"
        "- For current weather or travel recommendations, search for current forecast/current conditions. Do not invent or substitute old years unless the user explicitly asks for historical weather."
    )
    criteria_block = (
        "\n".join(f"- {item}" for item in criteria)
        if criteria else
        "- Complete the assigned task and return the requested result or artifact."
    )
    mode_block = (
        f"""## Execution Worker Mode
- There is no normal model-turn or tool-round limit. Continue while the task is incomplete and the execution lease is making useful progress.
- Finish as soon as the success criteria are satisfied. More searching or rereading is not inherently better.
- Use the minimum sufficient tool calls. Do not reread an unchanged file or repeat an equivalent search unless prior evidence shows a concrete reason.
- The lease checkpoints every {limits["checkpoint_calls"]} tool calls. New evidence, state change, or a completed acceptance item renews it.
- If a checkpoint shows no progress, change approach. After {limits["no_progress_turns"]} consecutive no-progress tool rounds, return the best partial/blocked result.
- Absolute safety fuses are {limits["max_tool_calls"]} actual tool calls, {limits["max_wall_seconds"]} seconds, and ${limits["max_cost_usd"]:.2f} estimated model cost (0 disables the cost fuse). They are resource guards, never normal completion targets.
- Context is mechanically compacted before it reaches {limits["max_context_tokens"] or "the active model's configured"} token window; the task contract and recent evidence are retained."""
        if effective_mode == EXECUTION_MODE else
        f"""## Discussion Worker Mode
- Contribute substantive arguments, evidence, or synthesis. Do not perform unbounded execution work.
- Each communication turn may send at most ONE targeted peer message or ONE broadcast.
- Runtime limits: at most {limits["max_rounds"]} discussion rounds, {limits["max_messages_per_agent"]} messages from you, {limits["max_total_messages"]} messages across the discussion, {limits["max_message_chars"]} characters per message, and {limits["max_wall_seconds"]} seconds.
- Do not send greetings, readiness checks, acknowledgements without content, or repeated points.
- When the topic has enough coverage or a runtime limit is reached, summarize your position and call `quit`."""
    )
    peer_block = (
        """## Peer Communication
- Use subagent.send_message for one peer or subagent.broadcast for all peers, never both in the same turn.
- Prefer targeted messages. Broadcast only information every peer genuinely needs.
- Read substantive peer messages and incorporate relevant evidence."""
        if effective_mode == DISCUSSION_MODE else
        """## Peer Communication
- Execution workers are independent and must not send peer messages or broadcasts.
- Return findings to the parent through quit."""
    )
    subagent_prompt = f"""You are a Cyrene sub-agent with a single assigned responsibility.

## Task Contract
- Complete only the assigned task. Keep its acceptance criteria in view.
- Success criteria:
{criteria_block}
- Concrete deferred capabilities are behind module gateways. Use operation=discover, then describe, then invoke.
- You cannot ask the user, spawn subagents, query the parent round, or deliver the parent agent's final answer.
- If your task produces a file, write it inside the workspace and report the path in your `quit` result.
- For a normal round, return the complete result through `quit(reply=...)`. The parent collects that reply.
- If you receive a [DIRECT_MESSAGE] from the user, acknowledge it once through delivery.send_message_to_user when available, then adjust the work immediately.
- Every tool call must be grounded in the assigned task. Do not fabricate tool success or evidence.

{mode_block}

{peer_block}
- When done, call `quit` immediately. Do not wait for permission.

{extra_prompt}"""

    if role == "moderator":
        subagent_prompt += """
## Your Role: Moderator
You are the **moderator** of this discussion. Your responsibilities:
1. **Start immediately.** Your FIRST message must announce the topic and kick off the discussion. Do NOT wait for participants to confirm readiness — they are already listening.
2. **Drive the discussion.** Call on participants by name, pose questions, redirect off-topic threads, and keep things moving.
3. **Address one participant per turn.** Each turn, talk to ONE specific participant via `subagent.send_message`. Do NOT address multiple participants in the same message — if something concerns everyone, use `subagent.broadcast` instead.
4. **Summarize and close.** When the discussion has covered enough ground, synthesize key points and wrap up.

CRITICAL: Do NOT ask "is everyone ready?" or wait for confirmations. All participants are live and listening from the moment you speak. Begin the discussion in your very first turn.
"""
    elif role == "participant":
        subagent_prompt += """
## Your Role: Participant
You are a **participant** in this discussion. Rules:
1. **No readiness announcements.** Do NOT send "ready", "waiting", "standing by", or any greeting/confirmation. These are prohibited.
2. **Respond substantively.** When the moderator or another participant addresses you, reply with actual content — arguments, evidence, opinions. Never reply with just an acknowledgment.
3. **One person per reply.** Reply to ONE agent per turn via `subagent.send_message`. If your point truly concerns everyone, use `subagent.broadcast` instead. Do not send multiple individual replies.
4. **Engage proactively.** If you have something relevant to say, speak up via `subagent.send_message`. Don't wait to be called on for every point.
5. **Stay in character.** Focus on delivering value through the substance of your contributions.
"""

    wire_tool_defs = get_subagent_wire_tool_defs()
    enabled_wire_names = {
        str((tool_def.get("function") or {}).get("name") or "")
        for tool_def in wire_tool_defs
        if str((tool_def.get("function") or {}).get("name") or "").endswith(
            "_tools"
        )
    }
    subagent_prompt = prompt_for_enabled_tool_packs(
        subagent_prompt,
        enabled_wire_names,
    )
    try:
        from cyrene.shell_runtime import resolve_shell
        _shell_kind = resolve_shell()[0]
    except Exception:
        _shell_kind = "bash"
    subagent_prompt += (
        "\n\n"
        + temporal_context
        + "\n\n"
        + prompt_for_enabled_tool_packs(
            workspace_scope_block(
                active_workspace_dir(),
                shell_kind=_shell_kind,
            ),
            enabled_wire_names,
        )
    )
    workbench_context = ""
    if _subagent_session_id:
        try:
            from cyrene.workbench_task_context import build_subagent_context, resolve_task_scope

            _payload, workbench_project, workbench_session = resolve_task_scope(
                _subagent_session_id,
                db_path=db_path,
            )
            workbench_context = build_subagent_context(workbench_project, workbench_session, task)
            if workbench_context:
                subagent_prompt += "\n\n" + workbench_context
        except Exception:
            logger.debug("Failed to inject Workbench task context for sub-agent %s", agent_id, exc_info=True)

    if resume_messages:
        # 被唤醒：从已有历史续跑，注入一条提示让 LLM 知道发生了什么
        messages = list(resume_messages)
        for index, message in enumerate(messages):
            if (
                isinstance(message, dict)
                and str(message.get("role") or "") == "system"
            ):
                messages[index] = {
                    **message,
                    "content": subagent_prompt,
                }
                break
        messages.append({"role": "user", "content": "[你已被唤醒 — inbox 中有新消息需要处理。处理完后再决定是否 quit。]"})
        if workbench_context:
            messages.append({"role": "user", "content": "[Workbench 任务共享上下文已刷新]\n" + workbench_context})
    else:
        messages = [
            {"role": "system", "content": subagent_prompt},
            {"role": "user", "content": task},
        ]

    await set_running(agent_id)

    final_text = ""
    stop_reason = "completed"
    incomplete_outcome = ""
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    async with _lock:
        persisted_metrics = dict((_registry.get(agent_id, {}).get("metrics") or {}))
    model_turns = int(persisted_metrics.get("model_turns") or 0)
    total_tool_calls = int(persisted_metrics.get("tool_calls") or 0)
    tool_calls_used = int(
        persisted_metrics.get("lease_tool_calls")
        if persisted_metrics.get("lease_tool_calls") is not None
        else total_tool_calls
    )
    no_progress_turns = int(persisted_metrics.get("no_progress_turns") or 0)
    prompt_tokens = int(persisted_metrics.get("prompt_tokens") or 0)
    completion_tokens = int(persisted_metrics.get("completion_tokens") or 0)
    total_tokens = int(persisted_metrics.get("total_tokens") or 0)
    estimated_cost_usd = float(persisted_metrics.get("estimated_cost_usd") or 0.0)
    lease_estimated_cost_usd = float(
        persisted_metrics.get("lease_estimated_cost_usd") or 0.0
    )
    context_compactions = int(persisted_metrics.get("context_compactions") or 0)
    context_limit = (
        _effective_subagent_context_limit(
            limits.get("max_context_tokens", 0),
            use_secondary=use_secondary,
        )
        if effective_mode == EXECUTION_MODE else
        0
    )
    discussion_state = (
        await _get_discussion_state(agent_id)
        if effective_mode == DISCUSSION_MODE else
        {}
    )
    discussion_rounds = int(discussion_state.get("rounds") or 0)
    discussion_messages = int(persisted_metrics.get("discussion_messages") or 0)
    discussion_no_new_info_rounds = int(
        discussion_state.get("no_new_info_rounds") or 0
    )
    next_checkpoint = limits.get("checkpoint_calls", 0)
    seen_tool_signatures: set[str] = set()
    seen_result_fingerprints: set[str] = set()
    force_finalize_reason = ""
    finalization_requested = False
    quit_tool_defs = [
        tool_def
        for tool_def in wire_tool_defs
        if str((tool_def.get("function") or {}).get("name") or "") == "quit"
    ]

    def _resolved_subagent_call(
        name: str,
        args: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        try:
            resolution = resolve_wire_call(name, args, actor="subagent")
            return resolution.capability_id, resolution.concrete_arguments
        except Exception:
            return str(name or ""), dict(args)

    async def _save_if_registered() -> None:
        """Keep registry messages resumable after any local history mutation."""
        await save_messages(agent_id, messages)

    try:
        while True:
            elapsed = loop.time() - started_at
            if effective_mode == EXECUTION_MODE:
                if not force_finalize_reason and elapsed >= limits["max_wall_seconds"]:
                    force_finalize_reason = "execution_wall_time_safety_limit"
                    incomplete_outcome = "resource_exhausted"
                if not force_finalize_reason and tool_calls_used >= limits["max_tool_calls"]:
                    force_finalize_reason = "execution_tool_call_safety_limit"
                    incomplete_outcome = "resource_exhausted"
                if (
                    not force_finalize_reason
                    and limits["max_cost_usd"] > 0
                    and lease_estimated_cost_usd >= limits["max_cost_usd"]
                ):
                    force_finalize_reason = "execution_cost_safety_limit"
                    incomplete_outcome = "resource_exhausted"
                if (
                    not force_finalize_reason
                    and no_progress_turns >= limits["no_progress_turns"]
                ):
                    force_finalize_reason = "execution_no_progress"
                    incomplete_outcome = "partial"
            else:
                discussion_state = await _get_discussion_state(agent_id)
                discussion_rounds = int(discussion_state.get("rounds") or 0)
                discussion_no_new_info_rounds = int(
                    discussion_state.get("no_new_info_rounds") or 0
                )
                if not force_finalize_reason and elapsed >= limits["max_wall_seconds"]:
                    force_finalize_reason = "discussion_wall_time_limit"
                if not force_finalize_reason and tool_calls_used >= limits["max_tool_calls"]:
                    force_finalize_reason = "discussion_tool_call_limit"
                if not force_finalize_reason and discussion_rounds >= limits["max_rounds"]:
                    force_finalize_reason = "discussion_round_limit"
                if (
                    not force_finalize_reason
                    and discussion_messages >= limits["max_messages_per_agent"]
                ):
                    force_finalize_reason = "discussion_message_limit_per_agent"
                if (
                    not force_finalize_reason
                    and await _discussion_message_total(agent_id) >= limits["max_total_messages"]
                ):
                    force_finalize_reason = "discussion_message_limit_total"
                if (
                    not force_finalize_reason
                    and discussion_no_new_info_rounds >= limits["no_new_info_rounds"]
                ):
                    force_finalize_reason = "discussion_no_new_information"

            # 每次 LLM 调用前注入注册表和 inbox 作为独立消息，保持 messages[0] 稳定
            registry_ctx = (
                await get_context(
                    exclude=agent_id,
                    round_id=round_id,
                    discussion_id=discussion_id,
                    session_id=_subagent_session_id,
                    strict_session=True,
                )
                if effective_mode == DISCUSSION_MODE else ""
            )
            inbox_text = _get_inbox(agent_id)

            # 移除上一轮的旧上下文消息（以特定前缀开头的用户消息）
            messages = [m for m in messages if not (
                m.get("role") == "user" and (
                    str(m.get("content", "")).startswith("[活跃子 agent]") or
                    str(m.get("content", "")).startswith("[收件箱]") or
                    str(m.get("content", "")).startswith("[Execution Checkpoint]") or
                    str(m.get("content", "")).startswith("[Runtime Finalization]")
                )
            )]
            # 注入新上下文
            if registry_ctx:
                messages.append({"role": "user", "content": registry_ctx})
            if inbox_text:
                if _direct_message_mode.get():
                    # 正在处理用户引导：丢弃所有 inbox 消息（含 agent 间通信），
                    # 让 subagent 专注执行用户指令不被干扰。
                    await _mark_inbox_read(agent_id)
                else:
                    messages.append({"role": "user", "content": f"[收件箱]\n{inbox_text}"})
                    # 注入后立即标记为已读 —— 避免下一轮重复展示同一批消息
                    await _mark_inbox_read(agent_id)
                    _direct_message_mode.set("[DIRECT_MESSAGE]" in inbox_text)

            if (
                effective_mode == EXECUTION_MODE
                and next_checkpoint
                and tool_calls_used >= next_checkpoint
                and not force_finalize_reason
            ):
                messages.append({
                    "role": "user",
                    "content": (
                        "[Execution Checkpoint]\n"
                        f"You have executed {tool_calls_used} tools. Re-check the success criteria now. "
                        "Continue only if a concrete criterion remains unmet and the next action can produce new evidence or state change. "
                        "Otherwise call quit with the result."
                    ),
                })
                next_checkpoint += limits["checkpoint_calls"]

            if force_finalize_reason and not finalization_requested:
                finalization_requested = True
                messages.append({
                    "role": "user",
                    "content": (
                        "[Runtime Finalization]\n"
                        f"Stop reason: {force_finalize_reason}. Do not call additional work tools. "
                        "Return the best available result through quit(reply=...). "
                        "State what is complete, what remains, and any blocker."
                    ),
                })

            if effective_mode == EXECUTION_MODE and context_limit > 0:
                from cyrene.call_llm import _approx_token_count

                active_tool_defs = (
                    quit_tool_defs if finalization_requested else wire_tool_defs
                )
                reserved_tool_tokens = _approx_token_count(
                    json.dumps(
                        active_tool_defs,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                )
                compacted_messages, context_before, context_after, compacted = (
                    _compact_subagent_context(
                        messages,
                        max_context_tokens=context_limit,
                        reserved_tokens=reserved_tool_tokens,
                    )
                )
                if compacted:
                    messages = compacted_messages
                    context_compactions += 1
                    await _save_if_registered()
                await _update_metrics(
                    agent_id,
                    context_tokens_before=context_before,
                    context_tokens_after=context_after,
                    context_compactions=context_compactions,
                )
                if context_after > context_limit:
                    final_text = (
                        "Stopped before completion: the task contract and minimum "
                        "tool schema exceed the execution context safety ceiling."
                    )
                    stop_reason = "execution_context_safety_limit"
                    incomplete_outcome = "resource_exhausted"
                    await _save_if_registered()
                    break

            response = await _call_llm(
                messages,
                tools=quit_tool_defs if finalization_requested else wire_tool_defs,
                max_tokens=None,
                secondary=use_secondary,
            )
            model_turns += 1
            turn_usage, turn_cost_usd = _response_usage_cost_usd(response)
            prompt_tokens += turn_usage["prompt_tokens"]
            completion_tokens += turn_usage["completion_tokens"]
            total_tokens += turn_usage["total_tokens"]
            estimated_cost_usd += turn_cost_usd
            lease_estimated_cost_usd += turn_cost_usd
            await _update_metrics(
                agent_id,
                model_turns=model_turns,
                tool_calls=total_tool_calls,
                lease_tool_calls=tool_calls_used,
                no_progress_turns=no_progress_turns,
                discussion_rounds=discussion_rounds,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=estimated_cost_usd,
                lease_estimated_cost_usd=lease_estimated_cost_usd,
                context_compactions=context_compactions,
            )

            entry: dict = {"role": "assistant", "content": response.get("content") or ""}
            if response.get("reasoning_content"):
                entry["reasoning_content"] = response["reasoning_content"]
            if response.get("tool_calls"):
                entry["tool_calls"] = response["tool_calls"]
            if response.get("usage"):
                entry["usage"] = response["usage"]
            messages.append(entry)

            # Save messages to registry for WebUI display
            await _save_if_registered()

            tcs = response.get("tool_calls") or []

            # 检测 quit 或纯文本（活干完了）
            has_quit = any(t.get("function", {}).get("name") == "quit" for t in tcs)
            quit_completion_status = str(
                _quit_arguments(response).get("completion_status") or ""
            ).strip().lower()
            invalid_completion_status = bool(
                has_quit
                and effective_mode == EXECUTION_MODE
                and criteria
                and not finalization_requested
                and quit_completion_status not in {"completed", "partial", "blocked"}
            )
            missing_completion_evidence = (
                _completion_evidence_missing(response, criteria)
                if (
                    has_quit
                    and effective_mode == EXECUTION_MODE
                    and not finalization_requested
                )
                else []
            )
            if invalid_completion_status or missing_completion_evidence:
                remediation = json.dumps({
                    "status": "error",
                    "reason": (
                        "completion_status_missing"
                        if invalid_completion_status else
                        "completion_evidence_missing"
                    ),
                    "missing_criteria": missing_completion_evidence,
                    "remediation": (
                        "Call quit with completion_status=completed, partial, or blocked. "
                        "A completed status also requires one non-empty criteria_evidence "
                        "item for every success criterion."
                    ),
                }, ensure_ascii=False)
                for tc in tcs:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id") or f"completion_{model_turns}",
                        "content": (
                            remediation
                            if tc.get("function", {}).get("name") == "quit"
                            else "Skipped because the same batch contained an invalid terminal quit."
                        ),
                    })
                await _save_if_registered()
                continue

            if (
                not tcs
                and effective_mode == EXECUTION_MODE
                and criteria
                and not finalization_requested
            ):
                final_text = _assistant_text(response).strip() or "No completion evidence was provided."
                stop_reason = "completion_evidence_missing"
                incomplete_outcome = "partial"
                break

            should_exit = has_quit or not tcs
            if should_exit:
                for tc in tcs:
                    is_quit = tc.get("function", {}).get("name") == "quit"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id") or f"terminal_{model_turns}",
                        "content": (
                            "Interaction ended."
                            if is_quit else
                            "Skipped because the same batch contained terminal quit."
                        ),
                    })
                if tcs:
                    await _save_if_registered()

                # Include all sent agent messages in the final text so that
                # creative output (poems, reviews, etc.) is preserved in the
                # registry result and shown in the final synthesis.
                async with _lock:
                    delivered = list(
                        (_registry.get(agent_id, {}).get("delivered_communications") or [])
                    )
                sent_output = [
                    f"[to {item.get('to') or '?'}]\n{item.get('content')}"
                    for item in delivered
                    if str(item.get("content") or "").strip()
                ]
                agent_text = (
                    _assistant_text(response).strip()
                    or _quit_reply(response)
                    or "Done."
                )
                if sent_output:
                    final_text = agent_text + "\n\n---\n\n" + "\n\n".join(sent_output)
                else:
                    final_text = agent_text

                if finalization_requested:
                    stop_reason = force_finalize_reason or "runtime_finalization"
                    break
                if (
                    effective_mode == EXECUTION_MODE
                    and criteria
                    and quit_completion_status in {"partial", "blocked"}
                ):
                    stop_reason = f"subagent_reported_{quit_completion_status}"
                    incomplete_outcome = quit_completion_status
                    break

                # 标记 willing_to_quit（带 result），等别人（每 5 秒检查 inbox）
                inbox_msg = await wait_for_others(agent_id, _get_inbox, mark_read_func=_mark_inbox_read, result=final_text)
                if inbox_msg == "":
                    break  # 全部 finished，正常退出
                elif inbox_msg == "timeout":
                    break  # 超时，强制退出
                else:
                    # 有新消息，标记 RESUMED，继续干活
                    await set_resumed(agent_id)
                    messages.append({"role": "user", "content": f"[等待期间收到新消息]\n{inbox_msg}"})
                    _direct_message_mode.set("[DIRECT_MESSAGE]" in str(inbox_msg))
                    await _save_if_registered()
                    continue

            if finalization_requested:
                # A provider should only return quit when quit is the sole visible
                # tool. Preserve protocol pairing even if it hallucinates another
                # call, then settle without granting another work turn.
                for tc in tcs:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id") or f"finalize_{model_turns}",
                        "content": "Skipped: runtime finalization allows only quit.",
                    })
                final_text = _assistant_text(response).strip() or (
                    f"Stopped before completion: {force_finalize_reason}."
                )
                stop_reason = force_finalize_reason or "runtime_finalization"
                await _save_if_registered()
                break

            fresh_inbox = False
            cancel_remaining_batch = False
            round_had_execution_work = False
            round_made_progress = False
            for tc in tcs:
                name = tc["function"]["name"]
                discussion_slot_claimed = False
                counted_tool_call = False
                if cancel_remaining_batch:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "Skipped because new inbox guidance superseded the remaining tool-call batch.",
                    })
                    continue
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                    capability_id, concrete_args = _resolved_subagent_call(name, args)
                    is_communication = capability_id in {
                        "subagent.send_message",
                        "subagent.broadcast",
                    }
                    if (
                        tool_calls_used >= limits["max_tool_calls"]
                    ):
                        result = json.dumps({
                            "status": "skipped",
                            "reason": (
                                "execution_tool_call_safety_limit"
                                if effective_mode == EXECUTION_MODE
                                else "discussion_tool_call_limit"
                            ),
                        })
                        force_finalize_reason = (
                            "execution_tool_call_safety_limit"
                            if effective_mode == EXECUTION_MODE
                            else "discussion_tool_call_limit"
                        )
                        if effective_mode == EXECUTION_MODE:
                            incomplete_outcome = "resource_exhausted"
                    elif is_communication and effective_mode == EXECUTION_MODE:
                        tool_calls_used += 1
                        total_tool_calls += 1
                        counted_tool_call = True
                        result = json.dumps({
                            "status": "error",
                            "reason": "communication_requires_discussion_mode",
                            "remediation": (
                                "Execution workers are independent. Return your result through quit; "
                                "peer communication requires a discussion-mode worker."
                            ),
                        })
                        round_had_execution_work = True
                    elif is_communication and effective_mode == DISCUSSION_MODE:
                        content = str(concrete_args.get("content", "") or "")
                        if len(content) > limits["max_message_chars"]:
                            tool_calls_used += 1
                            total_tool_calls += 1
                            counted_tool_call = True
                            result = json.dumps({
                                "status": "skipped",
                                "reason": "discussion_message_too_long",
                                "max_message_chars": limits["max_message_chars"],
                                "actual_message_chars": len(content),
                            })
                        else:
                            allowed, denied_reason = await _claim_discussion_message_slot(
                                agent_id,
                                max_per_agent=limits["max_messages_per_agent"],
                                max_total=limits["max_total_messages"],
                            )
                            if not allowed:
                                result = json.dumps({
                                    "status": "skipped",
                                    "reason": denied_reason,
                                })
                                force_finalize_reason = denied_reason
                            else:
                                discussion_slot_claimed = True
                                tool_calls_used += 1
                                total_tool_calls += 1
                                counted_tool_call = True
                                discussion_messages += 1
                                token = _current_agent_id.set(agent_id)
                                try:
                                    result = await execute_wire_tool(
                                        name, args, bot, chat_id, db_path, None, actor="subagent"
                                    )
                                finally:
                                    _current_agent_id.reset(token)
                                if _communication_delivery_succeeded(capability_id, result):
                                    await _record_delivered_communication(
                                        agent_id,
                                        tool_call_id=str(tc.get("id") or ""),
                                        capability_id=capability_id,
                                        arguments=concrete_args,
                                    )
                                    discussion_state = await _record_discussion_delivery(
                                        agent_id,
                                        content,
                                    )
                                    discussion_rounds = int(
                                        discussion_state.get("rounds") or 0
                                    )
                                    discussion_no_new_info_rounds = int(
                                        discussion_state.get("no_new_info_rounds") or 0
                                    )
                                else:
                                    await _release_discussion_message_slot(agent_id)
                                    discussion_slot_claimed = False
                                    discussion_messages = max(
                                        0,
                                        discussion_messages - 1,
                                    )
                    else:
                        tool_calls_used += 1
                        total_tool_calls += 1
                        counted_tool_call = True
                        token = _current_agent_id.set(agent_id)
                        try:
                            result = await execute_wire_tool(
                                name, args, bot, chat_id, db_path, None, actor="subagent"
                            )
                        finally:
                            _current_agent_id.reset(token)
                        if not is_communication:
                            round_had_execution_work = True
                            signature = _tool_signature(capability_id, concrete_args)
                            result_hash = _result_fingerprint(result)
                            if _tool_result_is_progress(
                                capability_id,
                                signature,
                                result_hash,
                                seen_signatures=seen_tool_signatures,
                                seen_results=seen_result_fingerprints,
                            ):
                                round_made_progress = True
                            seen_tool_signatures.add(signature)
                            seen_result_fingerprints.add(result_hash)
                except Exception as e:
                    if not counted_tool_call:
                        tool_calls_used += 1
                        total_tool_calls += 1
                    if discussion_slot_claimed:
                        await _release_discussion_message_slot(agent_id)
                        discussion_messages = max(0, discussion_messages - 1)
                    result = f"Tool {name} failed: {e}"
                    capability_id = str(name or "")
                    if effective_mode == EXECUTION_MODE:
                        round_had_execution_work = True
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": _truncate(result)})
                # 每执行完一个工具检查 inbox，用户引导时能更快响应
                inbox_text = _get_inbox(agent_id)
                if inbox_text:
                    fresh_inbox = True
                    cancel_remaining_batch = True

            if effective_mode == EXECUTION_MODE and round_had_execution_work:
                no_progress_turns = 0 if round_made_progress else no_progress_turns + 1
            await _update_metrics(
                agent_id,
                model_turns=model_turns,
                tool_calls=total_tool_calls,
                lease_tool_calls=tool_calls_used,
                no_progress_turns=no_progress_turns,
                discussion_rounds=discussion_rounds,
                discussion_no_new_info_rounds=discussion_no_new_info_rounds,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=estimated_cost_usd,
                lease_estimated_cost_usd=lease_estimated_cost_usd,
                context_compactions=context_compactions,
            )
            if fresh_inbox:
                await _save_if_registered()
                continue
            if tcs:
                await _save_if_registered()
    except Exception as e:
        logger.exception("Sub-agent %s crashed", agent_id)
        final_text = f"Sub-agent crashed: {e}"
        stop_reason = "subagent_crashed"
        incomplete_outcome = "blocked"
    finally:
        reset_catalog_snapshot(catalog_snapshot_token)
        _caller_type.reset(caller_token)
        _direct_message_mode.reset(dm_token)
        if round_token is not None:
            _current_round_id.reset(round_token)

    if _subagent_session_id and final_text:
        try:
            from cyrene.workbench_task_context import append_shared_outcome

            append_shared_outcome(
                db_path=db_path,
                session_id=_subagent_session_id,
                agent_id=agent_id,
                source="subagent",
                text=final_text,
            )
        except Exception:
            logger.debug("Failed to append Workbench shared outcome for sub-agent %s", agent_id, exc_info=True)
    if incomplete_outcome:
        await mark_incomplete(
            agent_id,
            final_text,
            reason=stop_reason,
            outcome=incomplete_outcome,
        )
    else:
        await mark_done(agent_id, final_text, reason=stop_reason)
    return final_text
