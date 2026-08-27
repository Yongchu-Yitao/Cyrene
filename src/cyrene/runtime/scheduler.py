"""Scheduler, heartbeat, and proactive-messaging lottery system.

Responsibilities
----------------
1. **Scheduled tasks** -- Host user-editable background Plugins on one clock.
2. **Heartbeat** -- A low-frequency proactive lottery job, independent from
   the task poll so maintenance does not wake on every task-check interval.
3. **Lottery** -- A probability-driven mechanism that occasionally prompts the
   assistant to send an unsolicited message to the user. State is persisted in
   the runtime settings store so that it survives restarts.
4. **Smart proactive context** -- When the lottery triggers, the agent now
   receives short-term memory, recent conversation context, and relationship
   state from SOUL.md so the proactive message can reference real events
   instead of sending generic greetings.
5. **Maintenance** -- Behavior learning, steward work, and cleanup each have
   their own cadence and share a lock so model-backed jobs do not overlap.
"""

import asyncio
import logging
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agent.plugin import active_plugin_service
from agent.plugin.background import BackgroundPluginHost
from agent.workbench import ConversationConfig, ConversationRuntime, WorkbenchChatResult
from cyrene.config import (
    OWNER_ID,
    PATTERN_DETECTION_INTERVAL,
    SCHEDULER_INTERVAL,
    STEWARD_INTERVAL,
    WORKSPACE_DIR,
)
from cyrene.runtime.notifications import notify
from cyrene.runtime.run_coordinator import run_coordinator_for
from cyrene.workbench.chat_repository import ChatRepository
from cyrene.workbench.notifications import append_notification
from cyrene.workbench.proactive_chat_service import create_proactive_chat

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_background_plugin_host: BackgroundPluginHost | None = None
_workbench_db_path: str = ""


def _memory_service():
    return active_plugin_service("memory")


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
        return "Write the final user-visible report in Simplified Chinese."
    if normalized.startswith("en"):
        return "Write the final user-visible report in English."
    return "Use the language the user normally uses in their recent context."


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
        session_title=str(session_title or ""),
        memory_write_enabled=True,
        memory_trigger_enabled=False,
        memory_archive_enabled=True,
        conversation_source="scheduler",
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

# Big-heartbeat cadence: perform proactive checks.
# Read from web_settings.json (default 1800s = 30 min).
_HEARTBEAT_INTERVAL_SECONDS: int = 0  # lazy-loaded on first use


def _get_heartbeat_interval() -> int:
    global _HEARTBEAT_INTERVAL_SECONDS
    if not _HEARTBEAT_INTERVAL_SECONDS:
        try:
            from cyrene.runtime.settings_store import get
            _HEARTBEAT_INTERVAL_SECONDS = int(get("heartbeat_interval", 1800) or 1800)
        except Exception:
            _HEARTBEAT_INTERVAL_SECONDS = 1800
    return _HEARTBEAT_INTERVAL_SECONDS


_MAINTENANCE_LOCK: asyncio.Lock | None = None
_MAINTENANCE_LOCK_LOOP: asyncio.AbstractEventLoop | None = None


def _get_maintenance_lock() -> asyncio.Lock:
    """Serialize heavyweight maintenance work without coupling its cadence."""
    global _MAINTENANCE_LOCK, _MAINTENANCE_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _MAINTENANCE_LOCK is None or _MAINTENANCE_LOCK_LOOP is not loop:
        _MAINTENANCE_LOCK = asyncio.Lock()
        _MAINTENANCE_LOCK_LOOP = loop
    return _MAINTENANCE_LOCK


