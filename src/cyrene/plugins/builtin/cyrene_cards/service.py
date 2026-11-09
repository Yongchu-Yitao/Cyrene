"""Persistent card commands and trusted UI input ingestion (no model execution)."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import uuid

from .definitions import DEFINITIONS, ID, REVISION, obj
from .schemas import MAX_BYTES, CardError, encoded, fingerprint, normalize, validate, validate_values
from .store import Store

EVENT_SCHEMA = obj({"event_id": ID, "base_revision": REVISION, "base_input_revision": {"type": "integer", "minimum": 0},
                    "kind": {"enum": ["change", "submit", "confirm"]}, "action_id": ID, "values": {"type": "object"}},
                   ["event_id", "base_revision", "base_input_revision", "kind", "values"])


def now():
    return datetime.now(timezone.utc).isoformat()


def summary(card):
    return {key: card[key] for key in ("card_id", "template", "origin", "revision", "input_revision", "placement_revision", "placement", "status", "updated_at")} | {
        "title": card["spec"]["title"], "home_kind": card["spec"].get("home_kind"), "user_unpinned": card["user_unpinned"], "presentation": {"kind": "assistant_card", "card_id": card["card_id"], "revision": card["revision"], **({"home_kind": card["spec"]["home_kind"]} if card["spec"].get("home_kind") else {})},
    }


class CardService:
    def __init__(self, directory):
        self.store = Store(directory)
        # Welcome slots now live exclusively in the home surface, including older user pins.
        with self.store.connect(write=True) as db:
            rows = db.execute("SELECT document FROM cards WHERE pinned=1 AND json_extract(document,'$.spec.home_kind') IS NOT NULL").fetchall()
            for row in rows:
                card = json.loads(row["document"])
                card.update(placement="conversation", placement_revision=card["placement_revision"] + 1)
                self._save(db, card)

    @staticmethod
    def _get(db, owner, card_id):
        row = db.execute("SELECT document FROM cards WHERE id=? AND owner=?", (card_id, owner)).fetchone()
        if row is None:
            raise CardError("card_not_found", "Card not found in this assistant's scope")
        return json.loads(row["document"])

    @staticmethod
    def _save(db, card, *, version=False):
        document = encoded(card)
        db.execute("INSERT INTO cards VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,pinned=excluded.pinned,document=excluded.document",
                   (card["card_id"], card["origin"]["session_id"], card["origin"]["context_id"], card["status"], card["placement"] == "pinned", document))
        if version:
            db.execute("INSERT INTO versions VALUES (?,?,?)", (card["card_id"], card["revision"], document))

    @staticmethod
    def _active(card):
        if card["status"] != "active":
            raise CardError("card_archived", "Archived cards are read-only")

    @staticmethod
    def _revision(card, key, expected):
        if card[key] != expected:
            raise CardError("revision_conflict", f"Expected current {key}={card[key]}; read the card again")

    async def command(self, owner, context_id, operation, args, *, effect=None, origin=None):
        validate(DEFINITIONS[operation][1], args)
        encoded(args)
        return await asyncio.to_thread(self._command, owner, context_id, operation, deepcopy(args), effect, origin)

    def _command(self, owner, context_id, operation, args, effect, origin):
        writing = operation not in {"card.get", "card.list"}
        if writing and not effect:
            raise CardError("missing_execution", "Card mutations require a runtime call identity")
        digest = fingerprint({"operation": operation, "args": args, "context_id": context_id})
        with self.store.connect(write=writing) as db:
            if writing:
                row = db.execute("SELECT * FROM effects WHERE owner=? AND effect=?", (owner, effect)).fetchone()
                if row:
                    if row["fingerprint"] != digest:
                        raise CardError("replay_conflict", "Call identity was already used with different arguments")
                    return json.loads(row["result"])
            if operation == "card.list":
                return self._list(db, owner, context_id, args)
            if operation == "card.create":
                spec = normalize(args["template"], args["spec"])
                if spec.get("home_kind"):
                    existing = db.execute("SELECT id FROM cards WHERE owner=? AND status='active' AND json_extract(document,'$.spec.home_kind')=?",
                                          (owner, spec["home_kind"])).fetchone()
                    if existing:
                        raise CardError("home_card_exists", f"Use card.get and card.update for {existing['id']}")
                card = {"card_id": "card_" + uuid.uuid4().hex, "template": args["template"], "template_version": 1,
                        "spec": spec, "origin": {**(origin or {}), "session_id": owner, "context_id": context_id},
                        "revision": 1, "input_revision": 0, "placement_revision": 1,
                        "placement": "pinned" if args.get("pinned", False) and not spec.get("home_kind") else "conversation", "user_unpinned": False,
                        "status": "active", "created_at": now(), "updated_at": now(),
                        "input_state": {"draft": {}, "submitted": None}}
                self._save(db, card, version=True)
            else:
                card = self._get(db, owner, args["card_id"])
                if operation == "card.get":
                    submitted = card["input_state"].get("submitted")
                    if submitted:
                        submitted["delivery"] = self._delivery(db, owner, submitted["event_id"])
                    if args.get("include_events"):
                        card["events"], card["next_event_id"] = self._events(db, owner, card["card_id"], args)
                    return card
                if operation == "card.pin":
                    self._pin(card, args["pinned"], args["expected_placement_revision"], user=False)
                else:
                    self._modify(db, owner, context_id, operation, card, args)
                card["updated_at"] = now()
                self._save(db, card, version=operation != "card.pin")
            result = summary(card)
            result["presentation"]["operation"] = operation
            db.execute("INSERT INTO effects VALUES (?,?,?,?)", (owner, effect, digest, encoded(result)))
            return result

    def _modify(self, db, owner, context_id, operation, card, args):
        self._active(card)
        self._revision(card, "revision", args["expected_revision"])
        if not card["spec"].get("home_kind") and card["origin"]["context_id"] != context_id:
            raise CardError("source_context_mismatch", "Resume the card's source context before modifying it")
        if operation == "card.update":
            self._update(db, owner, card, args)
        elif operation == "card.archive":
            if card["spec"].get("home_kind"):
                raise CardError("invalid_card", "Keep home slot IDs stable; use home_visible=false instead of archiving")
            card.update(status="archived", archive_reason=args.get("reason", ""))
        card["revision"] += 1

    def _update(self, db, owner, card, args):
        if "home_kind" in args["changes"] and args["changes"]["home_kind"] != card["spec"].get("home_kind"):
            raise CardError("invalid_card", "home_kind is assigned at creation and cannot be changed")
        spec = normalize(card["template"], {**card["spec"], **args["changes"]})
        old_fields = card["spec"]["input_schema"].get("properties", {})
        new_fields = spec["input_schema"].get("properties", {})
        draft = card["input_state"]["draft"]
        try:
            if any(new_fields.get(key, {}).get("type") != old_fields.get(key, {}).get("type") for key in draft):
                raise CardError("input_conflict", "Input type changed")
            validate_values(spec, draft)
        except CardError as exc:
            raise CardError("input_conflict", "Update would invalidate user input; preserve the input schema and selected rows") from exc
        event_id = args.get("in_response_to_event_id")
        if event_id:
            event = db.execute("SELECT 1 FROM events WHERE owner=? AND card_id=? AND event_id=?", (owner, card["card_id"], event_id)).fetchone()
            if not event:
                raise CardError("event_not_found", "Response event does not belong to this card")
            card["in_response_to_event_id"] = event_id
        card["spec"] = spec

    @staticmethod
    def _pin(card, pinned, expected, *, user):
        CardService._revision(card, "placement_revision", expected)
        if pinned and card["spec"].get("home_kind"):
            raise CardError("user_placement_override", "Home sections cannot be pinned")
        if pinned and card["user_unpinned"] and not user:
            raise CardError("user_placement_override", "User unpinned this card; only the user can restore pinning")
        card["placement"] = "pinned" if pinned else "conversation"
        card["placement_revision"] += 1
        card["placement_actor"] = "user" if user else "assistant"
        if user:
            card["user_unpinned"] = not pinned

    def _list(self, db, owner, context_id, args):
        where, params = ["owner=?"], [owner]
        if args.get("scope", "current_task") == "pinned":
            where.append("pinned=1")
        elif args.get("scope") == "home":
            where.append("json_extract(document,'$.spec.home_kind') IS NOT NULL")
        else:
            where.append("context_id IS ?")
            params.append(context_id)
        if args.get("status"):
            where.append("status=?")
            params.append(args["status"])
        if args.get("cursor"):
            row = db.execute("SELECT rowid FROM cards WHERE id=? AND owner=?", (args["cursor"], owner)).fetchone()
            if not row:
                raise CardError("invalid_cursor", "Unknown card cursor")
            where.append("rowid>?")
            params.append(row["rowid"])
        limit = args.get("limit", 20)
        rows = db.execute("SELECT document FROM cards WHERE " + " AND ".join(where) + " ORDER BY rowid LIMIT ?", [*params, limit + 1]).fetchall()
        cards = [summary(json.loads(row["document"])) for row in rows[:limit]]
        return {"cards": cards, "next_cursor": cards[-1]["card_id"] if len(rows) > limit else None}

    @staticmethod
    def _events(db, owner, card_id, args):
        seq = 0
        if args.get("after_event_id"):
            row = db.execute("SELECT seq FROM events WHERE owner=? AND card_id=? AND event_id=?", (owner, card_id, args["after_event_id"])).fetchone()
            if not row:
                raise CardError("invalid_cursor", "Unknown event cursor for this card")
            seq = row["seq"]
        limit = args.get("limit", 20)
        rows = db.execute("SELECT document FROM events WHERE owner=? AND card_id=? AND seq>? ORDER BY seq LIMIT ?", (owner, card_id, seq, limit + 1)).fetchall()
        events, size = [], 0
        for row in rows[:limit]:
            row_size = len(row["document"].encode())
            if events and size + row_size > MAX_BYTES:
                break
            events.append(json.loads(row["document"]))
            size += row_size
        return events, events[-1]["event_id"] if len(rows) > len(events) else None

    async def user_pin(self, owner, card_id, pinned, expected):
        validate(obj({"pinned": {"type": "boolean"}, "revision": REVISION}, ["pinned", "revision"]), {"pinned": pinned, "revision": expected})
        return await asyncio.to_thread(self._user_pin, owner, card_id, pinned, expected)

    def _user_pin(self, owner, card_id, pinned, expected):
        with self.store.connect(write=True) as db:
            card = self._get(db, owner, card_id)
            self._pin(card, pinned, expected, user=True)
            card["updated_at"] = now()
            self._save(db, card)
            return summary(card)

    async def user_event(self, owner, card_id, event, *, delivery_version=None):
        validate(EVENT_SCHEMA, event)
        encoded(event)
        return await asyncio.to_thread(self._user_event, owner, card_id, deepcopy(event), delivery_version)

    def _user_event(self, owner, card_id, event, delivery_version=None):
        digest = fingerprint({"card_id": card_id, "event": event})
        with self.store.connect(write=True) as db:
            card = self._get(db, owner, card_id)
            previous = db.execute("SELECT * FROM events WHERE owner=? AND event_id=?", (owner, event["event_id"])).fetchone()
            if previous:
                if previous["fingerprint"] != digest:
                    raise CardError("replay_conflict", "Event ID conflicts with an existing event")
                return json.loads(previous["document"])
            self._active(card)
            self._revision(card, "revision", event["base_revision"])
            self._revision(card, "input_revision", event["base_input_revision"])
            complete = event["kind"] != "change"
            values = event["values"] if complete else {**card["input_state"]["draft"], **event["values"]}
            validate_values(card["spec"], values, complete=complete)
            if complete:
                action = next((a for a in card["spec"]["actions"] if a["action_id"] == event.get("action_id")), None)
                if not action or action["kind"] != event["kind"] or not set(action["fields"]) <= values.keys():
                    raise CardError("invalid_action", "Action kind and fields must match the current card")
            elif "action_id" in event:
                raise CardError("invalid_action", "Changes cannot invoke an action")
            card["input_revision"] += 1
            card["input_state"]["draft"] = values
            receipt = {**event, "values": values, "card_id": card_id, "origin": card["origin"], "actor": "user",
                       "input_revision": card["input_revision"], "received_at": now(), "status": "accepted"}
            if complete:
                # Immutable evidence survives subsequent user edits and Agent updates.
                receipt["spec_snapshot"] = card["spec"]
                card["input_state"]["submitted"] = {"event_id": event["event_id"], "kind": event["kind"], "values": values,
                                                     "revision": card["revision"], "input_revision": card["input_revision"]}
            card["updated_at"] = now()
            self._save(db, card)
            db.execute("INSERT INTO events(owner,event_id,card_id,fingerprint,document) VALUES (?,?,?,?,?)",
                       (owner, event["event_id"], card_id, digest, encoded(receipt)))
            if complete and delivery_version is not None:
                db.execute("INSERT INTO deliveries VALUES (?,?,?,?,?)",
                           (owner, event["event_id"], delivery_version, "pending", "{}"))
            return receipt

    @staticmethod
    def _delivery(db, owner, event_id):
        row = db.execute("SELECT state,detail FROM deliveries WHERE owner=? AND event_id=?", (owner, event_id)).fetchone()
        return {**json.loads(row["detail"]), "status": row["state"]} if row else None

    async def context_snapshot(self, owner, context_id):
        return await asyncio.to_thread(self._context_snapshot, owner, context_id)

    def _context_snapshot(self, owner, context_id):
        with self.store.connect() as db:
            rows = db.execute("SELECT document FROM cards WHERE owner=? AND context_id IS ? ORDER BY rowid DESC LIMIT 21", (owner, context_id)).fetchall()
            cards = []
            for row in rows[:20]:
                card = json.loads(row["document"])
                state = card["input_state"]
                # Large fields stay inspectable through card.get instead of swelling prompts.
                small = len(encoded(state)) <= 4000
                cards.append({**summary(card), "input_state": state if small else {"detail": "Read card.get for user input"}})
            return {"cards": cards, "has_more": len(rows) > 20}
