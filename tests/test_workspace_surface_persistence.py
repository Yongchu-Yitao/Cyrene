from __future__ import annotations

from pathlib import Path

import pytest

from cyrene.workbench.chat import chat_application
from cyrene.workbench.chat.chat_application import (
    normalize_workspace_surface,
    public_chat_full,
    public_chat_light,
)
from cyrene.workbench.http.schemas import ChatUpdateBody, body_dict


def _surface(**resource_overrides):
    resource = {
        "kind": "file",
        "projectId": "project-1",
        "path": "src/app.js",
        **resource_overrides,
    }
    return {
        "schemaVersion": 1,
        "surfaceId": "file-editor",
        "packId": "cyrene_code",
        "resource": resource,
        "resourceKey": "project-1:file:src/app.js",
        "activity": "write",
        "attention": "update",
        "priority": "normal",
        "lifetime": "sticky",
        "preferredSide": "right",
        "chatId": "untrusted-chat",
        "runId": "ephemeral-run",
        "state": {"phase": "completed"},
    }


def test_workspace_surface_is_normalized_for_durable_chat_state() -> None:
    normalized = normalize_workspace_surface(
        _surface(),
        chat_id="chat-1",
        project_id="project-1",
    )

    assert normalized == {
        "schemaVersion": 1,
        "surfaceId": "file-editor",
        "packId": "cyrene_code",
        "resource": {
            "kind": "file",
            "projectId": "project-1",
            "path": "src/app.js",
        },
        "resourceKey": "project-1:file:src/app.js",
        "activity": "write",
        "attention": "reveal",
        "chatId": "chat-1",
        "priority": "normal",
        "lifetime": "sticky",
        "preferredSide": "right",
    }


@pytest.mark.parametrize(
    "surface",
    [
        _surface(projectId="another-project"),
        _surface(path="../outside.js"),
        _surface(path="/tmp/outside.js"),
        _surface() | {"resourceKey": "project-1:file:another.js"},
    ],
)
def test_workspace_surface_rejects_cross_workspace_resources(surface) -> None:
    with pytest.raises(ValueError, match="invalid_workspace_surface"):
        normalize_workspace_surface(
            surface,
            chat_id="chat-1",
            project_id="project-1",
        )


def test_workspace_surface_round_trips_through_public_chat_projections(
    monkeypatch,
) -> None:
    class ComposerContext:
        @staticmethod
        def normalize(_value):
            return {}

    monkeypatch.setattr(
        chat_application,
        "_composer_context_service",
        lambda: ComposerContext(),
    )
    surface = normalize_workspace_surface(
        _surface(),
        chat_id="chat-1",
        project_id="project-1",
    )
    chat = {
        "id": "chat-1",
        "projectId": "project-1",
        "kind": "chat",
        "messages": [],
        "soulActive": False,
        "workspaceActive": False,
        "workspaceSurface": surface,
    }

    assert public_chat_light(chat)["workspaceSurface"] == surface
    assert public_chat_full(chat)["workspaceSurface"] == surface


def test_chat_update_schema_preserves_workspace_surface_field() -> None:
    body = body_dict(ChatUpdateBody.model_validate({"workspaceSurface": _surface()}))

    assert body["workspaceSurface"]["surfaceId"] == "file-editor"


def test_chat_update_route_persists_the_validated_workspace_surface() -> None:
    root = Path(__file__).resolve().parents[1]
    route = (
        root
        / "src/cyrene/workbench/http/workbench/chat_routes/detail_routes.py"
    ).read_text(encoding="utf-8")

    assert 'if "workspaceSurface" not in body:' in route
    assert "service.normalize_workspace_surface(" in route
    assert 'chat["workspaceSurface"] = workspace_surface' in route