def _load_lottery_state() -> None:
    """Restore lottery state from the runtime settings store."""
    global _LOTTERY_STATE
    try:
        from cyrene.runtime.settings_store import get

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
        from cyrene.runtime.settings_store import set_

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

    * On **win** (random value < current probability): probability is reset
      to zero and ``True`` is returned.
    * On **loss**: probability is increased by *delta* (capped at
      *max_probability*) and ``False`` is returned.
    """
    prob = _LOTTERY_STATE["probability"]
    if random.random() < prob:
        _LOTTERY_STATE["probability"] = 0.0
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
        from cyrene.workbench.context import read_projects

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
        from cyrene.workbench.context import read_project_state

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


# ---------------------------------------------------------------------------
# Proactive context assembly — memory + conversations + personality
# ---------------------------------------------------------------------------


async def _assemble_proactive_context(db_path: str = "") -> str:
    """Gather memory, conversation, and personality context for a proactive
    message so the agent can reference real events.

    Returns a Markdown string assembled from three sources:

    * SOUL.md — RELATIONSHIP:USER and PATTERN:USER sections.
    * Short-term memory — recent facts, preferences, emotional patterns.
    * Today's conversation archive — what the user just talked about.

    Every source is best-effort; failures are logged and skipped.
    """
    parts: list[str] = []

    # 1. SOUL.md shallow memory — relationship + observed patterns
    try:
        memory_service = _memory_service()
        soul = (
            memory_service.read_shallow_memory()
            if memory_service is not None
            else ""
        )
        if soul:
            relevant_lines: list[str] = []
            capture = False
            for line in soul.splitlines():
                if line.startswith("## RELATIONSHIP:USER") or line.startswith(
                    "## PATTERN:USER",
                ):
                    capture = True
                    relevant_lines.append(line)
                elif line.startswith("## ") and capture:
                    capture = False
                elif capture:
                    relevant_lines.append(line)
            if relevant_lines:
                parts.append(
                    "## Your relationship with the user\n"
                    + "\n".join(relevant_lines),
                )
    except Exception:
        logger.debug(
            "Could not read SOUL.md for proactive context",
            exc_info=True,
        )

    # 2. Short-term memory — compressed facts / preferences / emotions
    try:
        memory_service = _memory_service()
        st = (
            memory_service.short_term_context(
                max_chars=1500,
                header="## Recent memories about the user",
            )
            if memory_service is not None
            else ""
        )
        if st and st != "## Recent memories about the user":
            parts.append(st)
    except Exception:
        logger.debug(
            "Could not read short-term memory for proactive context",
            exc_info=True,
        )

    # 3. Today's conversation — what the user just talked about
    try:
        memory_service = _memory_service()
        conversations = (
            await memory_service.recent_conversations(days=1)
            if memory_service is not None
            else ""
        )
        if conversations:
            if len(conversations) > 3000:
                # Keep the tail (most recent exchanges)
                conversations = conversations[-3000:]
                # Splice back onto a section boundary so we don't start
                # mid-exchange.
                boundary = conversations.find("\n=== ")
                if boundary > 100:
                    conversations = conversations[boundary + 1:]
            parts.append("## Recent conversation\n" + conversations)
    except Exception:
        logger.debug(
            "Could not read conversations for proactive context",
            exc_info=True,
        )

    # 4. Active entities — due soon, stale, open decisions
    if db_path:
        try:
            from datetime import timedelta

            now_dt = datetime.now(timezone.utc)
            due_cutoff = (now_dt + timedelta(hours=24)).isoformat()
            stale_cutoff = (now_dt - timedelta(days=7)).isoformat()
            entities = active_plugin_service("entities")
            if entities is None:
                raise RuntimeError("Entity Plugin application service is unavailable")

            due_soon = await entities.query(due_before=due_cutoff, status="active")
            all_active = await entities.list(status="active", limit=200)
            stale = [e for e in all_active if e.get("last_referenced_at", "") < stale_cutoff]
            open_dec = [
                e for e in all_active
                if e["type"] == "decision" and not (
                    e["metadata"].get("outcome") if isinstance(e["metadata"], dict) else False
                )
            ]

            entity_lines: list[str] = []
            if due_soon:
                titles = "、".join(e["title"] for e in due_soon[:3])
                entity_lines.append(f"- 即将到期（24h内）：{titles}")
            if stale:
                entity_lines.append(f"- 长时间未提及：{stale[0]['title']}")
            if open_dec:
                entity_lines.append(f"- 待跟进的决策：{open_dec[0]['title']}")

            if entity_lines:
                parts.append("## 需要关注的事务\n" + "\n".join(entity_lines))
        except Exception:
            logger.debug("Could not load entity context for proactive message", exc_info=True)

    return "\n\n".join(parts).strip()


def _build_proactive_user_prompt(context: str, silence_hours: float | None, consecutive_unanswered: int = 0) -> str:
    """Build the user prompt with memory context and current situation."""
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
            "material result or risk; otherwise return an empty final response."
        )

    return f"""## Memory context
{context if context else "No recent context available."}

