"""Application-owned persistence and UI bridge; no agent creation or second loop."""

import json

from fastapi import HTTPException
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

    def owner():
        # Match the canonical assistant identity; never accept an owner/actor from JSON.
        port = chat_application_port()
        chat = port.service.repository.get(ASSISTANT_ID)
        if not chat or chat.get("kind") != "personal-assistant":
            raise HTTPException(404, "Personal assistant session not found")
        return ASSISTANT_ID

    async def respond(operation):
        try:
            return await operation()
        except CardError as exc:
            status = 404 if exc.code == "card_not_found" else 409 if exc.code in {
                "revision_conflict", "replay_conflict", "card_archived", "user_placement_override"} else 422
            return JSONResponse({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, status_code=status)

    @context.router.get("/api/assistant/cards")
    async def cards(scope: str = "pinned", context_id: str | None = None, cursor: str | None = None, limit: int = 20):
        args = {"scope": scope, "limit": limit}
        if cursor is not None:
            args["cursor"] = cursor
        return await respond(lambda: service.command(owner(), context_id, "card.list", args))

    @context.router.get("/api/assistant/cards/home")
    async def saved_home():
        return await respond(lambda: home.saved(owner()))

    @context.router.post("/api/assistant/cards/home/display")
    async def display_home(body: dict):
        return await respond(lambda: home.claim_display(owner(), body))

    @context.router.post("/api/assistant/cards/home/refresh")
    async def refresh_home(body: dict):
        return await respond(lambda: home.refresh(owner(), body))

    @context.router.get("/api/assistant/cards/{card_id}")
    async def card(card_id: str, include_events: bool = False, after_event_id: str | None = None, limit: int = 20):
        args = {"card_id": card_id, "include_events": include_events, "limit": limit}
        if after_event_id is not None:
            args["after_event_id"] = after_event_id
        return await respond(lambda: service.command(owner(), None, "card.get", args))

    @context.router.post("/api/assistant/cards/{card_id}/events")
    async def event(card_id: str, body: dict):
        async def submit():
            session_id = owner()
            chat = chat_application_port().service.repository.get(session_id)
            version = json.dumps([chat.get("createdAt", ""), chat.get("messageGeneration", 0)])
            receipt = await service.user_event(session_id, card_id, body, delivery_version=version)
            if receipt["kind"] != "change":
                receipt["delivery"] = await delivery.deliver(session_id, card_id, receipt["event_id"])
            return receipt
        return await respond(submit)

    @context.router.post("/api/assistant/cards/{card_id}/events/{event_id}/retry")
    async def retry(card_id: str, event_id: str):
        return await respond(lambda: delivery.deliver(owner(), card_id, event_id, retry=True))

    @context.router.put("/api/assistant/cards/{card_id}/pin")
    async def pin(card_id: str, body: dict):
        from .definitions import REVISION, obj
        from .schemas import validate

        async def update():
            validate(obj({"pinned": {"type": "boolean"}, "expected_placement_revision": REVISION},
                         ["pinned", "expected_placement_revision"]), body)
            return await service.user_pin(owner(), card_id, body["pinned"], body["expected_placement_revision"])
        return await respond(update)

    # The conversation and pinned right column share card IDs and this API.
