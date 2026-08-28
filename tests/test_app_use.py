from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
from unittest.mock import AsyncMock

import pytest
import PIL as _REAL_PIL
from PIL import Image, ImageDraw as _REAL_IMAGE_DRAW

from agent.plugin import PluginContext

_REAL_PIL_IMAGE = sys.modules["PIL.Image"]
_REAL_PIL_IMAGE_DRAW = _REAL_IMAGE_DRAW


def _png_base64(width: int, height: int, color: tuple[int, int, int] = (240, 240, 240)) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.fixture(autouse=True)
def clear_app_use_runtime_session_state(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    monkeypatch.setitem(sys.modules, "PIL", _REAL_PIL)
    monkeypatch.setitem(sys.modules, "PIL.Image", _REAL_PIL_IMAGE)
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", _REAL_PIL_IMAGE_DRAW)
    monkeypatch.setattr(_REAL_PIL, "Image", _REAL_PIL_IMAGE)
    monkeypatch.setattr(_REAL_PIL, "ImageDraw", _REAL_PIL_IMAGE_DRAW)
    app_use._SESSION_MEASUREMENTS.clear()
    app_use._SESSION_FOCUS_READY.clear()
    app_use._SESSION_VISUAL_READY.clear()
    app_use._SESSION_PRIMARY_CLICK_RESULTS.clear()
    app_use._SESSION_CAPABILITIES.clear()
    yield
    app_use._SESSION_MEASUREMENTS.clear()
    app_use._SESSION_FOCUS_READY.clear()
    app_use._SESSION_VISUAL_READY.clear()
    app_use._SESSION_PRIMARY_CLICK_RESULTS.clear()
    app_use._SESSION_CAPABILITIES.clear()


def test_app_use_is_one_stable_main_only_tool():
    from agent.plugin.plugin_impl.cyrene_desktop import plugin_pack

    plugins = [plugin for plugin in plugin_pack.plugins if plugin.name == "app_use"]
    assert len(plugins) == 1
    assert plugins[0].metadata["main_only"] is True


def test_app_use_schema_keeps_runtime_capabilities_out_of_function_enum():
    from agent.plugin.plugin_impl.cyrene_desktop.app_use import TOOL_DEF

    function = TOOL_DEF["function"]
    description = function["description"]
    properties = function["parameters"]["properties"]
    assert function["name"] == "app_use"
    assert "visual_click" in description
    assert "Purely visual desktop control" in description
    assert "never invokes semantic refs" in description
    assert "AppUISnapshot" in description
    assert "It is not an OS mouse event" not in description
    assert properties["operation"]["enum"] == [
        "list_targets", "connect", "call", "status", "disconnect"
    ]
    assert "enum" not in properties["capability"]
    assert function["parameters"]["required"] == ["operation"]


@pytest.mark.asyncio
async def test_execute_app_use_validates_gateway_arguments(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        return {"status": "success"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)

    invalid = await app_use.execute_app_use({"operation": "call", "capability": "snapshot"})
    assert invalid["status"] == "error"
    assert invalid["type"] == "invalid_arguments"

    result = await app_use.execute_app_use({
        "operation": "call",
        "session_id": "session-1",
        "capability": "snapshot",
        "parameters": {"max_nodes": 40},
    })
    assert result["status"] == "error"
    assert result["type"] == "unsupported_visual_capability"
    assert result["alternate_scheme"]["tool"] == "AppUISnapshot"
    assert calls == []


@pytest.mark.asyncio
async def test_app_use_forces_visual_mode_and_rejects_cross_scheme_connect(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        return {"status": "success", "session_id": "visual-1", "capabilities": [{"name": "visual_describe"}]}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    connected = await app_use.execute_app_use({"operation": "connect", "target_id": "target-1"})
    assert connected["mode"] == "visual"
    assert calls[0][1]["parameters"]["mode"] == "visual"

    rejected = await app_use.execute_app_use({
        "operation": "connect", "target_id": "target-1", "parameters": {"mode": "semantic"},
    })
    assert rejected["type"] == "wrong_scheme"
    assert rejected["alternate_scheme"]["tool"] == "AppUISnapshot"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_visual_describe_converts_window_capture_to_text(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use
    from cyrene.runtime import attachments

    async def fake_rpc(_operation, _arguments, **_kwargs):
        return {
            "status": "success",
            "session_id": "visual-description-session",
            "image_base64": "aW1hZ2U=",
            "mime_type": "image/png",
            "width": 800,
            "height": 600,
        }

    async def fake_vision(content, content_prompt="", **kwargs):
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert content_prompt == "Explain the chart"
        assert kwargs["timeout"] == 60.0
        assert kwargs["record_latency"] is True
        return {"vision_text": "A rising line chart.", "vision_model": "vision-test"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    monkeypatch.setattr(attachments, "run_vision_chat", fake_vision)
    result = await app_use.execute_app_use({
        "operation": "call",
        "session_id": "visual-description-session",
        "capability": "visual_describe",
        "parameters": {"prompt": "Explain the chart"},
    })
    assert result["status"] == "success"
    assert result["visual_observation"] == "A rising line chart."
    assert result["vision_model"] == "vision-test"
    assert "image_base64" not in result


@pytest.mark.asyncio
async def test_visual_describe_default_prompt_requires_a_concise_coordinate_summary(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    async def fake_rpc(_operation, _arguments, **_kwargs):
        return {
            "status": "success",
            "session_id": "session-brief",
            "image_base64": "aW1hZ2U=",
            "mime_type": "image/png",
            "width": 800,
            "height": 600,
        }

    seen_prompt = ""

    async def fake_analysis(_image_base64, _mime_type, prompt, **_kwargs):
        nonlocal seen_prompt
        seen_prompt = prompt
        return "Current screen; Maps center (400,300).", "vision-test"

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    monkeypatch.setattr(app_use, "_analyze_capture", fake_analysis)
    result = await app_use.execute_app_use({
        "operation": "call",
        "session_id": "session-brief",
        "capability": "visual_describe",
        "parameters": {},
    })
    assert result["status"] == "success"
    assert "at most 8 short bullets and 600 characters" in seen_prompt
    assert "Omit exhaustive OCR and decorative details" in seen_prompt
    assert "captured-image pixels" in seen_prompt


@pytest.mark.asyncio
async def test_visual_describe_reports_capture_success_separately_from_vision_timeout(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    async def fake_rpc(_operation, _arguments, **_kwargs):
        return {
            "status": "success",
            "session_id": "session-1",
            "image_base64": "aW1hZ2U=",
            "mime_type": "image/png",
            "width": 800,
            "height": 600,
        }

    async def fake_analysis(*_args, **_kwargs):
        raise asyncio.TimeoutError

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    monkeypatch.setattr(app_use, "_analyze_capture", fake_analysis)
    result = await app_use.execute_app_use({
        "operation": "call",
        "session_id": "session-1",
        "capability": "visual_describe",
        "parameters": {},
    })
    assert result["status"] == "error"
    assert result["type"] == "vision_timeout"
    assert result["capture_succeeded"] is True
    assert result["retryable"] is True


@pytest.mark.asyncio
async def test_connect_discloses_python_visual_click_workflow(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    async def fake_rpc(_operation, _arguments, **_kwargs):
        return {
            "status": "success",
            "session_id": "session-1",
            "capabilities": [
                {"name": "focus_window"}, {"name": "click_at"},
                {"name": "visual_describe"}, {"name": "snapshot"},
                {"name": "virtual_click_at"}, {"name": "virtual_type_at"},
            ],
        }

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    result = await app_use.execute_app_use({"operation": "connect", "target_id": "target-1"})
    names = [item["name"] for item in result["capabilities"]]
    assert names == [
        "visual_describe", "measure_coordinates", "focus_window", "click_at",
        "visual_click", "visual_type", "virtual_type_at",
    ]
    assert "target" in result["capabilities"][1]["arguments"]
    visual_click = result["capabilities"][4]
    assert visual_click["background"] == "requires_focus"
    assert "fallback" not in visual_click["arguments"]
    visual_type = next(item for item in result["capabilities"] if item["name"] == "visual_type")
    assert visual_type["background"] == "safe_when_supported"
    assert result["interaction_priority"][:2] == [
        "inspect_fresh_window_capture", "measure_agent_selected_coordinates",
    ]
    assert result["interaction_priority"][3] == "primary_foreground_click_at"
    assert result["required_first_activation_action"] == "call:visual_describe"
    assert result["primary_click"] == {
        "capability": "click_at",
        "coordinate_space": "window",
        "required_parameters": {"allow_foreground_input": True},
        "point_source": "latest measure_coordinates.window_point",
    }
    assert result["fallback_click_capabilities"] == ["visual_click"]
    assert result["mode"] == "visual"
    assert result["next_valid_actions"][:2] == ["call:visual_describe", "call:measure_coordinates"]


@pytest.mark.asyncio
async def test_connect_does_not_disclose_mac_only_or_focus_dependent_python_capabilities(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    responses = iter([
        {
            "status": "success", "session_id": "session-windows",
            "target": {"platform": "win32"},
            "focus_policy": "when_required",
            "capabilities": [
                {"name": "visual_describe"}, {"name": "focus_window"},
                {"name": "click_at"}, {"name": "virtual_click_at"},
            ],
        },
        {
            "status": "success", "session_id": "session-no-focus",
            "target": {"platform": "win32"},
            "focus_policy": "never",
            "capabilities": [
                {"name": "visual_describe"}, {"name": "virtual_click_at"},
            ],
        },
        {"status": "uncertain", "session_id": "session-no-focus"},
    ])

    async def fake_rpc(_operation, _arguments, **_kwargs):
        return next(responses)

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    windows = await app_use.execute_app_use({"operation": "connect", "target_id": "target-win"})
    windows_names = [item["name"] for item in windows["capabilities"]]
    assert "visual_click" in windows_names
    assert "visual_type" not in windows_names

    no_focus = await app_use.execute_app_use({
        "operation": "connect", "target_id": "target-win", "parameters": {"focus_policy": "never"},
    })
    no_focus_names = [item["name"] for item in no_focus["capabilities"]]
    assert "visual_click" not in no_focus_names
    assert "visual_type" not in no_focus_names
    assert no_focus["fallback_click_capabilities"] == []
    app_use._SESSION_MEASUREMENTS["session-no-focus"] = {
        "target": "Save button",
        "window_point": {"x": 120, "y": 88},
        "screen_point": {"x": 220, "y": 188},
    }
    background_click = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-no-focus", "capability": "virtual_click_at",
        "parameters": {"x": 120, "y": 88, "coordinate_space": "window"},
    })
    assert background_click["status"] == "error"
    assert background_click["type"] == "unsupported_visual_capability"
    assert background_click["alternate_scheme"]["tool"] == "AppUISnapshot"


def test_app_use_timeout_covers_two_vision_passes():
    from agent.plugin.plugin_impl.cyrene_desktop._app_use_backend import VISION_ANALYSIS_TIMEOUT_SECONDS
    from agent.plugin.plugin_impl.cyrene_desktop import plugin_pack

    plugin = next(plugin for plugin in plugin_pack.plugins if plugin.name == "app_use")
    assert plugin.timeout_seconds >= (2 * VISION_ANALYSIS_TIMEOUT_SECONDS) + 30


@pytest.mark.asyncio
async def test_connect_filters_every_semantic_or_ax_fallback_from_visual_scheme(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    async def fake_rpc(_operation, _arguments, **_kwargs):
        return {
            "status": "success",
            "session_id": "session-container-only",
            "capabilities": [
                {"name": "focus_window"},
                {"name": "click_at"},
                {"name": "virtual_click_at"},
                {"name": "visual_describe"},
            ],
            "semantic_profile": {
                "status": "unavailable",
                "reason": "container_only_tree",
            },
        }

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    result = await app_use.execute_app_use({"operation": "connect", "target_id": "target-1"})
    assert "semantic_profile" not in result
    assert "virtual_click_at" not in {item["name"] for item in result["capabilities"]}
    assert result["next_valid_actions"][:2] == ["call:visual_describe", "call:measure_coordinates"]


@pytest.mark.asyncio
async def test_only_coordinate_actions_require_visual_inspection_then_measurement(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        if operation == "call" and arguments.get("capability") == "visual_describe":
            return {
                "status": "success", "session_id": "session-gated",
                "image_base64": _png_base64(100, 80), "mime_type": "image/png",
                "width": 100, "height": 80,
            }
        if operation == "connect":
            return {
                "status": "success", "session_id": "session-gated",
                "capabilities": [
                    {"name": "focus_window"}, {"name": "click_at"},
                    {"name": "visual_describe"}, {"name": "virtual_click_at"},
                    {"name": "menu_command"}, {"name": "key_chord"}, {"name": "swipe"},
                ],
            }
        return {"status": "success", "session_id": "session-gated"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    app_use._SESSION_MEASUREMENTS.pop("session-gated", None)
    connected = await app_use.execute_app_use({"operation": "connect", "target_id": "target-1"})
    assert connected["status"] == "success"
    focused = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-gated", "capability": "focus_window",
        "parameters": {},
    })
    assert focused["status"] == "success"
    menu = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-gated", "capability": "menu_command",
        "parameters": {"name": "Home"},
    })
    assert menu["status"] == "error"
    assert menu["type"] == "unsupported_visual_capability"
    assert menu["alternate_scheme"]["tool"] == "AppUISnapshot"
    shortcut = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-gated", "capability": "key_chord",
        "parameters": {"keys": ["command", "h"], "allow_foreground_input": True},
    })
    assert shortcut["status"] == "success"
    blocked = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-gated", "capability": "swipe",
        "parameters": {"x": 50, "y": 75, "direction": "up", "allow_foreground_input": True},
    })
    assert blocked["status"] == "error"
    assert blocked["type"] == "coordinate_measurement_required"
    assert blocked["required_action"] == "call:visual_describe"
    assert blocked["next_valid_actions"] == ["call:visual_describe", "call:measure_coordinates", "disconnect"]
    assert [arguments.get("capability") for operation, arguments in calls if operation == "call"] == [
        "focus_window", "key_chord",
    ]
    monkeypatch.setattr(app_use, "_analyze_capture", AsyncMock(return_value=("A window.", "vision-test")))
    inspected = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-gated", "capability": "visual_describe",
        "parameters": {},
    })
    assert inspected["status"] == "success"
    assert os.path.isfile(inspected["capture_image"]["path"])
    os.unlink(inspected["capture_image"]["path"])
    still_blocked = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-gated", "capability": "swipe",
        "parameters": {"x": 50, "y": 75, "direction": "up", "allow_foreground_input": True},
    })
    assert still_blocked["type"] == "coordinate_measurement_required"
    assert still_blocked["required_action"] == "call:measure_coordinates"
    assert still_blocked["next_valid_actions"] == ["call:measure_coordinates", "call:visual_describe", "disconnect"]


