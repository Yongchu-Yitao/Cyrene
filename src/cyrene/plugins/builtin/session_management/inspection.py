"""Bounded, explicit paging of task evidence and captured final replies."""

MAX_CONTENT_CHARS = 20000
NOTIFICATION_REPLY_CHARS = 6000


def inspection_view(args):
    view = args.get("view", "content" if args.get("block_ids") else "directory")
    if view not in {"directory", "content", "latest_result"}:
        raise ValueError("Unknown inspection view")
    anchor = args.get("anchor", "start")
    if anchor not in {"start", "end"}:
        raise ValueError("anchor must be start or end")
    if anchor == "end" and "content_offset" in args:
        raise ValueError("anchor=end cannot be combined with content_offset")
    if view == "content" and not args.get("block_ids"):
        raise ValueError("Select block_ids for content, or use view=latest_result")
    if view == "latest_result" and any(key in args for key in ("block_ids", "offset", "limit")):
        raise ValueError("latest_result does not accept block pagination")
    if view != "latest_result" and "node_id" in args:
        raise ValueError("node_id is only supported by latest_result")
    if view == "directory" and any(key in args for key in ("anchor", "content_offset", "content_limit")):
        raise ValueError("Content pagination requires view=content or latest_result")
    return view


def content_page(content, args):
    limit = args.get("content_limit", 4000)
    offset = args.get("content_offset", 0)
    if type(limit) is not int or not 1 <= limit <= MAX_CONTENT_CHARS:
        raise ValueError("content_limit must be between 1 and 20000")
    if type(offset) is not int or offset < 0:
        raise ValueError("content_offset must be a nonnegative integer")
    if args.get("anchor", "start") not in {"start", "end"}:
        raise ValueError("anchor must be start or end")
    if args.get("anchor") == "end":
        if "content_offset" in args:
            raise ValueError("anchor=end cannot be combined with content_offset")
        offset = max(0, len(content) - limit)
    length = len(content)
    status = ("out_of_range" if offset > length else "empty" if not length
              else "end_of_content" if offset == length else "ok")
    end = min(length, offset + limit)
    result = {
        "status": status, "content_length": length,
        "content_offset": offset, "content_end": max(offset, end),
        "content": content[offset:end],
        "has_before": bool(length and offset), "has_after": end < length,
        "next_content_offset": end if end < length else None,
        # Keep the legacy meaning: more text follows this page.
        "truncated": end < length,
    }
    if status == "out_of_range":
        result.update(content_end=None, suggested_tail_offset=max(0, length - limit))
    return result


def final_reply(path, checkpoint):
    """Only a completed execution can supply a final reply; never use progress."""
    if checkpoint.get("status") != "completed":
        return None
    return next((node for node in reversed(path)
                 if node.value.get("role") == "assistant"
                 and node.value.get("run_id") == checkpoint.get("run_id")
                 and not node.value.get("error") and not node.value.get("cancelled")
                 and not node.value.get("tool_calls")
                 and (node.value.get("answer_complete") is True
                      or node.value.get("session_end_complete") is True)), None)
