"""Scheduler, heartbeat, and proactive-messaging lottery system.

Responsibilities
----------------
1. **Scheduled tasks** -- Check the SQLite database for due tasks and execute
   them (inherited from the original scheduler).
2. **Heartbeat** -- A low-frequency proactive lottery job, independent from
   the task poll so maintenance does not wake on every task-check interval.
3. **Lottery** -- A probability-driven mechanism that occasionally prompts the
   assistant to send an unsolicited message to the user.  State is persisted
   to ``data/lottery_state.json`` so that it survives restarts.
4. **Smart proactive context** -- When the lottery triggers, the agent now
   receives short-term memory, recent conversation context, and relationship
   state from SOUL.md so the proactive message can reference real events
   instead of sending generic greetings.
5. **Maintenance** -- Behavior learning, steward work, and cleanup each have
   their own cadence and share a lock so model-backed jobs do not overlap.
"""

import asyncio
import importlib
import json
import logging
import random
import re as _re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from cyrene.runtime import database as db
from cyrene.agent import (
    append_system_message,
    is_session_running,
    run_heartbeat_agent,
    run_steward_agent,
    run_task_agent,
)
from cyrene.config import (
    BASE_DIR,
    DATA_DIR,
    OWNER_ID,
    PATTERN_DETECTION_INTERVAL,
    SCHEDULER_INTERVAL,
    STATE_FILE,
    STEWARD_INTERVAL,
)
from cyrene.runtime.memory.conversations import CONVERSATIONS_DIR, get_recent_conversations
from cyrene.runtime.io import atomic_write_json, read_json_safe
from cyrene.runtime.notifications import notify
from cyrene.runtime.schedule_spec import compute_next_run
from cyrene.runtime.memory.short_term import clear_old_entries, get_context as get_short_term_context
from cyrene.runtime.memory.soul import apply_soul_update, read_shallow_memory, read_soul
from cyrene.workbench.notifications import append_notification

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_workbench_db_path: str = ""

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
_LOTTERY_FILE = BASE_DIR / "data" / "lottery_state.json"

# If this many consecutive proactive messages go unanswered, enter cooldown.
_PROACTIVE_COOLDOWN_THRESHOLD: int = 2
# Duration of the cooldown period in seconds (3 days).
_PROACTIVE_COOLDOWN_SECONDS: int = 3 * 86400

# ---------------------------------------------------------------------------
# Steward state  (persisted to disk)
# ---------------------------------------------------------------------------

