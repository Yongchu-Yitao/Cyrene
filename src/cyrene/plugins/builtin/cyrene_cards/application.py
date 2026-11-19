"""Application-owned persistence and UI bridge; no agent creation or second loop."""

import asyncio
import json

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .schemas import CardError
from .service import CardService
from .delivery import CardDelivery


def setup_application(context):
    from cyrene.workbench.application.app_services import chat_application_port
    from cyrene.workbench.projects.assistant import ASSISTANT_ID

    service = CardService(context.data_directory / "plugin_data" / "cyrene_cards")
    context.provide("cards", service)
    from .home import HomeCards
    home = HomeCards(service, chat_application_port)
    context.on_startup(home.start)
    context.on_shutdown(home.stop)
    delivery = CardDelivery(service, chat_application_port)
    context.on_startup(delivery.start)
    context.on_shutdown(delivery.stop)

    def owner(request: Request | None = None):
        # Match the canonical assistant identity; never accept an owner/actor from JSON.
        port = chat_application_port()
        session_id = request.path_params.get("session_id", ASSISTANT_ID) if request else ASSISTANT_ID
        chat = port.service.repository.get(session_id)
        if not chat or (session_id == ASSISTANT_ID and chat.get("kind") != "personal-assistant"):
            raise HTTPException(404, "Conversation not found")
        return session_id

    async def respond(operation):
        try:
            return await operation()
        except CardError as exc:
            status = 404 if exc.code == "card_not_found" else 409 if exc.code in {
                "revision_conflict", "replay_conflict", "card_archived", "user_placement_override"} else 422
            return JSONResponse({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, status_code=status)

    @context.router.get("/api/chats/{session_id}/cards")
    @context.router.get("/api/assistant/cards")
    async def cards(request: Request, scope: str = "pinned", context_id: str | None = None, cursor: str | None = None, limit: int = 20):
        args = {"scope": scope, "limit": limit}
        if cursor is not None:
            args["cursor"] = cursor
        session_id = owner(request)
        async def listing():
            page = await service.command(session_id, context_id, "card.list", args, allow_home=session_id == ASSISTANT_ID)
            if scope == "pinned" and cursor is None:
                page["supports_bulk_unpin"] = True
                page["groups"] = await asyncio.to_thread(service.pinned_groups, session_id)
            return page
        return await respond(listing)

    @context.router.delete("/api/chats/{session_id}/cards/pins")
    @context.router.delete("/api/assistant/cards/pins")
    async def unpin_all(request: Request):
        return await respond(lambda: asyncio.to_thread(service.unpin_all, owner(request)))

    @context.router.put("/api/chats/{session_id}/cards/groups/pin")
    @context.router.put("/api/assistant/cards/groups/pin")
    async def group_pin(request: Request, body: dict):
        return await respond(lambda: asyncio.to_thread(service.pin_group, owner(request), body))


    @context.router.get("/api/assistant/cards/home")
    async def saved_home():
        return await respond(lambda: home.saved(owner()))

    @context.router.post("/api/assistant/cards/home/display")
    async def display_home(body: dict):
        return await respond(lambda: home.claim_display(owner(), body))

    @context.router.post("/api/assistant/cards/home/refresh")
    async def refresh_home(body: dict):
        return await respond(lambda: home.refresh(owner(), body))

    @context.router.get("/api/chats/{session_id}/cards/{card_id}")
    @context.router.get("/api/assistant/cards/{card_id}")
    async def card(request: Request, card_id: str, revision: int | None = None, include_events: bool = False, after_event_id: str | None = None, limit: int = 20):
        if revision is not None:
            return await respond(lambda: service.version(owner(request), card_id, revision))
        args = {"card_id": card_id, "include_events": include_events, "limit": limit}
        if after_event_id is not None:
            args["after_event_id"] = after_event_id
        return await respond(lambda: service.command(owner(request), None, "card.get", args))

    @context.router.post("/api/chats/{session_id}/cards/{card_id}/events")
    @context.router.post("/api/assistant/cards/{card_id}/events")
    async def event(request: Request, card_id: str, body: dict):
        async def submit():
            session_id = owner(request)
            chat = chat_application_port().service.repository.get(session_id)
            version = json.dumps([chat.get("createdAt", ""), chat.get("messageGeneration", 0)])
            receipt = await service.user_event(session_id, card_id, body, delivery_version=version)
            if receipt["kind"] != "change":
                receipt["delivery"] = await delivery.deliver(session_id, card_id, receipt["event_id"])
            return receipt
        return await respond(submit)

    @context.router.post("/api/chats/{session_id}/cards/{card_id}/events/{event_id}/retry")
    @context.router.post("/api/assistant/cards/{card_id}/events/{event_id}/retry")
    async def retry(request: Request, card_id: str, event_id: str):
        return await respond(lambda: delivery.deliver(owner(request), card_id, event_id, retry=True))

    @context.router.put("/api/chats/{session_id}/cards/{card_id}/pin")
    @context.router.put("/api/assistant/cards/{card_id}/pin")
    async def pin(request: Request, card_id: str, body: dict):
        from .definitions import REVISION, obj
        from .schemas import validate

        async def update():
            validate(obj({"pinned": {"type": "boolean"}, "expected_placement_revision": REVISION},
                         ["pinned", "expected_placement_revision"]), body)
            return await service.user_pin(owner(request), card_id, body["pinned"], body["expected_placement_revision"])
        return await respond(update)

    # The conversation and pinned right column share card IDs and this API.
