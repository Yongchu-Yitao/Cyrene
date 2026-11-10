"""Task catalog edits and bounded searches over the tree-owned task state."""
from __future__ import annotations

import hashlib
import json

from .tasks import context_catalog, context_title


def edit_catalog(state, args):
    action = args.get("action")
    target = args.get("context_id")
    if target not in state["documents"]:
        raise ValueError("Use an existing task context ID; shared cannot be edited here")
    fields = {}
    if action == "update":
        if set(args) - {"action", "context_id", "title", "summary"}:
            raise ValueError("update accepts only context_id, title and summary")
        if "title" in args:
            fields["title"] = context_title(args["title"])
        if "summary" in args:
            summary = args["summary"]
            if not isinstance(summary, str) or not summary.strip() or len(summary) > 200:
                raise ValueError("summary must contain 1 to 200 characters")
            fields["summary"] = summary.strip()
        if not fields:
            raise ValueError("Supply at least one of title or summary")
    elif action in {"archive", "restore"}:
        if set(args) - {"action", "context_id"}:
            raise ValueError("archive and restore accept only context_id")
        if action == "archive" and target == state["active"]:
            raise ValueError("Checkpoint and switch or unload the active context before archiving it")
        fields["archived"] = action == "archive"
    else:
        raise ValueError("Unknown catalog action")
    state["documents"][target].update(fields)
    return fields


def search_catalog(state, args):
    if set(args) - {"action", "query", "status", "cursor"}:
        raise ValueError("search accepts only query, status and cursor")
    query = args.get("query", "")
    status = args.get("status", "all")
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError("query must be a string of at most 200 characters")
    if status not in {"all", "archived", "unarchived"}:
        raise ValueError("status must be all, archived or unarchived")
    query = query.strip().casefold()
    fingerprint = hashlib.sha256(json.dumps([query, status]).encode()).hexdigest()[:16]
    entries = [entry for entry in context_catalog(state)[1:]
               if (status == "all" or bool(entry.get("archived")) == (status == "archived"))
               and any(query in entry[field].casefold() for field in ("id", "title", "summary"))]
    start = 0
    cursor = args.get("cursor")
    if cursor is not None:
        prefix, separator, after = cursor.partition(":")
        ids = [entry["id"] for entry in entries]
        if prefix != fingerprint or not separator or after not in ids:
            raise ValueError("Search cursor is invalid or its entry changed; restart the search")
        start = ids.index(after) + 1
    page = [{**entry, "archived": bool(entry.get("archived"))} for entry in entries[start:start + 10]]
    next_cursor = f"{fingerprint}:{page[-1]['id']}" if start + len(page) < len(entries) else None
    return {"entries": page, "next_cursor": next_cursor}
