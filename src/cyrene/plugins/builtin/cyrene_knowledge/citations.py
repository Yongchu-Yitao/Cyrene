"""Readable bibliographic labels and citations."""
from typing import Any, Mapping, Sequence


def creator_label(creators: Sequence[Mapping[str, Any]]) -> str:
    names: list[str] = []
    for creator in creators:
        literal = str(creator.get("name") or "").strip()
        first = str(creator.get("first_name") or "").strip()
        last = str(creator.get("last_name") or "").strip()
        label = literal or " ".join(value for value in (first, last) if value)
        if label:
            names.append(label)
    return ", ".join(names)


def render_citation(item: Mapping[str, Any], style: str) -> str:
    authors = creator_label(list(item.get("creators") or [])) or "Unknown author"
    title = str(item.get("title") or "Untitled")
    year = str(item.get("year") or "n.d.")
    venue = str(item.get("venue") or item.get("publisher") or "").strip()
    doi = str(item.get("doi") or "").strip()
    url = str(item.get("url") or "").strip()
    source = f" https://doi.org/{doi}" if doi else (f" {url}" if url else "")
    normalized_style = str(style or "ieee").casefold()
    if normalized_style in {"apa", "apa7"}:
        return f"{authors} ({year}). {title}. {venue}.{source}".strip()
    if normalized_style in {"mla", "mla9"}:
        return f'{authors}. "{title}." {venue}, {year}.{source}'.strip()
    if normalized_style in {"chicago", "chicago-author-date"}:
        return f'{authors}. {year}. "{title}." {venue}.{source}'.strip()
    return f'{authors}, "{title}," {venue}, {year}.{source}'.strip()


