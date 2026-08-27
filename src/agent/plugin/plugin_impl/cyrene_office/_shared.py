from __future__ import annotations

import base64
import asyncio
import binascii
from copy import deepcopy
from io import BytesIO
import secrets
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from agent.plugin import PluginContext
from cyrene.config import DATA_DIR
from .gateway import get_office_gateway_runtime
from .file_engine import PptxFileError, get_pptx_file_engine
from .slide_layout import compile_slide_spec
from .service import OfficeBridgeError, get_office_bridge
from cyrene.localization import localized
from agent.plugin.native_runtime import (
    json_result,
    plugin_localized,
    resolve_workspace_path,
)

CONTEXT_PROPERTIES = {
    "mode": {
        "type": "string",
        "enum": ["live_office", "file"],
        "description": "Execution backend. Passing filePath selects file mode; otherwise the connected add-in is used.",
    },
    "sessionId": {"type": "string", "maxLength": 160, "description": "Connected Office session. Omit only when exactly one PowerPoint presentation is connected."},
    "filePath": {"type": "string", "description": "Local .pptx path for file mode."},
    "outputPath": {"type": "string", "description": "Optional output .pptx path for file-mode mutations."},
}
SESSION_PROPERTY = CONTEXT_PROPERTIES
SLIDE_PROPERTIES = {
    "slideId": {"type": "string", "maxLength": 200, "description": "Stable PowerPoint slide ID."},
    "slideIndex": {"type": "integer", "minimum": 0, "description": "Zero-based slide index."},
}
MUTATION_PROPERTIES = {
    "expectedRevision": {"type": "integer", "minimum": 0, "description": "Revision returned by the latest inspection."},
    "idempotencyKey": {"type": "string", "minLength": 1, "maxLength": 160, "description": "Unique key for safe retry of this exact mutation."},
}
OFFICE_RESOURCE_METADATA = {
    "resource_keys": (),
    "resource_key_alternatives": (
        ("filePath", "office-file:{filePath}"),
        ("sessionId", "office-session:{sessionId}"),
    ),
    "resource_key_default": "office:powerpoint",
    "requires_order": True,
}


def office_tool_metadata(*, read_only: bool) -> dict[str, Any]:
    return {**OFFICE_RESOURCE_METADATA, "read_only": read_only}

STYLE_SCHEMA = {
    "type": "object",
    "properties": {
        "fillColor": {"type": "string"},
        "fillTransparency": {"type": "number", "minimum": 0, "maximum": 1},
        "lineColor": {"type": "string"},
        "lineWeight": {"type": "number", "minimum": 0},
        "lineTransparency": {"type": "number", "minimum": 0, "maximum": 1},
        "fontName": {"type": "string"},
        "fontSize": {"type": "number", "minimum": 1, "maximum": 400},
        "fontColor": {"type": "string"},
        "bold": {"type": "boolean"},
        "italic": {"type": "boolean"},
        "horizontalAlignment": {"type": "string", "enum": ["Left", "Center", "Right", "Justify", "Distributed"]},
        "verticalAlignment": {"type": "string", "enum": ["Top", "Middle", "Bottom", "TopCentered", "MiddleCentered", "BottomCentered"]},
        "wordWrap": {"type": "boolean"},
    },
    "additionalProperties": False,
}

OPERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["add_textbox", "add_shape", "add_line", "update_shape", "move_shape", "resize_shape", "update_text", "apply_style", "delete_shape", "group_shapes", "ungroup_shapes", "set_z_order", "insert_image"]},
        "shapeRef": {"type": "string", "description": "Persistent shape reference returned by inspection."},
        "shapeRefs": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
        "target": {"type": "string", "description": "Shape id, name, or Cyrene ref."},
        "targets": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
        "ref": {"type": "string", "maxLength": 120, "description": "Agent-stable reference assigned as the shape's Cyrene name."},
        "name": {"type": "string", "maxLength": 250},
        "text": {"type": "string"},
        "geometry": {"type": "string", "description": "PowerPoint geometric shape type, e.g. Rectangle, RoundRectangle, Ellipse, Chevron."},
        "connector": {"type": "string", "enum": ["Straight", "Elbow", "Curve"]},
        "imagePath": {"type": "string", "description": "Workspace-relative PNG/JPEG path. Cyrene validates and converts it; never encode the image manually."},
        "imageBase64": {"type": "string", "description": "Validated image payload for programmatic callers. Agents should prefer imagePath."},
        "assetRef": {"type": "string", "description": "Workspace-relative asset reference resolved by Cyrene."},
        "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4, "description": "[left, top, width, height] in PowerPoint points."},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "width": {"type": "number", "minimum": 0},
        "height": {"type": "number", "minimum": 0},
        "rotation": {"type": "number"},
        "position": {"type": "string", "enum": ["BringForward", "BringToFront", "SendBackward", "SendToBack"]},
        "style": STYLE_SCHEMA,
    },
    "required": ["op"],
    "additionalProperties": False,
}


def tool_def(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required or []),
                "additionalProperties": False,
            },
        },
    }


