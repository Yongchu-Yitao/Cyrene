"""Self-contained file extraction and bibliography parsing for the knowledge Plugin."""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(str(data or "").split())
        if value:
            self.parts.append(value)


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _office_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "word/document.xml" in names:
                targets = ["word/document.xml"]
            elif any(name.startswith("ppt/slides/slide") for name in names):
                targets = sorted(name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name))
            elif "xl/sharedStrings.xml" in names:
                targets = ["xl/sharedStrings.xml"]
            else:
                return ""
            blocks: list[str] = []
            for name in targets:
                try:
                    root = ElementTree.fromstring(archive.read(name))
                except Exception:
                    continue
                text = " ".join(node.text.strip() for node in root.iter() if node.text and node.text.strip())
                if text:
                    blocks.append(text)
            return "\n\n".join(blocks)
    except (OSError, zipfile.BadZipFile):
        return ""


def extract_text(path: Path, content_type: str = "") -> tuple[str, int]:
    """Extract user-visible text and a PDF page count without legacy services."""

    suffix = path.suffix.casefold()
    media_type = str(content_type or "").casefold().split(";", 1)[0]
    if suffix == ".pdf" or media_type == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return "\n\n".join((page.extract_text() or "") for page in reader.pages), len(reader.pages)
        except Exception:
            return "", 0
    if suffix in {".docx", ".pptx", ".xlsx"} or zipfile.is_zipfile(path):
        office_text = _office_text(path)
        if office_text:
            return office_text, 0
    if suffix in {".html", ".htm"} or media_type == "text/html":
        parser = _HTMLText()
        try:
            parser.feed(_decode(path.read_bytes()))
        except (OSError, UnicodeError):
            return "", 0
        return "\n".join(parser.parts), 0
    text_like = media_type.startswith("text/") or suffix in {
        ".bib",
        ".bibtex",
        ".csv",
        ".json",
        ".log",
        ".md",
        ".ris",
        ".rtf",
        ".tex",
        ".toml",
        ".tsv",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
    if not text_like:
        return "", 0
    try:
        return _decode(path.read_bytes()), 0
    except OSError:
        return "", 0


def split_text(text: str, *, size: int = 1200, overlap: int = 160) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > size:
            if current:
                chunks.append(current)
                current = ""
            step = max(1, size - overlap)
            chunks.extend(paragraph[index : index + size] for index in range(0, len(paragraph), step))
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= size:
            current = candidate
            continue
        chunks.append(current)
        prefix = current[-overlap:] if overlap else ""
        current = f"{prefix}\n\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks


def _year(value: Any) -> int | None:
    match = re.search(r"(?<!\d)(1[5-9]\d{2}|20\d{2}|21\d{2})(?!\d)", str(value or ""))
    return int(match.group(1)) if match else None


def _authors(values: list[str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for raw in values:
        name = " ".join(str(raw or "").split())
        if not name:
            continue
        if "," in name:
            last, first = (part.strip() for part in name.split(",", 1))
            result.append(
                {
                    "creator_type": "author",
                    "first_name": first,
                    "last_name": last,
                }
            )
        else:
            result.append({"creator_type": "author", "name": name})
    return result


def _parse_ris(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    last_tag = ""
    for line in text.splitlines():
        match = re.match(r"^([A-Z0-9]{2})\s*-\s?(.*)$", line)
        if match:
            tag, value = match.groups()
            if tag == "TY" and current:
                records.append(current)
                current = {}
            if tag == "ER":
                if current:
                    records.append(current)
                current = {}
                last_tag = ""
                continue
            current.setdefault(tag, []).append(value.strip())
            last_tag = tag
        elif last_tag and line.strip():
            current[last_tag][-1] += " " + line.strip()
    if current:
        records.append(current)
    type_map = {
        "BOOK": "book",
        "CHAP": "bookSection",
        "CONF": "conferencePaper",
        "CPAPER": "conferencePaper",
        "ELEC": "webpage",
        "JOUR": "journalArticle",
        "RPRT": "report",
        "THES": "thesis",
    }
    result: list[dict[str, Any]] = []
    for record in records:

        def first(*keys: str) -> str:
            return next((record[key][0] for key in keys if record.get(key)), "")

        start, end = first("SP"), first("EP")
        date_text = first("DA", "PY", "Y1")
        result.append(
            {
                "item_type": type_map.get(first("TY"), "document"),
                "title": first("TI", "T1", "CT"),
                "abstract": first("AB", "N2"),
                "doi": first("DO"),
                "isbn": first("SN"),
                "url": first("UR", "L1"),
                "venue": first("JO", "JF", "T2", "BT"),
                "publisher": first("PB"),
                "volume": first("VL"),
                "issue": first("IS"),
                "pages": f"{start}-{end}" if start and end else start or end,
                "language": first("LA"),
                "year": _year(date_text),
                "date_text": date_text,
                "tags": record.get("KW", []),
                "creators": _authors(record.get("AU", []) + record.get("A1", [])),
            }
        )
    return [item for item in result if item.get("title") or item.get("creators")]


def _bib_entries(text: str) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    index = 0
    while True:
        match = re.search(r"@([A-Za-z]+)\s*([({])", text[index:])
        if not match:
            return result
        start = index + match.end()
        opener, closer = match.group(2), "}" if match.group(2) == "{" else ")"
        depth, position, quoted, escaped = 1, start, False, False
        while position < len(text) and depth:
            char = text[position]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted and char == opener:
                depth += 1
            elif not quoted and char == closer:
                depth -= 1
            position += 1
        content = text[start : position - 1]
        citekey, _, body = content.partition(",")
        result.append((match.group(1).casefold(), citekey.strip(), body))
        index = position


def _bib_fields(body: str) -> dict[str, str]:
    result: dict[str, str] = {}
    pattern = re.compile(
        r"(?ms)([A-Za-z][A-Za-z0-9_-]*)\s*=\s*"
        r"(\{(?:[^{}]|\{[^{}]*\})*\}|\"(?:\\.|[^\"])*\"|[^,]+)\s*,?"
    )
    for match in pattern.finditer(body):
        value = match.group(2).strip()
        if len(value) > 1 and (value[0], value[-1]) in {("{", "}"), ('"', '"')}:
            value = value[1:-1]
        result[match.group(1).casefold()] = re.sub(r"[{}]", "", value).strip()
    return result


def _parse_bibtex(text: str) -> list[dict[str, Any]]:
    type_map = {
        "article": "journalArticle",
        "book": "book",
        "conference": "conferencePaper",
        "inbook": "bookSection",
        "incollection": "bookSection",
        "inproceedings": "conferencePaper",
        "mastersthesis": "thesis",
        "phdthesis": "thesis",
        "techreport": "report",
    }
    result: list[dict[str, Any]] = []
    for entry_type, citekey, body in _bib_entries(text):
        fields = _bib_fields(body)
        result.append(
            {
                "item_type": type_map.get(entry_type, "document"),
                "title": fields.get("title", ""),
                "abstract": fields.get("abstract", ""),
                "doi": fields.get("doi", ""),
                "isbn": fields.get("isbn", ""),
                "url": fields.get("url", ""),
                "venue": fields.get("journal") or fields.get("booktitle", ""),
                "publisher": fields.get("publisher", ""),
                "volume": fields.get("volume", ""),
                "issue": fields.get("number", ""),
                "pages": fields.get("pages", ""),
                "language": fields.get("language", ""),
                "year": _year(fields.get("year")),
                "date_text": fields.get("year", ""),
                "citekey": citekey,
                "tags": [part.strip() for part in re.split(r"[,;]", fields.get("keywords", "")) if part.strip()],
                "creators": _authors(re.split(r"\s+and\s+", fields.get("author", ""), flags=re.I)),
            }
        )
    return result


def _parse_json(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    records = payload if isinstance(payload, list) else [payload]
    result: list[dict[str, Any]] = []
    for value in records:
        if not isinstance(value, dict):
            continue
        raw = value.get("data") if isinstance(value.get("data"), dict) else value
        creators = raw.get("creators") or raw.get("author") or []
        if not isinstance(creators, list):
            creators = []
        normalized_creators: list[dict[str, Any]] = []
        for creator in creators:
            if isinstance(creator, str):
                normalized_creators.append({"name": creator, "creator_type": "author"})
            elif isinstance(creator, dict):
                normalized_creators.append(
                    {
                        "name": str(creator.get("literal") or creator.get("name") or ""),
                        "first_name": str(creator.get("given") or creator.get("firstName") or ""),
                        "last_name": str(creator.get("family") or creator.get("lastName") or ""),
                        "creator_type": str(creator.get("creatorType") or "author"),
                    }
                )
        issued = raw.get("issued") if isinstance(raw.get("issued"), dict) else {}
        date_parts = issued.get("date-parts") if isinstance(issued.get("date-parts"), list) else []
        date = date_parts[0] if date_parts and isinstance(date_parts[0], list) else []
        tags = raw.get("tags") or raw.get("keyword") or raw.get("keywords") or []
        if isinstance(tags, str):
            tags = [part.strip() for part in re.split(r"[,;]", tags) if part.strip()]
        result.append(
            {
                "item_type": str(raw.get("itemType") or raw.get("type") or "document"),
                "title": str(raw.get("title") or ""),
                "abstract": str(raw.get("abstractNote") or raw.get("abstract") or ""),
                "doi": str(raw.get("DOI") or raw.get("doi") or ""),
                "isbn": str(raw.get("ISBN") or raw.get("isbn") or ""),
                "url": str(raw.get("url") or raw.get("URL") or ""),
                "venue": str(raw.get("publicationTitle") or raw.get("container-title") or ""),
                "publisher": str(raw.get("publisher") or ""),
                "volume": str(raw.get("volume") or ""),
                "issue": str(raw.get("issue") or ""),
                "pages": str(raw.get("pages") or raw.get("page") or ""),
                "language": str(raw.get("language") or ""),
                "year": int(date[0]) if date and str(date[0]).isdigit() else _year(raw.get("date")),
                "date_text": "-".join(str(part) for part in date) or str(raw.get("date") or ""),
                "citekey": str(raw.get("citationKey") or raw.get("id") or ""),
                "tags": tags if isinstance(tags, list) else [],
                "creators": normalized_creators,
            }
        )
    return [item for item in result if item.get("title") or item.get("doi") or item.get("isbn") or item.get("creators")]


def _parse_tabular(text: str, *, dialect: str) -> list[dict[str, Any]]:
    try:
        rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    except csv.Error:
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        normalized = {re.sub(r"[^a-z0-9]+", "_", str(key or "").casefold()).strip("_"): value for key, value in row.items()}

        def first(*keys: str) -> str:
            return next(
                (str(normalized.get(key) or "").strip() for key in keys if str(normalized.get(key) or "").strip()),
                "",
            )

        title = first("title", "name")
        if not title:
            continue
        author_text = first("authors", "author", "creators")
        tags = first("tags", "keywords", "keyword")
        date_text = first("date_text", "date", "published", "year")
        result.append(
            {
                "item_type": first("item_type", "type") or "document",
                "title": title,
                "abstract": first("abstract", "abstract_note", "summary"),
                "doi": first("doi"),
                "isbn": first("isbn"),
                "url": first("url"),
                "venue": first("venue", "publication_title", "container_title", "journal"),
                "publisher": first("publisher"),
                "volume": first("volume"),
                "issue": first("issue", "number"),
                "pages": first("pages", "page"),
                "language": first("language"),
                "year": _year(date_text),
                "date_text": date_text,
                "citekey": first("citekey", "citation_key", "id"),
                "tags": [part.strip() for part in re.split(r"[,;]", tags) if part.strip()],
                "creators": _authors([part.strip() for part in re.split(r"\s+and\s+|[;；\n]", author_text) if part.strip()]),
            }
        )
    return result


def parse_bibliography(filename: str, data: bytes) -> list[dict[str, Any]] | None:
    suffix = Path(filename).suffix.casefold()
    text = _decode(data)
    if suffix == ".ris":
        return _parse_ris(text)
    if suffix in {".bib", ".bibtex"}:
        return _parse_bibtex(text)
    if suffix == ".json":
        try:
            return _parse_json(text)
        except (ValueError, TypeError):
            return None
    if suffix in {".csv", ".tsv"}:
        dialect = "excel-tab" if suffix == ".tsv" else "excel"
        records = _parse_tabular(text, dialect=dialect)
        return records or None
    return None


__all__ = ["extract_text", "parse_bibliography", "split_text"]
