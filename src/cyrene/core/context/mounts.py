"""Pure normalization of persisted and plugin-contributed context mounts."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any


def stored_context_mounts(raw_mounts: Any) -> list[dict[str, str]]:
    if not isinstance(raw_mounts, list):
        return []
    return [
        {
            "kind": str(item.get("kind") or "context"),
            "content": str(item.get("content") or "").strip(),
            "source": str(item.get("source") or "context_tree"),
            "lifecycle": str(item.get("lifecycle") or ""),
        }
        for item in raw_mounts
        if isinstance(item, Mapping)
        and str(item.get("content") or "").strip()
    ]


def unique_context_mounts(
    mounts: tuple[dict[str, str], ...] | list[dict[str, str]],
) -> list[dict[str, str]]:
    """Keep later turn mounts from shadowing an earlier stable kind."""

    result: list[dict[str, str]] = []
    used_names: set[str] = set()
    next_suffix: dict[str, int] = {}
    for raw in mounts:
        mount = dict(raw)
        base = str(mount.get("kind") or "context")
        kind = base
        if kind in used_names:
            suffix = max(2, next_suffix.get(base, 2))
            while f"{base}.{suffix}" in used_names:
                suffix += 1
            kind = f"{base}.{suffix}"
            next_suffix[base] = suffix + 1
        used_names.add(kind)
        mount["kind"] = kind
        result.append(mount)
    return result


def contribution_mounts(
    contributions: tuple[dict[str, str], ...],
    *,
    system_kind: str,
    ordinary_kind: str,
    system_source: str,
    ordinary_source: str,
    lifecycle: str,
) -> list[dict[str, str]]:
    mounts: list[dict[str, str]] = []
    used_kinds: dict[str, int] = {}
    for item in contributions:
        content = str(item.get("context") or "").strip()
        if not content:
            continue
        is_system = str(item.get("position") or "") == "system"
        base_kind = str(item.get("context_kind") or "").strip() or (
            system_kind if is_system else ordinary_kind
        )
        occurrence = used_kinds.get(base_kind, 0) + 1
        used_kinds[base_kind] = occurrence
        kind = base_kind if occurrence == 1 else f"{base_kind}.{occurrence}"
        mounts.append({
            "kind": kind,
            "content": content,
            "source": str(item.get("context_source") or "").strip()
            or (system_source if is_system else ordinary_source),
            "lifecycle": lifecycle,
        })
    return mounts