SETUP_DEF = tool_def(
    "OfficeSetupInfo",
    "Read installation, certificate, manifest, gateway and requirement-set information for the local Cyrene Office add-in.",
    {},
)
LIST_SESSIONS_DEF = tool_def(
    "OfficeListSessions",
    "List live Office documents connected to Cyrene. Use first when the target presentation is ambiguous or disconnected.",
    {"host": {"type": "string", "enum": ["powerpoint", "word"]}},
)
GET_CONTEXT_DEF = tool_def(
    "PowerPointGetContext",
    "Inspect the connected presentation, current revision, selected slides/shapes, and supported PowerPoint API levels.",
    dict(SESSION_PROPERTY),
)
INSPECT_DEF = tool_def(
    "PowerPointInspect",
    "Read presentation, selection, one slide, or one shape in a single structured response before editing.",
    {
        **SESSION_PROPERTY,
        **SLIDE_PROPERTIES,
        "scope": {"type": "string", "enum": ["presentation", "selection", "slide", "shape"]},
        "shapeRef": {"type": "string"},
        "includeText": {"type": "boolean"},
    },
)
LIST_SLIDES_DEF = tool_def(
    "PowerPointListSlides",
    "List slide IDs and indices in the live presentation. Read-only and inexpensive.",
    dict(SESSION_PROPERTY),
)
LIST_SHAPES_DEF = tool_def(
    "PowerPointListShapes",
    "List IDs, stable refs, names, types, bounds, z-order, and optionally text for shapes on one live slide.",
    {**SESSION_PROPERTY, **SLIDE_PROPERTIES, "includeText": {"type": "boolean"}},
)
READ_TEXT_DEF = tool_def(
    "PowerPointReadText",
    "Read text-bearing shapes on one live slide without rendering it.",
    {**SESSION_PROPERTY, **SLIDE_PROPERTIES},
)
APPLY_BATCH_DEF = tool_def(
    "PowerPointApplyBatch",
    "Apply one ordered typed mutation to a slide. Live PowerPoint synchronizes dependency-safe operation stages by default; request element granularity only for an explicitly visible step-by-step build. The batch returns one revision and undo token. Prefer one slide per batch.",
    {
        **SESSION_PROPERTY,
        **SLIDE_PROPERTIES,
        **MUTATION_PROPERTIES,
        "operations": {"type": "array", "items": OPERATION_SCHEMA, "maxItems": 200},
        "progressiveGranularity": {"type": "string", "enum": ["stage", "element"], "description": "stage batches compatible operations to reduce Office.js round trips; element is a slower presentation mode."},
    },
    ["expectedRevision", "idempotencyKey", "operations"],
)
RENDER_SLIDE_DEF = tool_def(
    "PowerPointRenderSlide",
    "Render the actual live slide through PowerPoint and save the PNG locally for visual inspection.",
    {**SESSION_PROPERTY, **SLIDE_PROPERTIES, "width": {"type": "integer", "minimum": 320, "maximum": 3840}},
)
VERIFY_SLIDE_DEF = tool_def(
    "PowerPointVerifySlide",
    "Check one live slide for out-of-bounds shapes, material overlap, and likely text overflow. Render separately for final visual judgment.",
    {**SESSION_PROPERTY, **SLIDE_PROPERTIES, "overlapThreshold": {"type": "number", "minimum": 0.05, "maximum": 1}},
)
UNDO_BATCH_DEF = tool_def(
    "PowerPointUndoBatch",
    "Undo the immediately preceding Cyrene batch using its undo token. Refuses if any newer presentation revision exists.",
    {**SESSION_PROPERTY, **MUTATION_PROPERTIES, "undoToken": {"type": "string", "minLength": 1, "maxLength": 200}},
    ["expectedRevision", "idempotencyKey", "undoToken"],
)
INSERT_SLIDES_DEF = tool_def(
    "PowerPointInsertSlides",
    "Advanced composition: insert one or more slides from a local PPTX or Base64 presentation into the live deck, preserving source or destination formatting.",
    {
        **SESSION_PROPERTY,
        **MUTATION_PROPERTIES,
        "presentationPath": {"type": "string", "description": "Local .pptx path."},
        "presentationBase64": {"type": "string", "description": "Base64-encoded .pptx bytes."},
        "targetSlideId": {"type": "string", "description": "Insert after this slide; omit to insert at the beginning."},
        "sourceSlideIds": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
        "formatting": {"type": "string", "enum": ["KeepSourceFormatting", "UseDestinationTheme"]},
    },
    ["expectedRevision", "idempotencyKey"],
)


class PowerPointRequestError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _office_localized(
    context: PluginContext | None,
    en: str,
    zh: str,
    **values: Any,
) -> str:
    if context is None:
        return localized(en, zh, **values)
    return plugin_localized(context, en, zh, **values)


