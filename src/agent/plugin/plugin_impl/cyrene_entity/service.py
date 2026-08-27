"""Editable service for durable entities and their reminder side effects."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .store import EntityRepository
from cyrene.runtime.persistence.scheduler import SchedulerRepository

_CREATE_FIELDS = frozenset(
    {
        "type",
        "title",
        "content",
        "status",
        "tags",
        "priority",
        "effort",
        "due_date",
        "parent_id",
        "linked_ids",
        "people",
        "source",
        "source_round_id",
        "confidence",
        "metadata",
        "project_id",
    }
)
_UPDATE_FIELDS = frozenset(
    {
        "status",
        "priority",
        "due_date",
        "content",
        "tags",
        "people",
        "title",
        "effort",
        "metadata",
        "linked_ids",
        "parent_id",
    }
)
_ENTITY_STATUSES = frozenset({"active", "paused", "done", "archived", "abandoned"})
_ENTITY_PRIORITIES = frozenset({"high", "medium", "low"})
_ENTITY_SOURCES = frozenset({"explicit", "extracted"})


def _required_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"entity {field} cannot be empty")
    return normalized


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _choice(value: Any, field: str, allowed: frozenset[str], default: str) -> str:
    normalized = str(value or default).strip()
    if normalized not in allowed:
        raise ValueError(
            f"invalid entity {field}: {normalized!r}; expected one of "
            + ", ".join(sorted(allowed))
        )
    return normalized


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"entity {field} must be an array")
    return [str(item).strip() for item in value if str(item).strip()]


class EntityService:
    """Single entity boundary shared by Plugins, HTTP, Workbench and jobs."""

    def __init__(
        self,
        db_path: str,
        *,
        reminder_chat_id: Any = None,
        origin_session_id: str = "",
        repository: EntityRepository | None = None,
        scheduler: SchedulerRepository | None = None,
    ) -> None:
        self.repository = repository or EntityRepository(db_path)
        self.scheduler = scheduler or SchedulerRepository(self.repository.db_path)
        self.reminder_chat_id = reminder_chat_id
        self.origin_session_id = str(origin_session_id or "").strip()

    @property
    def db_path(self) -> str:
        return self.repository.db_path

    async def startup(self) -> None:
        """Initialize the Plugin-owned database schema."""

        await self.repository.ensure_ready()

    def _chat_id(self) -> int:
        value = self.reminder_chat_id
        if value is None:
            from cyrene.config import OWNER_ID

            value = OWNER_ID
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _limit(value: Any, *, default: int, maximum: int = 500) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(1, min(parsed, maximum))

    @staticmethod
    def _metadata(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError("entity metadata must be an object")
        return dict(value)

    async def _create_reminder(self, entity: Mapping[str, Any]) -> str:
        due_date = _required_text(entity.get("due_date"), "due_date")
        return await self.scheduler.create(
            chat_id=self._chat_id(),
            prompt=f"提醒用户：{entity['title']} 到期了",
            schedule_type="once",
            schedule_value=due_date,
            next_run=due_date,
            project_id=str(entity.get("project_id") or "default"),
            origin_session_id=self.origin_session_id,
            action_type="agent_task",
        )

    async def _attach_new_reminder(self, entity: dict[str, Any]) -> dict[str, Any]:
        task_id = ""
        try:
            task_id = await self._create_reminder(entity)
            metadata = self._metadata(entity.get("metadata"))
            metadata["reminder_task_id"] = task_id
            updated = await self.repository.update(entity["id"], metadata=metadata)
            if updated is None:
                raise RuntimeError("entity disappeared while attaching its reminder")
            return updated
        except BaseException:
            if task_id:
                await self.scheduler.delete(task_id)
            await self.repository.delete(entity["id"], permanent=True)
            raise

    async def _sync_reminder(self, entity: dict[str, Any]) -> dict[str, Any]:
        metadata = self._metadata(entity.get("metadata"))
        task_id = str(metadata.get("reminder_task_id") or "").strip()
        should_exist = (
            entity.get("source") == "explicit"
            and entity.get("status") == "active"
            and bool(entity.get("due_date"))
        )

        if should_exist:
            if task_id and await self.scheduler.get(task_id) is not None:
                due_date = str(entity["due_date"])
                await self.scheduler.edit(
                    task_id,
                    {
                        "prompt": f"提醒用户：{entity['title']} 到期了",
                        "schedule_type": "once",
                        "schedule_value": due_date,
                        "next_run": due_date,
                    },
                )
                await self.scheduler.update_status(task_id, "active")
                return entity

            new_task_id = await self._create_reminder(entity)
            metadata["reminder_task_id"] = new_task_id
            updated = await self.repository.update(entity["id"], metadata=metadata)
            if updated is None:
                await self.scheduler.delete(new_task_id)
                raise RuntimeError("entity disappeared while attaching its reminder")
            return updated

        if not task_id:
            return entity
        await self.scheduler.update_status(task_id, "cancelled")
        metadata.pop("reminder_task_id", None)
        updated = await self.repository.update(entity["id"], metadata=metadata)
        return updated or entity

    async def create(self, **values: Any) -> dict[str, Any]:
        unsupported = sorted(set(values) - _CREATE_FIELDS)
        if unsupported:
            raise ValueError("unsupported entity field(s): " + ", ".join(unsupported))

        entity_type = _required_text(values.get("type"), "type")
        title = _required_text(values.get("title"), "title")
        confidence = float(values.get("confidence", 1.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("entity confidence must be between 0 and 1")

        normalized = {
            "type": entity_type,
            "title": title,
            "content": str(values.get("content") or ""),
            "status": _choice(
                values.get("status"),
                "status",
                _ENTITY_STATUSES,
                "active",
            ),
            "tags": _string_list(values.get("tags"), "tags"),
            "priority": _choice(
                values.get("priority"),
                "priority",
                _ENTITY_PRIORITIES,
                "medium",
            ),
            "effort": _optional_text(values.get("effort")),
            "due_date": _optional_text(values.get("due_date")),
            "parent_id": _optional_text(values.get("parent_id")),
            "linked_ids": _string_list(values.get("linked_ids"), "linked_ids"),
            "people": _string_list(values.get("people"), "people"),
            "source": _choice(
                values.get("source"),
                "source",
                _ENTITY_SOURCES,
                "extracted",
            ),
            "source_round_id": _optional_text(values.get("source_round_id")),
            "confidence": confidence,
            "metadata": self._metadata(values.get("metadata")),
            "project_id": str(values.get("project_id") or "default"),
        }
        entity = await self.repository.create(**normalized)
        if normalized["source"] == "explicit" and normalized["due_date"]:
            return await self._attach_new_reminder(entity)
        return entity

    async def update(self, entity_id: str, **fields: Any) -> dict[str, Any] | None:
        normalized_id = _required_text(entity_id, "id")
        unsupported = sorted(set(fields) - _UPDATE_FIELDS)
        if unsupported:
            raise ValueError("unsupported entity field(s): " + ", ".join(unsupported))
        previous = await self.repository.get(normalized_id)
        if previous is None:
            return None
        if not fields:
            return previous

        normalized = dict(fields)
        for field in ("tags", "people", "linked_ids"):
            if field in normalized:
                normalized[field] = _string_list(normalized[field], field)
        if "status" in normalized:
            normalized["status"] = _choice(
                normalized["status"],
                "status",
                _ENTITY_STATUSES,
                "active",
            )
        if "priority" in normalized:
            normalized["priority"] = _choice(
                normalized["priority"],
                "priority",
                _ENTITY_PRIORITIES,
                "medium",
            )
        if "metadata" in normalized:
            previous_metadata = self._metadata(previous.get("metadata"))
            next_metadata = self._metadata(normalized["metadata"])
            if (
                "reminder_task_id" in previous_metadata
                and "reminder_task_id" not in next_metadata
            ):
                next_metadata["reminder_task_id"] = previous_metadata[
                    "reminder_task_id"
                ]
            normalized["metadata"] = next_metadata
        if "title" in normalized:
            normalized["title"] = _required_text(normalized["title"], "title")
        if "content" in normalized:
            normalized["content"] = str(normalized["content"] or "")
        if "due_date" in normalized:
            normalized["due_date"] = _optional_text(normalized["due_date"])
        for field in ("effort", "parent_id"):
            if field in normalized:
                normalized[field] = _optional_text(normalized[field])

        updated = await self.repository.update(normalized_id, **normalized)
        if updated is None:
            return None
        if {"title", "due_date", "metadata", "status"}.intersection(normalized):
            try:
                return await self._sync_reminder(updated)
            except Exception:
                rollback = {field: previous.get(field) for field in normalized}
                await self.repository.update(normalized_id, **rollback)
                raise
        return updated

    async def delete(self, entity_id: str, *, permanent: bool = False) -> bool:
        entity = await self.repository.get(_required_text(entity_id, "id"))
        if entity is None:
            return False
        metadata = self._metadata(entity.get("metadata"))
        task_id = str(metadata.get("reminder_task_id") or "").strip()
        if task_id:
            await self.scheduler.update_status(task_id, "cancelled")
        return await self.repository.delete(entity["id"], permanent=permanent)

    async def get(self, entity_id: str) -> dict[str, Any] | None:
        return await self.repository.get(str(entity_id or "").strip())

    async def find_by_title(
        self,
        title: str,
        *,
        type: str | None = None,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return await self.repository.find_by_title(
            title,
            type=type,
            project_id=project_id,
            limit=self._limit(limit, default=20),
        )

    async def find_by_id_prefix(
        self,
        prefix: str,
        *,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return await self.repository.find_by_id_prefix(
            prefix,
            project_id=project_id,
            limit=self._limit(limit, default=20),
        )

    async def list(self, **filters: Any) -> list[dict[str, Any]]:
        filters["limit"] = self._limit(filters.get("limit"), default=100)
        return await self.repository.list(**filters)

    async def query(self, q: str = "", **filters: Any) -> list[dict[str, Any]]:
        filters["limit"] = self._limit(filters.get("limit"), default=50)
        return await self.repository.query(q, **filters)

    async def add_candidate(self, **values: Any) -> str:
        return await self.repository.add_candidate(**values)

    async def list_candidates(
        self,
        *,
        limit: int = 50,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.repository.list_candidates(
            limit=self._limit(limit, default=50),
            project_id=project_id,
        )

    async def promote_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        return await self.repository.promote_candidate(candidate_id)

    async def reject_candidate(self, candidate_id: str) -> bool:
        return await self.repository.reject_candidate(candidate_id)

    async def process_candidates(self) -> list[dict[str, Any]]:
        return await self.repository.process_candidates()

    async def has_similar(
        self,
        type: str,
        title: str,
        *,
        project_id: str = "default",
    ) -> bool:
        return await self.repository.has_similar(
            type,
            title,
            project_id=project_id,
        )


__all__ = ["EntityService"]