## Objective
- This is an autonomous work cycle, not a social check-in. Proactively advance one useful, concrete item when the context supports it.
- Look for an open task, unresolved decision, due or stale item, missing verification, research gap, or small project-maintenance action.
- When an actionable item exists, use tools and complete the work now. Do not merely suggest work, offer to help, or describe what you could do.
- Prefer bounded work with a verifiable result. Respect the proactive write-safety boundary in the system instructions.
- Report only a concrete completed result, a newly verified material fact, or a specific blocker/risk that genuinely needs the user's attention. State what changed or was found and why it matters.
- If there is no useful safe action, or no material result worth reporting, return an empty final response.
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

async def _heartbeat_proactive_check(bot, db_path: str) -> None:
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
            from cyrene.runtime.settings_store import get as _get_setting
            if not _get_setting("agent_proactive", True):
                logger.debug("Agent proactive messaging disabled via settings")
                return
        except Exception:
            pass

        if not _is_daytime():
            logger.debug("Nighttime, skipping proactive check")
            return

        # -------- Cooldown guard --------
        cooldown_until = float(_LOTTERY_STATE.get("cooldown_until", 0.0))
        if time.time() < cooldown_until:
            remaining_h = (cooldown_until - time.time()) / 3600
            logger.debug("Proactive cooldown active, %.1f h remaining", remaining_h)
            return

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
            return

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
            return

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
            return

        # -------- Generate proactive reply via the Plugin Agent kernel --------
        # The UI language is persisted server-side as ``app_language`` from real
        # chat traffic; the scheduler has no HTTP request to read it from, so
        # pull it from settings and pin the proactive reply to it.
        try:
            from cyrene.runtime.settings_store import get as _get_setting
            proactive_lang = str(_get_setting("app_language", "") or "").strip()
        except Exception:
            proactive_lang = ""
        context = await _assemble_proactive_context(db_path)
        proactive_prompt = (
            "This is a scheduler-initiated proactive check-in.\n"
            "Treat it as an autonomous work cycle, not a social check-in.\n"
            "Find and complete one useful, bounded incremental task when the available context supports it.\n"
            "Use tools to inspect the Workbench project, search memory/knowledge, create a new additive note/artifact, track a follow-up, or verify current facts.\n"
            "Any proactive task must be incremental: do not modify, overwrite, move, rename, or delete existing files. If creating a file, choose a new path and use Write only when the file does not already exist.\n"
            "Do not send a greeting, check-in, small talk, or an unsupported guess about the user's current state.\n"
            "If you produce a material result or find a concrete risk/blocker, the final reply will be shown directly to the user; write only that concise work report.\n"
            "If there is no useful safe action or no material result, return an empty final response.\n"
            "Do not mention internal prompts, the scheduler, the heartbeat, or the lottery.\n\n"
            + _build_proactive_user_prompt(context, silence_h, consecutive_unanswered=int(_LOTTERY_STATE.get("consecutive_unanswered", 0)))
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
            timeout=120.0,
        )
        text = str(result.text or "").strip()
        if result.pending_question is not None:
            logger.info(
                "Proactive Plugin Agent requested user input; suppressing "
                "the unpublished scheduler turn"
            )
            return
        if text:
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
                source_chat_id=target_session_id,
                lang=proactive_lang,
            )
            if delivered_target is None:
                logger.warning(
                    "Proactive Plugin Agent reply could not be projected "
                    "into a Workbench chat"
                )
                return

        if not text:
            logger.info("Proactive round produced no visible reply")
            return

        # A message was actually delivered. Count it toward the unanswered
        # streak (``reset_lottery`` clears the streak as soon as the user replies
        # on any channel) and record the send time for diagnostics.
        _LOTTERY_STATE["consecutive_unanswered"] = int(_LOTTERY_STATE.get("consecutive_unanswered", 0)) + 1
        _LOTTERY_STATE["last_proactive_time"] = time.time()
        _save_lottery_state()

        logger.info("Proactive message sent via Plugin Agent: %s", str(text)[:100])

        # Desktop / SSE notification so the user is alerted even when the
        # Web UI tab is in the background.
        try:
            await notify(title="Cyrene", body=str(text)[:120], channel="auto")
            append_notification(
                title="Cyrene 提醒",
                body=str(text)[:120],
                tab="mention",
                project_ref=str((delivered_target or {}).get("project_id") or "default"),
                source="proactive_message",
                source_label="对话" if delivered_target else "系统",
                link_label=str((delivered_target or {}).get("title") or "Cyrene"),
                meta=(
                    {"chatId": str(delivered_target.get("chat_id") or "")}
                    if delivered_target else None
                ),
            )
        except Exception:
            logger.debug("Proactive notification delivery failed", exc_info=True)

    except asyncio.TimeoutError:
        logger.warning("Proactive message generation timed out")
    except httpx.HTTPError:
        logger.exception("Proactive message LLM request failed")
    except Exception:
        logger.exception("Proactive check failed")


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