_OFFICE_ERROR_MESSAGES: dict[str, tuple[str, str]] = {
    "non_canonical_field": (
        "Use the canonical camelCase PowerPoint field name.",
        "请使用规范的 camelCase PowerPoint 字段名。",
    ),
    "asset_outside_workspace": (
        "The image asset must be inside the active workspace.",
        "图像资源必须位于当前工作区内。",
    ),
    "asset_not_found": ("The image asset was not found.", "未找到图像资源。"),
    "asset_too_large": (
        "The image asset exceeds the 25 MB input limit.",
        "图像资源超过 25 MB 输入限制。",
    ),
    "unsupported_image_format": (
        "Use a PNG or JPEG image.",
        "请使用 PNG 或 JPEG 图像。",
    ),
    "invalid_image": (
        "The image is corrupt or unsupported.",
        "图像已损坏或格式不受支持。",
    ),
    "invalid_image_base64": (
        "imageBase64 is not valid Base64 data.",
        "imageBase64 不是有效的 Base64 数据。",
    ),
    "image_required": (
        "Image insertion requires imagePath, assetRef, or imageBase64.",
        "插入图像需要 imagePath、assetRef 或 imageBase64。",
    ),
    "invalid_operation": (
        "Each PowerPoint operation must be an object.",
        "每个 PowerPoint 操作都必须是对象。",
    ),
    "unknown_style_field": (
        "The request contains an unsupported style field.",
        "请求包含不受支持的样式字段。",
    ),
    "invalid_box": (
        "box must contain [left, top, width, height].",
        "box 必须包含 [left, top, width, height]。",
    ),
    "unknown_operation_field": (
        "The request contains an unsupported operation field.",
        "请求包含不受支持的操作字段。",
    ),
    "operation_type_required": (
        "The PowerPoint operation is missing op.",
        "PowerPoint 操作缺少 op。",
    ),
    "unsupported_operation": (
        "The requested PowerPoint operation is unsupported.",
        "不支持所请求的 PowerPoint 操作。",
    ),
    "shape_target_required": (
        "This operation requires shapeRef or target.",
        "此操作需要 shapeRef 或 target。",
    ),
    "shape_targets_required": (
        "This operation requires shapeRefs or targets.",
        "此操作需要 shapeRefs 或 targets。",
    ),
    "geometry_required": (
        "This operation requires complete numeric geometry.",
        "此操作需要完整的数值几何信息。",
    ),
    "invalid_geometry": (
        "Created shapes require positive width and height.",
        "新建形状的宽度和高度必须为正数。",
    ),
    "invalid_template_bindings": (
        "templateBindings must be an array.",
        "templateBindings 必须是数组。",
    ),
    "shape_ref_required": (
        "Each template binding requires shapeRef.",
        "每个模板绑定都需要 shapeRef。",
    ),
    "invalid_template_binding": (
        "A template binding must provide either text or delete=true.",
        "模板绑定必须提供 text，或设置 delete=true。",
    ),
    "invalid_slide_element": (
        "Each SlideSpec element must be an object.",
        "每个 SlideSpec 元素都必须是对象。",
    ),
    "invalid_batch": (
        "operations must contain at least one operation.",
        "operations 必须至少包含一个操作。",
    ),
    "batch_too_large": (
        "A PowerPoint batch cannot exceed 200 operations.",
        "一个 PowerPoint 批次不能超过 200 个操作。",
    ),
    "slide_specs_required": (
        "slideSpecs must contain at least one SlideSpec.",
        "slideSpecs 必须至少包含一个 SlideSpec。",
    ),
    "too_many_slides": (
        "A single request cannot create more than 100 slides.",
        "单个请求最多只能创建 100 张幻灯片。",
    ),
    "confirmation_required": (
        "This advanced PowerPoint operation requires confirmed=true.",
        "此高级 PowerPoint 操作需要设置 confirmed=true。",
    ),
    "invalid_presentation": (
        "Provide a valid PowerPoint presentation file or Base64 payload.",
        "请提供有效的 PowerPoint 演示文稿文件或 Base64 数据。",
    ),
    "invalid_source_presentation": (
        "The source PowerPoint presentation is invalid.",
        "源 PowerPoint 演示文稿无效。",
    ),
    "source_slide_not_found": (
        "A requested source slide was not found.",
        "未找到所请求的源幻灯片。",
    ),
    "template_slide_required": (
        "This operation requires a template slide.",
        "此操作需要模板幻灯片。",
    ),
    "empty_template_binding": (
        "A template binding did not contain replacement content.",
        "模板绑定未包含替换内容。",
    ),
    "idempotency_required": (
        "This edit requires an idempotencyKey.",
        "此编辑操作需要 idempotencyKey。",
    ),
    "office_not_connected": (
        "No PowerPoint presentation is connected to Cyrene.",
        "当前没有 PowerPoint 演示文稿连接到 Cyrene。",
    ),
    "session_not_found": (
        "The Office session was not found. Reconnect the presentation.",
        "未找到 Office 会话，请重新连接演示文稿。",
    ),
    "session_disconnected": (
        "The Office session disconnected. Reconnect the presentation.",
        "Office 会话已断开，请重新连接演示文稿。",
    ),
    "session_required": (
        "More than one PowerPoint presentation is connected; provide sessionId.",
        "当前连接了多个 PowerPoint 演示文稿，请提供 sessionId。",
    ),
    "office_timeout": (
        "PowerPoint did not respond before the operation timed out.",
        "PowerPoint 未在操作超时前响应。",
    ),
    "revision_conflict": (
        "The presentation changed. Inspect it again before editing.",
        "演示文稿已发生变化，请重新检查后再编辑。",
    ),
    "undo_revision_conflict": (
        "The presentation changed after that batch, so undo was refused.",
        "该批次后演示文稿已发生变化，因此无法撤销。",
    ),
    "undo_not_found": (
        "The undo token is missing or expired.",
        "撤销令牌不存在或已过期。",
    ),
    "slide_not_found": ("The requested slide was not found.", "未找到所请求的幻灯片。"),
    "shape_not_found": ("The requested shape was not found.", "未找到所请求的形状。"),
    "table_not_found": ("The requested table was not found.", "未找到所请求的表格。"),
    "layout_not_found": ("The requested layout was not found.", "未找到所请求的版式。"),
    "render_unavailable": (
        "Slide rendering is unavailable in the current PowerPoint host.",
        "当前 PowerPoint 主机不支持幻灯片渲染。",
    ),
    "empty_render": ("PowerPoint returned an empty slide render.", "PowerPoint 返回了空的幻灯片渲染结果。"),
    "empty_export": ("PowerPoint returned an empty slide export.", "PowerPoint 返回了空的幻灯片导出结果。"),
    "invalid_render": ("The PowerPoint slide render is invalid.", "PowerPoint 幻灯片渲染结果无效。"),
    "invalid_asset": ("The supplied PowerPoint asset is invalid.", "提供的 PowerPoint 资源无效。"),
    "invalid_background": ("The slide background is invalid.", "幻灯片背景无效。"),
    "invalid_chart": ("The chart request is invalid.", "图表请求无效。"),
    "invalid_group": ("The requested shape group is invalid.", "请求的形状组无效。"),
    "invalid_master_edit": ("The slide-master edit is invalid.", "幻灯片母版编辑无效。"),
    "invalid_ooxml": ("The supplied OOXML is invalid.", "提供的 OOXML 无效。"),
    "invalid_ooxml_part": ("The requested OOXML part is invalid.", "请求的 OOXML 部件无效。"),
    "ooxml_part_not_found": ("The requested OOXML part was not found.", "未找到请求的 OOXML 部件。"),
    "invalid_output": ("PowerPoint produced an invalid output file.", "PowerPoint 生成的输出文件无效。"),
    "invalid_shape_part": ("The requested shape part is invalid.", "请求的形状部件无效。"),
    "invalid_slide": ("The requested slide data is invalid.", "请求的幻灯片数据无效。"),
    "notes_body_missing": ("Speaker-note text is required.", "需要提供演讲者备注文本。"),
    "native_chart_failed": ("The native chart operation failed.", "原生图表操作失败。"),
    "ooxml_patch_failed": ("The OOXML patch operation failed.", "OOXML 修补操作失败。"),
    "rollback_failed": ("PowerPoint rollback failed.", "PowerPoint 回滚失败。"),
    "unknown_method": ("The requested PowerPoint method is unsupported.", "不支持所请求的 PowerPoint 方法。"),
    "file_operation_failed": (
        "The PowerPoint file operation failed.",
        "PowerPoint 文件操作失败。",
    ),
    "send_failed": ("The request could not be sent to PowerPoint.", "无法向 PowerPoint 发送请求。"),
    "wrong_host": ("This operation requires PowerPoint.", "此操作需要 PowerPoint。"),
    "unsupported_host": ("The connected Office host is unsupported.", "不支持当前连接的 Office 主机。"),
    "wrong_mode": ("This operation is unavailable in the selected Office mode.", "所选 Office 模式不支持此操作。"),
    "invalid_tool_result": ("PowerPoint returned an invalid result.", "PowerPoint 返回了无效结果。"),
}