_STEWARD_STATE_FILE = DATA_DIR / "steward_state.json"

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
    """Restore lottery state from ``_LOTTERY_FILE``."""
    global _LOTTERY_STATE
    try:
        data = read_json_safe(_LOTTERY_FILE)
        if data is not None:
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
    """Persist current lottery state to ``_LOTTERY_FILE``."""
    try:
        atomic_write_json(_LOTTERY_FILE, _LOTTERY_STATE)
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
    ``## HH:MM:SS UTC`` headings track real user turns. ``state.json``'s
    modification time is only a degraded fallback — the agent rewrites that
    file on its own (proactive replies, steward, behaviour learning, pattern
    detection), so its mtime is trusted only when the latest message is the
    user's.

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
        if CONVERSATIONS_DIR.exists():
            files = sorted(CONVERSATIONS_DIR.glob("*.md"), reverse=True)
            for filepath in files:
                content = filepath.read_text(encoding="utf-8")
                # Each exchange starts with "## HH:MM:SS UTC", then optional
                # metadata comments, then "**User**: ..." — match lazily.
                matches = _re.findall(
                    r"## (\d{2}:\d{2}:\d{2} UTC)\n.*?\*\*User\*\*:",
                    content,
                    _re.DOTALL,
                )
                if matches:
                    latest_ts = matches[-1]
                    date_str = filepath.stem  # YYYY-MM-DD
                    clean_ts = latest_ts.replace(" UTC", "")
                    dt_str = f"{date_str} {clean_ts}"
                    try:
                        candidates.append(
                            datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(
                                tzinfo=timezone.utc,
                            )
                        )
                        break
                    except ValueError:
                        logger.debug(
                            "Unparseable timestamp in %s: %s",
                            filepath.name,
                            latest_ts,
                            exc_info=True,
                        )
                        continue
    except Exception:
        logger.debug(
            "Could not scan conversation archives for silence detection",
            exc_info=True,
        )

    # 3. Degraded fallback: state.json mtime, trusted only when the most recent
    #    non-empty message is the user's (otherwise the mtime reflects one of the
    #    agent's own writes and would make the user look more recently active
    #    than they really are). Mostly relevant before any exchange is archived.
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            messages = data.get("messages", []) if isinstance(data, dict) else []
            last_msg = next(
                (
                    m
                    for m in reversed(messages)
                    if isinstance(m, dict) and str(m.get("content", "")).strip()
                ),
                None,
            )
            if last_msg is not None and str(last_msg.get("role") or "") == "user":
                mtime = STATE_FILE.stat().st_mtime
                candidates.append(datetime.fromtimestamp(mtime, tz=timezone.utc))
    except Exception:
        logger.debug("Could not read state.json for silence detection", exc_info=True)

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
    if _workbench_db_path:
        from cyrene.workbench.store import read_document

        data = read_document(
            _workbench_db_path,
            "chats",
            lambda: {"chats": []},
            legacy_path=DATA_DIR / "workbench_chats.json",
        )
    else:
        data = read_json_safe(DATA_DIR / "workbench_chats.json")
    if not isinstance(data, dict) or not isinstance(data.get("chats"), list):
        return None

    latest: dict[str, object] | None = None
    for chat in data["chats"]:
        if not isinstance(chat, dict):
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
                "timestamp": timestamp,
            }
    return latest


