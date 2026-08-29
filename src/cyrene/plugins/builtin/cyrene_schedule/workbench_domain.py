"""Calendar projection rules owned by the schedule Plugin pack."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from cyrene.localization import localized


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


def entity_events(entities: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entity in entities:
        due = parse_iso_utc(entity.get("due_date"))
        if not due or due < start or due > end:
            continue
        result.append({"id": f"entity:{entity['id']}", "entity_id": entity["id"], "source": "entity",
            "title": entity.get("title") or localized("Task due", "任务截止"), "start": entity.get("due_date"), "end": None,
            "all_day": True, "category": "entity_due", "entity_type": entity.get("type") or "task",
            "status": entity.get("status") or "active", "priority": entity.get("priority")})
    return result


__all__ = ["entity_events", "occurrence_window", "parse_iso_utc"]