@pytest.mark.asyncio
async def test_measure_coordinates_requires_prior_visual_inspection_for_connected_session(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    app_use._SESSION_MEASUREMENTS["session-needs-visual"] = None
    blocked = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-needs-visual", "capability": "measure_coordinates",
        "parameters": {"x": 50, "y": 40, "width": 100, "height": 80},
    })
    assert blocked["status"] == "error"
    assert blocked["type"] == "visual_capture_required"
    assert blocked["next_valid_actions"] == ["call:visual_describe", "disconnect"]


@pytest.mark.asyncio
async def test_measure_coordinates_rejects_empty_target_binding():
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    result = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-legacy", "capability": "measure_coordinates",
        "parameters": {"target": "  ", "x": 10, "y": 10},
    })
    assert result["status"] == "error"
    assert result["type"] == "invalid_arguments"
    assert "target" in result["message"]


@pytest.mark.asyncio
async def test_visual_session_rejects_accessibility_backed_virtual_click(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        return {"status": "uncertain", "session_id": "session-measured"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    app_use._SESSION_CAPABILITIES["session-measured"] = {"click_at", "visual_describe"}
    result = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-measured", "capability": "virtual_click_at",
        "parameters": {"x": 120.25, "y": 88.5},
    })
    assert result["status"] == "error"
    assert result["type"] == "unsupported_visual_capability"
    assert result["alternate_scheme"]["tool"] == "AppUISnapshot"
    assert calls == []