def _workbench_workspace_dir_for_project(project_id: str) -> str:
    """Return an existing Workbench project workspace for scheduler runs."""
    project_id = str(project_id or "").strip()
    if not project_id:
        return ""
    try:
        if _workbench_db_path:
            from cyrene.workbench.store import read_document

            payload = read_document(
                _workbench_db_path,
                "projects",
                lambda: {"projects": []},
                legacy_path=DATA_DIR / "workbench_projects.json",
            )
        else:
            payload = read_json_safe(DATA_DIR / "workbench_projects.json")
        projects = payload.get("projects") if isinstance(payload, dict) else None
        if not isinstance(projects, list):
            return ""
        project = next(
            (
                item
                for item in projects
                if isinstance(item, dict) and str(item.get("id") or "") == project_id
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
        soul = read_shallow_memory()
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
        st = get_short_term_context(
            max_chars=1500,
            header="## Recent memories about the user",
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
        conversations = await get_recent_conversations(days=1)
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
            from cyrene.tool_impl.entity.store import list_entities, query_entities

            now_dt = datetime.now(timezone.utc)
            due_cutoff = (now_dt + timedelta(hours=24)).isoformat()
            stale_cutoff = (now_dt - timedelta(days=7)).isoformat()

            due_soon = await query_entities(db_path, due_before=due_cutoff, status="active")
            all_active = await list_entities(db_path, status="active", limit=200)
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
            "material result or risk; otherwise call `quit` silently."
        )

    return f"""## Memory context
{context if context else "No recent context available."}

## Objective
- This is an autonomous work cycle, not a social check-in. Proactively advance one useful, concrete item when the context supports it.
- Look for an open task, unresolved decision, due or stale item, missing verification, research gap, or small project-maintenance action.
- When an actionable item exists, use tools and complete the work now. Do not merely suggest work, offer to help, or describe what you could do.
- Prefer bounded work with a verifiable result. Respect the proactive write-safety boundary in the system instructions.
- Report only a concrete completed result, a newly verified material fact, or a specific blocker/risk that genuinely needs the user's attention. State what changed or was found and why it matters.
- If there is no useful safe action, or no material result worth reporting, call `quit` silently.
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
# Scheduled-task execution  (preserved from the original scheduler)
# ---------------------------------------------------------------------------


def _user_visible_text(result: str, prompt: str) -> str:
    """Return *result* if it conveys real user-facing output, else *prompt*.

    The execution-agent returns ``"Done."`` as a default when it produces no
    text (see ``_run_execution_agent_locked``).  A bare ``"Done."`` or empty
    string is indistinguishable from "I have nothing to report" — in that case
    the original task prompt is a better signal than a dead ``"Done."``.
    """
    text = (result or prompt).strip()
    if not text or text.lower().rstrip(".") == "done":
        return prompt
    return text


def _plaintext(body: str) -> str:
    """Strip common Markdown formatting for plaintext notification channels.

    macOS ``terminal-notifier``, WeChat, and the in-app SSE notification panel
    all display the content verbatim — they do not interpret Markdown.
    """
    body = _re.sub(r'\*\*(.+?)\*\*', r'\1', body)
    body = _re.sub(r'\*(.+?)\*', r'\1', body)
    body = _re.sub(r'`([^`]+)`', r'\1', body)
    body = _re.sub(r'#{1,6}\s+', '', body)
    body = _re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', body)
    return body


def _truncate(text: str, limit: int) -> str:
    """Truncate *text* to *limit* characters, appending ``…`` when cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


async def _check_and_execute_tasks(bot, db_path: str) -> None:
    """Query all due tasks from the database and execute each one."""
    try:
        tasks = await db.get_due_tasks(db_path)
    except Exception:
        logger.exception("Failed to query due tasks")
        return

    for task in tasks:
        try:
            await _execute_task(task, bot, db_path)
        except Exception:
            logger.exception("Failed to execute task %s", task["id"])


async def _execute_task(task: dict, bot, db_path: str) -> None:
    """Run a single scheduled task and update its next-run time."""
    task_id = task["id"]
    task_chat_id = task["chat_id"]
    prompt = task["prompt"]
    permission_mode = str(task.get("permission_mode") or "workspace_only").strip().lower()
    logger.info(
        "Executing task %s for chat %s (permission: %s): %s",
        task_id, task_chat_id, permission_mode, prompt[:80],
    )

    # Apply stored permission_mode: temporarily elevate write permissions via ContextVar
    from cyrene.agent.context import bind_run_context
    permission_binding = None
    if permission_mode == "full_access":
        permission_binding = bind_run_context(temporary_full_access=True)
        logger.info("Temporarily elevated write permissions to full_access for task %s", task_id)

    wrapped_prompt = (
        "You are executing a scheduled task. "
        "First use tools to complete the task, then use the send_message tool "
        "to report the result to the user. "
        f"Task: {prompt}"
    )
    notify_state: dict[str, bool] = {"sent": False}

    start = time.monotonic()
    had_error = False
    try:
        result = await run_task_agent(
            wrapped_prompt, bot, task_chat_id, db_path, notify_state,
        )

        # Fallback: if the model forgot to call send_message, surface what it
        # actually did so the result doesn't go silent in web-only mode.
        # Nested try/except so a notification failure never corrupts the result.
        if not notify_state["sent"]:
            try:
                fallback_text = _user_visible_text(result, prompt)
                truncated = _truncate(fallback_text, 2000)
                await append_system_message(
                    f"Result: {truncated}",
                    message_meta={"scheduled": True},
                    publish_event={"scheduled": True},
                )
            except Exception:
                logger.warning("Failed to append fallback message for task %s", task_id)

        duration_ms = int((time.monotonic() - start) * 1000)
        await db.log_task_run(
            db_path, task_id, duration_ms, "success", result=result,
        )
    except Exception as e:
        had_error = True
        duration_ms = int((time.monotonic() - start) * 1000)
        await db.log_task_run(
            db_path, task_id, duration_ms, "error", error=str(e),
        )
        result = f"Error: {e}"
    finally:
        # Restore original permission mode after task execution
        if permission_binding is not None:
            permission_binding.reset()
            logger.info("Restored write permissions after task %s", task_id)

    # Re-arm (or retire) the task based on its schedule type. ``next_run`` is
    # computed through the shared schedule spec so a recurring task fires at the
    # same cadence the REST API and agent tool promised at creation time.
    stype = task["schedule_type"]
    svalue = task["schedule_value"]
    now = datetime.now(timezone.utc)

    try:
        if stype == "once":
            await db.update_task_after_run(
                db_path, task_id, result, None, "completed",
            )
        else:
            next_run = compute_next_run(stype, svalue, now=now)
            await db.update_task_after_run(
                db_path, task_id, result, next_run, "active",
            )
    except ValueError:
        logger.warning(
            "Unknown/invalid schedule %s(%s) for task %s", stype, svalue, task_id,
        )
    except Exception:
        logger.exception(
            "Failed to update task %s after execution", task_id,
        )

    # ── Multi-channel notifications after task execution ─────────────────
    try:
        # Use agent's execution result for notifications; fall back to prompt
        # when needed.  On error the title already says "Scheduled task error",
        # so use the safe prompt text rather than the raw exception message.
        if had_error:
            notify_body = prompt
        else:
            notify_body = _user_visible_text(result, prompt)
        notify_body = _plaintext(notify_body)  # strip markdown for plain channels
        summary = _truncate(notify_body, 120)
        status_label = "error" if had_error else "completed"

        for ch in ("desktop", "sse", "wechat"):
            await notify(
                title=f"Scheduled task {status_label}",
                body=summary,
                channel=ch,
            )
        append_notification(
            title="日程提醒",
            body=summary,
            tab="system",
            project_ref=task.get("project_id"),
            source="scheduled_task_run",
            source_label="日程",
            link_label="日程",
            meta={"taskId": task_id, "status": status_label},
        )
    except Exception:
        logger.exception("Failed to send task execution notifications")


# ---------------------------------------------------------------------------
# Proactive message delivery — bot + session state + SSE event
# ---------------------------------------------------------------------------


async def _deliver_proactive_message(text: str, bot, chat_id: int) -> None:
    """Deliver a proactive message so it appears in both the bot and the Web UI.

    1. Sends the text through the bot (Telegram or WebBot).
    2. Appends an assistant entry to ``state.json`` so the message is visible
       in the Web UI chat history on the next page load.
    3. Publishes a ``chat_message`` SSE event so connected frontends update
       in real time without a refresh.

    The state.json write is best-effort — failures are logged and swallowed
    so a corrupt or missing state file never blocks proactive delivery.
    """
    # 1. Bot delivery (Telegram push or WebBot memory queue)
    if bot is not None:
        await bot.send_message(chat_id=chat_id, text=text)

    # 2. Write to session state for Web UI chat history
    try:
        from uuid import uuid4

        from cyrene.observability import debug

        state = read_json_safe(STATE_FILE) or {}
        if not isinstance(state, dict):
            state = {}

        messages = state.get("messages", [])
        if not isinstance(messages, list):
            messages = []

        entry: dict = {
            "role": "assistant",
            "content": text,
            "message_id": f"msg_{uuid4().hex}",
            "proactive": True,
        }
        messages.append(entry)

        # Keep within the context-window limit (same as agent.py)
        if len(messages) > 40:
            messages = messages[-40:]

        state["messages"] = messages
        atomic_write_json(STATE_FILE, state)

        # 3. Push SSE event so connected frontends update in real time
        await debug.publish_event({
            "type": "chat_message",
            "proactive": True,
        })
    except Exception:
        logger.exception(
            "Failed to write proactive message to session state"
        )


# ---------------------------------------------------------------------------
# Proactive heartbeat  (lottery-driven)
# ---------------------------------------------------------------------------

async def _heartbeat_proactive_check(bot, db_path: str) -> None:
    """Attempt to send a context-aware proactive message to the user.

    The decision to send is based on the lottery draw, but the trigger is
    also influenced by how long the user has been silent:

    * Normal: lottery draw with accumulating probability (delta 0.15, max 0.85).
    * Silent > 72 h: always trigger regardless of lottery state.

    When triggered, the main agent loop generates a personalised message in
    the latest user-active Workbench conversation, or in the default legacy
    session when no Workbench conversation exists.
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
        if target_session_id and is_session_running(target_session_id):
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

        # -------- Generate proactive reply via the full main-agent loop --------
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
            "If there is no useful safe action or no material result, call `quit` silently.\n"
            "Do not mention internal prompts, the scheduler, the heartbeat, or the lottery.\n\n"
            + _build_proactive_user_prompt(context, silence_h, consecutive_unanswered=int(_LOTTERY_STATE.get("consecutive_unanswered", 0)))
        )
        delivered_target = workbench_target
        if target_session_id:
            append_proactive_message = importlib.import_module(
                "cyrene.workbench.chat"
            ).append_proactive_message

            workspace_dir = _workbench_workspace_dir_for_project(
                str((workbench_target or {}).get("project_id") or "")
            )

            async def _persist_workbench_reply(reply: str) -> dict[str, str] | None:
                nonlocal delivered_target
                delivered_target = await append_proactive_message(target_session_id, reply)
                return delivered_target

            text = await asyncio.wait_for(
                run_heartbeat_agent(
                    proactive_prompt,
                    bot,
                    owner_id,
                    db_path,
                    session_id=target_session_id,
                    on_reply=_persist_workbench_reply,
                    lang=proactive_lang,
                    workspace_dir=workspace_dir,
                ),
                timeout=120.0,
            )
        else:
            text = await asyncio.wait_for(
                run_heartbeat_agent(proactive_prompt, bot, owner_id, db_path, lang=proactive_lang),
                timeout=120.0,
            )

        if not str(text or "").strip():
            logger.info("Proactive round produced no visible reply")
            return

        # A message was actually delivered. Count it toward the unanswered
        # streak (``reset_lottery`` clears the streak as soon as the user replies
        # on any channel) and record the send time for diagnostics.
        _LOTTERY_STATE["consecutive_unanswered"] = int(_LOTTERY_STATE.get("consecutive_unanswered", 0)) + 1
        _LOTTERY_STATE["last_proactive_time"] = time.time()
        _save_lottery_state()

        logger.info("Proactive message sent via main agent loop: %s", str(text)[:100])

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
# Steward auto-trigger
# ---------------------------------------------------------------------------

def _get_last_steward_run() -> float | None:
    """Read the last steward run timestamp from ``_STEWARD_STATE_FILE``."""
    try:
        data = read_json_safe(_STEWARD_STATE_FILE)
        if data is not None:
            return float(data.get("last_run", 0))
    except Exception:
        logger.exception("Failed to read steward state")
    return None


def _save_steward_run(timestamp: float) -> None:
    """Persist the steward run timestamp to ``_STEWARD_STATE_FILE``."""
    try:
        atomic_write_json(_STEWARD_STATE_FILE, {"last_run": timestamp})
    except Exception:
        logger.exception("Failed to save steward state")


def _has_new_conversation() -> bool:
    """Check whether today's conversation file exists and has actual content.

    A freshly created file contains only the header line; this function
    returns ``False`` in that case.  At least one archived exchange (with a
    ``## `` timestamp heading) is required.
    """
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_file = CONVERSATIONS_DIR / f"{today}.md"
        if not today_file.exists():
            return False
        content = today_file.read_text(encoding="utf-8").strip()
        # Look for at least one ``## HH:MM:SS`` timestamp heading added by
        # ``archive_exchange``, which indicates real conversation content.
        return bool(content) and "##" in content
    except Exception:
        logger.exception("Failed to check for new conversations")
        return False


def _recent_workbench_conversations(
    since_timestamp: float | None,
    *,
    now: float | None = None,
    max_files: int = 12,
    max_chars: int = 80_000,
    max_chars_per_file: int = 12_000,
) -> str:
    """Read recently modified per-session Workbench conversation archives.

    Workbench stores ``conversations/<session_id>.md`` instead of the legacy
    daily ``YYYY-MM-DD.md`` files. Scan the default workspace plus every
    configured project workspace, bounded by file count and characters.
    """
    current = float(now if now is not None else time.time())
    cutoff = (
        float(since_timestamp)
        if since_timestamp is not None
        else current - 24 * 60 * 60
    )
    directories: dict[Path, str] = {CONVERSATIONS_DIR: "default"}
    try:
        from cyrene.workbench import runtime as workbench_runtime
        from cyrene.runtime.memory.conversations import session_conversations_dir

        payload = workbench_runtime._read_workbench_store()
        for project in payload.get("projects", []) or []:
            if not isinstance(project, dict):
                continue
            workspace_path = str(project.get("workspacePath") or "").strip()
            if workspace_path:
                directories[session_conversations_dir(workspace_path)] = str(
                    project.get("id") or "default"
                )
    except Exception:
        logger.debug("Could not enumerate Workbench conversation directories", exc_info=True)

    candidates: list[tuple[Path, str]] = []
    for directory, project_id in directories.items():
        try:
            for path in directory.glob("*.md"):
                # Legacy daily archives are loaded separately.
                if _re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", path.name):
                    continue
                if path.stat().st_mtime > cutoff:
                    candidates.append((path, project_id))
        except OSError:
            logger.debug("Could not scan Workbench conversations in %s", directory, exc_info=True)

    candidates.sort(key=lambda item: item[0].stat().st_mtime, reverse=True)
    parts: list[str] = []
    used = 0
    for path, project_id in candidates[:max_files]:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            logger.debug("Could not read Workbench conversation %s", path, exc_info=True)
            continue
        excerpt = content[-max_chars_per_file:]
        block = (
            "=== Workbench conversation: "
            f"{path.name} project_id={project_id} ===\n{excerpt}"
        )
        if parts and used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(reversed(parts))


async def _run_steward_if_needed(bot, db_path: str) -> None:
    """Check conditions and run the steward agent when appropriate.

    Triggers when:
    1. At least ``STEWARD_INTERVAL`` seconds have elapsed since the last run.
    2. A legacy daily archive or a recently modified Workbench session archive
       contains conversation text.
    """
    try:
        last_run = _get_last_steward_run()
        now = time.time()

        if last_run is not None and (now - last_run) < STEWARD_INTERVAL:
            logger.debug(
                "Steward not due yet (last run %.0f s ago)", now - last_run,
            )
            return

        legacy_text = (
            await get_recent_conversations(days=1)
            if _has_new_conversation()
            else ""
        )
        workbench_text = await asyncio.to_thread(
            _recent_workbench_conversations,
            last_run,
            now=now,
        )
        conversation_text = "\n\n".join(
            part for part in (legacy_text, workbench_text) if part
        )
        soulmd_content = read_soul()

        if not conversation_text:
            logger.debug("No new legacy or Workbench conversations, skipping steward")
            return

        logger.info("Steward conditions met -- running steward agent")
        # The steward does not deliver a chat reply. OWNER_ID is only a runtime
        # context identifier here, so Desktop/Web installs can safely use 0.
        steward_chat_id = OWNER_ID if OWNER_ID is not None else 0
        result = await run_steward_agent(
            conversation_text, soulmd_content, bot, steward_chat_id, db_path,
        )

        result_stripped = (result or "").strip()
        if result_stripped.upper().startswith("SKIP") and "ENTITY" not in result_stripped:
            logger.info("Steward returned SKIP -- no changes to SOUL.md")
        elif result_stripped:
            changes = apply_soul_update(result)
            logger.info(
                "Steward applied %d change(s) to SOUL.md", len(changes),
            )
        else:
            logger.info("Steward returned empty result, no changes applied")

        # 解析 Steward 提取的实体（ENTITY 行）
        try:
            from cyrene.tool_impl.entity.store import add_candidate, has_similar_entity
            for line in result_stripped.splitlines():
                line = line.strip()
                if not line.upper().startswith("ENTITY "):
                    continue
                # Parse: ENTITY type="task" title="..." confidence="0.85" content="..."
                import re as _re2
                e_type = _re2.search(r'type="([^"]*)"', line)
                e_title = _re2.search(r'title="([^"]*)"', line)
                e_conf = _re2.search(r'confidence="([^"]*)"', line)
                e_content = _re2.search(r'content="([^"]*)"', line)
                e_project = _re2.search(r'project_id="([^"]*)"', line)
                if e_type and e_title and e_conf:
                    entity_type = e_type.group(1)
                    entity_title = e_title.group(1)
                    project_id = (
                        e_project.group(1).strip()
                        if e_project
                        else "default"
                    ) or "default"
                    # 去重检查：同类型+相似标题的实体或候选已存在时跳过
                    if await has_similar_entity(
                        db_path,
                        entity_type,
                        entity_title,
                        project_id=project_id,
                    ):
                        logger.debug("Skipping duplicate entity: %s / %s", entity_type, entity_title)
                        continue
                    candidate_id = await add_candidate(
                        db_path,
                        type=entity_type,
                        title=entity_title,
                        content=e_content.group(1) if e_content else "",
                        confidence=float(e_conf.group(1)),
                        project_id=project_id,
                        raw_text=line,
                    )
                    logger.info("Steward extracted entity candidate %s: %s", candidate_id[:8], entity_title)
        except Exception:
            logger.exception("Failed to parse steward entity extractions")

        # 处理置信度 >= 0.8 的候选事务，自动提升为正式事务
        try:
            from cyrene.tool_impl.entity.store import process_candidates
            promoted = await process_candidates(db_path)
            if promoted:
                logger.info("Steward promoted %d candidate entity/entities", len(promoted))
        except Exception:
            logger.exception("process_candidates failed during steward run")

        _save_steward_run(now)

    except Exception:
        logger.exception("Steward auto-trigger failed")


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

async def _scheduled_task_tick(bot, db_path: str) -> None:
    """Keep due-task precision independent from low-frequency maintenance."""
    try:
        await _check_and_execute_tasks(bot, db_path)
    except Exception:
        logger.exception("Scheduled-task tick error")


async def _proactive_tick(bot, db_path: str) -> None:
    try:
        async with _get_maintenance_lock():
            await _heartbeat_proactive_check(bot, db_path)
    except Exception:
        logger.exception("Proactive heartbeat error")


async def _steward_tick(bot, db_path: str) -> None:
    try:
        async with _get_maintenance_lock():
            await _run_steward_if_needed(bot, db_path)
    except Exception:
        logger.exception("Steward tick error")


async def _behavior_learning_tick(bot, db_path: str) -> None:
    try:
        async with _get_maintenance_lock():
            # Import lazily so startup does not load the learning stack merely
            # to register the scheduler.
            from cyrene.learning import tick as _pattern_tick

            await _pattern_tick(bot, db_path)
    except Exception:
        logger.exception("Behavior-learning tick error")


async def _cleanup_tick() -> None:
    try:
        async with _get_maintenance_lock():
            clear_old_entries(days=7)
    except Exception:
        logger.exception("Short-term cleanup error")


async def _heartbeat(bot, db_path: str) -> None:
    """Backward-compatible alias for the lightweight due-task poll."""
    await _scheduled_task_tick(bot, db_path)


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
# Public entry point  (signature preserved for bot.py compatibility)
# ---------------------------------------------------------------------------

def setup_scheduler(bot, db_path: str) -> AsyncIOScheduler:
    """Create a scheduler with independent task and maintenance cadences.

    The signature is kept stable so that ``bot._post_init`` continues to
    work without modification.
    """
    global _scheduler
    global _workbench_db_path
    _workbench_db_path = str(db_path)
    try:
        from cyrene.workbench.notifications import configure_store as _configure_notifications

        _chat_store = importlib.import_module("cyrene.workbench.chat")
        _chat_store.configure_store(str(db_path))
        _configure_notifications(str(db_path))
    except Exception:
        logger.debug("Could not configure Workbench SQLite stores for scheduler", exc_info=True)
    _load_lottery_state()
    hb_seconds = _get_heartbeat_interval()
    _scheduler = AsyncIOScheduler()
    _add_interval_job(
        _scheduler,
        _scheduled_task_tick,
        seconds=SCHEDULER_INTERVAL,
        job_id="scheduled_tasks",
        args=[bot, db_path],
    )
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
        "Scheduler configured: tasks=%ds, behavior=%ds, proactive=%ds, "
        "steward=%ds, cleanup=86400s",
        SCHEDULER_INTERVAL,
        PATTERN_DETECTION_INTERVAL,
        hb_seconds,
        STEWARD_INTERVAL,
    )
    return _scheduler
