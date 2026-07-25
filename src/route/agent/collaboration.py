"""Agent collaboration routes."""

# ruff: noqa: F403,F405

from cyrene.workbench.runtime import *


def register_collaboration_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Group chat ----

    @router.get("/api/chat/agent-chat-messages")
    async def api_agent_chat_messages(round_id: str = ""):
        from cyrene.subagent import build_group_chat_messages

        if not round_id:
            return {"messages": [], "agents": []}
        return await build_group_chat_messages(round_id)

    @router.post("/api/chat/send-to-agents")
    async def api_send_to_agents(body: dict[str, Any]):
        from cyrene.subagent import _registry as _sub_reg
        from cyrene.runtime.inbox import send_message as _send_inbox, clear_inbox as _clear_inbox
        from cyrene.observability import debug as _debug_comm

        round_id = str(body.get("round_id", "") or "").strip()
        text = str(body.get("text", "") or "").strip()
        mentions = body.get("mentions")
        attachments = body.get("attachments") or []

        if not round_id or not text:
            return {"ok": False, "error": "round_id and text are required"}

        # Build the full message text (append file references)
        full_text = text
        for att in attachments:
            path = str(att.get("path", "") or "").strip()
            name = str(att.get("name", "") or "").strip()
            if path:
                full_text += f"\n\n[{name}]({path})" if name else f"\n\n{path}"

        # Determine target agents
        if mentions and isinstance(mentions, list):
            targets = [str(m).strip() for m in mentions if str(m).strip()]
        else:
            # Send to all active subagents in this round
            from cyrene.subagent import _lock as _reg_lock

            async with _reg_lock:
                targets = [
                    aid for aid, info in _sub_reg.items()
                    if round_id and str(info.get("round_id", "") or "").strip() == round_id
                    and aid != "main"
                ]

        if not targets:
            return {"ok": False, "error": "No target agents found"}

        sent_to: list[str] = []
        first_msg_id = ""
        for target in targets:
            info = _sub_reg.get(target)
            is_done_timeout = info and str(info.get("status", "")).strip() in ("done", "timeout", "incomplete")

            if is_done_timeout:
                wrapped = f"User sent you a new task. This is a round — complete it and report your result via quit.\n\n{full_text}"
            else:
                wrapped = (
                    f"[DIRECT_MESSAGE]\n"
                    f"The user has sent you guidance. This takes priority over your current approach — "
                    f"adjust your work accordingly. Use send_message_to_user ONCE to acknowledge and "
                    f"briefly say what you will change. Then continue working with the adjusted approach.\n\n"
                    f"User guidance:\n{full_text}"
                )

            # 清空 inbox 确保 subagent 只看到这条用户消息
            await _clear_inbox(target)

            msg_id = await _send_inbox(
                from_agent="user",
                to_agent=target,
                msg_type="guidance",
                content=wrapped,
                round_id=round_id,
            )
            if msg_id:
                sent_to.append(target)
                if not first_msg_id:
                    first_msg_id = msg_id
                # Handle DONE/TIMEOUT agents: reactivate + spawn new task
                if is_done_timeout:
                    from cyrene.subagent import (
                        reactivate as _reactivate,
                        get_raw_messages as _get_raw,
                        _spawn_subagent_task,
                        _run_subagent,
                    )

                    reactivated = await _reactivate(target)
                    if reactivated:
                        raw_msgs = await _get_raw(target)
                        _spawn_subagent_task(
                            _run_subagent(target, str(info.get("task") or ""), _bot, _CHAT_ID, _db_path, resume_messages=raw_msgs),
                            target,
                        )

        # Publish SSE event for real-time group-chat update
        await _debug_comm.publish_event({
            "type": "agent_chat_user_message",
            "round_id": round_id,
            "message": {
                "id": first_msg_id or f"user_msg_{int(time.time() * 1000)}",
                "type": "user_message",
                "from": "user",
                "to": "all" if not mentions else ",".join(mentions),
                "content": text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "round_id": round_id,
            },
        })

        return {"ok": True, "sent_to": sent_to}