@pytest.mark.asyncio
async def test_swipe_must_reuse_latest_measured_start_point(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        return {"status": "success", "session_id": "session-swipe"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    app_use._SESSION_MEASUREMENTS["session-swipe"] = {
        "window_point": {"x": 162.7, "y": 708.0},
        "screen_point": {"x": 1603.7, "y": -189.0},
    }
    blocked = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-swipe", "capability": "swipe",
        "parameters": {
            "x": 150, "y": 690, "direction": "up", "distance": 100,
            "coordinate_space": "window", "allow_foreground_input": True,
        },
    })
    assert blocked["status"] == "error"
    assert blocked["type"] == "measured_coordinate_mismatch"
    assert blocked["expected_point"] == {"x": 162.7, "y": 708.0}
    assert calls == []

    allowed = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-swipe", "capability": "swipe",
        "parameters": {
            "x": 162.7, "y": 708.0, "direction": "up", "distance": 100,
            "coordinate_space": "window", "allow_foreground_input": True,
        },
    })
    assert allowed["status"] == "success"
    assert calls[-1][1]["capability"] == "swipe"


@pytest.mark.asyncio
async def test_real_coordinate_click_requires_focus_and_reuses_measured_point(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        capability = arguments.get("capability")
        if capability == "focus_window":
            return {"status": "success", "summary": "focused"}
        if capability == "click_at":
            return {"status": "success", "focused_temporarily": True, "focus_restore": {"status": "success"}}
        raise AssertionError(capability)

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    app_use._SESSION_MEASUREMENTS["session-real"] = {
        "window_point": {"x": 204.5, "y": 295.25},
        "screen_point": {"x": 888.5, "y": -670.75},
    }
    blocked = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-real", "capability": "click_at",
        "parameters": {"x": 204.5, "y": 295.25, "allow_foreground_input": True},
    })
    assert blocked["type"] == "focus_window_required"
    assert calls == []
    focused = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-real", "capability": "focus_window", "parameters": {},
    })
    assert focused["status"] == "success"
    clicked = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-real", "capability": "click_at",
        "parameters": {"x": 204.5, "y": 295.25, "allow_foreground_input": True},
    })
    assert clicked["status"] == "success"
    assert clicked["focus_restore"]["status"] == "success"
    assert [arguments["capability"] for _, arguments in calls] == ["focus_window", "click_at"]
    duplicate = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-real", "capability": "virtual_click_at",
        "parameters": {"x": 204.5, "y": 295.25},
    })
    assert duplicate["type"] == "unsupported_visual_capability"
    assert duplicate["alternate_scheme"]["tool"] == "AppUISnapshot"
    assert [arguments["capability"] for _, arguments in calls] == ["focus_window", "click_at"]


