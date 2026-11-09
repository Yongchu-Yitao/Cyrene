"""Persistent presentation settings for existing assistant tasks."""


def organize_task(store, host, owner, target, args):
    changes = {k: v for k, v in args.items() if k != "session_id"}
    if "title" in changes:
        changes["title"] = changes["title"].strip()
        if not changes["title"]:
            raise ValueError("Title must not be empty")
    if "attachment_ids" in changes:
        source = host.chat(owner) or {}
        attachments = {a.get("id"): a for m in source.get("messages", [])
                       if m.get("role") == "assistant" and not m.get("questionPrompt")
                       for a in m.get("attachments", []) if a.get("id")}
        if any(key not in attachments for key in changes["attachment_ids"]):
            raise ValueError("Select attachments delivered in the assistant conversation")
        changes["files"] = [attachments[key] for key in dict.fromkeys(changes.pop("attachment_ids"))]
    return {"session_id": target, "presentation": store.save_presentation(target, changes)}