_OFFICE_WARNING_MESSAGES: dict[str, tuple[str, str]] = {
    "assumed_slide_size": (
        "Bounds checks used an assumed 16:9 slide size because the required PowerPoint API is unavailable.",
        "由于所需的 PowerPoint API 不可用，边界检查采用了默认的 16:9 幻灯片尺寸。",
    ),
    "unresolved_placeholder": (
        "An empty inherited placeholder remains visible in edit mode.",
        "编辑模式中仍有空的继承占位符可见。",
    ),
    "out_of_bounds": ("A shape extends outside the slide.", "有形状超出了幻灯片边界。"),
    "possible_text_overflow": (
        "Text may overflow its shape; render and inspect the slide.",
        "文本可能溢出形状，请渲染并检查幻灯片。",
    ),
    "shape_overlap": ("Shapes overlap materially.", "形状存在明显重叠。"),
    "low_contrast": ("Text contrast may be too low.", "文本对比度可能过低。"),
}


def _office_message(
    context: PluginContext | None,
    code: str,
) -> str:
    pair = _OFFICE_ERROR_MESSAGES.get(str(code or "")) or (
        "The PowerPoint operation failed. Review the error code and try again.",
        "PowerPoint 操作失败，请检查错误代码后重试。",
    )
    return _office_localized(context, pair[0], pair[1])


def _localize_office_details(
    value: Any,
    context: PluginContext | None,
) -> Any:
    if isinstance(value, list):
        return [_localize_office_details(item, context) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: _localize_office_details(item, context)
        for key, item in value.items()
    }
    for key in (
        "cause",
        "exception",
        "stack",
        "stacktrace",
        "stderr",
        "stdout",
        "traceback",
    ):
        result.pop(key, None)
    if result.get("suggestion"):
        result["suggestion"] = _office_localized(
            context,
            "Review the documented request fields and try again.",
            "请检查文档中的请求字段后重试。",
        )
    for key in ("message", "error"):
        if result.get(key):
            result[key] = _office_localized(
                context,
                "An additional Office diagnostic was omitted.",
                "已省略一条额外的 Office 诊断信息。",
            )
    return result