@pytest.mark.asyncio
async def test_visual_activation_requires_measurement_for_the_same_target(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        return {"status": "success"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    app_use._SESSION_MEASUREMENTS["session-target"] = {
        "target": "Messages app icon",
        "window_point": {"x": 120, "y": 88},
    }
    blocked = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-target", "capability": "visual_click",
        "parameters": {"target": "Maps app icon"},
    })
    assert blocked["status"] == "error"
    assert blocked["type"] == "measured_target_mismatch"
    assert blocked["next_valid_actions"] == ["call:measure_coordinates", "disconnect"]
    assert calls == []


@pytest.mark.asyncio
async def test_visual_activation_requires_measurement_with_a_bound_target(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        return {"status": "success"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    app_use._SESSION_MEASUREMENTS["session-unbound"] = {
        "window_point": {"x": 120, "y": 88},
    }
    blocked = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-unbound", "capability": "visual_click",
        "parameters": {"target": "Messages app icon"},
    })
    assert blocked["status"] == "error"
    assert blocked["type"] == "measured_target_required"
    assert blocked["next_valid_actions"] == ["call:measure_coordinates", "disconnect"]
    assert calls == []


@pytest.mark.asyncio
async def test_measure_coordinates_crops_marks_and_returns_all_coordinate_spaces(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use
    from cyrene.runtime import attachments

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        assert arguments["capability"] == "visual_describe"
        return {
            "status": "success", "session_id": "session-1", "image_base64": _png_base64(543, 1200),
            "mime_type": "image/png", "width": 543, "height": 1200,
            "target": {"bounds": {"x": 664, "y": -823, "width": 326, "height": 720}},
            "coordinate_mapping": {"logical_width": 326, "logical_height": 720},
        }

    async def fake_analysis(path, prompt):
        assert os.path.isfile(path)
        assert "crosshair" in prompt
        return {"vision_text": "The crosshair is centered on Messages.", "vision_model": "vision-test"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    monkeypatch.setattr(attachments, "primary_model_supports_vision", lambda: True)
    monkeypatch.setattr(attachments, "analyze_image_with_primary_model", fake_analysis)
    result = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-1", "capability": "measure_coordinates",
        "parameters": {
            "target": "Messages app icon", "x": 364, "y": 230,
            "width": 68, "height": 68, "coordinate_space": "captured",
        },
    })
    assert result["status"] == "success"
    assert result["target"] == "Messages app icon"
    assert result["captured_point"] == {"x": 364.0, "y": 230.0}
    assert result["window_point"]["x"] == pytest.approx(218.5267, rel=1e-4)
    assert result["window_point"]["y"] == 138.0
    assert result["screen_point"]["x"] == pytest.approx(882.5267, rel=1e-4)
    assert result["screen_point"]["y"] == -685.0
    assert result["captured_bbox"] == {"x": 330.0, "y": 196.0, "width": 68.0, "height": 68.0}
    assert result["window_bbox"]["width"] == pytest.approx(40.8287, rel=1e-4)
    assert result["visual_observation"] == "The crosshair is centered on Messages."
    image_path = result["calibration_image"]["path"]
    assert os.path.isfile(image_path)
    with Image.open(image_path) as marked:
        assert marked.size == (68, 68)
        marker = result["calibration_image"]["marker_point"]
        red, green, blue = marked.getpixel((round(marker["x"]), round(marker["y"])))
        assert red > 200 and green < 100 and blue < 100
    os.unlink(image_path)
    assert result["input_sent"] is False
    assert result["executed_action"] is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_visual_click_failure_hands_off_without_calling_semantic_actions(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        assert arguments["capability"] == "visual_describe"
        return {
            "status": "success", "session_id": "session-no-tree", "image_base64": "aW1hZ2U=",
            "mime_type": "image/png", "width": 100, "height": 100,
            "coordinate_mapping": {"logical_width": 100, "logical_height": 100},
        }

    async def fake_analysis(*_args, **_kwargs):
        return ('{"found":false,"confidence":0}', "vision-test")

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    monkeypatch.setattr(app_use, "_analyze_capture", fake_analysis)
    result = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-no-tree", "capability": "visual_click",
        "parameters": {"target": "Messages", "max_attempts": 1},
    })
    assert result["status"] == "uncertain"
    assert result["requested_action"]["scheme"] == "visual"
    assert result["alternate_scheme"]["tool"] == "AppUISnapshot"
    assert result["next_valid_actions"] == ["call:visual_describe", "disconnect", "switch:semantic"]
    assert [arguments["capability"] for _, arguments in calls] == ["visual_describe"]


