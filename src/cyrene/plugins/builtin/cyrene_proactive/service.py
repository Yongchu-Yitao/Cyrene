"""Editable proactive Agent policy, state, execution, and delivery service."""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from cyrene.core.plugin import application_plugin_service
from cyrene.workbench.core_adapter import ConversationConfig, ConversationRuntime, WorkbenchChatResult
from cyrene.config import OWNER_ID, WORKSPACE_DIR
from cyrene.localization import localized
from cyrene.platform.notifications import notify
from cyrene.platform.run_coordinator import run_coordinator_for
from cyrene.workbench.chat.chat_repository import ChatRepository
from cyrene.workbench.application.notifications import append_notification

from .outcome import TOOL_NAME as OUTCOME_TOOL_NAME, outcome_from_result
from .projection import create_proactive_chat

logger = logging.getLogger(__name__)
_workbench_db_path = ""


def _memory_service():
    return application_plugin_service("memory")


def _is_workbench_conversation_running(db_path: str, session_id: str) -> bool:
    """Check the Plugin run coordinator without consulting the retired Agent."""

    target = str(session_id or "").strip()
    return bool(
        target
        and run_coordinator_for(str(db_path or "")).get("conversation", target)
        is not None
    )


def _proactive_language_instruction(lang: str) -> str:
    normalized = str(lang or "").strip().lower()
    if normalized.startswith("zh"):
        return "Write the finish_proactive report in Simplified Chinese."
    if normalized.startswith("en"):
        return "Write the finish_proactive report in English."
    return (
        "Write the finish_proactive report in the language the user normally "
        "uses in their recent context."
    )


async def _run_plugin_proactive_turn(
    prompt: str,
    *,
    bot,
    owner_id: int,
    db_path: str,
    session_id: str,
    workspace_dir: str,
    project_id: str = "",
    session_title: str = "",
    lang: str = "",
) -> WorkbenchChatResult:
    """Run one scheduler turn through the same Plugin Agent as Workbench Chat."""

    runtime = ConversationRuntime(str(db_path or ""))
    memory_snapshot = None
    memory_service = _memory_service()
    snapshot_loader = getattr(memory_service, "current_snapshot", None)
    if callable(snapshot_loader):
        loaded = await asyncio.to_thread(snapshot_loader, str(project_id or ""))
        if isinstance(loaded, dict):
            memory_snapshot = dict(loaded)
    config = ConversationConfig(
        session_id=str(session_id),
        workspace_dir=str(workspace_dir or WORKSPACE_DIR),
        db_path=str(db_path or ""),
        bot=bot,
        host_chat_id=owner_id,
        permission_mode="auto",
        command="proactive-heartbeat",
        public_user_message="",
        workspace_enabled=True,
        system_extra=_proactive_language_instruction(lang),
        project_id=str(project_id or ""),
        project_memory_snapshot=memory_snapshot,
        session_title=str(session_title or ""),
        memory_write_enabled=True,
        memory_trigger_enabled=False,
        memory_archive_enabled=True,
        conversation_source="scheduler",
        extra_direct_tool_names=(OUTCOME_TOOL_NAME,),
    )
    run_id = f"proactive_run_{uuid.uuid4().hex}"
    return await runtime.send(
        config,
        str(prompt or ""),
        run_id=run_id,
        metadata={
            "system_initiated": True,
            "proactive": True,
            "source": "scheduler",
        },
    )


# ---------------------------------------------------------------------------
# Lottery state  (persisted to disk)
# ---------------------------------------------------------------------------

_LOTTERY_STATE: dict[str, float] = {
    "probability": 0.0,           # current draw probability 0.0 .. 1.0
    "delta": 0.15,                # increment on each failed draw
    "max_probability": 0.85,      # ceiling for the accumulated probability
    "consecutive_unanswered": 0,  # count of consecutive unanswered proactive messages
    "cooldown_until": 0.0,        # Unix timestamp: suppress proactive until this time
    "last_proactive_time": 0.0,   # Unix timestamp: when last proactive message was sent
}
_LOTTERY_SETTING_KEY = "proactive_lottery_state"