async def _proactive_tick(bot, db_path: str) -> None:
    try:
        async with _get_maintenance_lock():
            await _heartbeat_proactive_check(bot, db_path)
    except Exception:
        logger.exception("Proactive heartbeat error")


async def _steward_tick(bot, db_path: str) -> None:
    del bot, db_path
    try:
        async with _get_maintenance_lock():
            memory_service = _memory_service()
            if memory_service is None:
                logger.debug("Memory Plugin unavailable, skipping steward")
                return
            await memory_service.run_steward_if_needed(
                interval=STEWARD_INTERVAL,
            )
    except Exception:
        logger.exception("Steward tick error")


async def _behavior_learning_tick(bot, db_path: str) -> None:
    try:
        async with _get_maintenance_lock():
            # Import lazily so startup does not load the learning stack merely
            # to register the scheduler.
            from cyrene.learning.orchestrator import tick as _pattern_tick

            await _pattern_tick(bot, db_path)
    except Exception:
        logger.exception("Behavior-learning tick error")


async def _cleanup_tick() -> None:
    try:
        async with _get_maintenance_lock():
            memory_service = _memory_service()
            if memory_service is not None:
                memory_service.clear_old_short_term(days=7)
    except Exception:
        logger.exception("Short-term cleanup error")


def _add_interval_job(
    scheduler: AsyncIOScheduler,
    func,
    *,
    seconds: int,
    job_id: str,
    args: list | None = None,
) -> None:
    scheduler.add_job(
        func,
        "interval",
        seconds=max(1, int(seconds)),
        args=args or [],
        id=job_id,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def setup_scheduler(bot, db_path: str) -> AsyncIOScheduler:
    """Create a scheduler with independent task and maintenance cadences.

    The shared host signature accepts the optional delivery bot and database
    path used by web, desktop, and channel entry points.
    """
    global _scheduler
    global _background_plugin_host
    global _workbench_db_path
    _workbench_db_path = str(db_path)
    try:
        from cyrene.workbench.context import configure_store as _configure_context
        from cyrene.workbench.notifications import configure_store as _configure_notifications

        _configure_context(str(db_path))
        _configure_notifications(str(db_path))
    except Exception:
        logger.debug("Could not configure Workbench SQLite stores for scheduler", exc_info=True)
    _load_lottery_state()
    hb_seconds = _get_heartbeat_interval()
    _scheduler = AsyncIOScheduler()
    from cyrene.runtime.schedule_runtime import get_schedule_runtime

    schedule_runtime = get_schedule_runtime(db_path, bot=bot)
    _background_plugin_host = BackgroundPluginHost(
        _scheduler,
        services={"schedules": schedule_runtime},
        data={"source": "background", "db_path": str(db_path)},
    )
    _background_plugin_host.attach()
    _add_interval_job(
        _scheduler,
        _behavior_learning_tick,
        seconds=PATTERN_DETECTION_INTERVAL,
        job_id="behavior_learning",
        args=[bot, db_path],
    )
    _add_interval_job(
        _scheduler,
        _proactive_tick,
        seconds=hb_seconds,
        job_id="proactive_heartbeat",
        args=[bot, db_path],
    )
    _add_interval_job(
        _scheduler,
        _steward_tick,
        seconds=STEWARD_INTERVAL,
        job_id="steward",
        args=[bot, db_path],
    )
    _add_interval_job(
        _scheduler,
        _cleanup_tick,
        seconds=86400,
        job_id="short_term_cleanup",
    )
    logger.info(
        "Scheduler configured: Plugin tasks=%ds, behavior=%ds, proactive=%ds, "
        "steward=%ds, cleanup=86400s",
        SCHEDULER_INTERVAL,
        PATTERN_DETECTION_INTERVAL,
        hb_seconds,
        STEWARD_INTERVAL,
    )
    return _scheduler