@pytest.mark.asyncio
async def test_visual_type_owns_coordinate_mapping_and_requires_exact_text_verification(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    calls = []
    analyses = iter([
        ('{"found":true,"confidence":0.94,"x":600,"y":900,"label":"composer"}', "vision-locate"),
        ('{"exact_text_present":true,"observed_text":"hello Claude"}', "vision-verify"),
    ])

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        capability = arguments.get("capability")
        if capability == "visual_describe":
            return {
                "status": "success", "session_id": "session-1", "image_base64": "aW1hZ2U=",
                "mime_type": "image/png", "width": 1800, "height": 1200,
                "coordinate_mapping": {"logical_width": 1200, "logical_height": 800},
            }
        if capability == "virtual_type_at":
            assert arguments["parameters"]["x"] == 400
            assert arguments["parameters"]["y"] == 600
            assert arguments["parameters"]["text"] == "hello Claude"
            assert arguments["parameters"]["verify_effect"] is False
            return {
                "status": "uncertain", "session_id": "session-1",
                "executed_action": {"capability": "virtual_type_at", "input_mode": "background_pid_event"},
                "verification": {"event_delivered": True, "effect_verified": None},
            }
        raise AssertionError(f"unexpected RPC call: {operation} {arguments}")

    async def fake_analysis(*_args, **_kwargs):
        return next(analyses)

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    monkeypatch.setattr(app_use, "_analyze_capture", fake_analysis)
    result = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-1", "capability": "visual_type",
        "parameters": {"target": "bottom composer", "text": "hello Claude"},
    })
    assert result["status"] == "success"
    assert result["captured_point"] == {"x": 600.0, "y": 900.0}
    assert result["window_point"] == {"x": 400.0, "y": 600.0}
    assert result["verification"]["exact_text_present"] is True
    assert result["verification"]["event_delivered"] is True
    assert [item[1]["capability"] for item in calls] == ["visual_describe", "virtual_type_at", "visual_describe"]