# If this many consecutive proactive messages go unanswered, enter cooldown.
_PROACTIVE_COOLDOWN_THRESHOLD: int = 2
# Duration of the cooldown period in seconds (3 days).
_PROACTIVE_COOLDOWN_SECONDS: int = 3 * 86400

# Proactive work may inspect project state and use several tools before it has
# a material result.  The old 120-second limit routinely cancelled otherwise
# healthy runs.  Keep this below the enclosing Plugin timeout so cancellation
# and cleanup remain owned by the proactive service.
_PROACTIVE_GENERATION_TIMEOUT_SECONDS: float = 240.0

# Big-heartbeat cadence: perform proactive checks.
# Read from web_settings.json (default 1800s = 30 min).
_HEARTBEAT_INTERVAL_SECONDS: int = 0  # lazy-loaded on first use


def _get_heartbeat_interval() -> int:
    global _HEARTBEAT_INTERVAL_SECONDS
    if not _HEARTBEAT_INTERVAL_SECONDS:
        try:
            from cyrene.platform.settings_store import get
            _HEARTBEAT_INTERVAL_SECONDS = int(get("heartbeat_interval", 1800) or 1800)
        except Exception:
            _HEARTBEAT_INTERVAL_SECONDS = 1800
    return _HEARTBEAT_INTERVAL_SECONDS


def _load_lottery_state() -> None:
    """Restore lottery state from the runtime settings store."""
    global _LOTTERY_STATE
    try:
        from cyrene.platform.settings_store import get

        data = get(_LOTTERY_SETTING_KEY, {})
        if isinstance(data, dict):
            _LOTTERY_STATE["probability"] = float(data.get("probability", 0.0))
            _LOTTERY_STATE["delta"] = float(data.get("delta", 0.15))
            _LOTTERY_STATE["max_probability"] = float(data.get("max_probability", 0.85))
            _LOTTERY_STATE["consecutive_unanswered"] = int(data.get("consecutive_unanswered", 0))
            _LOTTERY_STATE["cooldown_until"] = float(data.get("cooldown_until", 0.0))
            _LOTTERY_STATE["last_proactive_time"] = float(data.get("last_proactive_time", 0.0))
            logger.debug(
                "Loaded lottery state: probability=%.2f consecutive_unanswered=%d cooldown_until=%.0f",
                _LOTTERY_STATE["probability"],
                _LOTTERY_STATE["consecutive_unanswered"],
                _LOTTERY_STATE["cooldown_until"],
            )
    except Exception:
        logger.exception("Failed to load lottery state, using defaults")


def _save_lottery_state() -> None:
    """Persist current lottery state through the settings boundary."""
    try:
        from cyrene.platform.settings_store import set_

        set_(_LOTTERY_SETTING_KEY, dict(_LOTTERY_STATE))
    except Exception:
        logger.exception("Failed to save lottery state")


def reset_lottery() -> None:
    """Reset the lottery state when the user sends a message.

    Clears the accumulated probability, the consecutive-unanswered counter,
    and any active cooldown so the proactive system starts fresh.
    """
    _LOTTERY_STATE["probability"] = 0.0
    _LOTTERY_STATE["consecutive_unanswered"] = 0
    _LOTTERY_STATE["cooldown_until"] = 0.0
    _save_lottery_state()
    logger.debug("Lottery state reset by user activity")


def _is_daytime() -> bool:
    """``True`` between 06:00 and 22:00 in local time."""
    hour = datetime.now().hour
    return 6 <= hour < 22


def _lottery_draw() -> bool:
    """Perform a probabilistic draw.

    * On **win** (random value < current probability): ``True`` is returned.
      The accumulated probability is consumed only after a visible result is
      durably delivered.  A timeout, empty result, or projection failure must
      not throw away the user's next proactive opportunity.
    * On **loss**: probability is increased by *delta* (capped at
      *max_probability*) and ``False`` is returned.
    """
    prob = _LOTTERY_STATE["probability"]
    if random.random() < prob:
        return True
    _LOTTERY_STATE["probability"] = min(
        _LOTTERY_STATE["probability"] + _LOTTERY_STATE["delta"],
        _LOTTERY_STATE["max_probability"],
    )
    return False


