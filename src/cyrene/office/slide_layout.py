"""Deterministic semantic SlideSpec compiler.

The public PowerPoint tools accept compact, content-oriented slide fields.  This
module turns those fields into the legacy positioned element representation so
the Office.js and OOXML backends share exactly the same layout behavior.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any


SEMANTIC_LAYOUTS = (
    "blank",
    "title",
    "title-body",
    "title-bullets",
    "two-column",
    "section-grid",
    "image-left",
    "image-right",
    "quote",
)

DEFAULT_THEME = {
    "background": "#F7F5F2",
    "foreground": "#1F2937",
    "accent": "#C2410C",
    "muted": "#64748B",
    "fontFamily": "Aptos",
}


def _text_element(
    ref: str,
    text: Any,
    box: list[float],
    *,
    font_size: float,
    color: str,
    font_name: str,
    bold: bool = False,
    alignment: str = "Left",
    vertical: str = "Top",
) -> dict[str, Any]:
    return {
        "ref": ref,
        "type": "text",
        "box": box,
        "text": str(text or ""),
        "style": {
            "fontName": font_name,
            "fontSize": font_size,
            "fontColor": color,
            "bold": bold,
            "horizontalAlignment": alignment,
            "verticalAlignment": vertical,
            "wordWrap": True,
        },
    }


def _shape_element(
    ref: str,
    box: list[float],
    *,
    fill: str,
    geometry: str = "Rectangle",
    line: str | None = None,
) -> dict[str, Any]:
    return {
        "ref": ref,
        "type": "shape",
        "geometry": geometry,
        "box": box,
        "style": {
            "fillColor": fill,
            "lineColor": line or fill,
            "lineWeight": 0,
        },
    }


def _theme(spec: dict[str, Any]) -> dict[str, str]:
    supplied = spec.get("theme") if isinstance(spec.get("theme"), dict) else {}
    result = dict(DEFAULT_THEME)
    for key in result:
        value = supplied.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    if isinstance(spec.get("background"), str) and spec["background"].strip():
        result["background"] = spec["background"].strip()
    return result


def _body_text(spec: dict[str, Any]) -> str:
    body = spec.get("body")
    if isinstance(body, list):
        return "\n".join(str(item) for item in body if str(item).strip())
    return str(body or "")


def _bullet_text(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return "\n".join(f"• {str(item).strip()}" for item in values if str(item).strip())


def _section_text(section: dict[str, Any]) -> str:
    body = section.get("body")
    body_text = "\n".join(str(item) for item in body) if isinstance(body, list) else str(body or "")
    bullets = _bullet_text(section.get("bullets"))
    return "\n".join(value for value in (body_text, bullets) if value)


def _media_text(spec: dict[str, Any]) -> str:
    """Preserve semantic copy when an image requires a media-safe layout."""
    values: list[str] = []
    quote = str(spec.get("quote") or "").strip()
    if quote:
        values.append(f"“{quote}”")
        attribution = str(spec.get("attribution") or "").strip()
        if attribution:
            values.append(f"— {attribution}")
    else:
        for value in (
            str(spec.get("subtitle") or "").strip(),
            _body_text(spec).strip(),
            _bullet_text(spec.get("bullets")).strip(),
        ):
            if value:
                values.append(value)
        structured = spec.get("columns") or spec.get("sections") or []
        for item in structured:
            if not isinstance(item, dict):
                continue
            heading = str(item.get("heading") or item.get("title") or "").strip()
            text = _section_text(item).strip()
            combined = "\n".join(value for value in (heading, text) if value)
            if combined:
                values.append(combined)
    return "\n\n".join(values)


def _image_element(image: dict[str, Any], box: list[float]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ref": str(image.get("ref") or "hero-image"),
        "type": "image",
        "box": box,
    }
    for key in ("imagePath", "imageBase64", "assetRef"):
        if image.get(key):
            result[key] = image[key]
    return result


def _semantic_present(spec: dict[str, Any]) -> bool:
    return any(
        key in spec
        for key in (
            "title", "subtitle", "body", "bullets", "sections", "columns",
            "image", "quote", "attribution", "footer", "theme",
        )
    )


def compile_slide_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Compile a compact semantic SlideSpec into positioned legacy elements."""
    if not isinstance(spec, dict):
        return spec
    if not _semantic_present(spec):
        return deepcopy(spec)

    result = {
        key: deepcopy(value)
        for key, value in spec.items()
        if key not in {
            "title", "subtitle", "body", "bullets", "sections", "columns",
            "image", "quote", "attribution", "footer", "theme", "elements",
        }
    }
    theme = _theme(spec)
    layout = str(spec.get("layout") or "title-body").strip().lower()
    if layout not in SEMANTIC_LAYOUTS:
        layout = "title-body"
    requested_layout = layout
    image = spec.get("image") if isinstance(spec.get("image"), dict) else None
    layout_fallback_reason = ""
    if image and layout not in {"image-left", "image-right"}:
        # A semantic image is an explicit content request. Never discard it just
        # because the selected text layout has no media frame.
        layout = "image-right"
        layout_fallback_reason = "image_requires_media_layout"
    result["layout"] = layout
    result["background"] = theme["background"]
    font = theme["fontFamily"]
    foreground = theme["foreground"]
    muted = theme["muted"]
    accent = theme["accent"]
    title = str(spec.get("title") or "")
    subtitle = str(spec.get("subtitle") or "")
    body = _body_text(spec)
    bullets = _bullet_text(spec.get("bullets"))
    sections = [item for item in (spec.get("sections") or []) if isinstance(item, dict)]
    columns = [item for item in (spec.get("columns") or []) if isinstance(item, dict)]
    elements: list[dict[str, Any]] = []

    if layout == "title":
        elements.append(_shape_element("accent", [390, 152, 180, 6], fill=accent))
        if title:
            elements.append(_text_element("title", title, [90, 180, 780, 100], font_size=46, color=foreground, font_name=font, bold=True, alignment="Center", vertical="Middle"))
        if subtitle:
            elements.append(_text_element("subtitle", subtitle, [150, 295, 660, 54], font_size=22, color=muted, font_name=font, alignment="Center", vertical="Middle"))
    else:
        if title:
            elements.append(_text_element("title", title, [64, 40, 832, 58], font_size=32, color=foreground, font_name=font, bold=True, vertical="Middle"))
        elements.append(_shape_element("accent", [64, 106, 72, 5], fill=accent))
        content_top = 132
        content_height = 334

        if layout in {"image-left", "image-right"} and image:
            image_left = 64 if layout == "image-left" else 536
            text_left = 536 if layout == "image-left" else 64
            elements.append(_image_element(image, [image_left, content_top, 360, content_height]))
            text = _media_text(spec)
            if text:
                elements.append(_text_element("body", text, [text_left, content_top + 10, 360, content_height - 20], font_size=21, color=foreground, font_name=font, vertical="Middle"))
            caption = str(image.get("caption") or "")
            if caption:
                elements.append(_text_element("image-caption", caption, [image_left, 474, 360, 24], font_size=12, color=muted, font_name=font, alignment="Center"))
        elif layout == "two-column":
            source = columns or sections[:2]
            if not source:
                source = [{"heading": subtitle or "", "body": body}, {"heading": "", "bullets": spec.get("bullets") or []}]
            for index, column in enumerate(source[:2]):
                left = 64 + index * 424
                elements.append(_shape_element(f"column-{index + 1}-panel", [left, content_top, 400, content_height], fill="#FFFFFF", geometry="RoundRectangle", line="#E2E8F0"))
                heading = str(column.get("heading") or column.get("title") or "")
                if heading:
                    elements.append(_text_element(f"column-{index + 1}-title", heading, [left + 24, content_top + 22, 352, 42], font_size=22, color=accent, font_name=font, bold=True))
                text = _section_text(column)
                if text:
                    elements.append(_text_element(f"column-{index + 1}-body", text, [left + 24, content_top + 78, 352, 226], font_size=18, color=foreground, font_name=font))
        elif layout == "section-grid" and sections:
            count = min(6, len(sections))
            column_count = 2 if count <= 4 else 3
            row_count = math.ceil(count / column_count)
            gap = 18
            width = (832 - gap * (column_count - 1)) / column_count
            height = (content_height - gap * (row_count - 1)) / row_count
            for index, section in enumerate(sections[:count]):
                row, column = divmod(index, column_count)
                left = 64 + column * (width + gap)
                top = content_top + row * (height + gap)
                elements.append(_shape_element(f"section-{index + 1}-panel", [left, top, width, height], fill="#FFFFFF", geometry="RoundRectangle", line="#E2E8F0"))
                heading = str(section.get("heading") or section.get("title") or "")
                if heading:
                    elements.append(_text_element(f"section-{index + 1}-title", heading, [left + 20, top + 18, width - 40, 34], font_size=19, color=accent, font_name=font, bold=True))
                text = _section_text(section)
                if text:
                    elements.append(_text_element(f"section-{index + 1}-body", text, [left + 20, top + 60, width - 40, height - 78], font_size=16, color=foreground, font_name=font))
        elif layout == "quote":
            quote = str(spec.get("quote") or body or "")
            if quote:
                elements.append(_text_element("quote", f"“{quote}”", [120, 160, 720, 190], font_size=30, color=foreground, font_name=font, bold=True, alignment="Center", vertical="Middle"))
            attribution = str(spec.get("attribution") or subtitle or "")
            if attribution:
                elements.append(_text_element("attribution", attribution, [240, 366, 480, 38], font_size=17, color=muted, font_name=font, alignment="Center"))
        else:
            text = bullets if layout == "title-bullets" else (body or bullets or subtitle)
            if text:
                elements.append(_text_element("body", text, [84, content_top + 8, 792, content_height - 16], font_size=22 if layout == "title-bullets" else 20, color=foreground, font_name=font, vertical="Middle" if len(text) < 220 else "Top"))

    footer = str(spec.get("footer") or "")
    if footer:
        elements.append(_text_element("footer", footer, [64, 500, 832, 20], font_size=11, color=muted, font_name=font, alignment="Right"))
    elements.extend(deepcopy(spec.get("elements") or []))
    result["elements"] = elements
    metadata = deepcopy(spec.get("metadata") or {})
    metadata["semanticLayoutCompiled"] = True
    if layout_fallback_reason:
        metadata.update({
            "requestedLayout": requested_layout,
            "resolvedLayout": layout,
            "layoutFallbackReason": layout_fallback_reason,
        })
    result["metadata"] = metadata
    return result


__all__ = ["DEFAULT_THEME", "SEMANTIC_LAYOUTS", "compile_slide_spec"]
