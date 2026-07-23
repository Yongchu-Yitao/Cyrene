"""Small, dependency-free importers for common bibliography exchange files."""

from __future__ import annotations

import json
import re
from typing import Any


def _year(value: Any) -> int | None:
    match = re.search(r"(?<!\d)(1[5-9]\d{2}|20\d{2}|21\d{2})(?!\d)", str(value or ""))
    return int(match.group(1)) if match else None


def _authors(values: list[str]) -> list[dict[str, str]]:
    result = []
    for value in values:
        name = re.sub(r"\s+", " ", str(value or "")).strip()
        if not name:
            continue
        if "," in name:
            last, first = (part.strip() for part in name.split(",", 1))
        else:
            parts = name.split()
            first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", name)
        result.append({"creator_type": "author", "first_name": first, "last_name": last})
    return result


def _csl_item(value: dict[str, Any]) -> dict[str, Any]:
    issued = value.get("issued") if isinstance(value.get("issued"), dict) else {}
    date_parts = issued.get("date-parts") if isinstance(issued.get("date-parts"), list) else []
    first_date = date_parts[0] if date_parts and isinstance(date_parts[0], list) else []
    csl_authors = value.get("author") if isinstance(value.get("author"), list) else []
    creators = []
    for creator in csl_authors:
        if not isinstance(creator, dict):
            continue
        creators.append({
            "creator_type": "author", "first_name": str(creator.get("given") or ""),
            "last_name": str(creator.get("family") or ""), "name": str(creator.get("literal") or ""),
        })
    type_map = {
        "article-journal": "journalArticle", "paper-conference": "conferencePaper",
        "book": "book", "chapter": "bookSection", "thesis": "thesis",
        "report": "report", "webpage": "webpage",
    }
    tags = value.get("keyword") or value.get("keywords") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in re.split(r"[,;]", tags) if part.strip()]
    return {
        "item_type": type_map.get(str(value.get("type") or ""), str(value.get("type") or "document")),
        "title": str(value.get("title") or ""), "abstract": str(value.get("abstract") or ""),
        "doi": str(value.get("DOI") or value.get("doi") or ""),
        "isbn": str(value.get("ISBN") or value.get("isbn") or ""),
        "url": str(value.get("URL") or value.get("url") or ""),
        "venue": str(value.get("container-title") or value.get("publisher-place") or ""),
        "publisher": str(value.get("publisher") or ""), "volume": str(value.get("volume") or ""),
        "issue": str(value.get("issue") or ""), "pages": str(value.get("page") or ""),
        "language": str(value.get("language") or ""),
        "year": int(first_date[0]) if first_date and str(first_date[0]).isdigit() else None,
        "date_text": "-".join(str(part) for part in first_date),
        "citekey": str(value.get("id") or ""), "tags": tags if isinstance(tags, list) else [],
        "creators": creators, "csl_json": value, "raw_json": value,
    }


def parse_json(data: bytes) -> list[dict[str, Any]]:
    payload = json.loads(data.decode("utf-8-sig"))
    values = payload if isinstance(payload, list) else [payload]
    result = []
    for value in values:
        if not isinstance(value, dict):
            continue
        if isinstance(value.get("data"), dict) or "itemType" in value:
            raw = value.get("data") if isinstance(value.get("data"), dict) else value
            creators = raw.get("creators") if isinstance(raw.get("creators"), list) else []
            result.append({
                "item_type": str(raw.get("itemType") or "document"),
                "title": str(raw.get("title") or ""), "abstract": str(raw.get("abstractNote") or ""),
                "doi": str(raw.get("DOI") or ""), "isbn": str(raw.get("ISBN") or ""),
                "url": str(raw.get("url") or ""),
                "venue": str(raw.get("publicationTitle") or raw.get("conferenceName") or ""),
                "publisher": str(raw.get("publisher") or ""), "volume": str(raw.get("volume") or ""),
                "issue": str(raw.get("issue") or ""), "pages": str(raw.get("pages") or ""),
                "language": str(raw.get("language") or ""), "year": _year(raw.get("date")),
                "date_text": str(raw.get("date") or ""), "citekey": str(raw.get("citationKey") or ""),
                "tags": raw.get("tags") if isinstance(raw.get("tags"), list) else [],
                "creators": creators, "raw_json": value,
            })
        else:
            result.append(_csl_item(value))
    return result