# ---------------------------------------------------------------------------
# Silence detection — infer how long since the user last spoke
# ---------------------------------------------------------------------------


def _last_user_message_time() -> datetime | None:
    """Infer the timestamp of the user's most recent message.

    Workbench ``lastUserMessageAt`` values and conversation archive
    ``## HH:MM:SS UTC`` headings track real user turns. The removed Agent
    session file is deliberately not consulted: ChatRepository and the memory
    Plugin are the two durable sources of user activity.

    Returns ``None`` when no user message can be found.
    """
    candidates: list[datetime] = []

    # 1. Workbench chats — use explicit user-activity timestamps, never the
    # generic updatedAt field (assistant replies and renames also change it).
    workbench = _latest_workbench_user_activity()
    if workbench is not None:
        candidates.append(workbench["timestamp"])

    # 2. Conversation archives — authoritative per-user-turn timestamps.
    try:
        memory_service = _memory_service()
        archived_at = (
            memory_service.latest_archived_user_message_time()
            if memory_service is not None
            else None
        )
        if archived_at is not None:
            candidates.append(archived_at)
    except Exception:
        logger.debug(
            "Could not scan conversation archives for silence detection",
            exc_info=True,
        )

    return max(candidates) if candidates else None


def _parse_activity_timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_workbench_user_activity() -> dict[str, object] | None:
    """Return the Workbench chat most recently touched by the user."""
    if not _workbench_db_path:
        return None
    repository = ChatRepository(_workbench_db_path)
    data = repository.read()

    latest: dict[str, object] | None = None
    for chat in data["chats"]:
        if not isinstance(chat, dict):
            continue
        if str(chat.get("kind") or "chat") != "chat":
            continue
        activity_times = [
            value
            for value in [_parse_activity_timestamp(chat.get("lastUserMessageAt"))]
            if value is not None
        ]
        for message in reversed(chat.get("messages") or []):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            message_time = _parse_activity_timestamp(message.get("createdAt"))
            if message_time is not None:
                activity_times.append(message_time)
                break
        timestamp = max(activity_times) if activity_times else None
        if timestamp is None:
            continue
        if latest is None or timestamp > latest["timestamp"]:
            latest = {
                "chat_id": str(chat.get("id") or ""),
                "project_id": str(chat.get("projectId") or ""),
                "title": str(chat.get("title") or ""),
                "model": str(chat.get("model") or ""),
                "timestamp": timestamp,
            }
    return latest


def _workbench_workspace_dir_for_project(project_id: str) -> str:
    """Return an existing Workbench project workspace for scheduler runs."""
    project_id = str(project_id or "").strip()
    if not project_id:
        return ""
    try:
        from cyrene.workbench.sessions.context import read_projects

        projects = read_projects()
        project = next(
            (
                item
                for item in projects
                if isinstance(item, dict)
                and project_id in {
                    str(item.get("id") or ""),
                    str(item.get("dataKey") or ""),
                }
            ),
            None,
        )
        workspace_raw = str((project or {}).get("workspacePath") or "").strip()
        if not workspace_raw:
            return ""
        workspace = Path(workspace_raw).expanduser().resolve()
        if workspace.is_dir():
            return str(workspace)
    except Exception:
        logger.debug("Could not resolve Workbench workspace for %s", project_id, exc_info=True)
    return ""


