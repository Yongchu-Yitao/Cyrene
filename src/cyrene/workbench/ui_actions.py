"""Semantic UI action and user-gesture coverage ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ActionKind = Literal[
    "invoke", "set_value", "select", "toggle", "adjust", "scroll",
    "move", "set_frame", "open_menu", "dismiss",
]


@dataclass(frozen=True, slots=True)
class UIActionSpec:
    action_id: str
    owner: str
    kind: ActionKind
    gesture_family: str
    exposure: Literal["ui_surface", "existing_capability", "presentation_only", "forbidden"]
    risk: Literal["R0", "R1", "R2", "R3", "R4"]
    requires_capability: str = ""


UI_ACTIONS: tuple[UIActionSpec, ...] = (
    UIActionSpec("invoke", "renderer.controls", "invoke", "press", "ui_surface", "R1"),
    UIActionSpec("maximize", "browser.titlebar", "invoke", "double_press", "ui_surface", "R1"),
    UIActionSpec("restore", "browser.titlebar", "invoke", "double_press", "ui_surface", "R1"),
    UIActionSpec("open_menu", "renderer.menu", "open_menu", "context_menu", "ui_surface", "R1"),
    UIActionSpec("move_before", "renderer.reorder", "move", "drag_drop", "ui_surface", "R1"),
    UIActionSpec("move_after", "renderer.reorder", "move", "drag_drop", "ui_surface", "R1"),
    UIActionSpec("set_frame", "browser.titlebar", "set_frame", "pointer_move_resize", "ui_surface", "R1"),
    UIActionSpec("adjust", "renderer.splitter", "adjust", "pointer_resize", "ui_surface", "R1"),
    UIActionSpec("scroll_page", "renderer.viewport", "scroll", "wheel", "ui_surface", "R1"),
    UIActionSpec("select_next", "renderer.selection", "select", "keyboard", "ui_surface", "R1"),
    UIActionSpec("select_previous", "renderer.selection", "select", "keyboard", "ui_surface", "R1"),
    UIActionSpec("submit", "renderer.form", "invoke", "keyboard", "ui_surface", "R2"),
    UIActionSpec("dismiss", "renderer.overlay", "dismiss", "keyboard", "ui_surface", "R1"),
    UIActionSpec("set_value", "renderer.form", "set_value", "text_input", "ui_surface", "R1"),
    UIActionSpec("composer_set_draft", "session.composer", "set_value", "text_input", "ui_surface", "R1"),
    UIActionSpec("composer_clear_exact_draft", "session.composer", "set_value", "semantic_clear", "ui_surface", "R1"),
    UIActionSpec("question_answer_option", "session.pending_question", "invoke", "press", "ui_surface", "R2"),
    UIActionSpec("approval_answer_option", "session.pending_approval", "invoke", "press", "ui_surface", "R3"),
    UIActionSpec("question_answer_custom", "session.pending_question", "set_value", "text_input", "ui_surface", "R2"),
    UIActionSpec("project_switch", "workbench.project_menu", "select", "press", "ui_surface", "R1"),
    UIActionSpec("chat_search", "workbench.chat_rail", "set_value", "text_input", "ui_surface", "R1"),
    UIActionSpec("select", "renderer.form", "select", "selection", "ui_surface", "R1"),
    UIActionSpec("toggle", "renderer.form", "toggle", "press", "ui_surface", "R1"),
    UIActionSpec("accessible_invoke", "renderer.accessibility", "invoke", "bounded_semantic_event", "ui_surface", "R1"),
    UIActionSpec("accessible_context_menu", "renderer.accessibility", "open_menu", "bounded_semantic_event", "ui_surface", "R1"),
    UIActionSpec("browser_page_input", "browser", "invoke", "pointer_keyboard", "existing_capability", "R1", "cyrene_browser"),
    UIActionSpec("hover_style", "renderer", "invoke", "hover", "presentation_only", "R0"),
    UIActionSpec("raw_pointer", "security", "invoke", "raw_coordinate", "forbidden", "R4"),
    UIActionSpec("arbitrary_synthetic_event", "security", "invoke", "synthetic_event", "forbidden", "R4"),
)

GESTURE_FAMILIES = frozenset(
    {"press", "double_press", "context_menu", "drag_drop", "pointer_move_resize",
     "pointer_resize", "wheel", "keyboard", "text_input", "selection", "hover",
     "pointer_keyboard", "bounded_semantic_event", "raw_coordinate", "synthetic_event"}
)


def validate_ui_action_ledger() -> tuple[str, ...]:
    errors: list[str] = []
    covered = {item.gesture_family for item in UI_ACTIONS}
    missing = sorted(GESTURE_FAMILIES - covered)
    if missing:
        errors.append("unclassified gestures: " + ", ".join(missing))
    for item in UI_ACTIONS:
        if item.risk == "R4" and item.exposure != "forbidden":
            errors.append(f"{item.action_id} exposes an R4 gesture")
    return tuple(errors)


__all__ = ["GESTURE_FAMILIES", "UI_ACTIONS", "UIActionSpec", "validate_ui_action_ledger"]
