"""Editable schemas for the durable entity Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_ENTITY_STATUS = ["active", "paused", "done", "archived", "abandoned"]

_PLUGIN_SPECS: dict[str, dict[str, Any]] = {
    "entity.track": {
        "description": (
            "Create a durable entity such as a task, project, decision, fact, "
            "relationship, event, resource, idea, problem, or habit."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Entity type; custom non-empty types are allowed.",
                },
                "title": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "due_date": {
                    "type": "string",
                    "minLength": 1,
                    "description": "ISO 8601 due date or time.",
                },
                "people": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source": {"type": "string", "enum": ["explicit", "extracted"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "source_round_id": {"type": "string"},
            },
            "required": ["type", "title"],
            "additionalProperties": False,
        },
    },
    "entity.update": {
        "description": (
            "Update one entity field using a full ID, unique ID prefix, or exact title."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "type": {"type": "string", "minLength": 1},
                "field": {
                    "type": "string",
                    "enum": [
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
                    ],
                },
                "value": {"description": "New value for the selected field."},
            },
            "required": ["field", "value"],
            "additionalProperties": False,
        },
    },
    "entity.list": {
        "description": "List entities in the current Workbench project.",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": _ENTITY_STATUS},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
    },
    "entity.query": {
        "description": "Search entities in the current Workbench project.",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "type": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": _ENTITY_STATUS},
                "due_before": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Only include entities due before this ISO 8601 value.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
    },
    "entity.delete": {
        "description": (
            "Archive or permanently delete an entity using a full ID, unique ID "
            "prefix, or exact title. Ambiguous matches are never changed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "type": {"type": "string", "minLength": 1},
                "permanent": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
}


def get_plugin_spec(name: str) -> dict[str, Any]:
    """Return the current description and input schema for one Plugin."""

    normalized = str(name)
    try:
        spec = _PLUGIN_SPECS[normalized]
    except KeyError as exc:
        raise KeyError(f"unknown entity Plugin: {normalized}") from exc
    return {
        "description": str(spec["description"]),
        "input_schema": deepcopy(spec["parameters"]),
    }


__all__ = ["get_plugin_spec"]