def _localize_office_success(
    value: Any,
    context: PluginContext | None,
) -> Any:
    if isinstance(value, list):
        return [_localize_office_success(item, context) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "warnings" and isinstance(item, list):
            result[key] = [
                (
                    _office_localized(
                        context,
                        "The connected PowerPoint host reported a capability limitation.",
                        "已连接的 PowerPoint 主机报告了一项能力限制。",
                    )
                    if isinstance(warning, str)
                    else _localize_office_success(warning, context)
                )
                for warning in item
            ]
        else:
            result[key] = _localize_office_success(item, context)
    warning_code = str(result.get("code") or "")
    warning_pair = _OFFICE_WARNING_MESSAGES.get(warning_code)
    if warning_pair is not None:
        result["message"] = _office_localized(
            context,
            warning_pair[0],
            warning_pair[1],
        )
    return result


_SUPPORTED_OPERATIONS = set(OPERATION_SCHEMA["properties"]["op"]["enum"])
_OPERATION_FIELDS = set(OPERATION_SCHEMA["properties"])
_TARGET_OPERATIONS = {"update_shape", "move_shape", "resize_shape", "update_text", "apply_style", "delete_shape", "ungroup_shapes", "set_z_order"}
_CREATE_OPERATIONS = {"add_textbox", "add_shape", "add_line", "insert_image"}


def normalize_powerpoint_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """Copy one request that already follows the public camelCase contract."""
    non_canonical = next((key for key in args if "_" in str(key)), "")
    if non_canonical:
        raise PowerPointRequestError(
            "non_canonical_field",
            f"Use the canonical camelCase field instead of {non_canonical!r}.",
            details={"field": non_canonical},
        )
    return deepcopy(args)


def _request_error(code: str, message: str, *, index: int | None = None, field: str | None = None, suggestion: str | None = None) -> PowerPointRequestError:
    details: dict[str, Any] = {}
    if index is not None:
        details["operationIndex"] = index
    if field:
        details["field"] = field
    if suggestion:
        details["suggestion"] = suggestion
    return PowerPointRequestError(code, message, details=details)


def _resolve_asset_path(path_value: str) -> Path:
    try:
        path = resolve_workspace_path(path_value)
    except ValueError as exc:
        raise PowerPointRequestError("asset_outside_workspace", "", details={"field": "imagePath", "suggestion": "Use a workspace-relative PNG or JPEG path."}) from exc
    if not path.is_file():
        raise PowerPointRequestError("asset_not_found", f"Image asset does not exist: {path}", details={"field": "imagePath", "suggestion": "Verify the asset path relative to the active workspace."})
    return path


def _normalize_image_bytes(raw: bytes, *, source: str) -> str:
    if len(raw) > 25 * 1024 * 1024:
        raise PowerPointRequestError("asset_too_large", "Image asset exceeds the 25 MB input limit.", details={"field": source, "suggestion": "Resize or compress the image before inserting it."})
    try:
        with Image.open(BytesIO(raw)) as image:
            if str(image.format or "").upper() not in {"PNG", "JPEG", "JPG"}:
                raise PowerPointRequestError("unsupported_image_format", f"Unsupported image format: {image.format or 'unknown'}.", details={"field": source, "suggestion": "Use a PNG or JPEG image."})
            image.load()
            if max(image.size) > 4096:
                image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
            normalized = image.convert("RGBA")
            output = BytesIO()
            normalized.save(output, format="PNG", optimize=True)
    except PowerPointRequestError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise PowerPointRequestError("invalid_image", "The image payload is corrupt or unsupported.", details={"field": source, "suggestion": "Use a valid PNG or JPEG file."}) from exc
    return base64.b64encode(output.getvalue()).decode("ascii")


def _prepare_image(container: dict[str, Any], *, index: int | None = None) -> None:
    path_value = str(container.pop("imagePath", "") or container.pop("assetRef", "") or "").strip()
    raw_base64 = str(container.get("imageBase64") or "").strip()
    if path_value:
        path = _resolve_asset_path(path_value)
        container["imageBase64"] = _normalize_image_bytes(path.read_bytes(), source="imagePath")
        return
    if raw_base64:
        encoded = raw_base64.split(",", 1)[1] if raw_base64.startswith("data:") and "," in raw_base64 else raw_base64
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise _request_error("invalid_image_base64", "imageBase64 is not valid Base64.", index=index, field="imageBase64", suggestion="Pass imagePath and let Cyrene encode the image.") from exc
        container["imageBase64"] = _normalize_image_bytes(raw, source="imageBase64")
        return
    raise _request_error("image_required", "Image insertion requires imagePath, assetRef, or imageBase64.", index=index, field="imagePath", suggestion="Pass a workspace-relative PNG/JPEG path; Cyrene handles encoding.")


def _normalize_operation(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _request_error("invalid_operation", "Each operation must be an object.", index=index, suggestion="Use an object with an op field.")
    operation = dict(value)
    if isinstance(operation.get("style"), dict):
        operation["style"] = dict(operation["style"])
        unknown_style = sorted(set(operation["style"]) - set(STYLE_SCHEMA["properties"]))
        if unknown_style:
            raise _request_error("unknown_style_field", f"Unsupported style field: {unknown_style[0]}.", index=index, field=f"style.{unknown_style[0]}")
    box = operation.pop("box", None)
    if box is not None:
        if not isinstance(box, list) or len(box) != 4 or not all(isinstance(item, (int, float)) for item in box):
            raise _request_error("invalid_box", "box must contain [left, top, width, height].", index=index, field="box")
        operation.update({"x": box[0], "y": box[1], "width": box[2], "height": box[3]})
    unknown = sorted(set(operation) - _OPERATION_FIELDS)
    if unknown:
        raise _request_error("unknown_operation_field", f"Unsupported operation field: {unknown[0]}.", index=index, field=unknown[0], suggestion="Use toolbox.describe for the PowerPointApplyBatch Plugin and follow its canonical schema.")
    op = str(operation.get("op") or "").strip()
    if not op:
        raise _request_error("operation_type_required", "Operation is missing op.", index=index, field="op", suggestion="Use op, not type, in agent-visible calls.")
    operation["op"] = op
    if op not in _SUPPORTED_OPERATIONS:
        raise _request_error("unsupported_operation", f"Unsupported PowerPoint operation: {op}.", index=index, field="op", suggestion="Use toolbox.list, then toolbox.describe, to discover the matching PowerPoint Plugin.")
    target = operation.get("shapeRef") or operation.get("target")
    if op in _TARGET_OPERATIONS and not target:
        raise _request_error("shape_target_required", f"{op} requires shapeRef or target.", index=index, field="shapeRef", suggestion="Inspect the slide and reuse a returned stable shape reference.")
    if op == "group_shapes" and not (operation.get("shapeRefs") or operation.get("targets")):
        raise _request_error("shape_targets_required", "group_shapes requires shapeRefs or targets.", index=index, field="shapeRefs")
    if op in _CREATE_OPERATIONS:
        for field in ("x", "y", "width", "height"):
            if not isinstance(operation.get(field), (int, float)):
                raise _request_error("geometry_required", f"{op} requires numeric {field}.", index=index, field=field, suggestion="Pass box: [left, top, width, height] or canonical geometry fields.")
        if operation["width"] <= 0 or operation["height"] <= 0:
            raise _request_error("invalid_geometry", "Created shapes require positive width and height.", index=index, field="width")
    if op == "insert_image":
        _prepare_image(operation, index=index)
    return operation


def _normalize_slide_spec(spec: Any) -> Any:
    if not isinstance(spec, dict):
        return spec
    bindings = spec.get("templateBindings")
    if bindings is not None:
        if not isinstance(bindings, list):
            raise PowerPointRequestError(
                "invalid_template_bindings",
                "templateBindings must be an array.",
                details={"field": "templateBindings"},
            )
        for index, binding in enumerate(bindings):
            if not isinstance(binding, dict) or not str(binding.get("shapeRef") or ""):
                raise PowerPointRequestError(
                    "shape_ref_required",
                    "Every template binding requires shapeRef.",
                    details={"bindingIndex": index, "field": "templateBindings.shapeRef"},
                )
            has_text = "text" in binding
            wants_delete = binding.get("delete") is True
            if has_text == wants_delete:
                raise PowerPointRequestError(
                    "invalid_template_binding",
                    "A template binding must provide exactly one of text or delete=true.",
                    details={"bindingIndex": index, "suggestion": "Replace inherited content with text, or delete the shape explicitly."},
                )
    result = compile_slide_spec(spec)
    elements = result.get("elements")
    if isinstance(elements, list):
        normalized = []
        for index, value in enumerate(elements):
            if not isinstance(value, dict):
                raise PowerPointRequestError("invalid_slide_element", "Each SlideSpec element must be an object.", details={"elementIndex": index})
            element = dict(value)
            if isinstance(element.get("style"), dict):
                element["style"] = dict(element["style"])
                unknown_style = sorted(set(element["style"]) - set(STYLE_SCHEMA["properties"]))
                if unknown_style:
                    raise PowerPointRequestError("unknown_style_field", f"Unsupported style field: {unknown_style[0]}.", details={"elementIndex": index, "field": f"style.{unknown_style[0]}"})
            if element.get("type") == "image":
                _prepare_image(element, index=index)
            normalized.append(element)
        result["elements"] = normalized
    return result


def _prepare_request(method: str, args: dict[str, Any]) -> dict[str, Any]:
    params = normalize_powerpoint_arguments(args)
    if "slideSpec" in params:
        params["slideSpec"] = _normalize_slide_spec(params["slideSpec"])
    if isinstance(params.get("slideSpecs"), list):
        params["slideSpecs"] = [_normalize_slide_spec(spec) for spec in params["slideSpecs"]]
    operations = params.get("operations")
    if method == "ppt.apply_batch" and (not isinstance(operations, list) or not operations):
        raise PowerPointRequestError("invalid_batch", "operations must contain at least one operation.", details={"field": "operations"})
    if isinstance(operations, list):
        if len(operations) > 200:
            raise PowerPointRequestError("batch_too_large", "A PowerPoint batch cannot exceed 200 operations.", details={"field": "operations", "suggestion": "Split work by slide or logical stage."})
        params["operations"] = [_normalize_operation(item, index) for index, item in enumerate(operations)]
    return params


def _success(
    operation: str,
    result: dict[str, Any],
    context: PluginContext | None = None,
) -> str:
    payload = _localize_office_success(dict(result), context)
    payload.setdefault("status", "success")
    payload.setdefault("operation", operation)
    return json_result(payload)


def _failure(
    operation: str,
    exc: OfficeBridgeError,
    context: PluginContext | None = None,
) -> str:
    payload: dict[str, Any] = {
        "status": "error",
        "operation": operation,
        "error_code": exc.code,
        "message": _office_message(context, exc.code),
    }
    if exc.details is not None:
        payload["details"] = _localize_office_details(exc.details, context)
    if exc.code == "office_not_connected":
        payload["setup"] = get_office_gateway_runtime().info()
    return json_result(payload)


def _file_failure(
    operation: str,
    exc: PptxFileError,
    context: PluginContext | None = None,
) -> str:
    payload: dict[str, Any] = {
        "status": "error",
        "operation": operation,
        "mode": "file",
        "error_code": exc.code,
        "message": _office_message(context, exc.code),
    }
    if exc.details is not None:
        payload["details"] = _localize_office_details(exc.details, context)
    return json_result(payload)


async def execute_powerpoint_request(
    args: dict[str, Any],
    operation: str,
    method: str,
    *,
    timeout: float = 45,
    context: PluginContext | None = None,
) -> str:
    try:
        params = await asyncio.to_thread(_prepare_request, method, args)
    except PowerPointRequestError as exc:
        return json_result({
            "status": "error",
            "operation": operation,
            "error_code": exc.code,
            "message": _office_message(context, exc.code),
            "details": _localize_office_details(exc.details, context),
        })
    selected_mode = str(params.get("mode") or "").strip()
    if selected_mode == "file" or params.get("filePath"):
        try:
            result = await asyncio.to_thread(get_pptx_file_engine().call, method, params)
            return _success(operation, result, context)
        except PptxFileError as exc:
            return _file_failure(operation, exc, context)
        except (OSError, ValueError, subprocess.SubprocessError):
            return _file_failure(
                operation,
                PptxFileError("file_operation_failed", ""),
                context,
            )
    params.pop("mode", None)
    session_id = str(params.pop("sessionId", "") or "") or None
    try:
        result = await get_office_bridge().call(session_id, method, params, timeout=timeout)
        result.setdefault("mode", "live_office")
        return _success(operation, result, context)
    except OfficeBridgeError as exc:
        return _failure(operation, exc, context)


def _read_base64(path_value: str, *, expected_suffix: str | None = None, max_bytes: int = 50 * 1024 * 1024) -> str:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Local asset does not exist: {path}")
    if expected_suffix and path.suffix.lower() != expected_suffix:
        raise ValueError(f"Expected a {expected_suffix} file: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"Local asset is too large ({size} bytes; maximum {max_bytes}).")
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _store_render_image(raw_base64: str) -> tuple[str, Path]:
    try:
        content = base64.b64decode(raw_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("PowerPoint returned an invalid Base64 slide render.") from exc
    output_dir = DATA_DIR / "office_gateway" / "renders"
    output_dir.mkdir(parents=True, exist_ok=True)
    render_id = secrets.token_urlsafe(12)
    path = output_dir / f"{render_id}.png"
    path.write_bytes(content)
    return render_id, path.resolve()


def _compile_exported_presentation(
    presentation_base64: str,
    request: dict[str, Any],
    *,
    method: str,
    directory_name: str,
) -> str:
    directory = DATA_DIR / "office_gateway" / directory_name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{secrets.token_urlsafe(12)}.pptx"
    compile_args = {
        key: value for key, value in request.items()
        if key not in {
            "mode", "sessionId", "slideId", "expectedRevision", "idempotencyKey",
        }
    }
    compile_args.update({
        "filePath": str(path),
        "slideIndex": 0,
        "expectedRevision": 0,
        "idempotencyKey": f"compile:{request.get('idempotencyKey') or secrets.token_urlsafe(8)}",
    })
    try:
        path.write_bytes(base64.b64decode(presentation_base64, validate=True))
        get_pptx_file_engine().call(method, compile_args)
        return base64.b64encode(path.read_bytes()).decode("ascii")
    finally:
        path.unlink(missing_ok=True)


async def setup_handler(
    _args: dict[str, Any],
    context: PluginContext,
) -> str:
    info = await asyncio.to_thread(get_office_gateway_runtime().info)
    return _success("office.setup.get", info, context)


async def list_sessions_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    setup = await asyncio.to_thread(get_office_gateway_runtime().info)
    return _success("office.sessions.list", {
        "sessions": get_office_bridge().list_sessions(str(args.get("host") or "") or None),
        "setup": setup,
    }, context)


async def get_context_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    return await execute_powerpoint_request(
        args,
        "ppt.context.get",
        "ppt.get_context",
        context=context,
    )


async def inspect_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    return await execute_powerpoint_request(
        args,
        "ppt.inspect",
        "ppt.inspect",
        timeout=60,
        context=context,
    )


async def list_slides_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    return await execute_powerpoint_request(
        args,
        "ppt.slides.list",
        "ppt.list_slides",
        context=context,
    )


async def list_shapes_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    return await execute_powerpoint_request(
        args,
        "ppt.shapes.list",
        "ppt.list_shapes",
        context=context,
    )


async def read_text_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    return await execute_powerpoint_request(
        args,
        "ppt.text.read",
        "ppt.read_text",
        context=context,
    )


async def apply_batch_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    return await execute_powerpoint_request(
        args,
        "ppt.batch.apply",
        "ppt.apply_batch",
        timeout=300,
        context=context,
    )


async def render_slide_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    params = dict(args)
    if str(params.get("mode") or "") == "file" or params.get("filePath"):
        return await execute_powerpoint_request(
            params,
            "ppt.slide.render",
            "ppt.render_slide",
            timeout=120,
            context=context,
        )
    params.pop("mode", None)
    session_id = str(params.pop("sessionId", "") or "") or None
    try:
        result = await get_office_bridge().call(session_id, "ppt.render_slide", params, timeout=60)
        raw = str(result.pop("imageBase64", "") or "")
        if not raw:
            raise OfficeBridgeError("empty_render", "PowerPoint returned an empty slide render.")
        render_id, path = await asyncio.to_thread(_store_render_image, raw)
        result.update({"renderId": render_id, "imagePath": str(path), "mode": "live_office"})
        return _success("ppt.slide.render", result, context)
    except (OfficeBridgeError, ValueError) as exc:
        bridge_exc = exc if isinstance(exc, OfficeBridgeError) else OfficeBridgeError("invalid_render", "")
        return _failure("ppt.slide.render", bridge_exc, context)


async def verify_slide_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    return await execute_powerpoint_request(
        args,
        "ppt.slide.verify",
        "ppt.verify_slide",
        timeout=60,
        context=context,
    )


async def edit_chart_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    request = dict(args)
    if str(request.get("chartMode") or "visual") != "native":
        return await execute_powerpoint_request(request, "ppt.edit_chart", "ppt.edit_chart", timeout=120, context=context)
    if str(request.get("mode") or "") == "file" or request.get("filePath"):
        return await execute_powerpoint_request(request, "ppt.edit_chart", "ppt.edit_chart", timeout=120, context=context)

    session_id = str(request.pop("sessionId", "") or "") or None
    try:
        exported = await get_office_bridge().call(session_id, "ppt.export_slide", request, timeout=60)
        old_base64 = str(exported.get("presentationBase64") or "")
        if not old_base64:
            raise OfficeBridgeError("empty_export", "PowerPoint returned an empty slide export.")
        prepared_base64 = str(request.pop("presentationBase64", "") or "")
        prepared_path = str(request.pop("presentationPath", "") or "")
        if prepared_path:
            prepared_base64 = await asyncio.to_thread(_read_base64, prepared_path, expected_suffix=".pptx", max_bytes=100 * 1024 * 1024)
        if not prepared_base64:
            prepared_base64 = await asyncio.to_thread(
                _compile_exported_presentation,
                old_base64,
                {**request, "chartMode": "native"},
                method="ppt.edit_chart",
                directory_name="native_charts",
            )
        result = await get_office_bridge().call(session_id, "ppt.replace_slide_ooxml", {
            **request,
            "slideId": exported.get("slideId"),
            "presentationBase64": prepared_base64,
            "undoBase64": old_base64,
            "chartMode": "native",
        }, timeout=120)
        result.update({"mode": "live_office", "nativeEditable": True, "chartMode": "native"})
        return _success("ppt.edit_chart", result, context)
    except OfficeBridgeError as exc:
        return _failure("ppt.edit_chart", exc, context)
    except (PptxFileError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, PptxFileError) else "native_chart_failed"
        return _failure("ppt.edit_chart", OfficeBridgeError(code, ""), context)


async def apply_ooxml_patch_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    """Compile a confirmed slide-package patch off-process, then replace one live slide."""
    request = dict(args)
    if str(request.get("mode") or "") == "file" or request.get("filePath"):
        return await execute_powerpoint_request(request, "ppt.apply_ooxml_patch", "ppt.apply_ooxml_patch", timeout=120, context=context)
    if not request.get("confirmed"):
        return json_result({
            "status": "error", "operation": "ppt.apply_ooxml_patch",
            "error_code": "confirmation_required",
            "message": _office_message(context, "confirmation_required"),
        })
    session_id = str(request.pop("sessionId", "") or "") or None
    request.pop("mode", None)
    try:
        exported = await get_office_bridge().call(session_id, "ppt.export_slide", request, timeout=60)
        old_base64 = str(exported.get("presentationBase64") or "")
        if not old_base64:
            raise OfficeBridgeError("empty_export", "PowerPoint returned an empty slide export.")
        prepared_base64 = await asyncio.to_thread(
            _compile_exported_presentation,
            old_base64,
            request,
            method="ppt.apply_ooxml_patch",
            directory_name="ooxml_patches",
        )
        result = await get_office_bridge().call(session_id, "ppt.replace_slide_ooxml", {
            **request,
            "slideId": exported.get("slideId"),
            "presentationBase64": prepared_base64,
            "undoBase64": old_base64,
        }, timeout=120)
        result.update({"mode": "live_office", "ooxmlPatched": True})
        return _success("ppt.apply_ooxml_patch", result, context)
    except OfficeBridgeError as exc:
        return _failure("ppt.apply_ooxml_patch", exc, context)
    except (PptxFileError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, PptxFileError) else "ooxml_patch_failed"
        return _failure("ppt.apply_ooxml_patch", OfficeBridgeError(code, ""), context)


async def undo_batch_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    return await execute_powerpoint_request(
        args,
        "ppt.batch.undo",
        "ppt.undo_batch",
        timeout=90,
        context=context,
    )


async def insert_slides_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    request = dict(args)
    if str(request.get("mode") or "") == "file" or request.get("filePath"):
        return await execute_powerpoint_request(request, "ppt.slides.import", "ppt.import_slides", timeout=120, context=context)
    try:
        path_value = str(request.pop("presentationPath", "") or "")
        if path_value:
            request["presentationBase64"] = await asyncio.to_thread(_read_base64, path_value, expected_suffix=".pptx", max_bytes=100 * 1024 * 1024)
        if not request.get("presentationBase64"):
            raise ValueError("presentationPath or presentationBase64 is required")
    except ValueError:
        return json_result({"status": "error", "operation": "ppt.slides.insert", "error_code": "invalid_presentation", "message": _office_message(context, "invalid_presentation")})
    return await execute_powerpoint_request(request, "ppt.slides.insert", "ppt.insert_slides", timeout=120, context=context)


async def replace_slide_ooxml_handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    request = dict(args)
    if request.get("confirmed") is not True:
        return json_result({
            "status": "error", "operation": "ppt.replace_slide_ooxml",
            "error_code": "confirmation_required",
            "message": _office_message(context, "confirmation_required"),
        })
    if str(request.get("mode") or "") == "file" or request.get("filePath"):
        return await execute_powerpoint_request(request, "ppt.replace_slide_ooxml", "ppt.replace_slide_ooxml", timeout=120, context=context)
    try:
        path_value = str(request.pop("presentationPath", "") or "")
        if path_value:
            request["presentationBase64"] = await asyncio.to_thread(_read_base64, path_value, expected_suffix=".pptx", max_bytes=100 * 1024 * 1024)
        if not request.get("presentationBase64"):
            raise ValueError("presentationPath or presentationBase64 is required")
    except ValueError:
        return json_result({"status": "error", "operation": "ppt.replace_slide_ooxml", "error_code": "invalid_presentation", "message": _office_message(context, "invalid_presentation")})
    return await execute_powerpoint_request(request, "ppt.replace_slide_ooxml", "ppt.replace_slide_ooxml", timeout=120, context=context)


__all__ = [name for name in globals() if name.endswith("_DEF") or name.endswith("_handler")]