def parse_ris(data: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    last_tag = ""
    for line in data.decode("utf-8-sig", errors="replace").splitlines():
        match = re.match(r"^([A-Z0-9]{2})\s*-\s?(.*)$", line)
        if match:
            tag, value = match.groups()
            if tag == "TY" and current:
                records.append(current)
                current = {}
            if tag == "ER":
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
        "JOUR": "journalArticle", "CONF": "conferencePaper", "CPAPER": "conferencePaper",
        "BOOK": "book", "CHAP": "bookSection", "THES": "thesis", "RPRT": "report",
        "ELEC": "webpage",
    }
    items = []
    for record in records:
        def get(*keys: str) -> str:
            return next((record[key][0] for key in keys if record.get(key)), "")

        start, end = get("SP"), get("EP")
        pages = f"{start}-{end}" if start and end else start or end
        date_text = get("DA", "PY", "Y1")
        items.append({
            "item_type": type_map.get(get("TY"), "document"), "title": get("TI", "T1", "CT"),
            "abstract": get("AB", "N2"), "doi": get("DO"), "isbn": get("SN"),
            "url": get("UR", "L1"), "venue": get("JO", "JF", "T2", "BT"),
            "publisher": get("PB"), "volume": get("VL"), "issue": get("IS"), "pages": pages,
            "language": get("LA"), "year": _year(date_text), "date_text": date_text,
            "tags": record.get("KW", []), "creators": _authors(record.get("AU", []) + record.get("A1", [])),
            "raw_json": record,
        })
    return [item for item in items if item["title"] or item["creators"]]


def _bib_entries(text: str) -> list[tuple[str, str, str]]:
    entries = []
    index = 0
    while True:
        match = re.search(r"@([A-Za-z]+)\s*([({])", text[index:])
        if not match:
            break
        start = index + match.end()
        opener = match.group(2)
        closer = "}" if opener == "{" else ")"
        depth = 1
        pos = start
        quoted = False
        escaped = False
        while pos < len(text) and depth:
            char = text[pos]
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
            pos += 1
        content = text[start:pos - 1]
        citekey, _, body = content.partition(",")
        entries.append((match.group(1).lower(), citekey.strip(), body))
        index = pos
    return entries


def _bib_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(
        r"(?ms)([A-Za-z][A-Za-z0-9_-]*)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"(?:\\.|[^\"])*\"|[^,]+)\s*,?",
        body,
    ):
        value = match.group(2).strip()
        if len(value) >= 2 and ((value[0] == "{" and value[-1] == "}") or (value[0] == '"' and value[-1] == '"')):
            value = value[1:-1]
        fields[match.group(1).lower()] = re.sub(r"[{}]", "", value).strip()
    return fields


def parse_bibtex(data: bytes) -> list[dict[str, Any]]:
    type_map = {
        "article": "journalArticle", "inproceedings": "conferencePaper", "conference": "conferencePaper",
        "book": "book", "inbook": "bookSection", "incollection": "bookSection",
        "phdthesis": "thesis", "mastersthesis": "thesis", "techreport": "report", "misc": "document",
    }
    items = []
    for entry_type, citekey, body in _bib_entries(data.decode("utf-8-sig", errors="replace")):
        fields = _bib_fields(body)
        pages = fields.get("pages", "")
        items.append({
            "item_type": type_map.get(entry_type, entry_type), "title": fields.get("title", ""),
            "abstract": fields.get("abstract", ""), "doi": fields.get("doi", ""),
            "isbn": fields.get("isbn", ""), "url": fields.get("url", ""),
            "venue": fields.get("journal") or fields.get("booktitle", ""),
            "publisher": fields.get("publisher", ""), "volume": fields.get("volume", ""),
            "issue": fields.get("number", ""), "pages": pages, "language": fields.get("language", ""),
            "year": _year(fields.get("year")), "date_text": fields.get("year", ""), "citekey": citekey,
            "tags": [part.strip() for part in re.split(r"[,;]", fields.get("keywords", "")) if part.strip()],
            "creators": _authors(re.split(r"\s+and\s+", fields.get("author", ""), flags=re.I)),
            "raw_json": {"entry_type": entry_type, "citekey": citekey, "fields": fields},
        })
    return items


def parse(filename: str, data: bytes) -> list[dict[str, Any]] | None:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "ris":
        return parse_ris(data)
    if suffix in {"bib", "bibtex"}:
        return parse_bibtex(data)
    if suffix == "json":
        return parse_json(data)
    return None


__all__ = ["parse", "parse_json", "parse_ris", "parse_bibtex"]