@pytest.mark.asyncio
async def test_visual_type_rejected_input_requires_isolated_desktop_not_foreground(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    analyses = iter([
        ('{"found":true,"confidence":0.9,"x":600,"y":900}', "vision-locate"),
        ('{"exact_text_present":false,"observed_text":"placeholder"}', "vision-verify"),
    ])

    async def fake_rpc(_operation, arguments, **_kwargs):
        if arguments.get("capability") == "virtual_type_at":
            return {
                "status": "uncertain", "session_id": "session-1",
                "executed_action": {"capability": "virtual_type_at", "input_mode": "background_pid_event"},
                "verification": {"event_delivered": True},
                "next_valid_actions": ["call:visual_describe", "disconnect"],
            }
        return {
            "status": "success", "session_id": "session-1", "image_base64": "aW1hZ2U=",
            "mime_type": "image/png", "width": 1800, "height": 1200,
            "coordinate_mapping": {"logical_width": 1200, "logical_height": 800},
        }

    async def fake_analysis(*_args, **_kwargs):
        return next(analyses)

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    monkeypatch.setattr(app_use, "_analyze_capture", fake_analysis)
    result = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-1", "capability": "visual_type",
        "parameters": {"target": "composer", "text": "hello"},
    })
    assert result["status"] == "error"
    assert result["type"] == "unsupported_background_text_input"
    assert result["isolation_required"] is True
    assert result["foreground_fallback_allowed"] is False
    assert result["next_valid_actions"] == ["disconnect"]


