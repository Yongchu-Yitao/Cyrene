"""Leaf-level document extraction helpers shared by runtime surfaces."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


def extract_office_xml_text(path: Path) -> str:
    """Extract text from Office XML files, even without a useful extension."""
    suffix = path.suffix.lower()
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            slide_names = sorted(
                name
                for name in names
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            )
            sheet_names = [
                name
                for name in names
                if name == "xl/sharedStrings.xml"
                or re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            ]
            if suffix == ".docx" or "word/document.xml" in names:
                targets = [
                    name
                    for name in names
                    if name == "word/document.xml"
                ]
            elif suffix == ".pptx" or slide_names:
                targets = slide_names
            elif suffix == ".xlsx" or sheet_names:
                targets = sheet_names
            else:
                return ""
            blocks: list[str] = []
            for name in targets:
                try:
                    root = ElementTree.fromstring(archive.read(name))
                except Exception:
                    continue
                text = " ".join(
                    node.text.strip()
                    for node in root.iter()
                    if node.text and node.text.strip()
                )
                if text:
                    blocks.append(text)
            return "\n\n".join(blocks)
    except (OSError, zipfile.BadZipFile):
        return ""