def _default_workbench_project_scope() -> dict[str, str]:
    """Return the active (or first) project without legacy JSON fallbacks."""

    try:
        from cyrene.workbench.sessions.context import read_project_state

        state = read_project_state()
        projects = [
            item
            for item in state.get("projects") or ()
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        active_id = str(state.get("activeProjectId") or "").strip()
        project = next(
            (
                item
                for item in projects
                if str(item.get("id") or "") == active_id
            ),
            projects[0] if projects else None,
        )
        if isinstance(project, dict):
            return {
                "project_id": str(project.get("id") or "default"),
                "workspace_dir": _workbench_workspace_dir_for_project(
                    str(project.get("id") or "")
                )
                or str(WORKSPACE_DIR),
            }
    except Exception:
        logger.debug("Could not resolve the proactive default project", exc_info=True)
    return {"project_id": "default", "workspace_dir": str(WORKSPACE_DIR)}


def _silence_hours() -> float | None:
    """Return hours since the user's last message, or *None* if unknown."""
    last = _last_user_message_time()
    if last is None:
        return None
    delta = datetime.now(timezone.utc) - last.astimezone(timezone.utc)
    return delta.total_seconds() / 3600


def _build_proactive_user_prompt(
    silence_hours: float | None,
    consecutive_unanswered: int = 0,
) -> str:
    """Build the scheduler instruction; Plugins mount contextual evidence."""
    now = datetime.now().strftime("%H:%M")
    today = datetime.now().strftime("%Y-%m-%d")

    silence_line = (
        f"Hours since user's last message: {silence_hours:.0f}"
        if silence_hours is not None
        else "Unable to determine when the user last messaged"
    )

    unanswered_note = ""
    if consecutive_unanswered >= 1:
        unanswered_note = (
            "The user has not replied to the previous proactive report. "
            "Do not repeat it or send a substitute social message. Only report a new, "
            "material result or risk; otherwise finish with decision suppress."
        )

    return f"""## Objective
- This is an autonomous work cycle, not a social check-in. Proactively advance one useful, concrete item when the context supports it.
- Look for an open task, unresolved decision, due or stale item, missing verification, research gap, or small project-maintenance action.
- When an actionable item exists, use tools and complete the work now. Do not merely suggest work, offer to help, or describe what you could do.
- Prefer bounded work with a verifiable result. Respect the proactive write-safety boundary in the system instructions.
- Report only a concrete completed result, a newly verified material fact, or a specific blocker/risk that genuinely needs the user's attention. State what changed or was found and why it matters.
- Finish every cycle by calling finish_proactive exactly once. Use decision=deliver and put only the exact concise user-visible report in report when there is a material result. Use decision=suppress and report="" when nothing should be shown.
- After finish_proactive succeeds, return an empty assistant response. Ordinary assistant text is not a delivery decision and will be ignored.
- Do not greet the user, make small talk, ask how they are, send lifestyle reminders, or revive a casual topic merely to have something to say.
- No new user message triggered this round. Never claim or imply that the user just woke up, came online, returned, became available, finished work, is currently busy, or is currently doing anything.
- Treat the current time and silence duration only as scheduling/deadline context; they are not evidence of the user's present state.
{unanswered_note}

## Current situation
- Date: {today}
- Current time: {now}
- Trigger: system scheduler; no new user activity
- {silence_line}
- Consecutive proactive messages not replied to: {consecutive_unanswered}"""


# ---------------------------------------------------------------------------
# Proactive message delivery — Workbench chat + optional bot
# ---------------------------------------------------------------------------


async def _deliver_proactive_message(
    text: str,
    bot,
    chat_id: int,
    *,
    db_path: str,
    project_id: str,
    session_id: str,
    model: str = "",
    usage: Mapping[str, Any] | None = None,
    latest_request_usage: Mapping[str, Any] | None = None,
    source_chat_id: str = "",
    lang: str = "",
) -> dict[str, str] | None:
    """Project one result through the new chat service and optional bot."""

    projected = await create_proactive_chat(
        db_path,
        project_id,
        text,
        chat_id=session_id,
        model=model,
        usage=usage,
        latest_request_usage=latest_request_usage,
        source_chat_id=source_chat_id,
        lang=lang,
    )
    if projected is None:
        return None
    if bot is not None:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception:
            # The ChatRepository/ContextTree commit is authoritative. A
            # transient transport failure must not erase or duplicate it.
            logger.exception("Failed to deliver proactive result through bot")
    return projected


# ---------------------------------------------------------------------------
# Proactive heartbeat  (lottery-driven)
# ---------------------------------------------------------------------------

async def _heartbeat_proactive_check(bot, db_path: str) -> dict[str, Any]:
    """Attempt to send a context-aware proactive message to the user.

    The decision to send is based on the lottery draw, but the trigger is
    also influenced by how long the user has been silent:

    * Normal: lottery draw with accumulating probability (delta 0.15, max 0.85).
    * Silent > 72 h: always trigger regardless of lottery state.

    When triggered, the Plugin Agent generates a personalised message in a new
    Workbench conversation, or in an isolated scheduler ContextTree when no
    Workbench conversation exists.
    """
    # In web-only mode OWNER_ID is not set — use 0 as a placeholder chat_id.
    # The session-state delivery path does not rely on chat_id at all.
    owner_id = OWNER_ID if OWNER_ID is not None else 0

    try:
        _load_lottery_state()

        # Check whether agent proactive messaging is enabled in settings
        try:
            from cyrene.platform.settings_store import get as _get_setting
            if not _get_setting("agent_proactive", True):
                logger.debug("Agent proactive messaging disabled via settings")
                return {"status": "disabled"}
        except Exception:
            pass

        if not _is_daytime():
            logger.debug("Nighttime, skipping proactive check")
            return {"status": "outside_daytime"}

        # -------- Cooldown guard --------
        cooldown_until = float(_LOTTERY_STATE.get("cooldown_until", 0.0))
        if time.time() < cooldown_until:
            remaining_h = (cooldown_until - time.time()) / 3600
            logger.debug("Proactive cooldown active, %.1f h remaining", remaining_h)
            return {
                "status": "cooldown",
                "cooldown_remaining_seconds": max(
                    0, int(cooldown_until - time.time())
                ),
            }

        # Workbench conversations have independent session locks. Select the
        # latest user-owned conversation before drawing the lottery and skip
        # cleanly while it is running; proactive work must never preempt it.
        workbench_target = _latest_workbench_user_activity()
        target_session_id = str((workbench_target or {}).get("chat_id") or "")
        if target_session_id and _is_workbench_conversation_running(
            db_path,
            target_session_id,
        ):
            logger.debug(
                "Latest Workbench chat %s is running; skipping proactive check",
                target_session_id,
            )
            return {"status": "conversation_running"}

        # -------- Cooldown trigger --------
        # ``consecutive_unanswered`` counts proactive messages we actually
        # delivered without a user reply in between. It is incremented once per
        # delivery (see the send path below) and cleared by ``reset_lottery``
        # the moment the user speaks on any channel. Counting real deliveries —
        # rather than heartbeat ticks — is what stops a single ignored message
        # from snowballing into a multi-day silence.
        consecutive = int(_LOTTERY_STATE.get("consecutive_unanswered", 0))
        if consecutive >= _PROACTIVE_COOLDOWN_THRESHOLD:
            _LOTTERY_STATE["cooldown_until"] = time.time() + _PROACTIVE_COOLDOWN_SECONDS
            _LOTTERY_STATE["consecutive_unanswered"] = 0
            _LOTTERY_STATE["probability"] = 0.0
            _save_lottery_state()
            logger.info(
                "User ignored %d consecutive proactive messages; backing off for %d days",
                _PROACTIVE_COOLDOWN_THRESHOLD,
                _PROACTIVE_COOLDOWN_SECONDS // 86400,
            )
            return {"status": "cooldown_armed"}

        silence_h = _silence_hours()

        # -------- Trigger decision --------
        should_send = False
        if silence_h is not None and silence_h > 72:
            should_send = True
            logger.info(
                "Silence > 72 h — overriding lottery and sending proactive message"
            )
        elif _lottery_draw():
            should_send = True
            _save_lottery_state()
            logger.info(
                "Lottery won — sending proactive message (silence=%.1f h)",
                silence_h or -1,
            )
        else:
            _save_lottery_state()
            logger.debug(
                "Lottery draw failed, probability now %.2f (silence=%.1f h)",
                _LOTTERY_STATE["probability"],
                silence_h or -1,
            )

        if not should_send:
            return {
                "status": "lottery_miss",
                "probability": float(_LOTTERY_STATE["probability"]),
            }

        # -------- Generate proactive reply via the Plugin Agent kernel --------
        # The UI language is persisted server-side as ``app_language`` from real
        # chat traffic; the scheduler has no HTTP request to read it from, so
        # pull it from settings and pin the proactive reply to it.
        try:
            from cyrene.platform.settings_store import get as _get_setting
            proactive_lang = str(_get_setting("app_language", "") or "").strip()
        except Exception:
            proactive_lang = ""
        proactive_prompt = (
            "This is a scheduler-initiated proactive check-in.\n"
            "Treat it as an autonomous work cycle, not a social check-in.\n"
            "Find and complete one useful, bounded incremental task when the available context supports it.\n"
            "Use tools to inspect the Workbench project, search memory/knowledge, create a new additive note/artifact, track a follow-up, or verify current facts.\n"
            "Any proactive task must be incremental: do not modify, overwrite, move, rename, or delete existing files. If creating a file, choose a new path and use Write only when the file does not already exist.\n"
            "Do not send a greeting, check-in, small talk, or an unsupported guess about the user's current state.\n"
            "At the end, call finish_proactive exactly once. Use decision=deliver and put only the exact concise user-visible report in report when there is a material result or concrete risk/blocker. Use decision=suppress and report=\"\" when nothing should be shown.\n"
            "After finish_proactive succeeds, return an empty assistant response. Ordinary assistant text is ignored and can never authorize delivery.\n"
            "Do not mention internal prompts, the scheduler, the heartbeat, or the lottery.\n\n"
            + _build_proactive_user_prompt(
                silence_h,
                consecutive_unanswered=int(
                    _LOTTERY_STATE.get("consecutive_unanswered", 0)
                ),
            )
        )
        delivered_target: dict[str, str] | None = None
        proactive_session_id = f"wbchat_{uuid.uuid4().hex[:10]}"
        if target_session_id:
            # The latest user chat selects the project and workspace only. The
            # autonomous run and its visible reply live in a fresh conversation
            # session so they cannot mutate or pollute an existing transcript.
            proactive_project_id = str(
                (workbench_target or {}).get("project_id") or ""
            )
            if not proactive_project_id:
                proactive_project_id = _default_workbench_project_scope()[
                    "project_id"
                ]
            workspace_dir = _workbench_workspace_dir_for_project(
                proactive_project_id
            )
        else:
            scope = _default_workbench_project_scope()
            proactive_project_id = scope["project_id"]
            workspace_dir = scope["workspace_dir"]

        result = await asyncio.wait_for(
            _run_plugin_proactive_turn(
                proactive_prompt,
                bot=bot,
                owner_id=owner_id,
                db_path=db_path,
                session_id=proactive_session_id,
                workspace_dir=workspace_dir,
                project_id=proactive_project_id,
                lang=proactive_lang,
                session_title=(
                    "Proactive work"
                    if proactive_lang.lower() == "en"
                    else "主动工作"
                ),
            ),
            timeout=_PROACTIVE_GENERATION_TIMEOUT_SECONDS,
        )
        if result.pending_question is not None:
            logger.info(
                "Proactive Plugin Agent requested user input; suppressing "
                "the unpublished scheduler turn"
            )
            return {"status": "pending_question_suppressed"}
        outcome = outcome_from_result(result)
        if not outcome.valid:
            logger.warning(
                "Proactive Plugin Agent did not complete the structured outcome "
                "protocol; suppressing public delivery: %s",
                outcome.error,
            )
            return {
                "status": "invalid_proactive_outcome",
                "error": outcome.error,
            }
        if outcome.decision == "suppress":
            logger.info("Proactive round explicitly suppressed public delivery")
            return {"status": "no_visible_result"}

        text = outcome.report
        if outcome.decision == "deliver":
            delivered_target = await _deliver_proactive_message(
                text,
                bot,
                owner_id,
                db_path=db_path,
                project_id=proactive_project_id,
                session_id=proactive_session_id,
                model=str(
                    result.model
                    or (workbench_target or {}).get("model")
                    or ""
                ),
                usage=result.usage,
                latest_request_usage=result.latest_request_usage,
                source_chat_id=target_session_id,
                lang=proactive_lang,
            )
            if delivered_target is None:
                logger.warning(
                    "Proactive Plugin Agent reply could not be projected "
                    "into a Workbench chat"
                )
                return {"status": "projection_failed"}

        # A message was actually delivered. Count it toward the unanswered
        # streak (``reset_lottery`` clears the streak as soon as the user replies
        # on any channel) and record the send time for diagnostics.
        _LOTTERY_STATE["consecutive_unanswered"] = int(_LOTTERY_STATE.get("consecutive_unanswered", 0)) + 1
        _LOTTERY_STATE["last_proactive_time"] = time.time()
        _LOTTERY_STATE["probability"] = 0.0
        _save_lottery_state()

        logger.info("Proactive message sent via Plugin Agent: %s", str(text)[:100])

        # Desktop / SSE notification so the user is alerted even when the
        # Web UI tab is in the background.
        try:
            await notify(title="Cyrene", body=str(text)[:120], channel="auto")
            append_notification(
                title=localized(
                    "Cyrene reminder",
                    "Cyrene 提醒",
                    language=proactive_lang,
                ),
                body=str(text)[:120],
                tab="mention",
                project_ref=str((delivered_target or {}).get("project_id") or "default"),
                source="proactive_message",
                source_label=(
                    localized("Chat", "对话", language=proactive_lang)
                    if delivered_target
                    else localized("System", "系统", language=proactive_lang)
                ),
                link_label=str((delivered_target or {}).get("title") or "Cyrene"),
                meta=(
                    {"chatId": str(delivered_target.get("chat_id") or "")}
                    if delivered_target else None
                ),
                language=proactive_lang,
            )
        except Exception:
            logger.debug("Proactive notification delivery failed", exc_info=True)

        return {
            "status": "delivered",
            "chat_id": str((delivered_target or {}).get("chat_id") or ""),
            "project_id": str(
                (delivered_target or {}).get("project_id") or proactive_project_id
            ),
        }

    except asyncio.TimeoutError:
        logger.warning("Proactive message generation timed out")
        return {"status": "generation_timeout"}
    except httpx.HTTPError:
        logger.exception("Proactive message LLM request failed")
        return {"status": "model_request_failed"}
    except Exception:
        logger.exception("Proactive check failed")
        return {"status": "error"}


def heartbeat_interval_seconds() -> int:
    return _get_heartbeat_interval()


async def _proactive_tick(bot: Any, db_path: str) -> dict[str, Any]:
    from cyrene.plugins.background import maintenance_lock

    try:
        async with maintenance_lock():
            return await _heartbeat_proactive_check(bot, db_path)
    except Exception:
        logger.exception("Proactive heartbeat error")
        return {"status": "error"}


class ProactiveService:
    def __init__(self, *, bot: Any, db_path: str) -> None:
        self.bot = bot
        self.db_path = str(db_path or "")

    async def tick(self) -> dict[str, Any]:
        global _workbench_db_path
        _workbench_db_path = self.db_path
        return await _proactive_tick(self.bot, self.db_path)

    async def settings_changed(
        self,
        _namespace: str,
        changed: tuple[str, ...],
    ) -> None:
        """Rebind the owned background job when its cadence changes."""

        if "heartbeat_interval" not in changed:
            return
        global _HEARTBEAT_INTERVAL_SECONDS
        _HEARTBEAT_INTERVAL_SECONDS = 0
        from cyrene.core.plugin import application_plugin_scope
        from cyrene.plugins.background import reconcile_background_plugin_hosts

        host = application_plugin_scope()
        if host is not None:
            await reconcile_background_plugin_hosts(host)

    def reset_lottery(self) -> None:
        reset_lottery()

    def prepare_data_reset(self) -> None:
        """Clear persisted policy state before core resets application data."""

        reset_lottery()


__all__ = [
    "ProactiveService",
    "heartbeat_interval_seconds",
    "reset_lottery",
]
