"""Authoritative product-operation coverage for Cyrene self-management.

The manifest is deliberately descriptive.  It never dispatches functions and
therefore cannot become a reflection/eval escape hatch.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Exposure = Literal[
    "existing_capability",
    "cyrene_capability",
    "internal_service",
    "ui_surface",
    "user_ceremony",
    "presentation_only",
    "forbidden",
]
Risk = Literal["R0", "R1", "R2", "R3", "R4"]
ApplyMode = Literal["immediate", "next_run", "restart_required", "deferred"]


@dataclass(frozen=True, slots=True)
class AppOperationSpec:
    operation_id: str
    owner: str
    exposure: Exposure
    capability_id: str
    actors: frozenset[str]
    risk: Risk
    apply_mode: ApplyMode
    idempotency_required: bool
    audit_required: bool


def _op(
    operation_id: str,
    owner: str,
    exposure: Exposure,
    capability_id: str,
    risk: Risk,
    *,
    apply_mode: ApplyMode = "immediate",
    actors: tuple[str, ...] = ("main",),
    idempotency: bool | None = None,
) -> AppOperationSpec:
    return AppOperationSpec(
        operation_id=operation_id,
        owner=owner,
        exposure=exposure,
        capability_id=capability_id,
        actors=frozenset(actors),
        risk=risk,
        apply_mode=apply_mode,
        idempotency_required=(risk != "R0") if idempotency is None else idempotency,
        audit_required=exposure in {
            "cyrene_capability", "internal_service", "ui_surface", "user_ceremony",
        },
    )


APP_OPERATIONS: tuple[AppOperationSpec, ...] = (
    _op("cyrene.app.status", "application", "cyrene_capability", "cyrene.app.status", "R0"),
    _op("cyrene.app.window", "host", "cyrene_capability", "cyrene.app.window", "R1"),
    _op("cyrene.app.lifecycle", "host", "internal_service", "cyrene.app.lifecycle", "R3", apply_mode="deferred"),
    _op("cyrene.app.lifecycle.cancel", "host", "internal_service", "cyrene.app.lifecycle", "R1"),
    _op("cyrene.ui.snapshot", "renderer", "cyrene_capability", "cyrene.ui.snapshot", "R0"),
    _op("cyrene.ui.inspect", "renderer", "cyrene_capability", "cyrene.ui.inspect", "R0"),
    _op("cyrene.ui.click", "renderer", "cyrene_capability", "cyrene.ui.click", "R1"),
    _op("cyrene.ui.click.r2", "renderer", "cyrene_capability", "cyrene.ui.click", "R2"),
    _op("cyrene.ui.click.r3", "renderer", "cyrene_capability", "cyrene.ui.click", "R3"),
    _op("cyrene.ui.double_click", "renderer", "cyrene_capability", "cyrene.ui.double_click", "R1"),
    _op("cyrene.ui.double_click.r2", "renderer", "cyrene_capability", "cyrene.ui.double_click", "R2"),
    _op("cyrene.ui.double_click.r3", "renderer", "cyrene_capability", "cyrene.ui.double_click", "R3"),
    _op("cyrene.ui.type", "renderer", "cyrene_capability", "cyrene.ui.type", "R1"),
    _op("cyrene.ui.type.r2", "renderer", "cyrene_capability", "cyrene.ui.type", "R2"),
    _op("cyrene.ui.type.r3", "renderer", "cyrene_capability", "cyrene.ui.type", "R3"),
    _op("cyrene.ui.scroll", "renderer", "cyrene_capability", "cyrene.ui.scroll", "R1"),
    _op("cyrene.ui.scroll.r2", "renderer", "cyrene_capability", "cyrene.ui.scroll", "R2"),
    _op("cyrene.ui.scroll.r3", "renderer", "cyrene_capability", "cyrene.ui.scroll", "R3"),
    _op("cyrene.ui.drag", "renderer", "cyrene_capability", "cyrene.ui.drag", "R1"),
    _op("cyrene.ui.drag.r2", "renderer", "cyrene_capability", "cyrene.ui.drag", "R2"),
    _op("cyrene.ui.drag.r3", "renderer", "cyrene_capability", "cyrene.ui.drag", "R3"),
    _op("cyrene.question.answer", "renderer", "ui_surface", "cyrene.ui.click", "R2"),
    _op("cyrene.approval.answer", "renderer", "ui_surface", "cyrene.ui.click", "R3"),
    _op("cyrene.session.message", "workbench", "internal_service", "cyrene.session.message", "R2"),
    _op("cyrene.ui.navigation", "renderer", "ui_surface", "cyrene.ui.click", "R1"),
    _op("cyrene.ui.layout", "renderer", "ui_surface", "cyrene.ui.drag", "R1"),
    _op("cyrene.quick_chat", "host", "cyrene_capability", "cyrene.app.window", "R1"),
    _op("cyrene.settings.read", "settings", "cyrene_capability", "cyrene.settings.read", "R0"),
    _op("cyrene.settings.update", "settings", "cyrene_capability", "cyrene.settings.update", "R1"),
    _op("cyrene.settings.global", "settings", "cyrene_capability", "cyrene.settings.update", "R2", apply_mode="next_run"),
    _op("cyrene.settings.agent", "settings", "cyrene_capability", "cyrene.settings.update", "R2", apply_mode="next_run"),
    _op("cyrene.settings.capabilities", "settings", "cyrene_capability", "cyrene.settings.update", "R2", apply_mode="next_run"),
    _op("cyrene.settings.shortcuts", "settings", "cyrene_capability", "cyrene.settings.update", "R2"),
    _op("cyrene.profile.manage", "settings", "cyrene_capability", "cyrene.settings.update", "R1"),
    _op("cyrene.project.manage", "workbench", "internal_service", "cyrene.project.manage", "R2"),
    _op("cyrene.project.delete", "workbench", "internal_service", "cyrene.project.manage", "R3"),
    _op("cyrene.chat.manage", "workbench", "internal_service", "cyrene.chat.manage", "R2"),
    _op("cyrene.chat.delete", "workbench", "internal_service", "cyrene.chat.manage", "R3"),
    _op("cyrene.data.backup", "backup", "internal_service", "cyrene.data.manage", "R2"),
    _op("cyrene.data.restore", "backup", "internal_service", "cyrene.data.manage", "R3"),
    _op("cyrene.data.delete", "backup", "internal_service", "cyrene.data.manage", "R3"),
    _op("cyrene.update.check", "updater", "internal_service", "cyrene.update.manage", "R0"),
    _op("cyrene.update.download", "updater", "internal_service", "cyrene.update.manage", "R2"),
    _op("cyrene.update.install", "updater", "internal_service", "cyrene.update.manage", "R3", apply_mode="deferred"),
    _op("cyrene.secret.input", "renderer", "user_ceremony", "", "R3"),
    _op("cyrene.oauth", "identity", "user_ceremony", "", "R3"),
    _op("cyrene.os.permission", "host", "user_ceremony", "", "R3"),
    _op("cyrene.file_picker", "host", "user_ceremony", "", "R2"),
    _op("cyrene.browser", "browser", "existing_capability", "cyrene_browser", "R1", actors=("main",)),
    _op("cyrene.tasks", "task", "existing_capability", "cyrene_task", "R1"),
    _op("plugin.cyrene_memory", "memory", "existing_capability", "cyrene_memory", "R1"),
    _op("cyrene.knowledge", "knowledge", "existing_capability", "cyrene_knowledge", "R1"),
    _op("cyrene.skills", "skills", "existing_capability", "cyrene_skills", "R2"),
    _op("cyrene.remote", "remote", "existing_capability", "cyrene_remote", "R2"),
    _op("cyrene.delivery", "delivery", "existing_capability", "cyrene_delivery", "R2"),
    _op("cyrene.code", "code", "existing_capability", "cyrene_code", "R1"),
    _op("cyrene.native_edit_menu", "host", "presentation_only", "", "R0", actors=()),
    _op("cyrene.raw.internal", "security", "forbidden", "", "R4", actors=()),
    _op("cyrene.secret.read", "security", "forbidden", "", "R4", actors=()),
    _op("cyrene.permission.elevate", "security", "forbidden", "", "R4", actors=()),
    _op("cyrene.self.disable", "security", "forbidden", "", "R4", actors=()),
    _op("cyrene.approval.unprompted_self_answer", "security", "forbidden", "", "R4", actors=()),
)

OPERATION_BY_ID = {item.operation_id: item for item in APP_OPERATIONS}


def public_manifest() -> list[dict[str, object]]:
    rows = []
    for item in APP_OPERATIONS:
        row = asdict(item)
        row["actors"] = sorted(item.actors)
        rows.append(row)
    return rows


def validate_manifest() -> tuple[str, ...]:
    errors: list[str] = []
    ids = [item.operation_id for item in APP_OPERATIONS]
    if len(ids) != len(set(ids)):
        errors.append("operation ids must be unique")
    for item in APP_OPERATIONS:
        if item.exposure in {"cyrene_capability", "ui_surface"} and not item.capability_id:
            errors.append(f"{item.operation_id} has no capability owner")
        if item.risk == "R4" and item.exposure != "forbidden":
            errors.append(f"{item.operation_id} exposes an R4 operation")
        if item.exposure == "cyrene_capability" and item.actors != frozenset({"main"}):
            errors.append(f"{item.operation_id} must be main-only")
    return tuple(errors)


__all__ = [
    "APP_OPERATIONS",
    "OPERATION_BY_ID",
    "AppOperationSpec",
    "public_manifest",
    "validate_manifest",
]
