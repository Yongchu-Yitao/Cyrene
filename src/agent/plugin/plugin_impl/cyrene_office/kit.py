"""Deferred L1-L6 capabilities for the progressive PowerPoint agent kit."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Awaitable, Callable

from agent.plugin import PluginContext
from cyrene.office.slide_layout import SEMANTIC_LAYOUTS
from ._shared import (
    CONTEXT_PROPERTIES,
    MUTATION_PROPERTIES,
    OPERATION_SCHEMA,
    SLIDE_PROPERTIES,
    STYLE_SCHEMA,
    execute_powerpoint_request,
    apply_ooxml_patch_handler,
    apply_batch_handler,
    edit_chart_handler,
    insert_slides_handler,
    render_slide_handler,
    replace_slide_ooxml_handler,
    normalize_powerpoint_arguments,
    office_tool_metadata,
    tool_def,
)
from agent.plugin.execution import publish_plugin_progress as publish_tool_progress

BOX = {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4, "description": "[left, top, width, height] in PowerPoint points."}
ELEMENT = {
    "type": "object",
    "properties": {
        "ref": {"type": "string", "maxLength": 120},
        "type": {"type": "string", "enum": ["text", "shape", "image", "line", "chart", "table"]},
        "box": BOX,
        "text": {"type": "string"},
        "geometry": {"type": "string"},
        "imagePath": {"type": "string"},
        "imageBase64": {"type": "string"},
        "assetRef": {"type": "string", "description": "Workspace-relative PNG/JPEG asset resolved and encoded by Cyrene."},
        "chartType": {"type": "string"},
        "chartSpec": {"type": "object"},
        "data": {"type": "object"},
        "values": {"type": "array", "items": {"type": "array", "items": {}}},
        "style": STYLE_SCHEMA,
    },
    "required": ["ref", "type", "box"],
    "additionalProperties": False,
}
THEME = {
    "type": "object",
    "properties": {
        "background": {"type": "string"},
        "foreground": {"type": "string"},
        "accent": {"type": "string"},
        "muted": {"type": "string"},
        "fontFamily": {"type": "string"},
    },
    "additionalProperties": False,
}
SECTION = {
    "type": "object",
    "properties": {
        "heading": {"type": "string"},
        "title": {"type": "string"},
        "body": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
    },
    "additionalProperties": False,
}
SEMANTIC_IMAGE = {
    "type": "object",
    "properties": {
        "ref": {"type": "string"},
        "imagePath": {"type": "string"},
        "imageBase64": {"type": "string"},
        "assetRef": {"type": "string"},
        "caption": {"type": "string"},
    },
    "additionalProperties": False,
}
TEMPLATE_BINDING = {
    "type": "object",
    "properties": {
        "shapeRef": {
            "type": "string",
            "description": "Stable shape ref, PowerPoint shape ID, or shape name returned by inspection of the source slide.",
        },
        "text": {
            "type": "string",
            "description": "Replacement text. An empty string intentionally clears the inherited shape.",
        },
        "delete": {
            "type": "boolean",
            "description": "Delete this inherited shape from the duplicated slide.",
        },
    },
    "required": ["shapeRef"],
    "additionalProperties": False,
}
SLIDE_SPEC = {
    "type": "object",
    "properties": {
        "layout": {"type": "string", "enum": list(SEMANTIC_LAYOUTS), "description": "Semantic deterministic layout. Prefer title-body, title-bullets, two-column, section-grid, image-left, image-right, or quote. If image is supplied with a non-media layout, Cyrene preserves it by resolving to image-right and records the fallback in metadata."},
        "title": {"type": "string", "description": "Slide title. Cyrene chooses its coordinates and title typography."},
        "subtitle": {"type": "string"},
        "body": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
        "sections": {"type": "array", "items": SECTION, "maxItems": 6},
        "columns": {"type": "array", "items": SECTION, "minItems": 2, "maxItems": 2},
        "image": SEMANTIC_IMAGE,
        "quote": {"type": "string"},
        "attribution": {"type": "string"},
        "footer": {"type": "string"},
        "theme": THEME,
        "slideMasterId": {"type": "string"},
        "layoutId": {"type": "string"},
        "templateSlideId": {
            "type": "string",
            "description": "For multi-slide creation in an existing deck, duplicate this source slide instead of drawing a generic page.",
        },
        "templateBindings": {
            "type": "array",
            "items": TEMPLATE_BINDING,
            "maxItems": 100,
            "description": "Replace or delete inherited template shapes by stable ref/name/ID without supplying coordinates.",
        },
        "elements": {"type": "array", "items": ELEMENT, "maxItems": 200, "description": "Legacy escape hatch for exact positioned shapes. Omit for normal slide generation; use semantic content fields instead."},
        "background": {"type": "string"},
        "metadata": {"type": "object"},
    },
    "additionalProperties": False,
}
SHAPE_PART_OPERATION_SCHEMA = deepcopy(OPERATION_SCHEMA)
SHAPE_PART_OPERATION_SCHEMA["properties"]["op"]["enum"] = [
    "add_textbox", "add_shape", "update_shape", "move_shape", "resize_shape",
    "update_text", "apply_style", "delete_shape", "set_z_order",
]


def _definition(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return tool_def(name, description, properties, required)


READ_DEFS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    ("PowerPointListSlides", "ppt.list_slides", "List all slides with stable IDs and indices.", {}),
    ("PowerPointGetSlide", "ppt.get_slide", "Read a whole slide structure in one request, including speaker notes when the backend exposes them.", {**SLIDE_PROPERTIES, "includeText": {"type": "boolean"}, "includeNotes": {"type": "boolean"}}),
    ("PowerPointListShapes", "ppt.list_shapes", "Read all shapes on one slide in one request.", {**SLIDE_PROPERTIES, "includeText": {"type": "boolean"}}),
    ("PowerPointGetShape", "ppt.get_shape", "Read one shape by stable reference.", {**SLIDE_PROPERTIES, "shapeRef": {"type": "string"}}),
    ("PowerPointReadText", "ppt.read_text", "Read all text on one slide.", dict(SLIDE_PROPERTIES)),
    ("PowerPointGetMaster", "ppt.get_master", "Inspect master/layout support and OOXML-backed master information.", {}),
    ("PowerPointGetTheme", "ppt.get_theme", "Inspect presentation theme information and available theme tokens.", {}),
    ("PowerPointGetSelection", "ppt.get_selection", "Read the current slide and shape selection.", {}),
)

EDIT_OPS: tuple[tuple[str, str, str], ...] = (
    ("PowerPointAddShape", "ppt.add_shape", "add_shape"),
    ("PowerPointUpdateShape", "ppt.update_shape", "update_shape"),
    ("PowerPointMoveShape", "ppt.move_shape", "move_shape"),
    ("PowerPointResizeShape", "ppt.resize_shape", "resize_shape"),
    ("PowerPointUpdateText", "ppt.update_text", "update_text"),
    ("PowerPointApplyStyle", "ppt.apply_style", "apply_style"),
    ("PowerPointDeleteShape", "ppt.delete_shape", "delete_shape"),
    ("PowerPointGroupShapes", "ppt.group_shapes", "group_shapes"),
    ("PowerPointSetZOrder", "ppt.set_z_order", "set_z_order"),
    ("PowerPointInsertImage", "ppt.insert_image", "insert_image"),
)

COMPOSE: tuple[tuple[str, str, str], ...] = (
    ("PowerPointCreateSlide", "ppt.create_slide", "Create a slide from compact semantic content. Prefer layout/title/body/bullets/sections/theme; Cyrene computes coordinates."),
    ("PowerPointCreateSlides", "ppt.create_slides", "Create multiple slides from compact SlideSpecs; existing decks can duplicate a source slide per page and replace its named shapes without coordinates."),
    ("PowerPointDuplicateSlide", "ppt.duplicate_slide", "Duplicate one slide inside the active presentation."),
    ("PowerPointApplySlideSpec", "ppt.apply_slide_spec", "Apply a deterministic declarative SlideSpec to one slide."),
    ("PowerPointRelayoutSlide", "ppt.relayout_slide", "Rebuild or relayout a slide from a declarative SlideSpec."),
    ("PowerPointCreateFromTemplate", "ppt.create_from_template", "Duplicate a source slide and replace or delete inherited shapes through templateBindings; use generic elements only when the template needs additions."),
    ("PowerPointReplaceSlide", "ppt.replace_slide", "Replace a slide's contents from a SlideSpec with snapshot-backed undo."),
    ("PowerPointMoveSlide", "ppt.move_slide", "Move a slide to a zero-based target index."),
    ("PowerPointDeleteSlide", "ppt.delete_slide", "Delete a slide with revision locking and undo snapshot."),
)

REVIEW: tuple[tuple[str, str, str], ...] = (
    ("PowerPointRenderSlideAdvanced", "ppt.render_slide", "Render a slide to PNG."),
    ("PowerPointVerifySlide", "ppt.verify_slide", "Run all supported layout verification checks."),
    ("PowerPointCheckOverflow", "ppt.check_overflow", "Check likely text overflow."),
    ("PowerPointCheckOverlap", "ppt.check_overlap", "Check material shape overlap."),
    ("PowerPointCheckContrast", "ppt.check_contrast", "Check text/background contrast where color data is available."),
    ("PowerPointCompareBeforeAfter", "ppt.compare_before_after", "Compare the current render with the prior render for this slide."),
    ("PowerPointUndoBatch", "ppt.undo_batch", "Undo a batch or page mutation by undo token if no newer revision exists."),
)

ADVANCED: tuple[tuple[str, str, str], ...] = (
    ("PowerPointEditChart", "ppt.edit_chart", "Create or edit a chart. Choose chartMode from ppt.get_context capabilities: native creates an editable PowerPoint chart; visual requires imageInsertion.available=true."),
    ("PowerPointEditTable", "ppt.edit_table", "Create or edit table content and table layout."),
    ("PowerPointEditMaster", "ppt.edit_master", "Edit master-backed content where the selected backend supports it."),
    ("PowerPointEditLayout", "ppt.edit_layout", "Edit or apply a slide layout."),
    ("PowerPointEditNotes", "ppt.edit_notes", "Edit speaker notes."),
    ("PowerPointBindShape", "ppt.bind_shape", "Assign or update a persistent Cyrene shape reference."),
    ("PowerPointApplyOoxmlPatch", "ppt.apply_ooxml_patch", "Apply a confirmed patch to one explicit OOXML part with snapshot and rollback."),
    ("PowerPointImportSlides", "ppt.import_slides", "Import slides from another PPTX with explicit formatting semantics."),
)

ESCAPE: tuple[tuple[str, str, str], ...] = (
    ("PowerPointExecuteOfficeJs", "ppt.execute_officejs", "Execute one audited command from the restricted Office.js allowlist. Raw JavaScript is never evaluated."),
    ("PowerPointReplaceSlideOoxml", "ppt.replace_slide_ooxml", "Replace a slide through confirmed OOXML/package input with snapshot rollback."),
)


def _mutation_props() -> dict[str, Any]:
    return {**CONTEXT_PROPERTIES, **SLIDE_PROPERTIES, **MUTATION_PROPERTIES}


def _edit_properties(op: str) -> dict[str, Any]:
    properties = {
        **_mutation_props(),
        "shapeRef": {"type": "string"},
        "shapeRefs": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
        "ref": {"type": "string"}, "text": {"type": "string"}, "geometry": {"type": "string"},
        "x": {"type": "number"}, "y": {"type": "number"}, "width": {"type": "number", "minimum": 0}, "height": {"type": "number", "minimum": 0},
        "position": {"type": "string", "enum": ["BringForward", "BringToFront", "SendBackward", "SendToBack"]},
        "style": STYLE_SCHEMA, "imagePath": {"type": "string"}, "imageBase64": {"type": "string"},
    }
    if op == "group_shapes":
        properties.pop("shapeRef", None)
    return properties


async def _read_handler(
    method: str,
    args: dict[str, Any],
    _context: PluginContext,
) -> str:
    return await execute_powerpoint_request(args, method, method, timeout=60)


async def _edit_handler(
    op: str,
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    request = normalize_powerpoint_arguments(args)
    operation = {key: value for key, value in request.items() if key not in {*CONTEXT_PROPERTIES, *SLIDE_PROPERTIES, *MUTATION_PROPERTIES}}
    operation["op"] = op
    request["operations"] = [operation]
    return await apply_batch_handler(request, context)


async def _method_handler(
    method: str,
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    if method == "ppt.create_slides":
        return await _create_slides_handler(args)
    if method == "ppt.render_slide":
        return await render_slide_handler(args, context)
    if method == "ppt.edit_chart":
        return await edit_chart_handler(args, context)
    if method == "ppt.apply_ooxml_patch":
        return await apply_ooxml_patch_handler(args, context)
    if method == "ppt.replace_slide_ooxml":
        return await replace_slide_ooxml_handler(args, context)
    if method in {"ppt.import_slides"}:
        return await insert_slides_handler(args, context)
    request = deepcopy(args)
    if method in {"ppt.create_slide", "ppt.apply_slide_spec", "ppt.relayout_slide", "ppt.create_from_template", "ppt.replace_slide"}:
        if str(request.get("mode") or "") == "file" or request.get("filePath"):
            request["commitMode"] = "atomic"
        else:
            request["commitMode"] = "progressive"
            request["progressiveGranularity"] = request.get("progressiveGranularity") or "stage"
    if method in {"ppt.relayout_slide", "ppt.replace_slide"}:
        request["replaceExisting"] = True
    return await execute_powerpoint_request(request, method, method, timeout=300)


def _decode_tool_result(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"status": "error", "error_code": "invalid_tool_result", "message": str(raw)}
    return payload if isinstance(payload, dict) else {
        "status": "error", "error_code": "invalid_tool_result", "message": str(raw),
    }


def _rollback_context(args: dict[str, Any]) -> dict[str, Any]:
    return {
        key: args[key]
        for key in ("mode", "sessionId", "filePath", "outputPath")
        if args.get(key) not in (None, "")
    }


async def _rollback_created_slides(
    args: dict[str, Any],
    completed: list[dict[str, Any]],
    *,
    fallback_revision: Any,
    idempotency_key: str,
) -> dict[str, Any]:
    """Delete pages created by a failed plural create, newest first."""
    targets = [str(item.get("slideId") or "") for item in reversed(completed)]
    targets = [slide_id for slide_id in targets if slide_id]
    if not targets:
        return {"attempted": False, "completed": True, "deletedSlides": [], "errors": []}

    context_args = _rollback_context(args)
    revision = fallback_revision
    context_raw = await execute_powerpoint_request(
        context_args, "ppt.get_context", "ppt.get_context", timeout=60,
    )
    context_payload = _decode_tool_result(context_raw)
    if context_payload.get("status") != "error":
        revision = context_payload.get("revision", revision)

    deleted: list[str] = []
    errors: list[dict[str, Any]] = []
    for index, slide_id in enumerate(targets, start=1):
        request = {
            **context_args,
            "slideId": slide_id,
            "expectedRevision": revision,
            "idempotencyKey": f"{idempotency_key}:rollback:{index}",
        }
        raw = await execute_powerpoint_request(
            request, "ppt.delete_slide", "ppt.delete_slide", timeout=90,
        )
        payload = _decode_tool_result(raw)
        if payload.get("status") == "error":
            errors.append({
                "slideId": slide_id,
                "error_code": payload.get("error_code") or "rollback_failed",
                "message": payload.get("message") or "PowerPoint rollback failed.",
            })
            continue
        deleted.append(slide_id)
        revision = payload.get("revision", revision)
        if payload.get("filePath"):
            context_args["filePath"] = payload["filePath"]
            context_args.pop("outputPath", None)
    return {
        "attempted": True,
        "completed": not errors,
        "deletedSlides": deleted,
        "errors": errors,
        "revision": revision,
    }


async def _create_slides_handler(args: dict[str, Any]) -> str:
    args = normalize_powerpoint_arguments(args)
    specs = args.get("slideSpecs")
    if not isinstance(specs, list) or not specs:
        return json.dumps({
            "status": "error",
            "operation": "ppt.create_slides",
            "error_code": "slide_specs_required",
            "message": "slideSpecs must contain at least one SlideSpec.",
            "details": {"field": "slideSpecs", "suggestion": "Provide one focused SlideSpec per requested page."},
        }, ensure_ascii=False)
    if len(specs) > 100:
        return json.dumps({
            "status": "error", "operation": "ppt.create_slides", "error_code": "too_many_slides",
            "message": "A single request cannot create more than 100 slides.",
        }, ensure_ascii=False)
    revision = args.get("expectedRevision")
    key = str(args.get("idempotencyKey") or "")
    file_mode = str(args.get("mode") or "") == "file" or bool(args.get("filePath"))
    commit_mode = "atomic" if file_mode else "progressive"
    if file_mode:
        args["commitMode"] = "atomic"
        return await execute_powerpoint_request(args, "ppt.create_slides", "ppt.create_slides", timeout=300)
    completed: list[dict[str, Any]] = []
    warnings: list[Any] = []
    mode = str(args.get("mode") or "")
    await publish_tool_progress(current=0, total=len(specs), label="Preparing PowerPoint slides")
    for index, spec in enumerate(specs):
        request = {key_name: value for key_name, value in args.items() if key_name != "slideSpecs"}
        template_slide_id = str(spec.get("templateSlideId") or "") if isinstance(spec, dict) else ""
        request.update({
            "slideSpec": spec,
            "expectedRevision": revision,
            "idempotencyKey": f"{key}:slide:{index + 1}",
            "commitMode": commit_mode,
            "progressiveGranularity": request.get("progressiveGranularity") or "stage",
        })
        method = "ppt.create_from_template" if template_slide_id else "ppt.create_slide"
        if template_slide_id:
            request["templateSlideId"] = template_slide_id
        raw = await execute_powerpoint_request(request, method, method, timeout=300)
        payload = _decode_tool_result(raw)
        if payload.get("status") == "error":
            payload["operation"] = "ppt.create_slides"
            original_details = payload.get("details")
            details = dict(original_details) if isinstance(original_details, dict) else {}
            failed_slide_rollback = details.get("rollback")
            rollback = await _rollback_created_slides(
                args,
                completed,
                fallback_revision=revision,
                idempotency_key=key,
            )
            details.update({
                "slideIndex": index,
                "completedSlides": completed,
                "rollback": rollback,
            })
            if failed_slide_rollback is not None:
                details["failedSlideRollback"] = failed_slide_rollback
            payload["details"] = details
            return json.dumps(payload, ensure_ascii=False)
        revision = payload.get("revision", revision)
        mode = str(payload.get("mode") or mode)
        if mode == "file" and payload.get("filePath"):
            args["filePath"] = payload["filePath"]
            args.pop("outputPath", None)
        completed.append({
            "index": index,
            "slideId": payload.get("slideId"),
            "revision": revision,
            "undoToken": payload.get("undoToken"),
            "stages": payload.get("stages") or [],
        })
        warnings.extend(payload.get("warnings") or [])
        await publish_tool_progress(current=index + 1, total=len(specs), label=f"PowerPoint slide {index + 1}/{len(specs)}")
    return json.dumps({
        "status": "warning" if warnings else "applied",
        "operation": "ppt.create_slides",
        "mode": mode or "live_office",
        "revision": revision,
        "changed": [],
        "created": completed,
        "deleted": [],
        "warnings": warnings,
        "undoToken": None,
        "undoTokens": [item["undoToken"] for item in completed if item.get("undoToken")],
        "renderId": None,
        "audit": {"action": "create_slides", "slideCount": len(completed), "commitMode": commit_mode, "progressiveGranularity": args.get("progressiveGranularity") or "stage"},
    }, ensure_ascii=False)


def _bind(function: Callable[..., Awaitable[str]], value: str) -> Callable[..., Awaitable[str]]:
    async def handler(
        args: dict[str, Any],
        context: PluginContext,
    ) -> str:
        return await function(value, args, context)
    return handler


def _advanced_properties(method: str) -> dict[str, Any]:
    operation_schema = SHAPE_PART_OPERATION_SCHEMA if method in {"ppt.edit_master", "ppt.edit_layout"} else OPERATION_SCHEMA
    return {
        **_mutation_props(),
        "operations": {"type": "array", "items": operation_schema, "maxItems": 200},
        "slideSpec": SLIDE_SPEC,
        "shapeRef": {"type": "string"},
        "ref": {"type": "string"},
        "text": {"type": "string", "description": "Speaker-note text for ppt.edit_notes."},
        "chartMode": {"type": "string", "enum": ["visual", "native"]},
        "chartSpec": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["column", "bar", "line"]},
                "categories": {"type": "array", "items": {"type": "string"}},
                "series": {"type": "array", "items": {"type": "object"}},
                "background": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "values": {"type": "array", "items": {"type": "array", "items": {}}},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "width": {"type": "number"},
        "height": {"type": "number"},
        "layoutId": {"type": "string"},
        "layoutPart": {"type": "string"},
        "slideMasterId": {"type": "string"},
        "masterPart": {"type": "string"},
        "part": {"type": "string"},
        "xml": {"type": "string"},
        "confirmed": {"type": "boolean"},
        "presentationPath": {"type": "string"},
        "presentationBase64": {"type": "string"},
        "sourceSlideIds": {"type": "array", "items": {"type": "string"}},
        "targetSlideId": {"type": "string"},
        "formatting": {"type": "string", "enum": ["KeepSourceFormatting", "UseDestinationTheme"]},
    }


def register_all(tool_defs: list[dict[str, Any]], tool_handlers: dict[str, Any], tool_metadata: dict[str, dict[str, Any]]) -> None:
    for name, method, description, extra in READ_DEFS:
        definition = _definition(name, description, {**CONTEXT_PROPERTIES, **extra})
        tool_defs.append(definition)
        tool_handlers[name] = _bind(_read_handler, method)
        tool_metadata[name] = office_tool_metadata(read_only=True)
    for name, capability, op in EDIT_OPS:
        definition = _definition(name, f"Compile {capability} into one ppt.apply_batch operation.", _edit_properties(op), ["expectedRevision", "idempotencyKey"])
        tool_defs.append(definition)
        tool_handlers[name] = _bind(_edit_handler, op)
        tool_metadata[name] = office_tool_metadata(read_only=False)
    for name, method, description in COMPOSE:
        props = {**_mutation_props(), "slideSpec": SLIDE_SPEC, "templateSlideId": {"type": "string"}, "replaceExisting": {"type": "boolean"}, "targetIndex": {"type": "integer", "minimum": 0}, "commitMode": {"type": "string", "enum": ["atomic", "progressive"], "description": "File mode writes atomically. Live composition uses the connected PowerPoint add-in and normally publishes a few logical stages."}, "progressiveGranularity": {"type": "string", "enum": ["stage", "element"], "description": "stage batches compatible Office.js operations to reduce round trips; element is reserved for an explicitly visible step-by-step build."}}
        if method == "ppt.create_slides":
            props.pop("slideSpec", None)
            props["slideSpecs"] = {"type": "array", "items": SLIDE_SPEC, "minItems": 1, "maxItems": 100}
        required = ["expectedRevision", "idempotencyKey"]
        if method == "ppt.create_from_template":
            required.append("templateSlideId")
        definition = _definition(name, description, props, required)
        tool_defs.append(definition)
        tool_handlers[name] = _bind(_method_handler, method)
        tool_metadata[name] = office_tool_metadata(read_only=False)
    for name, method, description in REVIEW:
        props = {**CONTEXT_PROPERTIES, **SLIDE_PROPERTIES, "width": {"type": "integer", "minimum": 320, "maximum": 3840}, "overlapThreshold": {"type": "number", "minimum": 0, "maximum": 1}, "minimumRatio": {"type": "number", "minimum": 1, "maximum": 21}, "includeImages": {"type": "boolean"}}
        if method == "ppt.undo_batch":
            props.update(MUTATION_PROPERTIES)
            props["undoToken"] = {"type": "string"}
        required = ["expectedRevision", "idempotencyKey", "undoToken"] if method == "ppt.undo_batch" else []
        definition = _definition(name, description, props, required)
        tool_defs.append(definition)
        tool_handlers[name] = _bind(_method_handler, method)
        tool_metadata[name] = office_tool_metadata(read_only=method != "ppt.undo_batch")
    for name, method, description in ADVANCED:
        props = _advanced_properties(method)
        required = ["expectedRevision", "idempotencyKey"]
        if method == "ppt.edit_chart":
            required.append("chartMode")
        definition = _definition(name, description, props, required)
        tool_defs.append(definition)
        if method == "ppt.bind_shape":
            tool_handlers[name] = _bind(_edit_handler, "update_shape")
        else:
            tool_handlers[name] = _bind(_method_handler, method)
        tool_metadata[name] = office_tool_metadata(read_only=False)
    for name, method, description in ESCAPE:
        props = {**_mutation_props(), "confirmed": {"type": "boolean"}, "command": {"type": "string", "enum": ["get_context", "render_slide", "inspect_slide"]}, "arguments": {"type": "object"}, "part": {"type": "string"}, "xml": {"type": "string"}, "presentationPath": {"type": "string"}, "presentationBase64": {"type": "string"}}
        definition = _definition(name, description, props, ["expectedRevision", "idempotencyKey", "confirmed"])
        tool_defs.append(definition)
        tool_handlers[name] = _bind(_method_handler, method)
        tool_metadata[name] = office_tool_metadata(read_only=False)


__all__ = ["register_all", "SLIDE_SPEC"]
