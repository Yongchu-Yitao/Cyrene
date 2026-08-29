"""Editable schedule parsing, recurrence, and calendar projection rules."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from cyrene.localization import app_language, localized, localized_plural

MAX_OCCURRENCES_PER_TASK = 200
DEFAULT_EVENT_MINUTES = 30


def resolve_timezone(name: str | None) -> ZoneInfo:
    normalized = str(name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"invalid schedule timezone: {normalized!r}") from exc


def normalize_datetime(raw: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid ISO-8601 datetime: {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=datetime.now().astimezone().tzinfo or timezone.utc
        )
    return parsed.astimezone(timezone.utc).isoformat()


def next_run(
    schedule_type: str,
    schedule_value: str,
    *,
    timezone_name: str | None = None,
    now: datetime | None = None,
) -> str:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    kind = str(schedule_type or "").strip().lower()
    value = str(schedule_value or "").strip()
    if kind == "once":
        return normalize_datetime(value) if value else instant.isoformat()
    if kind == "interval":
        try:
            seconds = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("interval must be an integer number of seconds") from exc
        if seconds <= 0:
            raise ValueError("interval seconds must be positive")
        return (instant + timedelta(seconds=seconds)).isoformat()
    if kind == "cron":
        if not croniter.is_valid(value):
            raise ValueError(f"invalid cron expression: {value!r}")
        zone = resolve_timezone(timezone_name)
        local_wall = instant.astimezone(zone).replace(tzinfo=None)
        next_wall = croniter(value, local_wall).get_next(datetime)
        return next_wall.replace(tzinfo=zone).astimezone(timezone.utc).isoformat()
    raise ValueError(f"unknown schedule_type: {schedule_type!r}")


def parse_iso_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def occurrence_window(
    start: str,
    end: str,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    reference = now or datetime.now(timezone.utc)
    start_at = parse_iso_utc(start) or reference - timedelta(days=1)
    end_at = parse_iso_utc(end) or reference + timedelta(days=60)
    return (end_at, start_at) if end_at < start_at else (start_at, end_at)


def recurrence_label(
    schedule_type: str,
    schedule_value: str,
    schedule_timezone: str = "UTC",
    *,
    language: str | None = None,
) -> str:
    resolved_language = app_language(language)
    kind = str(schedule_type or "").strip()
    value = str(schedule_value or "").strip()
    if kind == "once":
        return localized("Once", "单次", language=resolved_language)
    if kind == "interval":
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            return localized("Fixed interval", "固定间隔", language=resolved_language)
        if seconds % 86400 == 0:
            return localized_plural(
                "Every {count} day",
                "Every {count} days",
                "每 {count} 天",
                count=seconds // 86400,
                language=resolved_language,
            )
        if seconds % 3600 == 0:
            return localized_plural(
                "Every {count} hour",
                "Every {count} hours",
                "每 {count} 小时",
                count=seconds // 3600,
                language=resolved_language,
            )
        if seconds % 60 == 0:
            return localized_plural(
                "Every {count} minute",
                "Every {count} minutes",
                "每 {count} 分钟",
                count=seconds // 60,
                language=resolved_language,
            )
        return localized_plural(
            "Every {count} second",
            "Every {count} seconds",
            "每 {count} 秒",
            count=seconds,
            language=resolved_language,
        )
    if kind != "cron":
        return kind or "—"
    parts = value.split()
    if len(parts) != 5:
        return f"Cron: {value}"
    minute, hour, day_of_month, month, day_of_week = parts
    clock = (
        f" {int(hour):02d}:{int(minute):02d}({schedule_timezone or 'UTC'})"
        if minute.isdigit() and hour.isdigit()
        else ""
    )
    if day_of_month == "*" and month == "*" and day_of_week == "*":
        return localized(
            "Daily{clock}",
            "每天{clock}",
            language=resolved_language,
            clock=clock,
        )
    if day_of_month == "*" and month == "*" and day_of_week != "*":
        try:
            index = int(day_of_week) % 7
            weekday = (
                ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][index]
                if resolved_language == "en"
                else ["日", "一", "二", "三", "四", "五", "六"][index]
            )
            return localized(
                "Every {weekday}{clock}",
                "每周{weekday}{clock}",
                language=resolved_language,
                weekday=weekday,
                clock=clock,
            )
        except (ValueError, IndexError):
            pass
    if day_of_month != "*" and month == "*" and day_of_week == "*":
        return localized(
            "Monthly on day {day}{clock}",
            "每月 {day} 号{clock}",
            language=resolved_language,
            day=day_of_month,
            clock=clock,
        )
    return f"Cron: {value}"


def expand_task(
    task: dict[str, Any],
    start: datetime,
    end: datetime,
) -> list[datetime]:
    kind = str(task.get("schedule_type") or "").strip()
    value = str(task.get("schedule_value") or "").strip()
    if kind == "once":
        anchor = parse_iso_utc(task.get("next_run")) or parse_iso_utc(value)
        return [anchor] if anchor and start <= anchor <= end else []
    if kind == "interval":
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            return []
        if seconds <= 0:
            return []
        anchor = parse_iso_utc(task.get("next_run")) or start
        offset = (start - anchor).total_seconds()
        first = anchor + timedelta(seconds=math.ceil(offset / seconds) * seconds)
        result: list[datetime] = []
        while first <= end and len(result) < MAX_OCCURRENCES_PER_TASK:
            result.append(first)
            first += timedelta(seconds=seconds)
        return result
    if kind != "cron" or not croniter.is_valid(value):
        return []
    try:
        zone = resolve_timezone(task.get("schedule_timezone"))
    except ValueError:
        return []
    iterator = croniter(
        value,
        start.astimezone(zone).replace(tzinfo=None) - timedelta(seconds=1),
    )
    result = []
    while len(result) < MAX_OCCURRENCES_PER_TASK:
        next_wall = iterator.get_next(datetime)
        fire_at = next_wall.replace(tzinfo=zone).astimezone(timezone.utc)
        if fire_at > end:
            break
        if fire_at >= start:
            result.append(fire_at)
    return result


def task_events(
    task: dict[str, Any],
    start: datetime,
    end: datetime,
    *,
    language: str | None = None,
) -> list[dict[str, Any]]:
    resolved_language = app_language(language)
    kind = str(task.get("schedule_type") or "").strip()
    value = str(task.get("schedule_value") or "")
    zone = str(task.get("schedule_timezone") or "UTC")
    events: list[dict[str, Any]] = []
    for fire_at in expand_task(task, start, end):
        events.append(
            {
                "id": f"{task['id']}@{fire_at.isoformat()}",
                "task_id": task["id"],
                "source": "task",
                "title": task.get("prompt") or localized(
                    "Scheduled task", "定时任务", language=resolved_language
                ),
                "start": fire_at.isoformat(),
                "end": (
                    fire_at + timedelta(minutes=DEFAULT_EVENT_MINUTES)
                ).isoformat(),
                "all_day": False,
                "category": "task_once" if kind == "once" else "task_recurring",
                "schedule_type": kind,
                "schedule_value": value,
                "schedule_timezone": zone,
                "recurrence": recurrence_label(
                    kind, value, zone, language=resolved_language
                ),
                "status": task.get("status") or "active",
                "next_run": task.get("next_run"),
                "last_run": task.get("last_run"),
                "permission_mode": task.get("permission_mode") or "workspace_only",
                "action_type": task.get("action_type") or "agent_task",
                "run_state": task.get("run_state") or "idle",
                "current_run_id": task.get("current_run_id"),
                "last_error": task.get("last_error"),
            }
        )
    return events


__all__ = [
    "expand_task",
    "next_run",
    "normalize_datetime",
    "occurrence_window",
    "parse_iso_utc",
    "recurrence_label",
    "resolve_timezone",
    "task_events",
]