@pytest.mark.asyncio
async def test_visual_click_scales_capture_coordinates_and_uses_foreground_quartz_click(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use
    from cyrene.runtime import attachments

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        capability = arguments.get("capability")
        if capability == "visual_describe":
            return {
                "status": "success",
                "session_id": "session-1",
                "target": {"platform": "darwin"},
                "image_base64": "aW1hZ2U=",
                "mime_type": "image/png",
                "width": 400,
                "height": 300,
                "coordinate_mapping": {
                    "logical_width": 800,
                    "logical_height": 600,
                    "captured_width": 400,
                    "captured_height": 300,
                },
            }
        if capability == "focus_window":
            return {"status": "success", "session_id": "session-1"}
        if capability == "click_at":
            point = arguments["parameters"]
            assert point["x"] == 200
            assert point["y"] == 100
            assert point["allow_foreground_input"] is True
            return {
                "status": "success",
                "session_id": "session-1",
                "diagnostics": {"method": "Quartz CGEvent"},
                "focus_restore": {"status": "success"},
            }
        raise AssertionError(f"unexpected RPC call: {operation} {arguments}")

    async def fake_vision(_content, content_prompt="", **kwargs):
        assert "untrusted UI data" in content_prompt
        assert kwargs["max_tokens"] is None
        assert kwargs["timeout"] == 60.0
        return {
            "vision_text": '{"found":true,"confidence":0.96,"x":100,"y":50,"bbox":[90,40,20,20],"label":"Close"}',
            "vision_model": "vision-test",
        }

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    monkeypatch.setattr(attachments, "run_vision_chat", fake_vision)
    result = await app_use.execute_app_use({
        "operation": "call",
        "session_id": "session-1",
        "capability": "visual_click",
        "parameters": {"target": "close button"},
    })
    assert result["status"] == "success"
    assert result["method"] == "visual_coordinate_to_foreground_quartz_click"
    assert result["foreground_input_used"] is True
    assert result["fallback_used"] is False
    assert result["attempts"][1]["window_point"] == {"x": 200, "y": 100}
    assert [item[1]["capability"] for item in calls] == ["visual_describe", "focus_window", "click_at"]


@pytest.mark.asyncio
async def test_visual_click_never_calls_semantic_find_or_press(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use
    from cyrene.runtime import attachments

    calls = []

    async def fake_rpc(_operation, arguments, **_kwargs):
        capability = arguments.get("capability")
        calls.append(capability)
        if capability == "visual_describe":
            return {
                "status": "success",
                "session_id": "session-1",
                "target": {"platform": "darwin"},
                "image_base64": "aW1hZ2U=",
                "mime_type": "image/png",
                "width": 400,
                "height": 300,
                "coordinate_mapping": {"logical_width": 400, "logical_height": 300},
            }
        raise AssertionError(f"unexpected capability: {capability}")

    async def fake_vision(_content, content_prompt="", **_kwargs):
        return {"vision_text": '{"found":false,"confidence":0}', "vision_model": "vision-test"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    monkeypatch.setattr(attachments, "run_vision_chat", fake_vision)
    result = await app_use.execute_app_use({
        "operation": "call",
        "session_id": "session-1",
        "capability": "visual_click",
        "parameters": {"target": "close button", "max_attempts": 1},
    })
    assert result["status"] == "uncertain"
    assert result["alternate_scheme"]["tool"] == "AppUISnapshot"
    assert calls == ["visual_describe"]


@pytest.mark.asyncio
async def test_visual_click_rejects_removed_cross_scheme_fallback_arguments():
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    result = await app_use.execute_app_use({
        "operation": "call",
        "session_id": "session-1",
        "capability": "visual_click",
        "parameters": {
            "target": "new tab",
            "fallback": ["semantic_press"],
            "keyboard_shortcut": ["command", "t"],
        },
    })
    assert result["status"] == "error"
    assert result["type"] == "invalid_arguments"
    assert "does not accept" in result["message"]
    assert "fallback" in result["message"]


@pytest.mark.asyncio
async def test_visual_click_attributes_only_the_visual_pointer_action(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use
    from cyrene.runtime import attachments

    async def fake_rpc(_operation, arguments, **_kwargs):
        capability = arguments.get("capability")
        if capability == "visual_describe":
            return {
                "status": "success", "session_id": "session-1", "target": {"platform": "darwin"},
                "image_base64": "aW1hZ2U=", "mime_type": "image/png", "width": 100, "height": 100,
                "coordinate_mapping": {"logical_width": 100, "logical_height": 100},
            }
        if capability == "focus_window":
            return {"status": "success", "session_id": "session-1"}
        if capability == "click_at":
            return {
                "status": "success", "session_id": "session-1",
                "diagnostics": {"method": "Quartz CGEvent"},
                "focus_restore": {"status": "success"},
            }
        raise AssertionError(capability)

    async def fake_vision(_content, content_prompt="", **_kwargs):
        return {"vision_text": '{"found":true,"confidence":1,"x":50,"y":20,"label":"plus"}', "vision_model": "test"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    monkeypatch.setattr(attachments, "run_vision_chat", fake_vision)
    result = await app_use.execute_app_use({
        "operation": "call", "session_id": "session-1", "capability": "visual_click",
        "parameters": {"target": "new tab"},
    })
    assert result["status"] == "success"
    assert result["executed_action"]["capability"] == "click_at"
    assert result["executed_action"]["native_action"] == "Quartz CGEvent"
    assert result["foreground_input_used"] is True
    assert result["fallback_used"] is False
    assert "unused_fallback_configuration" not in result


@pytest.mark.asyncio
async def test_app_use_tool_returns_structured_json(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use
    from agent.plugin.plugin_impl.cyrene_desktop import app_use as tool

    async def fake_execute(arguments, _context=None):
        return {"status": "success", "operation": arguments["operation"], "targets": []}

    monkeypatch.setattr(app_use, "execute_app_use", fake_execute)
    result = await tool.handler({"operation": "list_targets"}, PluginContext())
    parsed = json.loads(result)
    assert parsed == {"status": "success", "operation": "list_targets", "targets": []}


def test_app_use_result_limiter_prunes_nodes():
    from agent.plugin.plugin_impl.cyrene_desktop._app_use_backend import format_app_use_result

    result = {
        "status": "success",
        "nodes": [{"ref": f"e{i}", "name": "x" * 100} for i in range(100)],
    }
    rendered = format_app_use_result(result, max_chars=900)
    parsed = json.loads(rendered)
    assert len(rendered) <= 900
    assert parsed["status"] == "success"
    assert parsed["truncated"] is True
    assert len(parsed["nodes"]) < 100


def test_app_use_result_limiter_prunes_nested_verification():
    from agent.plugin.plugin_impl.cyrene_desktop._app_use_backend import format_app_use_result

    result = {
        "status": "success",
        "summary": "Pressed Save.",
        "verification": {
            "status": "success",
            "nodes": [{"ref": f"e{i}", "name": "x" * 100} for i in range(100)],
        },
    }
    rendered = format_app_use_result(result, max_chars=900)
    parsed = json.loads(rendered)
    assert len(rendered) <= 900
    assert parsed["status"] == "success"
    assert parsed["summary"] == "Pressed Save."
    assert parsed["verification"]["truncated"] is True
    assert len(parsed["verification"]["nodes"]) < 100


@pytest.mark.asyncio
async def test_electron_app_rpc_uses_app_endpoint(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_desktop import _app_use_backend as app_use

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "success"}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "43210")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "test-token")
    monkeypatch.setattr(app_use.httpx, "AsyncClient", FakeClient)

    result = await app_use._electron_app_rpc("list_targets", {})
    assert result == {"status": "success"}
    assert captured["url"] == "http://127.0.0.1:43210/app/rpc"
    assert captured["post_kwargs"]["headers"]["X-Cyrene-Token"] == "test-token"
    assert json.loads(captured["post_kwargs"]["content"])["method"] == "list_targets"


def test_electron_main_wires_app_rpc_and_quick_chat_origin():
    from pathlib import Path

    main = (Path(__file__).resolve().parents[1] / "electron" / "main.js").read_text(encoding="utf-8")
    assert "require('./app-use')" in main
    assert "'/app/rpc'" in main
    assert "handleAppUseRpc" in main
    assert "captureQuickChatOrigin" in main


def test_platform_provider_scripts_exist():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "electron"
    assert (root / "app-use-macos.jxa").is_file()
    assert (root / "app-use-windows.ps1").is_file()
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    extra_resources = package["build"]["extraResources"]
    assert {
        "from": "app-use-macos.jxa",
        "to": "app-use/app-use-macos.jxa",
    } in extra_resources
    assert {
        "from": "app-use-windows.ps1",
        "to": "app-use/app-use-windows.ps1",
    } in extra_resources
    # osascript and PowerShell cannot execute scripts from Electron's ASAR FS.
    assert "app-use-macos.jxa" not in package["build"]["files"]
    assert "app-use-windows.ps1" not in package["build"]["files"]


def test_app_use_localizes_public_errors_and_hides_bridge_diagnostics():
    from agent.plugin.plugin_impl.cyrene_desktop._app_use_backend import (
        format_app_use_result,
    )

    rendered = format_app_use_result(
        {
            "status": "error",
            "type": "desktop_host_error",
            "message": "ConnectionError: secret transport detail",
        },
        PluginContext(data={"language": "zh"}),
    )
    payload = json.loads(rendered)

    assert payload["message"] == "App Use 桌面桥接失败。"
    assert "secret transport detail" not in rendered
