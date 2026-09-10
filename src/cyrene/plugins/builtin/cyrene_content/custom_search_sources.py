"""Validated user-defined adapters for the managed SimpleXNG service."""
from __future__ import annotations

import json
import re
from string import Formatter
from urllib.parse import urlsplit

from cyrene.platform import config_store


class CustomSourceError(ValueError):
    pass


def _text(row: dict, key: str, *, required: bool = False, limit: int = 2000) -> str:
    value = row.get(key, "")
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise CustomSourceError(f"Invalid {key}")
    value = value.strip()
    if required and not value:
        raise CustomSourceError(f"{key} is required")
    return value


def _url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid or any(c.isspace() for c in value) or "{" in parsed.netloc or "}" in parsed.netloc:
        raise CustomSourceError("Use an HTTP(S) URL without embedded credentials")


def _template(value: str) -> None:
    try:
        for _, field, spec, conversion in Formatter().parse(value):
            if field is not None and (field not in {"query", "pageno", "lang"} or spec or conversion):
                raise ValueError()
    except ValueError:
        raise CustomSourceError("Only {query}, {pageno} and {lang} placeholders are supported; escape literal braces as {{ and }}") from None


def normalize_sources(value: object, previous: list[dict]) -> list[dict]:
    if not isinstance(value, list) or len(value) > 20:
        raise CustomSourceError("Use at most 20 custom search sources")
    old = {row["id"]: row for row in previous}
    result = []
    seen: set[str] = set()
    for row in value:
        if not isinstance(row, dict):
            raise CustomSourceError("Each custom source must be an object")
        source_id = _text(row, "id", required=True)
        if not re.fullmatch(r"custom-[a-f0-9]{32}", source_id) or source_id in seen:
            raise CustomSourceError("Invalid or duplicate custom source ID")
        seen.add(source_id)
        item = {"id": source_id, "name": _text(row, "name", required=True, limit=80)}
        kind = row.get("type")
        if kind not in ("json", "html"):
            raise CustomSourceError("Source type must be json or html")
        item["type"] = kind
        item["search_url"] = _text(row, "search_url", required=True)
        _url(item["search_url"])
        _template(item["search_url"])
        item["method"] = row.get("method", "GET")
        if item["method"] not in ("GET", "POST"):
            raise CustomSourceError("Method must be GET or POST")
        item["request_body"] = _text(row, "request_body", limit=10000) if item["method"] == "POST" else ""
        _template(item["request_body"])
        if "{query}" not in item["search_url"] + item["request_body"]:
            raise CustomSourceError("Include {query} in the search URL or request body")
        for key in ("results_path", "title_path", "url_path", "content_path"):
            item[key] = _text(row, key, required=key in ("title_path", "url_path"))
            if kind == "html" and item[key]:
                from lxml.etree import XPath, XPathSyntaxError
                try:
                    XPath(item[key])
                except XPathSyntaxError:
                    raise CustomSourceError(f"Invalid XPath: {key}") from None
        item["url_prefix"] = _text(row, "url_prefix") if kind == "json" else ""
        if item["url_prefix"]:
            _url(item["url_prefix"])
        item["content_type"] = row.get("content_type", "application/json")
        if item["content_type"] not in ("application/json", "application/x-www-form-urlencoded"):
            raise CustomSourceError("Unsupported request content type")
        if item["method"] == "POST" and item["content_type"] == "application/json":
            try:
                json.loads(item["request_body"].format(query="test", pageno=1, lang="en"))
            except (ValueError, KeyError):
                raise CustomSourceError("POST body must be valid JSON; double literal braces and place {query} inside a JSON string") from None
        item["auth_header"] = _text(row, "auth_header", limit=100)
        if item["auth_header"] and not re.fullmatch(r"[A-Za-z0-9-]+", item["auth_header"]):
            raise CustomSourceError("Invalid authentication header name")
        secret = _text(row, "auth_value", limit=4000)
        if not secret and not row.get("clear_auth"):
            secret = old.get(source_id, {}).get("auth_value", "")
        if any(c in secret for c in "\r\n"):
            raise CustomSourceError("Invalid authentication header value")
        if secret and not item["auth_header"]:
            raise CustomSourceError("Authentication header name is required")
        item["auth_value"] = secret
        result.append(item)
    return result


def stored_sources() -> list[dict]:
    search = config_store.get_setting("search", {})
    return list(search.get("custom_sources", [])) if isinstance(search, dict) else []


def public_sources() -> list[dict]:
    return [
        {**{key: value for key, value in row.items() if key != "auth_value"},
         "auth_configured": bool(row.get("auth_value"))}
        for row in stored_sources()
    ]


def engine_definitions() -> list[dict]:
    engines = []
    for source in stored_sources():
        suffix = "query" if source["type"] == "json" else "xpath"
        row = {
            "name": source["id"], "shortcut": source["id"], "engine": "json_engine" if suffix == "query" else "xpath",
            "categories": ["general"], "disabled": False,
            "search_url": source["search_url"], "method": source["method"],
            "request_body": source["request_body"], "timeout": 10.0,
            "headers": {},
            "enable_http": urlsplit(source["search_url"]).scheme == "http",
        }
        for field in ("results", "title", "url", "content"):
            # An empty XPath is invalid; an absent description yields no text.
            row[f"{field}_{suffix}"] = source[f"{field}_path"] or ("." if field == "content" and suffix == "xpath" else "")
        if suffix == "query":
            row["url_prefix"] = source["url_prefix"]
        if source["method"] == "POST":
            row["headers"]["Content-Type"] = source["content_type"]
        if source.get("auth_value"):
            row["headers"][source["auth_header"]] = source["auth_value"]
        engines.append(row)
    return engines
