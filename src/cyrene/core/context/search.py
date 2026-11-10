"""Read-only keyword search over the current branch's task contexts."""
from __future__ import annotations

import hashlib
import json
from bisect import bisect_left

from .tasks import SHARED_ID, user_owners


def _snippets(text, query):
    folded = text.casefold()
    # Case folding may expand characters (e.g. ß -> ss). Keep original text
    # offsets so snippets remain exact source excerpts.
    offsets = None
    if len(folded) != len(text):
        offsets = [i for i, char in enumerate(text) for _ in char.casefold()]
    position, previous_end = 0, 0
    while (match := folded.find(query, position)) >= 0:
        first = offsets[match] if offsets is not None else match
        last = offsets[match + len(query) - 1] + 1 if offsets is not None else match + len(query)
        start, end = max(previous_end, first - 80), min(len(text), last + 80)
        yield ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
        # Nearby occurrences belong to this same excerpt.
        position = bisect_left(offsets, end) if offsets is not None else end
        previous_end = end


def _sources(state, path, targets):
    owners = user_owners(path, state)
    histories = {key: [] for key in targets}
    for node in path[1:]:
        value = node.value
        role = value.get("role")
        if value.get("task_control") or role not in {"user", "assistant"}:
            continue
        keys = owners.get(node.id, ()) if role == "user" else (value.get("task_context_id"),)
        for key in keys:
            if key in histories:
                histories[key].append(value)
    for key in targets:
        doc = state[SHARED_ID] if key == SHARED_ID else state["documents"][key]
        for field in ("title", "summary", "body"):
            yield key, field, None, doc.get(field, "")
        seen = set()
        # Raw branch records retain dialogue hidden by compaction. Saved
        # messages also cover legacy histories, without repeating exact text.
        for message in [*histories[key], *doc.get("messages", [])]:
            role, content = message.get("role"), message.get("content")
            if role not in {"user", "assistant"} or message.get("compacted_block"):
                continue
            if not isinstance(content, str) or not content or (role, content) in seen:
                continue
            seen.add((role, content))
            yield key, "message", role, content


def _search_limit(args):
    limit = args.get("limit", 10)
    if type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("limit must be an integer from 1 to 30")
    return limit


def search_context(state, path, args):
    if set(args) - {"query", "context_id", "limit", "cursor"}:
        raise ValueError("search_context accepts only query, context_id, limit and cursor")
    query = args.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise ValueError("query must contain 1 to 200 characters")
    query = query.strip().casefold()
    limit = _search_limit(args)
    targets = [SHARED_ID, *state["documents"]]
    if "context_id" in args:
        if args["context_id"] not in targets:
            raise ValueError("Unknown context_id; use the context catalog")
        targets = [args["context_id"]]
    # Bind pagination to parameters and source contents, never to the search
    # call/result nodes themselves. Reject stale cursors instead of skipping hits.
    digest = hashlib.sha256(json.dumps([query, targets, limit]).encode())
    matches = []
    for key, source, role, content in _sources(state, path, targets):
        digest.update(json.dumps([key, source, role, content], ensure_ascii=False).encode())
        for snippet in _snippets(content, query):
            matches.append((key, {"source": source, **({"role": role} if role else {}), "snippet": snippet}))
    fingerprint = digest.hexdigest()[:16]
    start = 0
    if "cursor" in args:
        cursor = args["cursor"]
        if not isinstance(cursor, str):
            raise ValueError("Invalid search cursor")
        prefix, separator, offset = cursor.partition(":")
        if prefix != fingerprint or not separator or not offset.isascii() or not offset.isdecimal() or len(offset) > 12:
            raise ValueError("Search cursor is invalid or contents changed; restart the search")
        start = int(offset)
        if not 0 < start < len(matches):
            raise ValueError("Invalid search cursor offset")
    groups = {}
    for key, match in matches[start:start + limit]:
        doc = state[SHARED_ID] if key == SHARED_ID else state["documents"][key]
        group = groups.setdefault(key, {"context_id": key, "title": doc.get("title", ""),
                                        "archived": bool(doc.get("archived")), "matches": []})
        group["matches"].append(match)
    end = start + limit
    return {"contexts": list(groups.values()),
            "next_cursor": f"{fingerprint}:{end}" if end < len(matches) else None}
