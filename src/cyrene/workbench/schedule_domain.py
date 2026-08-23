"""Pure recurrence and calendar-occurrence rules for Workbench schedules."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from croniter import croniter

from cyrene.runtime.schedule_spec import resolve_schedule_timezone

MAX_OCCURRENCES_PER_TASK = 200
DEFAULT_EVENT_MINUTES = 30


def parse_iso_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(str(raw)[:10])
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def occurrence_window(start: str, end: str, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    reference = now or datetime.now(timezone.utc)
    start_at = parse_iso_utc(start) or reference - timedelta(days=1)
    end_at = parse_iso_utc(end) or reference + timedelta(days=60)
    return (end_at, start_at) if end_at < start_at else (start_at, end_at)


def recurrence_label(schedule_type: str, schedule_value: str, schedule_timezone: str = "UTC") -> str:
    schedule_type = (schedule_type or "").strip()
    schedule_value = (schedule_value or "").strip()
    if schedule_type == "once":
        return "单次"
    if schedule_type == "interval":
        return _interval_label(schedule_value)
    if schedule_type == "cron":
        return _cron_label(schedule_value, schedule_timezone)
    return schedule_type or "—"


def _interval_label(value: str) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return "固定间隔"
    if seconds % 86400 == 0:
        return f"每 {seconds // 86400} 天"
    if seconds % 3600 == 0:
        return f"每 {seconds // 3600} 小时"
    if seconds % 60 == 0:
        return f"每 {seconds // 60} 分钟"
    return f"每 {seconds} 秒"


def _cron_label(value: str, timezone_name: str) -> str:
    parts = value.split()
    if len(parts) != 5:
        return f"Cron: {value}"
    minute, hour, day_of_month, month, day_of_week = parts
    hhmm = f" {int(hour):02d}:{int(minute):02d}({timezone_name or 'UTC'})" if minute.isdigit() and hour.isdigit() else ""
    if day_of_month == "*" and month == "*" and day_of_week == "*":
        return f"每天{hhmm}"
    if day_of_month == "*" and month == "*" and day_of_week != "*":
        try:
            return f"每周{['日', '一', '二', '三', '四', '五', '六'][int(day_of_week) % 7]}{hhmm}"
        except (ValueError, IndexError):
            pass
    if day_of_month != "*" and month == "*" and day_of_week == "*":
        return f"每月 {day_of_month} 号{hhmm}"
    return f"Cron: {value}"


def expand_task(task: dict[str, Any], start: datetime, end: datetime) -> list[datetime]:
    schedule_type = str(task.get("schedule_type") or "").strip()
    schedule_value = str(task.get("schedule_value") or "").strip()
    if schedule_type == "once":
        anchor = parse_iso_utc(task.get("next_run")) or parse_iso_utc(schedule_value)
        return [anchor] if anchor and start <= anchor <= end else []
    if schedule_type == "interval":
        return _expand_interval(schedule_value, parse_iso_utc(task.get("next_run")), start, end)
    if schedule_type == "cron":
        return _expand_cron(schedule_value, task.get("schedule_timezone"), start, end)
    return []


def _expand_interval(value: str, anchor: datetime | None, start: datetime, end: datetime) -> list[datetime]:
    try:
        step = int(value)
    except (TypeError, ValueError):
        return []
    if step <= 0:
        return []
    anchor = anchor or start
    if anchor > start:
        first = anchor - timedelta(seconds=math.ceil((anchor - start).total_seconds() / step) * step)
        if first < start:
            first += timedelta(seconds=step)
    else:
        first = anchor + timedelta(seconds=math.floor((start - anchor).total_seconds() / step) * step)
        if first < start:
            first += timedelta(seconds=step)
    result: list[datetime] = []
    while first <= end and len(result) < MAX_OCCURRENCES_PER_TASK:
        result.append(first)
        first += timedelta(seconds=step)
    return result


def _expand_cron(value: str, timezone_name: Any, start: datetime, end: datetime) -> list[datetime]:
    if not croniter.is_valid(value):
        return []
    try:
        schedule_timezone = resolve_schedule_timezone(timezone_name)
    except ValueError:
        return []
    iterator = croniter(value, start.astimezone(schedule_timezone).replace(tzinfo=None) - timedelta(seconds=1))
    result: list[datetime] = []
    while len(result) < MAX_OCCURRENCES_PER_TASK:
        next_wall = iterator.get_next(datetime)
        next_utc = next_wall.replace(tzinfo=schedule_timezone).astimezone(timezone.utc)
        if next_utc > end:
            break
        if next_utc >= start:
            result.append(next_utc)
    return result


def task_events(task: dict[str, Any], start: datetime, end: datetime) -> list[dict[str, Any]]:
    schedule_type = str(task.get("schedule_type") or "").strip()
    schedule_value = task.get("schedule_value") or ""
    schedule_timezone = task.get("schedule_timezone") or "UTC"
    return [{
        "id": f"{task['id']}@{fire.isoformat()}", "task_id": task["id"], "source": "task",
        "title": task.get("prompt") or "定时任务", "start": fire.isoformat(),
        "end": (fire + timedelta(minutes=DEFAULT_EVENT_MINUTES)).isoformat(), "all_day": False,
        "category": "task_once" if schedule_type == "once" else "task_recurring",
        "schedule_type": schedule_type, "schedule_value": schedule_value,
        "schedule_timezone": schedule_timezone,
        "recurrence": recurrence_label(schedule_type, schedule_value, schedule_timezone),
        "status": task.get("status") or "active", "next_run": task.get("next_run"),
        "last_run": task.get("last_run"), "permission_mode": task.get("permission_mode") or "workspace_only",
        "action_type": task.get("action_type") or "agent_task",
    } for fire in expand_task(task, start, end)]


def entity_events(entities: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entity in entities:
        due = parse_iso_utc(entity.get("due_date"))
        if not due or due < start or due > end:
            continue
        result.append({"id": f"entity:{entity['id']}", "entity_id": entity["id"], "source": "entity",
            "title": entity.get("title") or "任务截止", "start": entity.get("due_date"), "end": None,
            "all_day": True, "category": "entity_due", "entity_type": entity.get("type") or "task",
            "status": entity.get("status") or "active", "priority": entity.get("priority")})
    return result


__all__ = ["entity_events", "expand_task", "occurrence_window", "recurrence_label", "task_events"]
