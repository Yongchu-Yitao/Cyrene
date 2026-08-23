"""Deterministic OOXML backend for the progressive PowerPoint agent kit.

The live add-in remains the preferred executor.  This module provides the same
semantic contract when the input is a .pptx file and deliberately operates on
the package parts instead of automating a foreground PowerPoint process.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import zipfile
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from cyrene.config import DATA_DIR
from cyrene.office.protocol import READ_ONLY_METHODS

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
}
for _prefix, _uri in NS.items():
    if _prefix not in {"rel", "ct"}:
        ET.register_namespace(_prefix, _uri)

EMU_PER_POINT = 12700


class PptxFileError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
    )


def _xml(data: bytes) -> ET.Element:
    return ET.fromstring(data)


def _bytes(root: ET.Element) -> bytes:
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if namespace not in {NS["rel"], NS["ct"]}:
        return data
    text = data.decode("utf-8")
    declaration = re.search(rf'xmlns:(ns\d+)="{re.escape(namespace)}"', text)
    if declaration is None:
        return data
    prefix = declaration.group(1)
    text = text.replace(f'xmlns:{prefix}="{namespace}"', f'xmlns="{namespace}"')
    text = text.replace(f"<{prefix}:", "<").replace(f"</{prefix}:", "</")
    return text.encode("utf-8")


def _decode_image_base64(raw: str) -> tuple[bytes, str]:
    encoded = raw
    declared_extension = ""
    if raw.startswith("data:"):
        metadata, separator, encoded = raw.partition(",")
        mime_extensions = {
            "data:image/png;base64": "png",
            "data:image/jpeg;base64": "jpg",
            "data:image/gif;base64": "gif",
            "data:image/bmp;base64": "bmp",
            "data:image/tiff;base64": "tiff",
        }
        if not separator or metadata.lower() not in mime_extensions:
            raise PptxFileError("invalid_asset", "imageBase64 must use a supported Base64 image data URL.")
        declared_extension = mime_extensions[metadata.lower()]
    try:
        image_data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PptxFileError("invalid_asset", "imageBase64 is not valid Base64 image data.") from exc
    signatures = (
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff", "jpg"),
        (b"GIF87a", "gif"),
        (b"GIF89a", "gif"),
        (b"BM", "bmp"),
        (b"II*\x00", "tiff"),
        (b"MM\x00*", "tiff"),
    )
    detected_extension = next((extension for signature, extension in signatures if image_data.startswith(signature)), "")
    if not detected_extension or (declared_extension and detected_extension != declared_extension):
        raise PptxFileError("invalid_asset", "imageBase64 content does not match a supported image format.")
    return image_data, detected_extension


def _pt(value: str | int | float | None) -> float:
    return round(int(value or 0) / EMU_PER_POINT, 3)


def _emu(value: Any) -> str:
    return str(round(float(value or 0) * EMU_PER_POINT))


def _column_name(index: int) -> str:
    if index < 1:
        raise ValueError("Spreadsheet column indices are one-based.")
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _rgb(value: str | None) -> tuple[int, int, int] | None:
    normalized = str(value or "").lstrip("#")
    if len(normalized) != 6:
        return None
    try:
        return tuple(int(normalized[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return None


def _contrast_ratio(first: str | None, second: str | None) -> float | None:
    colors = (_rgb(first), _rgb(second))
    if any(color is None for color in colors):
        return None
    luminances = []
    for color in colors:
        channels = [channel / 255 for channel in color or (0, 0, 0)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        luminances.append(0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2])
    high, low = max(luminances), min(luminances)
    return round((high + 0.05) / (low + 0.05), 3)


def _part_target(base_part: str, target: str) -> str:
    base = PurePosixPath(base_part).parent
    parts: list[str] = []
    for item in (base / target).parts:
        if item == "..":
            if parts:
                parts.pop()
        elif item not in {"", ".", "/"}:
            parts.append(item)
    return "/".join(parts)


def _rels_name(part: str) -> str:
    value = PurePosixPath(part)
    return str(value.parent / "_rels" / f"{value.name}.rels")


def _safe_part(value: str) -> str:
    part = str(value or "").lstrip("/")
    if not part or ".." in PurePosixPath(part).parts or part.startswith("_"):
        raise PptxFileError("invalid_ooxml_part", "OOXML part must be a safe package-relative path.")
    return part


class PptxFileEngine:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: dict[str, dict[str, Any]] = {}
        self._undo: dict[str, dict[str, Any]] = {}

    def _path(self, params: dict[str, Any]) -> Path:
        raw = params.get("filePath")
        path = Path(str(raw or "")).expanduser().resolve()
        if path.suffix.lower() != ".pptx" or not path.is_file():
            raise PptxFileError("invalid_presentation", f"A readable .pptx file is required: {path}")
        return path

    def _prepare_output(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raw_output = params.get("outputPath")
        if not raw_output or method in READ_ONLY_METHODS:
            return params
        source = self._path(params)
        output = Path(str(raw_output)).expanduser().resolve()
        if output.suffix.lower() != ".pptx" or output == source:
            raise PptxFileError("invalid_output", "outputPath must be a different .pptx path.")
        key = str(params.get("idempotencyKey") or "")
        output_state = self._state.get(str(output))
        if output_state is None or key not in output_state["idempotency"]:
            source_state = self._sync_state(source)
            expected = params.get("expectedRevision")
            if expected != source_state["revision"]:
                raise PptxFileError("revision_conflict", f"Expected revision {expected}; current revision is {source_state['revision']}.")
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output)
            self._state[str(output)] = {
                "revision": source_state["revision"],
                "digest": source_state["digest"],
                "identity": _file_identity(output),
                "idempotency": {},
                "slideRenders": {},
            }
        prepared = dict(params)
        prepared["filePath"] = str(output)
        prepared.pop("outputPath", None)
        prepared.pop("output_path", None)
        return prepared

    def _sync_state(self, path: Path) -> dict[str, Any]:
        key = str(path)
        identity = _file_identity(path)
        state = self._state.get(key)
        if state is not None and state.get("identity") == identity:
            return state
        digest = _digest(path)
        if state is None:
            state = {
                "revision": 0,
                "digest": digest,
                "identity": identity,
                "idempotency": {},
                "slideRenders": {},
            }
            self._state[key] = state
            return state
        if state["digest"] != digest:
            state["revision"] += 1
            state["digest"] = digest
            state["idempotency"].clear()
        state["identity"] = identity
        return state

    @staticmethod
    def _record_state_file(state: dict[str, Any], path: Path) -> None:
        state["digest"] = _digest(path)
        state["identity"] = _file_identity(path)

    def _package(self, path: Path) -> dict[str, bytes]:
        try:
            with zipfile.ZipFile(path) as archive:
                if "ppt/presentation.xml" not in archive.namelist():
                    raise PptxFileError("invalid_presentation", "The file is not a PowerPoint OOXML package.")
                return {name: archive.read(name) for name in archive.namelist()}
        except (zipfile.BadZipFile, OSError) as exc:
            raise PptxFileError("invalid_presentation", str(exc)) from exc

    def _package_base64(self, value: str) -> dict[str, bytes]:
        try:
            raw = base64.b64decode(str(value).split(",", 1)[-1], validate=True)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if "ppt/presentation.xml" not in archive.namelist():
                    raise PptxFileError("invalid_source_presentation", "The Base64 payload is not a PowerPoint OOXML package.")
                return {name: archive.read(name) for name in archive.namelist()}
        except (ValueError, zipfile.BadZipFile) as exc:
            raise PptxFileError("invalid_source_presentation", f"Invalid presentationBase64 payload: {exc}") from exc

    def _write(self, path: Path, package: dict[str, bytes]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, raw_tmp = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".pptx", dir=path.parent)
        os.close(handle)
        temp = Path(raw_tmp)
        try:
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in package.items():
                    archive.writestr(name, data)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def _slides(self, package: dict[str, bytes]) -> list[dict[str, Any]]:
        presentation = _xml(package["ppt/presentation.xml"])
        rels = _xml(package["ppt/_rels/presentation.xml.rels"])
        targets = {rel.get("Id"): rel.get("Target", "") for rel in rels}
        result = []
        for index, item in enumerate(presentation.findall("./p:sldIdLst/p:sldId", NS)):
            rel_id = item.get(f"{{{NS['r']}}}id", "")
            part = _part_target("ppt/presentation.xml", targets.get(rel_id, ""))
            result.append({"id": item.get("id", ""), "index": index, "relId": rel_id, "part": part})
        return result

    def _select_slide(self, package: dict[str, bytes], params: dict[str, Any]) -> dict[str, Any]:
        slides = self._slides(package)
        requested_id = str(params.get("slideId") or "")
        requested_index = params.get("slideIndex")
        if requested_id:
            for slide in slides:
                if slide["id"] == requested_id or slide["part"] == requested_id:
                    return slide
            raise PptxFileError("slide_not_found", f"Slide {requested_id!r} was not found.")
        if isinstance(requested_index, int) and 0 <= requested_index < len(slides):
            return slides[requested_index]
        if slides:
            return slides[0]
        raise PptxFileError("slide_not_found", "The presentation has no slides.")

    def _shape_info(self, element: ET.Element, index: int) -> dict[str, Any]:
        nv = element.find(".//p:cNvPr", NS)
        shape_id = str(nv.get("id") if nv is not None else index)
        name = str(nv.get("name") if nv is not None else "")
        xfrm = element.find("./p:spPr/a:xfrm", NS)
        if xfrm is None:
            xfrm = element.find("./p:xfrm", NS)
        if xfrm is None:
            xfrm = element.find("./p:grpSpPr/a:xfrm", NS)
        off = xfrm.find("a:off", NS) if xfrm is not None else None
        ext = xfrm.find("a:ext", NS) if xfrm is not None else None
        text = "".join(node.text or "" for node in element.findall(".//a:t", NS))
        fill_node = element.find("./p:spPr/a:solidFill/a:srgbClr", NS)
        font_node = element.find(".//a:rPr/a:solidFill/a:srgbClr", NS)
        if font_node is None:
            font_node = element.find(".//a:defRPr/a:solidFill/a:srgbClr", NS)
        return {
            "id": shape_id,
            "ref": name[7:] if name.startswith("cyrene:") else None,
            "name": name,
            "type": element.tag.rsplit("}", 1)[-1],
            "x": _pt(off.get("x") if off is not None else 0),
            "y": _pt(off.get("y") if off is not None else 0),
            "width": _pt(ext.get("cx") if ext is not None else 0),
            "height": _pt(ext.get("cy") if ext is not None else 0),
            "text": text,
            "fillColor": fill_node.get("val") if fill_node is not None else None,
            "fontColor": font_node.get("val") if font_node is not None else None,
            "zOrder": index,
        }

    def _shape_elements(self, root: ET.Element) -> list[ET.Element]:
        tree = root.find("./p:cSld/p:spTree", NS)
        if tree is None:
            return []
        return [item for item in list(tree) if item.tag.rsplit("}", 1)[-1] in {"sp", "pic", "graphicFrame", "cxnSp", "grpSp"}]

    def _inspect_slide(self, package: dict[str, bytes], slide: dict[str, Any]) -> dict[str, Any]:
        root = _xml(package[slide["part"]])
        shapes = [self._shape_info(item, index) for index, item in enumerate(self._shape_elements(root), 1)]
        notes = None
        rels_name = _rels_name(slide["part"])
        if rels_name in package:
            rels = _xml(package[rels_name])
            relation = next((item for item in rels if item.get("Type", "").endswith("/notesSlide")), None)
            if relation is not None:
                notes_part = _part_target(slide["part"], relation.get("Target", ""))
                if notes_part in package:
                    notes_root = _xml(package[notes_part])
                    body = next((item for item in notes_root.findall("./p:cSld/p:spTree/p:sp", NS) if item.find("./p:nvSpPr/p:nvPr/p:ph[@type='body']", NS) is not None), None)
                    if body is not None:
                        notes = "".join(node.text or "" for node in body.findall(".//a:t", NS))
        return {**slide, "shapes": shapes, "notes": notes}

    def context(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        state = self._sync_state(path)
        package = self._package(path)
        slides = self._slides(package)
        presentation = _xml(package["ppt/presentation.xml"])
        size = presentation.find("./p:sldSz", NS)
        page_size = {
            "width": _pt(size.get("cx") if size is not None else _emu(720)),
            "height": _pt(size.get("cy") if size is not None else _emu(405)),
        }
        return {
            "status": "success",
            "mode": "file",
            "sessionId": None,
            "documentId": hashlib.sha256(str(path).encode()).hexdigest()[:24],
            "filePath": str(path),
            "revision": state["revision"],
            "selectedSlides": [],
            "selectedShapes": [],
            "slideCount": len(slides),
            "pageSize": page_size,
            "capabilities": {
                "shapes": True, "charts": True, "tables": True,
                "slideMaster": True, "ooxml": True,
                "render": bool(shutil.which("soffice")),
                "importSlides": True,
                "notes": True,
                "escapeOfficeJs": False,
                "progressiveCommit": False,
                "chartModes": {"visual": True, "nativeEditable": True, "directOfficeJs": False},
                "masterOperations": {"inspect": True, "applyLayout": True, "editShapes": True},
                "notesOperations": {"read": True, "edit": True},
            },
        }

    def inspect(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        state = self._sync_state(path)
        package = self._package(path)
        scope = str(params.get("scope") or "slide")
        slides = self._slides(package)
        payload: dict[str, Any] = {"status": "success", "mode": "file", "revision": state["revision"], "filePath": str(path)}
        if scope == "presentation":
            payload["slides"] = [{**slide, "shapeCount": len(self._inspect_slide(package, slide)["shapes"])} for slide in slides]
        elif scope == "selection":
            payload.update({"selectedSlides": [], "selectedShapes": []})
        else:
            payload["slide"] = self._inspect_slide(package, self._select_slide(package, params))
        return payload

    def get_slide(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        state = self._sync_state(path)
        package = self._package(path)
        slide = self._inspect_slide(package, self._select_slide(package, params))
        if params.get("includeText") is False:
            for shape in slide["shapes"]:
                shape.pop("text", None)
        if params.get("includeNotes") is False:
            slide.pop("notes", None)
        return {"status": "success", "mode": "file", "revision": state["revision"], "filePath": str(path), "slide": slide}

    def get_shape(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self.get_slide(params)
        requested = str(params.get("shapeRef") or "")
        if not requested:
            raise PptxFileError("shape_ref_required", "shapeRef is required.")
        shape = next((item for item in result["slide"]["shapes"] if requested in {item["id"], item["name"], item["ref"]}), None)
        if shape is None:
            raise PptxFileError("shape_not_found", f"Shape {requested!r} was not found.")
        return {"status": "success", "mode": "file", "revision": result["revision"], "filePath": result["filePath"], "slideId": result["slide"]["id"], "shape": shape}

    def read_text(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self.get_slide(params)
        text = [{key: shape.get(key) for key in ("id", "ref", "name", "text")} for shape in result["slide"]["shapes"] if shape.get("text")]
        return {"status": "success", "mode": "file", "revision": result["revision"], "filePath": result["filePath"], "slideId": result["slide"]["id"], "text": text, "notes": result["slide"].get("notes")}

    def master_and_theme(self, params: dict[str, Any], *, theme: bool) -> dict[str, Any]:
        path = self._path(params)
        state = self._sync_state(path)
        package = self._package(path)
        if theme:
            themes = []
            for part, data in package.items():
                if not part.startswith("ppt/theme/") or not part.endswith(".xml"):
                    continue
                root = _xml(data)
                scheme = root.find(".//a:clrScheme", NS)
                colors: dict[str, str] = {}
                if scheme is not None:
                    for item in list(scheme):
                        color = next(iter(list(item)), None)
                        if color is not None:
                            colors[item.tag.rsplit("}", 1)[-1]] = str(color.get("val") or color.get("lastClr") or "")
                themes.append({"part": part, "name": root.get("name", ""), "colors": colors})
            return {"status": "success", "mode": "file", "revision": state["revision"], "themes": themes}
        masters = []
        for part, data in package.items():
            if not part.startswith("ppt/slideMasters/slideMaster") or not part.endswith(".xml"):
                continue
            root = _xml(data)
            common = root.find("./p:cSld", NS)
            rels_name = _rels_name(part)
            layouts = []
            if rels_name in package:
                rels = _xml(package[rels_name])
                layouts = [_part_target(part, rel.get("Target", "")) for rel in rels if rel.get("Type", "").endswith("/slideLayout")]
            master_shapes = [self._shape_info(item, index) for index, item in enumerate(self._shape_elements(root), 1)]
            layout_details = []
            for layout_part in layouts:
                if layout_part not in package:
                    continue
                layout_root = _xml(package[layout_part])
                layout_common = layout_root.find("./p:cSld", NS)
                layout_details.append({
                    "part": layout_part,
                    "name": layout_common.get("name", "") if layout_common is not None else "",
                    "type": layout_root.get("type", ""),
                    "shapes": [self._shape_info(item, index) for index, item in enumerate(self._shape_elements(layout_root), 1)],
                })
            masters.append({
                "part": part,
                "name": common.get("name", "") if common is not None else "",
                "shapes": master_shapes,
                "layouts": layouts,
                "layoutDetails": layout_details,
            })
        return {
            "status": "success", "mode": "file", "revision": state["revision"], "masters": masters,
            "capabilities": {"read": True, "editViaTypedOperations": True, "applyLayout": True, "editViaOoxml": True},
        }

    def _mutation(self, path: Path, params: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
        state = self._sync_state(path)
        key = str(params.get("idempotencyKey") or "")
        if not key:
            raise PptxFileError("idempotency_required", "idempotencyKey is required for file mutations.")
        replay = state["idempotency"].get(key)
        if replay is not None:
            return state, key, replay
        expected = params.get("expectedRevision")
        if expected != state["revision"]:
            raise PptxFileError("revision_conflict", f"Expected revision {expected}; current revision is {state['revision']}.")
        return state, key, None

    def _snapshot(self, path: Path, state: dict[str, Any]) -> str:
        token = uuid.uuid4().hex
        directory = DATA_DIR / "office_gateway" / "undo"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{token}.pptx"
        shutil.copy2(path, target)
        self._undo[token] = {"path": str(path), "snapshot": str(target), "revisionAfter": state["revision"] + 1}
        return token

    def _shape_map(self, root: ET.Element) -> dict[str, ET.Element]:
        result: dict[str, ET.Element] = {}
        for index, element in enumerate(self._shape_elements(root), 1):
            info = self._shape_info(element, index)
            for key in (info["id"], info["name"], info["ref"]):
                if key:
                    result[str(key)] = element
        return result

    def _xfrm(self, element: ET.Element) -> ET.Element:
        xfrm = element.find("./p:spPr/a:xfrm", NS)
        if xfrm is None:
            xfrm = element.find("./p:xfrm", NS)
        if xfrm is None:
            xfrm = element.find("./p:grpSpPr/a:xfrm", NS)
        if xfrm is None:
            property_tag = "grpSpPr" if element.tag.rsplit("}", 1)[-1] == "grpSp" else "spPr"
            properties = element.find(f"./p:{property_tag}", NS)
            if properties is None:
                properties = ET.SubElement(element, f"{{{NS['p']}}}{property_tag}")
            xfrm = ET.SubElement(properties, f"{{{NS['a']}}}xfrm")
        if xfrm.find("a:off", NS) is None:
            ET.SubElement(xfrm, f"{{{NS['a']}}}off", {"x": "0", "y": "0"})
        if xfrm.find("a:ext", NS) is None:
            ET.SubElement(xfrm, f"{{{NS['a']}}}ext", {"cx": "0", "cy": "0"})
        return xfrm

    def _set_geometry(self, element: ET.Element, op: dict[str, Any]) -> None:
        xfrm = self._xfrm(element)
        off = xfrm.find("a:off", NS)
        ext = xfrm.find("a:ext", NS)
        for source, target, node in (("x", "x", off), ("y", "y", off), ("left", "x", off), ("top", "y", off), ("width", "cx", ext), ("height", "cy", ext)):
            if source in op and node is not None:
                node.set(target, _emu(op[source]))

    def _set_slide_background(self, root: ET.Element, color: str) -> None:
        normalized = str(color or "").lstrip("#")
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", normalized):
            raise PptxFileError("invalid_background", "SlideSpec background must be a six-digit hex color.")
        common = root.find("./p:cSld", NS)
        if common is None:
            raise PptxFileError("invalid_slide", "Slide common data is missing.")
        old = common.find("./p:bg", NS)
        if old is not None:
            common.remove(old)
        background = ET.Element(f"{{{NS['p']}}}bg")
        properties = ET.SubElement(background, f"{{{NS['p']}}}bgPr")
        fill = ET.SubElement(properties, f"{{{NS['a']}}}solidFill")
        ET.SubElement(fill, f"{{{NS['a']}}}srgbClr", {"val": normalized.upper()})
        ET.SubElement(properties, f"{{{NS['a']}}}effectLst")
        common.insert(0, background)

    def _new_shape(self, tree: ET.Element, op: dict[str, Any]) -> ET.Element:
        used = [int(item.get("id", "0")) for item in tree.findall(".//p:cNvPr", NS)]
        shape_id = max(used or [1]) + 1
        ref = str(op.get("ref") or f"shape-{shape_id}")
        tag = f"{{{NS['p']}}}sp"
        shape = ET.SubElement(tree, tag)
        nv = ET.SubElement(shape, f"{{{NS['p']}}}nvSpPr")
        ET.SubElement(nv, f"{{{NS['p']}}}cNvPr", {"id": str(shape_id), "name": f"cyrene:{ref}"})
        ET.SubElement(nv, f"{{{NS['p']}}}cNvSpPr", {"txBox": "1" if op.get("op") in {"add_textbox", "update_text"} else "0"})
        ET.SubElement(nv, f"{{{NS['p']}}}nvPr")
        sp_pr = ET.SubElement(shape, f"{{{NS['p']}}}spPr")
        xfrm = ET.SubElement(sp_pr, f"{{{NS['a']}}}xfrm")
        ET.SubElement(xfrm, f"{{{NS['a']}}}off", {"x": _emu(op.get("x", 60)), "y": _emu(op.get("y", 60))})
        ET.SubElement(xfrm, f"{{{NS['a']}}}ext", {"cx": _emu(op.get("width", 240)), "cy": _emu(op.get("height", 60))})
        geometry = ET.SubElement(sp_pr, f"{{{NS['a']}}}prstGeom", {"prst": str(op.get("geometry") or "rect").lower()})
        ET.SubElement(geometry, f"{{{NS['a']}}}avLst")
        body = ET.SubElement(shape, f"{{{NS['p']}}}txBody")
        ET.SubElement(body, f"{{{NS['a']}}}bodyPr", {"wrap": "square"})
        ET.SubElement(body, f"{{{NS['a']}}}lstStyle")
        paragraph = ET.SubElement(body, f"{{{NS['a']}}}p")
        run = ET.SubElement(paragraph, f"{{{NS['a']}}}r")
        ET.SubElement(run, f"{{{NS['a']}}}rPr", {"lang": "zh-CN"})
        ET.SubElement(run, f"{{{NS['a']}}}t").text = str(op.get("text") or "")
        ET.SubElement(paragraph, f"{{{NS['a']}}}endParaRPr", {"lang": "zh-CN"})
        return shape

    def _new_picture(
        self,
        package: dict[str, bytes],
        slide_part: str,
        tree: ET.Element,
        op: dict[str, Any],
    ) -> ET.Element:
        image_path = str(op.get("imagePath") or "")
        if image_path:
            source = Path(image_path).expanduser().resolve()
            if not source.is_file():
                raise PptxFileError("invalid_asset", f"Image does not exist: {source}")
            image_data = source.read_bytes()
            extension = source.suffix.lower().lstrip(".")
        else:
            raw = str(op.get("imageBase64") or "")
            if not raw:
                raise PptxFileError("invalid_asset", "insert_image requires imagePath or imageBase64.")
            image_data, extension = _decode_image_base64(raw)
        if extension == "jpeg":
            extension = "jpg"
        if extension not in {"png", "jpg", "gif", "bmp", "tif", "tiff"}:
            raise PptxFileError("invalid_asset", f"Unsupported PowerPoint image format: {extension}")
        media_numbers = []
        for name in package:
            if name.startswith("ppt/media/image"):
                stem = PurePosixPath(name).stem.removeprefix("image")
                if stem.isdigit():
                    media_numbers.append(int(stem))
        media_name = f"ppt/media/image{max(media_numbers or [0]) + 1}.{extension}"
        package[media_name] = image_data

        rels_name = _rels_name(slide_part)
        rels = _xml(package.get(rels_name, b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'))
        rel_numbers = [int(rel.get("Id", "rId0")[3:]) for rel in rels if rel.get("Id", "").startswith("rId") and rel.get("Id", "rId0")[3:].isdigit()]
        rel_id = f"rId{max(rel_numbers or [0]) + 1}"
        ET.SubElement(rels, f"{{{NS['rel']}}}Relationship", {"Id": rel_id, "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", "Target": f"../media/{PurePosixPath(media_name).name}"})
        package[rels_name] = _bytes(rels)

        content_types = _xml(package["[Content_Types].xml"])
        if not any(item.get("Extension", "").lower() == extension for item in content_types.findall("./ct:Default", NS)):
            mime = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp", "tif": "image/tiff", "tiff": "image/tiff"}[extension]
            ET.SubElement(content_types, f"{{{NS['ct']}}}Default", {"Extension": extension, "ContentType": mime})
        package["[Content_Types].xml"] = _bytes(content_types)

        used = [int(item.get("id", "0")) for item in tree.findall(".//p:cNvPr", NS)]
        shape_id = max(used or [1]) + 1
        ref = str(op.get("ref") or f"image-{shape_id}")
        picture = ET.SubElement(tree, f"{{{NS['p']}}}pic")
        nv = ET.SubElement(picture, f"{{{NS['p']}}}nvPicPr")
        ET.SubElement(nv, f"{{{NS['p']}}}cNvPr", {"id": str(shape_id), "name": f"cyrene:{ref}"})
        ET.SubElement(nv, f"{{{NS['p']}}}cNvPicPr")
        ET.SubElement(nv, f"{{{NS['p']}}}nvPr")
        fill = ET.SubElement(picture, f"{{{NS['p']}}}blipFill")
        ET.SubElement(fill, f"{{{NS['a']}}}blip", {f"{{{NS['r']}}}embed": rel_id})
        stretch = ET.SubElement(fill, f"{{{NS['a']}}}stretch")
        ET.SubElement(stretch, f"{{{NS['a']}}}fillRect")
        sp_pr = ET.SubElement(picture, f"{{{NS['p']}}}spPr")
        xfrm = ET.SubElement(sp_pr, f"{{{NS['a']}}}xfrm")
        ET.SubElement(xfrm, f"{{{NS['a']}}}off", {"x": _emu(op.get("x", 60)), "y": _emu(op.get("y", 60))})
        ET.SubElement(xfrm, f"{{{NS['a']}}}ext", {"cx": _emu(op.get("width", 240)), "cy": _emu(op.get("height", 180))})
        geometry = ET.SubElement(sp_pr, f"{{{NS['a']}}}prstGeom", {"prst": "rect"})
        ET.SubElement(geometry, f"{{{NS['a']}}}avLst")
        return picture

    def _chart_workbook(self, categories: list[str], series: list[dict[str, Any]]) -> bytes:
        spreadsheet = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        office_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        sheet = ET.Element(f"{{{spreadsheet}}}worksheet")
        sheet_data = ET.SubElement(sheet, f"{{{spreadsheet}}}sheetData")
        header = ET.SubElement(sheet_data, f"{{{spreadsheet}}}row", {"r": "1"})
        for column, value in enumerate(["Category", *[str(item.get("name") or f"Series {index + 1}") for index, item in enumerate(series)]], 1):
            cell = ET.SubElement(header, f"{{{spreadsheet}}}c", {"r": f"{_column_name(column)}1", "t": "inlineStr"})
            inline = ET.SubElement(cell, f"{{{spreadsheet}}}is")
            ET.SubElement(inline, f"{{{spreadsheet}}}t").text = value
        for row_index, category in enumerate(categories, 2):
            row = ET.SubElement(sheet_data, f"{{{spreadsheet}}}row", {"r": str(row_index)})
            cell = ET.SubElement(row, f"{{{spreadsheet}}}c", {"r": f"A{row_index}", "t": "inlineStr"})
            inline = ET.SubElement(cell, f"{{{spreadsheet}}}is")
            ET.SubElement(inline, f"{{{spreadsheet}}}t").text = category
            for series_index, item in enumerate(series, 2):
                number = ET.SubElement(row, f"{{{spreadsheet}}}c", {"r": f"{_column_name(series_index)}{row_index}"})
                values = item.get("values") or []
                ET.SubElement(number, f"{{{spreadsheet}}}v").text = str(values[row_index - 2] if row_index - 2 < len(values) else 0)

        workbook = ET.Element(f"{{{spreadsheet}}}workbook")
        sheets = ET.SubElement(workbook, f"{{{spreadsheet}}}sheets")
        ET.SubElement(sheets, f"{{{spreadsheet}}}sheet", {"name": "Sheet1", "sheetId": "1", f"{{{office_rel}}}id": "rId1"})
        content_types = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'''
        root_rels = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
        workbook_rels = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'''
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", root_rels)
            archive.writestr("xl/workbook.xml", _bytes(workbook))
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            archive.writestr("xl/worksheets/sheet1.xml", _bytes(sheet))
        return output.getvalue()

    @staticmethod
    def _populate_chart_series(
        chart_node: ET.Element, categories: list[str], series: list[dict[str, Any]], kind: str,
    ) -> None:
        category_formula = f"Sheet1!$A$2:$A${len(categories) + 1}"
        for series_index, item in enumerate(series):
            ser = ET.SubElement(chart_node, f"{{{NS['c']}}}ser")
            ET.SubElement(ser, f"{{{NS['c']}}}idx", {"val": str(series_index)})
            ET.SubElement(ser, f"{{{NS['c']}}}order", {"val": str(series_index)})
            tx_ref = ET.SubElement(ET.SubElement(ser, f"{{{NS['c']}}}tx"), f"{{{NS['c']}}}strRef")
            column_name = _column_name(2 + series_index)
            ET.SubElement(tx_ref, f"{{{NS['c']}}}f").text = f"Sheet1!${column_name}$1"
            tx_cache = ET.SubElement(tx_ref, f"{{{NS['c']}}}strCache")
            ET.SubElement(tx_cache, f"{{{NS['c']}}}ptCount", {"val": "1"})
            point = ET.SubElement(tx_cache, f"{{{NS['c']}}}pt", {"idx": "0"})
            ET.SubElement(point, f"{{{NS['c']}}}v").text = str(item.get("name") or f"Series {series_index + 1}")
            category_ref = ET.SubElement(ET.SubElement(ser, f"{{{NS['c']}}}cat"), f"{{{NS['c']}}}strRef")
            ET.SubElement(category_ref, f"{{{NS['c']}}}f").text = category_formula
            category_cache = ET.SubElement(category_ref, f"{{{NS['c']}}}strCache")
            ET.SubElement(category_cache, f"{{{NS['c']}}}ptCount", {"val": str(len(categories))})
            for index, value in enumerate(categories):
                point = ET.SubElement(category_cache, f"{{{NS['c']}}}pt", {"idx": str(index)})
                ET.SubElement(point, f"{{{NS['c']}}}v").text = value
            value_ref = ET.SubElement(ET.SubElement(ser, f"{{{NS['c']}}}val"), f"{{{NS['c']}}}numRef")
            ET.SubElement(value_ref, f"{{{NS['c']}}}f").text = f"Sheet1!${column_name}$2:${column_name}${len(categories) + 1}"
            cache = ET.SubElement(value_ref, f"{{{NS['c']}}}numCache")
            ET.SubElement(cache, f"{{{NS['c']}}}formatCode").text = "General"
            ET.SubElement(cache, f"{{{NS['c']}}}ptCount", {"val": str(len(categories))})
            for index, value in enumerate(item.get("values") or []):
                point = ET.SubElement(cache, f"{{{NS['c']}}}pt", {"idx": str(index)})
                ET.SubElement(point, f"{{{NS['c']}}}v").text = str(value)
            if kind == "line":
                ET.SubElement(ET.SubElement(ser, f"{{{NS['c']}}}marker"), f"{{{NS['c']}}}symbol", {"val": "circle"})

    @staticmethod
    def _populate_chart_axes(plot: ET.Element, chart_node: ET.Element, number: int) -> None:
        category_axis_id, value_axis_id = 48650112 + number * 2, 48672768 + number * 2
        ET.SubElement(chart_node, f"{{{NS['c']}}}axId", {"val": str(category_axis_id)})
        ET.SubElement(chart_node, f"{{{NS['c']}}}axId", {"val": str(value_axis_id)})
        cat_axis = ET.SubElement(plot, f"{{{NS['c']}}}catAx")
        ET.SubElement(cat_axis, f"{{{NS['c']}}}axId", {"val": str(category_axis_id)})
        ET.SubElement(ET.SubElement(cat_axis, f"{{{NS['c']}}}scaling"), f"{{{NS['c']}}}orientation", {"val": "minMax"})
        ET.SubElement(cat_axis, f"{{{NS['c']}}}axPos", {"val": "b"})
        ET.SubElement(cat_axis, f"{{{NS['c']}}}crossAx", {"val": str(value_axis_id)})
        ET.SubElement(cat_axis, f"{{{NS['c']}}}crosses", {"val": "autoZero"})
        value_axis = ET.SubElement(plot, f"{{{NS['c']}}}valAx")
        ET.SubElement(value_axis, f"{{{NS['c']}}}axId", {"val": str(value_axis_id)})
        ET.SubElement(ET.SubElement(value_axis, f"{{{NS['c']}}}scaling"), f"{{{NS['c']}}}orientation", {"val": "minMax"})
        ET.SubElement(value_axis, f"{{{NS['c']}}}axPos", {"val": "l"})
        ET.SubElement(value_axis, f"{{{NS['c']}}}crossAx", {"val": str(category_axis_id)})
        ET.SubElement(value_axis, f"{{{NS['c']}}}crosses", {"val": "autoZero"})

    def _register_chart_parts(
        self, package: dict[str, bytes], slide_part: str, chart_part: str,
        workbook_part: str, chart_space: ET.Element,
    ) -> str:
        package[chart_part] = _bytes(chart_space)
        chart_rels = ET.Element(f"{{{NS['rel']}}}Relationships")
        ET.SubElement(chart_rels, f"{{{NS['rel']}}}Relationship", {"Id": "rId1", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package", "Target": f"../embeddings/{PurePosixPath(workbook_part).name}"})
        package[_rels_name(chart_part)] = _bytes(chart_rels)
        slide_rels_name = _rels_name(slide_part)
        slide_rels = _xml(package.get(slide_rels_name, b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'))
        numbers = [int(rel.get("Id", "rId0")[3:]) for rel in slide_rels if rel.get("Id", "").startswith("rId") and rel.get("Id", "rId0")[3:].isdigit()]
        slide_rel_id = f"rId{max(numbers or [0]) + 1}"
        ET.SubElement(slide_rels, f"{{{NS['rel']}}}Relationship", {"Id": slide_rel_id, "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart", "Target": f"../charts/{PurePosixPath(chart_part).name}"})
        package[slide_rels_name] = _bytes(slide_rels)
        content_types = _xml(package["[Content_Types].xml"])
        ET.SubElement(content_types, f"{{{NS['ct']}}}Override", {"PartName": f"/{chart_part}", "ContentType": "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"})
        if not any(item.get("Extension") == "xlsx" for item in content_types.findall("./ct:Default", NS)):
            ET.SubElement(content_types, f"{{{NS['ct']}}}Default", {"Extension": "xlsx", "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        package["[Content_Types].xml"] = _bytes(content_types)
        return slide_rel_id

    @staticmethod
    def _append_chart_frame(tree: ET.Element, params: dict[str, Any], slide_rel_id: str) -> ET.Element:
        used = [int(item.get("id", "0")) for item in tree.findall(".//p:cNvPr", NS)]
        shape_id = max(used or [1]) + 1
        frame = ET.SubElement(tree, f"{{{NS['p']}}}graphicFrame")
        nv = ET.SubElement(frame, f"{{{NS['p']}}}nvGraphicFramePr")
        ET.SubElement(nv, f"{{{NS['p']}}}cNvPr", {"id": str(shape_id), "name": f"cyrene:{params.get('ref') or 'chart'}"})
        ET.SubElement(nv, f"{{{NS['p']}}}cNvGraphicFramePr")
        ET.SubElement(nv, f"{{{NS['p']}}}nvPr")
        transform = ET.SubElement(frame, f"{{{NS['p']}}}xfrm")
        ET.SubElement(transform, f"{{{NS['a']}}}off", {"x": _emu(params.get("x", 60)), "y": _emu(params.get("y", 100))})
        ET.SubElement(transform, f"{{{NS['a']}}}ext", {"cx": _emu(params.get("width", 420)), "cy": _emu(params.get("height", 260))})
        graphic = ET.SubElement(frame, f"{{{NS['a']}}}graphic")
        data = ET.SubElement(graphic, f"{{{NS['a']}}}graphicData", {"uri": NS["c"]})
        ET.SubElement(data, f"{{{NS['c']}}}chart", {f"{{{NS['r']}}}id": slide_rel_id})
        return frame

    def _new_native_chart(
        self,
        package: dict[str, bytes],
        slide_part: str,
        tree: ET.Element,
        params: dict[str, Any],
    ) -> ET.Element:
        spec = dict(params.get("chartSpec") or {})
        categories = [str(item) for item in spec.get("categories") or []]
        series = [dict(item) for item in spec.get("series") or []]
        if not categories or not series:
            raise PptxFileError("invalid_chart", "Native charts require non-empty categories and series.")
        chart_numbers = [int(PurePosixPath(name).stem.removeprefix("chart")) for name in package if name.startswith("ppt/charts/chart") and name.endswith(".xml") and PurePosixPath(name).stem.removeprefix("chart").isdigit()]
        number = max(chart_numbers or [0]) + 1
        chart_part = f"ppt/charts/chart{number}.xml"
        workbook_part = f"ppt/embeddings/Microsoft_Excel_Worksheet{number}.xlsx"
        package[workbook_part] = self._chart_workbook(categories, series)

        chart_space = ET.Element(f"{{{NS['c']}}}chartSpace")
        ET.SubElement(chart_space, f"{{{NS['c']}}}date1904", {"val": "0"})
        chart = ET.SubElement(chart_space, f"{{{NS['c']}}}chart")
        plot = ET.SubElement(chart, f"{{{NS['c']}}}plotArea")
        ET.SubElement(plot, f"{{{NS['c']}}}layout")
        kind = str(spec.get("type") or "column")
        chart_tag = "lineChart" if kind == "line" else "barChart"
        chart_node = ET.SubElement(plot, f"{{{NS['c']}}}{chart_tag}")
        if kind != "line":
            ET.SubElement(chart_node, f"{{{NS['c']}}}barDir", {"val": "bar" if kind == "bar" else "col"})
            ET.SubElement(chart_node, f"{{{NS['c']}}}grouping", {"val": "clustered"})
        self._populate_chart_series(chart_node, categories, series, kind)
        self._populate_chart_axes(plot, chart_node, number)
        ET.SubElement(chart, f"{{{NS['c']}}}plotVisOnly", {"val": "1"})
        external = ET.SubElement(chart_space, f"{{{NS['c']}}}externalData", {f"{{{NS['r']}}}id": "rId1"})
        ET.SubElement(external, f"{{{NS['c']}}}autoUpdate", {"val": "0"})
        slide_rel_id = self._register_chart_parts(package, slide_part, chart_part, workbook_part, chart_space)
        return self._append_chart_frame(tree, params, slide_rel_id)

    def _set_table_values(self, table: ET.Element, values: list[list[Any]], width_points: float = 420, height_points: float = 180) -> None:
        for child in list(table):
            table.remove(child)
        rows = values or [[""]]
        column_count = max(1, max((len(row) for row in rows), default=1))
        properties = ET.SubElement(table, f"{{{NS['a']}}}tblPr", {"firstRow": "1", "bandRow": "1"})
        ET.SubElement(properties, f"{{{NS['a']}}}tableStyleId").text = "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"
        grid = ET.SubElement(table, f"{{{NS['a']}}}tblGrid")
        for _index in range(column_count):
            ET.SubElement(grid, f"{{{NS['a']}}}gridCol", {"w": _emu(width_points / column_count)})
        for row_index, raw_row in enumerate(rows):
            row = ET.SubElement(table, f"{{{NS['a']}}}tr", {"h": _emu(height_points / max(1, len(rows)))})
            padded = [*raw_row, *([""] * (column_count - len(raw_row)))]
            for value in padded:
                cell = ET.SubElement(row, f"{{{NS['a']}}}tc")
                body = ET.SubElement(cell, f"{{{NS['a']}}}txBody")
                ET.SubElement(body, f"{{{NS['a']}}}bodyPr")
                ET.SubElement(body, f"{{{NS['a']}}}lstStyle")
                paragraph = ET.SubElement(body, f"{{{NS['a']}}}p")
                run = ET.SubElement(paragraph, f"{{{NS['a']}}}r")
                run_props = {"lang": "zh-CN", "sz": "1400"}
                if row_index == 0:
                    run_props["b"] = "1"
                ET.SubElement(run, f"{{{NS['a']}}}rPr", run_props)
                ET.SubElement(run, f"{{{NS['a']}}}t").text = str(value)
                ET.SubElement(paragraph, f"{{{NS['a']}}}endParaRPr", {"lang": "zh-CN"})
                ET.SubElement(cell, f"{{{NS['a']}}}tcPr")

    def _new_table(self, tree: ET.Element, params: dict[str, Any]) -> ET.Element:
        used = [int(item.get("id", "0")) for item in tree.findall(".//p:cNvPr", NS)]
        shape_id = max(used or [1]) + 1
        ref = str(params.get("ref") or "table")
        frame = ET.SubElement(tree, f"{{{NS['p']}}}graphicFrame")
        nv = ET.SubElement(frame, f"{{{NS['p']}}}nvGraphicFramePr")
        ET.SubElement(nv, f"{{{NS['p']}}}cNvPr", {"id": str(shape_id), "name": f"cyrene:{ref}"})
        ET.SubElement(nv, f"{{{NS['p']}}}cNvGraphicFramePr")
        ET.SubElement(nv, f"{{{NS['p']}}}nvPr")
        transform = ET.SubElement(frame, f"{{{NS['p']}}}xfrm")
        ET.SubElement(transform, f"{{{NS['a']}}}off", {"x": _emu(params.get("x", 60)), "y": _emu(params.get("y", 100))})
        ET.SubElement(transform, f"{{{NS['a']}}}ext", {"cx": _emu(params.get("width", 420)), "cy": _emu(params.get("height", 180))})
        graphic = ET.SubElement(frame, f"{{{NS['a']}}}graphic")
        data = ET.SubElement(graphic, f"{{{NS['a']}}}graphicData", {"uri": "http://schemas.openxmlformats.org/drawingml/2006/table"})
        table = ET.SubElement(data, f"{{{NS['a']}}}tbl")
        self._set_table_values(table, [list(row) for row in (params.get("values") or [[""]])], float(params.get("width") or 420), float(params.get("height") or 180))
        return frame

    def edit_table(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        package = self._package(path)
        slide = self._select_slide(package, params)
        root = _xml(package[slide["part"]])
        tree = root.find("./p:cSld/p:spTree", NS)
        if tree is None:
            raise PptxFileError("invalid_slide", "Slide shape tree is missing.")
        target = str(params.get("shapeRef") or "")
        undo = self._snapshot(path, state)
        created: list[str] = []
        changed: list[str] = []
        if target:
            element = self._shape_map(root).get(target)
            table = element.find(".//a:tbl", NS) if element is not None else None
            if element is None or table is None:
                raise PptxFileError("table_not_found", f"Table {target!r} was not found.")
            info = self._shape_info(element, 0)
            self._set_table_values(table, [list(row) for row in (params.get("values") or [[""]])], info["width"] or 420, info["height"] or 180)
            changed.append(target)
        else:
            element = self._new_table(tree, params)
            info = self._shape_info(element, len(self._shape_elements(root)))
            created.append(info["ref"] or info["id"])
        package[slide["part"]] = _bytes(root)
        self._write(path, package)
        state["revision"] += 1
        self._record_state_file(state, path)
        result = {"status": "applied", "mode": "file", "revision": state["revision"], "filePath": str(path), "slideId": slide["id"], "changed": changed, "created": created, "deleted": [], "warnings": [], "undoToken": undo, "renderId": None, "audit": {"action": "edit_table", "rows": len(params.get("values") or [])}}
        state["idempotency"][key] = deepcopy(result)
        return result

    def _shape_part_edit(self, params: dict[str, Any], *, prefixes: tuple[str, ...]) -> dict[str, Any]:
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        part = _safe_part(str(params.get("part") or params.get("masterPart") or params.get("layoutPart") or ""))
        if not part.startswith(prefixes):
            raise PptxFileError("invalid_ooxml_part", f"Expected a part under {prefixes!r}.")
        package = self._package(path)
        if part not in package:
            raise PptxFileError("ooxml_part_not_found", f"Package part {part!r} does not exist.")
        root = _xml(package[part])
        tree = root.find("./p:cSld/p:spTree", NS)
        if tree is None:
            raise PptxFileError("invalid_shape_part", f"Part {part!r} has no shape tree.")
        shape_map = self._shape_map(root)
        changed: list[str] = []
        created: list[str] = []
        deleted: list[str] = []
        operations = list(params.get("operations") or [])
        if not operations:
            raise PptxFileError("invalid_batch", "operations must not be empty.")
        undo = self._snapshot(path, state)
        for operation in operations:
            kind = str(operation.get("op") or "")
            target = str(operation.get("shapeRef") or operation.get("target") or "")
            if kind in {"add_shape", "add_textbox"}:
                element = self._new_shape(tree, operation)
                info = self._shape_info(element, len(self._shape_elements(root)))
                shape_map[info["id"]] = element
                shape_map[info["name"]] = element
                if info["ref"]:
                    shape_map[info["ref"]] = element
                created.append(info["ref"] or info["id"])
                continue
            element = shape_map.get(target)
            if element is None:
                raise PptxFileError("shape_not_found", f"Shape {target!r} was not found in {part}.")
            if kind == "delete_shape":
                tree.remove(element)
                deleted.append(target)
            elif kind == "update_text":
                self._set_text(element, str(operation.get("text") or ""))
                changed.append(target)
            elif kind == "apply_style":
                self._apply_style(element, dict(operation.get("style") or {}))
                changed.append(target)
            elif kind in {"update_shape", "move_shape", "resize_shape"}:
                self._set_geometry(element, operation)
                if "text" in operation:
                    self._set_text(element, str(operation["text"]))
                changed.append(target)
            elif kind == "set_z_order":
                tree.remove(element)
                if str(operation.get("position")) in {"SendToBack", "SendBackward"}:
                    tree.insert(2, element)
                else:
                    tree.append(element)
                changed.append(target)
            else:
                raise PptxFileError("unsupported_operation", f"Unsupported shape-part operation: {kind}")
        package[part] = _bytes(root)
        self._write(path, package)
        state["revision"] += 1
        self._record_state_file(state, path)
        result = {"status": "applied", "mode": "file", "revision": state["revision"], "filePath": str(path), "changed": changed, "created": created, "deleted": deleted, "warnings": [], "undoToken": undo, "renderId": None, "audit": {"action": "edit_shape_part", "part": part}}
        state["idempotency"][key] = deepcopy(result)
        return result

    def _set_text(self, element: ET.Element, text: str) -> None:
        nodes = element.findall(".//a:t", NS)
        if nodes:
            nodes[0].text = text
            for node in nodes[1:]:
                node.text = ""
            return
        body = element.find("./p:txBody", NS)
        if body is None:
            body = ET.SubElement(element, f"{{{NS['p']}}}txBody")
            ET.SubElement(body, f"{{{NS['a']}}}bodyPr")
            ET.SubElement(body, f"{{{NS['a']}}}lstStyle")
        paragraph = ET.SubElement(body, f"{{{NS['a']}}}p")
        run = ET.SubElement(paragraph, f"{{{NS['a']}}}r")
        ET.SubElement(run, f"{{{NS['a']}}}t").text = text

    def _apply_style(self, element: ET.Element, style: dict[str, Any]) -> None:
        if not style:
            return
        sp_pr = element.find("./p:spPr", NS)
        if sp_pr is None:
            sp_pr = ET.SubElement(element, f"{{{NS['p']}}}spPr")

        def set_color(parent: ET.Element, color: Any, transparency: Any = None) -> None:
            fill = parent.find("a:solidFill", NS)
            if color and fill is None:
                for old in list(parent):
                    if old.tag.rsplit("}", 1)[-1] in {"solidFill", "noFill", "gradFill"}:
                        parent.remove(old)
                fill = ET.SubElement(parent, f"{{{NS['a']}}}solidFill")
            if fill is None:
                return
            color_node = fill.find("a:srgbClr", NS)
            if color and color_node is None:
                for old in list(fill):
                    fill.remove(old)
                color_node = ET.SubElement(fill, f"{{{NS['a']}}}srgbClr")
            if color:
                assert color_node is not None
                color_node.set("val", str(color).lstrip("#").upper())
            elif color_node is None:
                color_node = next(iter(fill), None)
            if transparency is not None:
                if color_node is None:
                    return
                alpha = color_node.find("a:alpha", NS)
                if alpha is None:
                    alpha = ET.SubElement(color_node, f"{{{NS['a']}}}alpha")
                alpha.set("val", str(round((1 - float(transparency)) * 100000)))

        fill_color = style.get("fillColor")
        fill_transparency = style.get("fillTransparency")
        if fill_color:
            set_color(sp_pr, fill_color, fill_transparency)
        elif fill_transparency is not None and sp_pr.find("a:solidFill", NS) is not None:
            set_color(sp_pr, None, fill_transparency)

        line_color = style.get("lineColor")
        line_transparency = style.get("lineTransparency")
        line_weight = style.get("lineWeight")
        if line_color or line_transparency is not None or line_weight is not None:
            line = sp_pr.find("a:ln", NS)
            if line is None:
                line = ET.SubElement(sp_pr, f"{{{NS['a']}}}ln")
            if line_weight is not None:
                line.set("w", _emu(float(line_weight)))
            if line_color or line_transparency is not None:
                set_color(line, line_color, line_transparency)

        font_size = style.get("fontSize")
        font_color = style.get("fontColor")
        font_name = style.get("fontName")
        text_properties = element.findall(".//a:rPr", NS) + element.findall(".//a:defRPr", NS) + element.findall(".//a:endParaRPr", NS)
        for rpr in text_properties:
            if font_size is not None:
                rpr.set("sz", str(round(float(font_size) * 100)))
            if isinstance(style.get("bold"), bool):
                rpr.set("b", "1" if style["bold"] else "0")
            if isinstance(style.get("italic"), bool):
                rpr.set("i", "1" if style["italic"] else "0")
            if font_name:
                latin = rpr.find("a:latin", NS)
                if latin is None:
                    latin = ET.SubElement(rpr, f"{{{NS['a']}}}latin")
                latin.set("typeface", str(font_name))
            if font_color:
                set_color(rpr, font_color)

        alignment = style.get("horizontalAlignment")
        if alignment:
            value = {"Left": "l", "Center": "ctr", "Right": "r", "Justify": "just", "Distributed": "dist"}[str(alignment)]
            for paragraph in element.findall(".//a:p", NS):
                properties = paragraph.find("a:pPr", NS)
                if properties is None:
                    properties = ET.Element(f"{{{NS['a']}}}pPr")
                    paragraph.insert(0, properties)
                properties.set("algn", value)

        body = element.find("./p:txBody/a:bodyPr", NS)
        if body is not None:
            vertical = style.get("verticalAlignment")
            if vertical:
                body.set("anchor", {"Top": "t", "Middle": "ctr", "Bottom": "b", "TopCentered": "t", "MiddleCentered": "ctr", "BottomCentered": "b"}[str(vertical)])
                body.set("anchorCtr", "1" if str(vertical).endswith("Centered") else "0")
            if isinstance(style.get("wordWrap"), bool):
                body.set("wrap", "square" if style["wordWrap"] else "none")

    def _chart_png(self, spec: dict[str, Any], width_points: Any, height_points: Any) -> bytes:
        """Render a deterministic visual chart for SlideSpec and visual chart mode."""
        from PIL import Image, ImageDraw

        width = max(320, round(float(width_points or 420) * 2))
        height = max(200, round(float(height_points or 260) * 2))
        categories = [str(item) for item in spec.get("categories") or []]
        series = [dict(item) for item in spec.get("series") or []]
        values = [float(value) for item in series for value in (item.get("values") or [])]
        maximum = max([1.0, *values])
        image = Image.new("RGB", (width, height), str(spec.get("background") or "#FFFFFF"))
        draw = ImageDraw.Draw(image)
        margin = (80, 40, 30, 70)
        plot_width = width - margin[0] - margin[2]
        plot_height = height - margin[1] - margin[3]
        draw.line((margin[0], margin[1] + plot_height, margin[0] + plot_width, margin[1] + plot_height), fill="#94A3B8", width=2)
        palette = ["#2563EB", "#7C3AED", "#059669", "#EA580C", "#DC2626"]
        group_width = plot_width / max(1, len(categories))
        if str(spec.get("type") or "column") == "line":
            for series_index, item in enumerate(series):
                points = []
                for index, value in enumerate(item.get("values") or []):
                    x = margin[0] + (index + 0.5) * group_width
                    y = margin[1] + plot_height - float(value) / maximum * plot_height
                    points.append((x, y))
                if len(points) > 1:
                    draw.line(points, fill=str(item.get("color") or palette[series_index % len(palette)]), width=5)
        else:
            bar_width = max(3, group_width * 0.72 / max(1, len(series)))
            for series_index, item in enumerate(series):
                color = str(item.get("color") or palette[series_index % len(palette)])
                for index, value in enumerate(item.get("values") or []):
                    bar_height = float(value) / maximum * plot_height
                    x = margin[0] + index * group_width + group_width * 0.14 + series_index * bar_width
                    draw.rounded_rectangle((x, margin[1] + plot_height - bar_height, x + bar_width, margin[1] + plot_height), radius=3, fill=color)
        for index, label in enumerate(categories):
            draw.text((margin[0] + (index + 0.5) * group_width, height - 45), label, fill="#475569", anchor="mm")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _create_batch_shape(
        self, package: dict[str, bytes], slide: dict[str, Any], root: ET.Element,
        tree: ET.Element, shape_map: dict[str, ET.Element], op: dict[str, Any], kind: str,
    ) -> str:
        shape_op = {**op, "geometry": "line", "height": op.get("height", 0)} if kind == "add_line" else op
        if kind == "add_chart":
            image = self._chart_png(dict(op.get("chartSpec") or op.get("data") or {}), op.get("width"), op.get("height"))
            shape_op = {**op, "imageBase64": base64.b64encode(image).decode("ascii")}
            element = self._new_picture(package, slide["part"], tree, shape_op)
        elif kind == "add_table":
            element = self._new_table(tree, op)
        else:
            element = self._new_picture(package, slide["part"], tree, op) if kind == "insert_image" else self._new_shape(tree, shape_op)
        info = self._shape_info(element, len(self._shape_elements(root)))
        shape_map[info["id"]] = element
        shape_map[info["ref"]] = element
        if kind != "add_table":
            self._apply_style(element, dict(op.get("style") or {}))
        return str(info["ref"] or info["id"])

    def _create_batch_group(
        self, root: ET.Element, tree: ET.Element, shape_map: dict[str, ET.Element], op: dict[str, Any],
    ) -> str:
        targets = [str(item) for item in (op.get("shapeRefs") or op.get("targets") or [])]
        members = [shape_map.get(item) for item in targets]
        if not targets or any(item is None for item in members):
            raise PptxFileError("shape_not_found", "Every group member must resolve to a shape reference.")
        used = [int(item.get("id", "0")) for item in tree.findall(".//p:cNvPr", NS)]
        group_id = max(used or [1]) + 1
        group_ref = str(op.get("ref") or f"group-{group_id}")
        group = ET.Element(f"{{{NS['p']}}}grpSp")
        nv = ET.SubElement(group, f"{{{NS['p']}}}nvGrpSpPr")
        ET.SubElement(nv, f"{{{NS['p']}}}cNvPr", {"id": str(group_id), "name": f"cyrene:{group_ref}"})
        ET.SubElement(nv, f"{{{NS['p']}}}cNvGrpSpPr")
        ET.SubElement(nv, f"{{{NS['p']}}}nvPr")
        transform = ET.SubElement(ET.SubElement(group, f"{{{NS['p']}}}grpSpPr"), f"{{{NS['a']}}}xfrm")
        bounds = [self._shape_info(member, index) for index, member in enumerate(members, 1) if member is not None]
        left, top = min(item["x"] for item in bounds), min(item["y"] for item in bounds)
        right = max(item["x"] + item["width"] for item in bounds)
        bottom = max(item["y"] + item["height"] for item in bounds)
        offset, extent = {"x": _emu(left), "y": _emu(top)}, {"cx": _emu(right - left), "cy": _emu(bottom - top)}
        for tag, values in (("off", offset), ("ext", extent), ("chOff", offset), ("chExt", extent)):
            ET.SubElement(transform, f"{{{NS['a']}}}{tag}", values)
        insert_at = min(list(tree).index(item) for item in members if item is not None)
        for member in members:
            tree.remove(member)
            group.append(member)
        tree.insert(insert_at, group)
        info = self._shape_info(group, len(self._shape_elements(root)))
        for reference in (info["id"], info["name"], info["ref"]):
            if reference:
                shape_map[str(reference)] = group
        return group_ref

    def _apply_batch_operation(
        self, package: dict[str, bytes], slide: dict[str, Any], root: ET.Element,
        tree: ET.Element, shape_map: dict[str, ET.Element], op: dict[str, Any],
        created: list[str], changed: list[str], deleted: list[str],
    ) -> None:
        kind = {"insert_chart": "add_chart", "insert_table": "add_table"}.get(str(op.get("op") or ""), str(op.get("op") or ""))
        target = str(op.get("shapeRef") or op.get("target") or "")
        if kind == "set_background":
            self._set_slide_background(root, str(op.get("color") or op.get("background") or ""))
            changed.append(slide["id"])
            return
        if kind in {"add_textbox", "add_shape", "add_line", "insert_image", "add_chart", "add_table"}:
            created.append(self._create_batch_shape(package, slide, root, tree, shape_map, op, kind))
            return
        if kind == "group_shapes":
            created.append(self._create_batch_group(root, tree, shape_map, op))
            return
        element = shape_map.get(target)
        if element is None:
            raise PptxFileError("shape_not_found", f"Shape {target!r} was not found.")
        if kind == "delete_shape":
            tree.remove(element)
            deleted.append(target)
            return
        if kind in {"update_shape", "move_shape", "resize_shape"}:
            self._set_geometry(element, op)
            if "text" in op:
                self._set_text(element, str(op["text"]))
            assigned_name = f"cyrene:{op['ref']}" if op.get("ref") else str(op.get("name") or "")
            if assigned_name:
                nv = element.find(".//p:cNvPr", NS)
                if nv is not None:
                    nv.set("name", assigned_name)
                    shape_map[assigned_name] = element
                    if assigned_name.startswith("cyrene:"):
                        shape_map[assigned_name[7:]] = element
        elif kind == "update_text":
            self._set_text(element, str(op.get("text") or ""))
        elif kind == "apply_style":
            self._apply_style(element, dict(op.get("style") or {}))
        elif kind == "set_z_order":
            tree.remove(element)
            tree.insert(2, element) if str(op.get("position")) in {"SendToBack", "SendBackward"} else tree.append(element)
        elif kind == "ungroup_shapes":
            self._ungroup_shape(tree, element, target)
        else:
            raise PptxFileError("unsupported_operation", f"Unsupported batch operation: {kind}")
        changed.append(target)

    @staticmethod
    def _ungroup_shape(tree: ET.Element, element: ET.Element, target: str) -> None:
        if element.tag.rsplit("}", 1)[-1] != "grpSp":
            raise PptxFileError("invalid_group", f"Shape {target!r} is not a group.")
        insert_at = list(tree).index(element)
        members = [child for child in list(element) if child.tag.rsplit("}", 1)[-1] in {"sp", "pic", "graphicFrame", "cxnSp", "grpSp"}]
        tree.remove(element)
        for offset, member in enumerate(members):
            element.remove(member)
            tree.insert(insert_at + offset, member)

    def apply_batch(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        package = self._package(path)
        slide = self._select_slide(package, params)
        before = self._inspect_slide(package, slide)
        root = _xml(package[slide["part"]])
        tree = root.find("./p:cSld/p:spTree", NS)
        if tree is None:
            raise PptxFileError("invalid_slide", "Slide shape tree is missing.")
        operations = list(params.get("operations") or [])
        if not operations:
            raise PptxFileError("invalid_batch", "operations must not be empty.")
        shape_map = self._shape_map(root)
        created: list[str] = []
        changed: list[str] = []
        deleted: list[str] = []
        undo_token = self._snapshot(path, state)
        for op in operations:
            self._apply_batch_operation(package, slide, root, tree, shape_map, op, created, changed, deleted)
        package[slide["part"]] = _bytes(root)
        self._write(path, package)
        state["revision"] += 1
        self._record_state_file(state, path)
        after = self._inspect_slide(package, slide)
        result = {
            "status": "applied", "mode": "file", "revision": state["revision"],
            "filePath": str(path), "slideId": slide["id"], "changed": changed,
            "created": created, "deleted": deleted, "warnings": [],
            "undoToken": undo_token, "renderId": None,
            "audit": {"beforeShapeCount": len(before["shapes"]), "afterShapeCount": len(after["shapes"]), "operations": [{"op": op.get("op"), "target": op.get("shapeRef") or op.get("target") or op.get("ref")} for op in operations]},
        }
        state["idempotency"][key] = deepcopy(result)
        return result

    def _next_slide_number(self, package: dict[str, bytes]) -> int:
        numbers = []
        for name in package:
            if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
                try:
                    numbers.append(int(name[16:-4]))
                except ValueError:
                    pass
        return max(numbers or [0]) + 1

    def _add_slide_part(self, package: dict[str, bytes], source: dict[str, Any], *, blank: bool) -> dict[str, Any]:
        number = self._next_slide_number(package)
        part = f"ppt/slides/slide{number}.xml"
        root = _xml(package[source["part"]])
        if blank:
            tree = root.find("./p:cSld/p:spTree", NS)
            if tree is not None:
                for item in list(tree)[2:]:
                    tree.remove(item)
        package[part] = _bytes(root)
        source_rels = _rels_name(source["part"])
        if source_rels in package:
            package[_rels_name(part)] = package[source_rels]
        pres_rels = _xml(package["ppt/_rels/presentation.xml.rels"])
        used_rel_ids = [int(rel.get("Id", "rId0")[3:]) for rel in pres_rels if rel.get("Id", "").startswith("rId") and rel.get("Id", "rId0")[3:].isdigit()]
        rel_id = f"rId{max(used_rel_ids or [0]) + 1}"
        ET.SubElement(pres_rels, f"{{{NS['rel']}}}Relationship", {"Id": rel_id, "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide", "Target": f"slides/slide{number}.xml"})
        package["ppt/_rels/presentation.xml.rels"] = _bytes(pres_rels)
        presentation = _xml(package["ppt/presentation.xml"])
        slide_list = presentation.find("./p:sldIdLst", NS)
        if slide_list is None:
            slide_list = ET.SubElement(presentation, f"{{{NS['p']}}}sldIdLst")
        used_ids = [int(item.get("id", "255")) for item in slide_list]
        slide_id = str(max(used_ids or [255]) + 1)
        ET.SubElement(slide_list, f"{{{NS['p']}}}sldId", {"id": slide_id, f"{{{NS['r']}}}id": rel_id})
        package["ppt/presentation.xml"] = _bytes(presentation)
        content_types = _xml(package["[Content_Types].xml"])
        ET.SubElement(content_types, f"{{{NS['ct']}}}Override", {"PartName": f"/{part}", "ContentType": "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"})
        package["[Content_Types].xml"] = _bytes(content_types)
        return {"id": slide_id, "part": part, "relId": rel_id, "index": len(self._slides(package)) - 1}

    def create_slide(self, params: dict[str, Any], *, duplicate: bool = False) -> dict[str, Any]:
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        package = self._package(path)
        source = self._select_slide(package, params)
        undo = self._snapshot(path, state)
        created_slide = self._add_slide_part(package, source, blank=not duplicate)
        self._write(path, package)
        state["revision"] += 1
        self._record_state_file(state, path)
        result = {"status": "applied", "mode": "file", "revision": state["revision"], "filePath": str(path), "changed": [], "created": [{"slideId": created_slide["id"]}], "deleted": [], "warnings": [], "undoToken": undo, "renderId": None}
        state["idempotency"][key] = deepcopy(result)
        spec = params.get("slideSpec")
        if spec and (spec.get("elements") or spec.get("background")):
            batch = dict(params)
            batch.update({"slideId": created_slide["id"], "expectedRevision": state["revision"], "idempotencyKey": key + ":spec", "operations": slide_spec_operations(spec)})
            result = self.apply_batch(batch)
            result["created"].insert(0, {"slideId": created_slide["id"]})
            batch_undo = result.get("undoToken")
            if batch_undo and batch_undo != undo:
                entry = self._undo.pop(str(batch_undo), None)
                if entry:
                    Path(str(entry["snapshot"])).unlink(missing_ok=True)
            self._undo[undo]["revisionAfter"] = state["revision"]
            result["undoToken"] = undo
            state["idempotency"][key] = deepcopy(result)
        return result

    def move_or_delete_slide(self, params: dict[str, Any], *, delete: bool) -> dict[str, Any]:
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        package = self._package(path)
        slide = self._select_slide(package, params)
        undo = self._snapshot(path, state)
        presentation = _xml(package["ppt/presentation.xml"])
        slide_list = presentation.find("./p:sldIdLst", NS)
        if slide_list is None:
            raise PptxFileError("invalid_presentation", "Presentation slide list is missing.")
        item = next(node for node in slide_list if node.get("id") == slide["id"])
        if delete:
            slide_list.remove(item)
            rels = _xml(package["ppt/_rels/presentation.xml.rels"])
            for rel in list(rels):
                if rel.get("Id") == slide["relId"]:
                    rels.remove(rel)
            package["ppt/_rels/presentation.xml.rels"] = _bytes(rels)
            package.pop(slide["part"], None)
            package.pop(_rels_name(slide["part"]), None)
            content_types = _xml(package["[Content_Types].xml"])
            for override in list(content_types):
                if override.get("PartName") == f"/{slide['part']}":
                    content_types.remove(override)
            package["[Content_Types].xml"] = _bytes(content_types)
        else:
            slide_list.remove(item)
            target = int(params.get("targetIndex") or 0)
            slide_list.insert(max(0, min(target, len(slide_list))), item)
        package["ppt/presentation.xml"] = _bytes(presentation)
        self._write(path, package)
        state["revision"] += 1
        self._record_state_file(state, path)
        result = {"status": "applied", "mode": "file", "revision": state["revision"], "filePath": str(path), "changed": [] if delete else [slide["id"]], "created": [], "deleted": [slide["id"]] if delete else [], "warnings": [], "undoToken": undo, "renderId": None}
        state["idempotency"][key] = deepcopy(result)
        return result

    def verify(self, params: dict[str, Any], *, check: str = "all") -> dict[str, Any]:
        inspected = self.inspect({**params, "scope": "slide"})
        shapes = inspected["slide"]["shapes"]
        warnings = []
        if check in {"all", "overflow"}:
            for shape in shapes:
                if shape["x"] < 0 or shape["y"] < 0 or shape["x"] + shape["width"] > 720 or shape["y"] + shape["height"] > 405:
                    warnings.append({"code": "out_of_bounds", "shapeId": shape["id"]})
                if shape["text"] and len(shape["text"]) > max(8, (shape["width"] / 7) * (shape["height"] / 16)) * 1.7:
                    warnings.append({"code": "possible_text_overflow", "shapeId": shape["id"]})
        if check in {"all", "overlap"}:
            for index, first in enumerate(shapes):
                if first["width"] * first["height"] > 720 * 405 * 0.75:
                    continue
                for second in shapes[index + 1:]:
                    if second["width"] * second["height"] > 720 * 405 * 0.75:
                        continue
                    width = max(0, min(first["x"] + first["width"], second["x"] + second["width"]) - max(first["x"], second["x"]))
                    height = max(0, min(first["y"] + first["height"], second["y"] + second["height"]) - max(first["y"], second["y"]))
                    if width * height > min(first["width"] * first["height"], second["width"] * second["height"]) * float(params.get("overlapThreshold") or 0.2):
                        warnings.append({"code": "shape_overlap", "shapeIds": [first["id"], second["id"]]})
        unverifiable: list[str] = []
        if check in {"all", "contrast"}:
            minimum = float(params.get("minimumRatio") or 4.5)
            for shape in shapes:
                if not shape.get("text"):
                    continue
                ratio = _contrast_ratio(shape.get("fontColor"), shape.get("fillColor"))
                if ratio is None:
                    unverifiable.append(shape["id"])
                elif ratio < minimum:
                    warnings.append({"code": "low_contrast", "shapeId": shape["id"], "ratio": ratio, "minimumRatio": minimum})
        return {"status": "warning" if warnings else "success", "mode": "file", "revision": inspected["revision"], "slideId": inspected["slide"]["id"], "check": check, "warnings": warnings, "unverifiableShapeIds": unverifiable}

    def compare_before_after(self, params: dict[str, Any]) -> dict[str, Any]:
        rendered = self.render(params)
        path = self._path(params)
        state = self._sync_state(path)
        slide_id = rendered["slideId"]
        current_path = Path(rendered["imagePath"])
        current_digest = _digest(current_path)
        previous = state["slideRenders"].get(slide_id)
        state["slideRenders"][slide_id] = {"digest": current_digest, "imagePath": str(current_path)}
        result = {
            "status": "success", "mode": "file", "revision": state["revision"], "slideId": slide_id,
            "beforeAvailable": previous is not None,
            "changed": previous is not None and previous["digest"] != current_digest,
            "renderId": rendered["renderId"], "imagePath": str(current_path),
        }
        if params.get("includeImages"):
            result.update({"beforeImagePath": previous["imagePath"] if previous else None, "afterImagePath": str(current_path)})
        return result

    def _ensure_notes_master(self, package: dict[str, bytes]) -> str:
        presentation = _xml(package["ppt/presentation.xml"])
        presentation_rels = _xml(package["ppt/_rels/presentation.xml.rels"])
        existing = next((item for item in presentation_rels if item.get("Type", "").endswith("/notesMaster")), None)
        if existing is not None:
            return _part_target("ppt/presentation.xml", existing.get("Target", ""))

        numbers = [int(match.group(1)) for name in package if (match := re.fullmatch(r"ppt/notesMasters/notesMaster(\d+)\.xml", name))]
        notes_master_part = f"ppt/notesMasters/notesMaster{max(numbers or [0]) + 1}.xml"
        template = Path(__file__).with_name("static") / "notes-master-template.xml"
        root = _xml(template.read_bytes())
        package[notes_master_part] = _bytes(root)

        notes_master_rels = ET.Element(f"{{{NS['rel']}}}Relationships")
        theme_source = next((name for name in sorted(package) if name.startswith("ppt/theme/") and name.endswith(".xml")), None)
        notes_theme_part = None
        if theme_source:
            notes_theme_part = self._next_clone_part(package, theme_source)
            package[notes_theme_part] = package[theme_source]
            ET.SubElement(notes_master_rels, f"{{{NS['rel']}}}Relationship", {
                "Id": "rId1", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme",
                "Target": posixpath.relpath(notes_theme_part, PurePosixPath(notes_master_part).parent),
            })
        package[_rels_name(notes_master_part)] = _bytes(notes_master_rels)

        rel_ids = [int(item.get("Id", "rId0")[3:]) for item in presentation_rels if item.get("Id", "").startswith("rId") and item.get("Id", "rId0")[3:].isdigit()]
        relation_id = f"rId{max(rel_ids or [0]) + 1}"
        ET.SubElement(presentation_rels, f"{{{NS['rel']}}}Relationship", {
            "Id": relation_id, "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster",
            "Target": posixpath.relpath(notes_master_part, "ppt"),
        })
        content_types = _xml(package["[Content_Types].xml"])
        if notes_theme_part and theme_source:
            source_override = next((item for item in content_types.findall("./ct:Override", NS) if item.get("PartName") == f"/{theme_source}"), None)
            if source_override is not None:
                ET.SubElement(content_types, f"{{{NS['ct']}}}Override", {
                    "PartName": f"/{notes_theme_part}",
                    "ContentType": str(source_override.get("ContentType") or "application/vnd.openxmlformats-officedocument.theme+xml"),
                })
        ET.SubElement(content_types, f"{{{NS['ct']}}}Override", {
            "PartName": f"/{notes_master_part}",
            "ContentType": "application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml",
        })
        package["[Content_Types].xml"] = _bytes(content_types)
        package["ppt/presentation.xml"] = _bytes(presentation)
        package["ppt/_rels/presentation.xml.rels"] = _bytes(presentation_rels)
        return notes_master_part

    def edit_notes(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        package = self._package(path)
        slide = self._select_slide(package, params)
        slide_rels_name = _rels_name(slide["part"])
        slide_rels = _xml(package.get(slide_rels_name, b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'))
        relation = next((item for item in slide_rels if item.get("Type", "").endswith("/notesSlide")), None)
        if relation is None:
            notes_master_part = self._ensure_notes_master(package)
            numbers = [int(match.group(1)) for name in package if (match := re.fullmatch(r"ppt/notesSlides/notesSlide(\d+)\.xml", name))]
            notes_part = f"ppt/notesSlides/notesSlide{max(numbers or [0]) + 1}.xml"
            rel_numbers = [int(item.get("Id", "rId0")[3:]) for item in slide_rels if item.get("Id", "").startswith("rId") and item.get("Id", "rId0")[3:].isdigit()]
            relation = ET.SubElement(slide_rels, f"{{{NS['rel']}}}Relationship", {"Id": f"rId{max(rel_numbers or [0]) + 1}", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide", "Target": f"../notesSlides/{PurePosixPath(notes_part).name}"})
            template = Path(__file__).with_name("static") / "notes-slide-template.xml"
            notes_root = _xml(template.read_bytes())
            package[notes_part] = _bytes(notes_root)
            notes_rels = ET.Element(f"{{{NS['rel']}}}Relationships")
            ET.SubElement(notes_rels, f"{{{NS['rel']}}}Relationship", {"Id": "rId1", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster", "Target": posixpath.relpath(notes_master_part, PurePosixPath(notes_part).parent)})
            ET.SubElement(notes_rels, f"{{{NS['rel']}}}Relationship", {"Id": "rId2", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide", "Target": f"../slides/{PurePosixPath(slide['part']).name}"})
            package[_rels_name(notes_part)] = _bytes(notes_rels)
            content_types = _xml(package["[Content_Types].xml"])
            ET.SubElement(content_types, f"{{{NS['ct']}}}Override", {"PartName": f"/{notes_part}", "ContentType": "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"})
            package["[Content_Types].xml"] = _bytes(content_types)
        else:
            notes_part = _part_target(slide["part"], relation.get("Target", ""))
            notes_root = _xml(package[notes_part])
        body_shape = next((shape for shape in notes_root.findall("./p:cSld/p:spTree/p:sp", NS) if shape.find("./p:nvSpPr/p:nvPr/p:ph[@type='body']", NS) is not None), None)
        if body_shape is None:
            raise PptxFileError("notes_body_missing", "The notes slide has no body placeholder.")
        undo = self._snapshot(path, state)
        self._set_text(body_shape, str(params.get("text") or ""))
        if notes_root.find("./p:clrMapOvr", NS) is None:
            color_map = ET.SubElement(notes_root, f"{{{NS['p']}}}clrMapOvr")
            ET.SubElement(color_map, f"{{{NS['a']}}}masterClrMapping")
        package[notes_part] = _bytes(notes_root)
        package[slide_rels_name] = _bytes(slide_rels)
        self._write(path, package)
        state["revision"] += 1
        self._record_state_file(state, path)
        result = {"status": "applied", "mode": "file", "revision": state["revision"], "filePath": str(path), "slideId": slide["id"], "changed": [notes_part], "created": [], "deleted": [], "warnings": [], "undoToken": undo, "renderId": None, "audit": {"action": "edit_notes", "part": notes_part}}
        state["idempotency"][key] = deepcopy(result)
        return result

    def apply_layout(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("operations") and (params.get("part") or params.get("layoutPart")):
            return self._shape_part_edit(params, prefixes=("ppt/slideLayouts/",))
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        package = self._package(path)
        slide = self._select_slide(package, params)
        layout_part = _safe_part(str(params.get("layoutId") or params.get("layoutPart") or ""))
        if not layout_part.startswith("ppt/slideLayouts/") or layout_part not in package:
            raise PptxFileError("layout_not_found", "layoutId must be a slide-layout part returned by ppt.get_master.")
        rels_name = _rels_name(slide["part"])
        rels = _xml(package.get(rels_name, b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'))
        relation = next((item for item in rels if item.get("Type", "").endswith("/slideLayout")), None)
        if relation is None:
            ids = [int(item.get("Id", "rId0")[3:]) for item in rels if item.get("Id", "").startswith("rId") and item.get("Id", "rId0")[3:].isdigit()]
            relation = ET.SubElement(rels, f"{{{NS['rel']}}}Relationship", {"Id": f"rId{max(ids or [0]) + 1}", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"})
        relation.set("Target", posixpath.relpath(layout_part, PurePosixPath(slide["part"]).parent))
        undo = self._snapshot(path, state)
        package[rels_name] = _bytes(rels)
        self._write(path, package)
        state["revision"] += 1
        self._record_state_file(state, path)
        result = {"status": "applied", "mode": "file", "revision": state["revision"], "filePath": str(path), "slideId": slide["id"], "changed": [slide["id"]], "created": [], "deleted": [], "warnings": [], "undoToken": undo, "renderId": None, "audit": {"action": "apply_layout", "layoutPart": layout_part}}
        state["idempotency"][key] = deepcopy(result)
        return result

    def _next_clone_part(self, package: dict[str, bytes], source_part: str) -> str:
        path = PurePosixPath(source_part)
        match = re.match(r"^(.*?)(\d+)$", path.stem)
        prefix = match.group(1) if match else path.stem + "-"
        suffix = path.suffix
        numbers = []
        for name in package:
            candidate = PurePosixPath(name)
            if candidate.parent != path.parent or candidate.suffix != suffix:
                continue
            found = re.match(rf"^{re.escape(prefix)}(\d+)$", candidate.stem)
            if found:
                numbers.append(int(found.group(1)))
        return str(path.parent / f"{prefix}{max(numbers or [0]) + 1}{suffix}")

    def _prepare_slide_import(
        self, path: Path, params: dict[str, Any],
    ) -> tuple[dict[str, bytes], dict[str, bytes], list[dict[str, Any]], Path | None]:
        source_raw = params.get("presentationPath")
        source_base64 = params.get("presentationBase64")
        source_path = Path(str(source_raw)).expanduser().resolve() if source_raw else None
        if source_path is not None and (source_path.suffix.lower() != ".pptx" or not source_path.is_file()):
            raise PptxFileError("invalid_source_presentation", "presentationPath must identify a readable source PPTX in file mode.")
        if source_path == path:
            raise PptxFileError("invalid_source_presentation", "The source and destination presentations must be different files.")
        if source_path is None and not source_base64:
            raise PptxFileError("invalid_source_presentation", "presentationPath or presentationBase64 is required in file mode.")
        package = self._package(path)
        source = self._package(source_path) if source_path is not None else self._package_base64(str(source_base64))
        requested = {str(item) for item in (params.get("sourceSlideIds") or [])}
        selected = [slide for slide in self._slides(source) if not requested or slide["id"] in requested or slide["part"] in requested]
        if not selected:
            raise PptxFileError("source_slide_not_found", "No requested source slides were found.")
        return package, source, selected, source_path

    def _clone_import_parts(
        self, package: dict[str, bytes], source: dict[str, bytes],
        selected: list[dict[str, Any]], formatting: str,
    ) -> tuple[list[str], dict[str, str], ET.Element]:
        content_types = _xml(package["[Content_Types].xml"])
        source_types = _xml(source["[Content_Types].xml"])
        mapping: dict[str, str] = {}

        def copy_content_type(source_part: str, target_part: str) -> None:
            override = next((item for item in source_types.findall("./ct:Override", NS) if item.get("PartName") == f"/{source_part}"), None)
            if override is not None and not any(item.get("PartName") == f"/{target_part}" for item in content_types.findall("./ct:Override", NS)):
                ET.SubElement(content_types, f"{{{NS['ct']}}}Override", {"PartName": f"/{target_part}", "ContentType": str(override.get("ContentType") or "application/xml")})
            extension = PurePosixPath(source_part).suffix.lstrip(".")
            default = next((item for item in source_types.findall("./ct:Default", NS) if item.get("Extension") == extension), None)
            if default is not None and not any(item.get("Extension") == extension for item in content_types.findall("./ct:Default", NS)):
                ET.SubElement(content_types, f"{{{NS['ct']}}}Default", {"Extension": extension, "ContentType": str(default.get("ContentType") or "application/octet-stream")})

        destination_layout = None
        if formatting == "UseDestinationTheme" and self._slides(package):
            first = self._slides(package)[0]
            rels_name = _rels_name(first["part"])
            if rels_name in package:
                rels = _xml(package[rels_name])
                layout_rel = next((item for item in rels if item.get("Type", "").endswith("/slideLayout")), None)
                if layout_rel is not None:
                    destination_layout = _part_target(first["part"], layout_rel.get("Target", ""))

        def clone_part(source_part: str, forced_target: str | None = None) -> str:
            if source_part in mapping:
                return mapping[source_part]
            target_part = forced_target or (source_part if source_part not in package else self._next_clone_part(package, source_part))
            mapping[source_part] = target_part
            package[target_part] = source[source_part]
            copy_content_type(source_part, target_part)
            source_rels_name = _rels_name(source_part)
            if source_rels_name in source:
                rels = _xml(source[source_rels_name])
                for relation in rels:
                    if relation.get("TargetMode") == "External":
                        continue
                    dependency = _part_target(source_part, relation.get("Target", ""))
                    if relation.get("Type", "").endswith("/slideLayout") and destination_layout:
                        cloned_dependency = destination_layout
                    elif dependency in source:
                        cloned_dependency = clone_part(dependency)
                    else:
                        continue
                    relation.set("Target", posixpath.relpath(cloned_dependency, PurePosixPath(target_part).parent))
                package[_rels_name(target_part)] = _bytes(rels)
            return target_part

        cloned = [clone_part(slide["part"], self._next_clone_part(package, slide["part"])) for slide in selected]
        return cloned, mapping, content_types

    @staticmethod
    def _append_imported_slides(
        package: dict[str, bytes], cloned_slides: list[str], mapping: dict[str, str],
        target_slide_id: str,
    ) -> tuple[list[dict[str, str]], ET.Element, ET.Element]:
        presentation = _xml(package["ppt/presentation.xml"])
        presentation_rels = _xml(package["ppt/_rels/presentation.xml.rels"])
        slide_list = presentation.find("./p:sldIdLst", NS)
        if slide_list is None:
            slide_list = ET.SubElement(presentation, f"{{{NS['p']}}}sldIdLst")
        insertion = len(slide_list)
        if target_slide_id:
            insertion = next((index + 1 for index, item in enumerate(list(slide_list)) if item.get("id") == target_slide_id), insertion)
        created = []
        for offset, slide_part in enumerate(cloned_slides):
            rel_ids = [int(item.get("Id", "rId0")[3:]) for item in presentation_rels if item.get("Id", "").startswith("rId") and item.get("Id", "rId0")[3:].isdigit()]
            rel_id = f"rId{max(rel_ids or [0]) + 1}"
            ET.SubElement(presentation_rels, f"{{{NS['rel']}}}Relationship", {"Id": rel_id, "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide", "Target": posixpath.relpath(slide_part, "ppt")})
            slide_ids = [int(item.get("id", "255")) for item in slide_list]
            new_id = str(max(slide_ids or [255]) + 1)
            item = ET.Element(f"{{{NS['p']}}}sldId", {"id": new_id, f"{{{NS['r']}}}id": rel_id})
            slide_list.insert(insertion + offset, item)
            created.append({"slideId": new_id, "part": slide_part})
        master_list = presentation.find("./p:sldMasterIdLst", NS)
        cloned_masters = [target for source_part, target in mapping.items() if source_part.startswith("ppt/slideMasters/")]
        if cloned_masters:
            if master_list is None:
                master_list = ET.Element(f"{{{NS['p']}}}sldMasterIdLst")
                presentation.insert(0, master_list)
            existing_targets = {_part_target("ppt/presentation.xml", item.get("Target", "")) for item in presentation_rels if item.get("Type", "").endswith("/slideMaster")}
            for master_part in cloned_masters:
                if master_part in existing_targets:
                    continue
                rel_ids = [int(item.get("Id", "rId0")[3:]) for item in presentation_rels if item.get("Id", "").startswith("rId") and item.get("Id", "rId0")[3:].isdigit()]
                rel_id = f"rId{max(rel_ids or [0]) + 1}"
                ET.SubElement(presentation_rels, f"{{{NS['rel']}}}Relationship", {"Id": rel_id, "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster", "Target": posixpath.relpath(master_part, "ppt")})
                master_ids = [int(item.get("id", "2147483647")) for item in master_list]
                ET.SubElement(master_list, f"{{{NS['p']}}}sldMasterId", {"id": str(max(master_ids or [2147483647]) + 1), f"{{{NS['r']}}}id": rel_id})
        return created, presentation, presentation_rels

    def import_slides(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        package, source, selected, source_path = self._prepare_slide_import(path, params)
        undo = self._snapshot(path, state)
        formatting = str(params.get("formatting") or "KeepSourceFormatting")
        cloned_slides, mapping, content_types = self._clone_import_parts(package, source, selected, formatting)
        created, presentation, presentation_rels = self._append_imported_slides(
            package, cloned_slides, mapping, str(params.get("targetSlideId") or ""),
        )
        package["[Content_Types].xml"] = _bytes(content_types)
        package["ppt/presentation.xml"] = _bytes(presentation)
        package["ppt/_rels/presentation.xml.rels"] = _bytes(presentation_rels)
        self._write(path, package)
        state["revision"] += 1
        self._record_state_file(state, path)
        source_label = str(source_path) if source_path is not None else "presentationBase64"
        result = {"status": "applied", "mode": "file", "revision": state["revision"], "filePath": str(path), "changed": [], "created": created, "deleted": [], "warnings": [], "undoToken": undo, "renderId": None, "audit": {"action": "import_slides", "source": source_label, "slideCount": len(created), "formatting": formatting}}
        state["idempotency"][key] = deepcopy(result)
        return result

    def replace_slide_from_presentation(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        if not params.get("confirmed"):
            raise PptxFileError("confirmation_required", "Slide replacement through package input requires confirmed=true.")
        target = self._select_slide(self._package(path), params)
        imported = self.import_slides({
            **params,
            "targetSlideId": target["id"],
            "idempotencyKey": key + ":import",
        })
        removed = self.move_or_delete_slide({
            **params,
            "slideId": target["id"],
            "expectedRevision": imported["revision"],
            "idempotencyKey": key + ":delete",
        }, delete=True)
        delete_undo = str(removed.get("undoToken") or "")
        delete_entry = self._undo.pop(delete_undo, None)
        if delete_entry:
            Path(str(delete_entry["snapshot"])).unlink(missing_ok=True)
        undo_token = str(imported.get("undoToken") or "")
        if undo_token in self._undo:
            self._undo[undo_token]["revisionAfter"] = state["revision"]
        result = {
            "status": "applied",
            "mode": "file",
            "revision": state["revision"],
            "filePath": str(path),
            "changed": [],
            "created": imported.get("created") or [],
            "deleted": [target["id"]],
            "warnings": imported.get("warnings") or [],
            "undoToken": undo_token or None,
            "renderId": None,
            "audit": {"action": "replace_slide_from_presentation", "source": imported.get("audit", {}).get("source")},
        }
        state["idempotency"][key] = deepcopy(result)
        return result

    def render(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        if not shutil.which("soffice"):
            raise PptxFileError("render_unavailable", "LibreOffice is required to render a closed PPTX on this device.")
        slide = self._select_slide(self._package(path), params)
        output = DATA_DIR / "office_gateway" / "renders"
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cyrene-ppt-render-") as raw_temp:
            temp = Path(raw_temp)
            subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(temp), str(path)], capture_output=True, check=True, timeout=120)
            pdf = temp / f"{path.stem}.pdf"
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(pdf)
            page = document[slide["index"]]
            bitmap = page.render(scale=max(1, int(params.get("width") or 1440) / 720))
            image = bitmap.to_pil()
            render_id = uuid.uuid4().hex
            image_path = output / f"{render_id}.png"
            image.save(image_path)
        state = self._sync_state(path)
        return {"status": "success", "mode": "file", "revision": state["revision"], "slideId": slide["id"], "renderId": render_id, "imagePath": str(image_path)}

    def undo(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        token = str(params.get("undoToken") or "")
        entry = self._undo.get(token)
        if not entry or entry["path"] != str(path):
            raise PptxFileError("undo_not_found", "Undo token is missing or belongs to another file.")
        if entry["revisionAfter"] != state["revision"]:
            raise PptxFileError("undo_revision_conflict", "The file changed after this operation; undo was refused.")
        shutil.copy2(entry["snapshot"], path)
        state["revision"] += 1
        self._record_state_file(state, path)
        result = {"status": "applied", "mode": "file", "revision": state["revision"], "filePath": str(path), "changed": [], "created": [], "deleted": [], "warnings": [], "undoToken": None, "renderId": None, "undone": token}
        state["idempotency"][key] = deepcopy(result)
        return result

    def patch_ooxml(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        if not params.get("confirmed"):
            raise PptxFileError("confirmation_required", "OOXML patches require confirmed=true.")
        part = _safe_part(str(params.get("part") or ""))
        package = self._package(path)
        if part not in package:
            raise PptxFileError("ooxml_part_not_found", f"Package part {part!r} does not exist.")
        xml_text = str(params.get("xml") or "")
        try:
            root = ET.fromstring(xml_text.encode())
        except ET.ParseError as exc:
            raise PptxFileError("invalid_ooxml", str(exc)) from exc
        undo = self._snapshot(path, state)
        package[part] = _bytes(root)
        self._write(path, package)
        state["revision"] += 1
        self._record_state_file(state, path)
        result = {"status": "applied", "mode": "file", "revision": state["revision"], "filePath": str(path), "changed": [part], "created": [], "deleted": [], "warnings": [], "undoToken": undo, "renderId": None}
        state["idempotency"][key] = deepcopy(result)
        return result

    def visual_chart(self, params: dict[str, Any]) -> dict[str, Any]:
        spec = dict(params.get("chartSpec") or {})
        image = self._chart_png(spec, params.get("width"), params.get("height"))
        operation = {
            "op": "insert_image", "ref": params.get("ref") or params.get("shapeRef") or "chart",
            "x": params.get("x", 60), "y": params.get("y", 100),
            "width": params.get("width", 420), "height": params.get("height", 260),
            "imageBase64": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
        }
        operations = []
        if params.get("shapeRef"):
            operations.append({"op": "delete_shape", "shapeRef": params["shapeRef"]})
        operations.append(operation)
        result = self.apply_batch({**params, "operations": operations})
        result.update({"chartMode": "visual", "nativeEditable": False})
        return result

    def native_chart(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        state, key, replay = self._mutation(path, params)
        if replay:
            return {**deepcopy(replay), "replayed": True}
        package = self._package(path)
        slide = self._select_slide(package, params)
        root = _xml(package[slide["part"]])
        tree = root.find("./p:cSld/p:spTree", NS)
        if tree is None:
            raise PptxFileError("invalid_slide", "Slide shape tree is missing.")
        target = str(params.get("shapeRef") or "")
        deleted: list[str] = []
        if target:
            element = self._shape_map(root).get(target)
            if element is None:
                raise PptxFileError("shape_not_found", f"Chart target {target!r} was not found.")
            tree.remove(element)
            deleted.append(target)
        undo = self._snapshot(path, state)
        frame = self._new_native_chart(package, slide["part"], tree, params)
        info = self._shape_info(frame, len(self._shape_elements(root)))
        package[slide["part"]] = _bytes(root)
        self._write(path, package)
        state["revision"] += 1
        self._record_state_file(state, path)
        result = {"status": "applied", "mode": "file", "revision": state["revision"], "filePath": str(path), "slideId": slide["id"], "changed": [], "created": [info["ref"] or info["id"]], "deleted": deleted, "warnings": [], "undoToken": undo, "renderId": None, "chartMode": "native", "nativeEditable": True, "audit": {"action": "create_native_chart", "seriesCount": len((params.get("chartSpec") or {}).get("series") or [])}}
        state["idempotency"][key] = deepcopy(result)
        return result

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            params = self._prepare_output(method, params)
            if method == "ppt.get_context":
                return self.context(params)
            if method == "ppt.get_master":
                return self.master_and_theme(params, theme=False)
            if method == "ppt.get_theme":
                return self.master_and_theme(params, theme=True)
            if method == "ppt.inspect":
                return self.inspect(params)
            if method == "ppt.list_slides":
                return self.inspect({**params, "scope": "presentation"})
            if method == "ppt.get_slide":
                return self.get_slide(params)
            if method == "ppt.list_shapes":
                result = self.get_slide(params)
                return {"status": "success", "mode": "file", "revision": result["revision"], "filePath": result["filePath"], "slideId": result["slide"]["id"], "shapes": result["slide"]["shapes"]}
            if method == "ppt.get_shape":
                return self.get_shape(params)
            if method == "ppt.read_text":
                return self.read_text(params)
            if method == "ppt.get_selection":
                return self.inspect({**params, "scope": "selection"})
            if method == "ppt.apply_batch":
                return self.apply_batch(params)
            if method == "ppt.create_slide":
                return self.create_slide(params)
            if method == "ppt.create_from_template":
                template_slide_id = str(params.get("templateSlideId") or "")
                if not template_slide_id:
                    raise PptxFileError("template_slide_required", "create_from_template requires templateSlideId.")
                return self.create_slide({**params, "slideId": template_slide_id}, duplicate=True)
            if method == "ppt.duplicate_slide":
                return self.create_slide(params, duplicate=True)
            if method in {"ppt.apply_slide_spec", "ppt.relayout_slide", "ppt.replace_slide"}:
                spec = params.get("slideSpec") or {}
                operations = slide_spec_operations(spec)
                if params.get("replaceExisting") or method in {"ppt.relayout_slide", "ppt.replace_slide"}:
                    slide = self.get_slide(params)["slide"]
                    operations = [
                        {"op": "delete_shape", "shapeRef": shape["id"]}
                        for shape in slide.get("shapes") or []
                    ] + operations
                batch = {**params, "operations": operations}
                return self.apply_batch(batch)
            if method == "ppt.move_slide":
                return self.move_or_delete_slide(params, delete=False)
            if method == "ppt.delete_slide":
                return self.move_or_delete_slide(params, delete=True)
            if method == "ppt.render_slide":
                return self.render(params)
            if method == "ppt.verify_slide":
                return self.verify(params)
            if method == "ppt.check_overflow":
                return self.verify(params, check="overflow")
            if method == "ppt.check_overlap":
                return self.verify(params, check="overlap")
            if method == "ppt.check_contrast":
                return self.verify(params, check="contrast")
            if method == "ppt.compare_before_after":
                return self.compare_before_after(params)
            if method == "ppt.undo_batch":
                return self.undo(params)
            if method == "ppt.apply_ooxml_patch":
                return self.patch_ooxml(params)
            if method == "ppt.replace_slide_ooxml":
                return self.replace_slide_from_presentation(params)
            if method == "ppt.edit_chart" and str(params.get("chartMode") or "visual") == "visual":
                return self.visual_chart(params)
            if method == "ppt.edit_chart" and str(params.get("chartMode") or "") == "native":
                return self.native_chart(params)
            if method == "ppt.edit_table":
                return self.edit_table(params)
            if method == "ppt.edit_master":
                if params.get("operations"):
                    return self._shape_part_edit(params, prefixes=("ppt/slideMasters/",))
                if params.get("part") and params.get("xml"):
                    return self.patch_ooxml(params)
                raise PptxFileError("invalid_master_edit", "edit_master requires typed operations plus masterPart/part, or a confirmed explicit OOXML patch.")
            if method == "ppt.edit_layout":
                if params.get("part") and params.get("xml"):
                    return self.patch_ooxml(params)
                return self.apply_layout(params)
            if method == "ppt.edit_notes":
                return self.edit_notes(params)
            if method == "ppt.bind_shape":
                operation = {
                    "op": "update_shape",
                    "shapeRef": params.get("shapeRef"),
                    "ref": params.get("ref"),
                    "name": params.get("name"),
                }
                return self.apply_batch({**params, "operations": [operation]})
            if method in {"ppt.import_slides", "ppt.insert_slides"}:
                return self.import_slides(params)
            if method == "ppt.execute_officejs":
                raise PptxFileError("wrong_mode", "Office.js commands require mode=live_office.")
            raise PptxFileError("unknown_method", f"Unknown PowerPoint method: {method}")


def slide_spec_operations(spec: dict[str, Any]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    if spec.get("background"):
        operations.append({"op": "set_background", "color": spec["background"]})
    for element in spec.get("elements") or []:
        operation = deepcopy(element)
        box = operation.pop("box", None)
        if isinstance(box, list) and len(box) == 4:
            operation.update({"x": box[0], "y": box[1], "width": box[2], "height": box[3]})
        element_type = str(operation.pop("type", "shape"))
        operation["op"] = {"text": "add_textbox", "image": "insert_image", "line": "add_line", "chart": "insert_chart", "table": "insert_table"}.get(element_type, "add_shape")
        operations.append(operation)
    return operations


_ENGINE = PptxFileEngine()


def get_pptx_file_engine() -> PptxFileEngine:
    return _ENGINE


__all__ = ["PptxFileEngine", "PptxFileError", "get_pptx_file_engine", "slide_spec_operations"]
