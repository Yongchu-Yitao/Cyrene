"""Schedule saved-home maintenance independently from foreground conversations."""

import asyncio
from contextlib import nullcontext
from datetime import datetime
import json
import logging
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .definitions import ID, obj
from .schemas import validate

from .home_publication import ensure_pending_table, publish_due
from .home_preparation import HomeModelUnavailable, prepare_home, source_snapshot
from .home_freshness import RETRY_SECONDS, evidence, weather_expired, display_slot, preparation_target, task_documents
logger = logging.getLogger(__name__)
HOME_CHECK_INTERVAL_SECONDS = 60


class HomeCards:
    def __init__(self, service, port, prepare=prepare_home):
        self.service, self.port, self.prepare = service, port, prepare
        self.lock = asyncio.Lock()
        self.worker = None
        with service.store.connect() as db:
            ensure_pending_table(db)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS home_maintenance (
                    owner TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS home_display_receipts (
                    owner TEXT PRIMARY KEY, slot TEXT NOT NULL, client TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS home_first_start (
                    owner TEXT PRIMARY KEY, day TEXT NOT NULL, client TEXT NOT NULL, eligible INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS home_preferences (
                    owner TEXT PRIMARY KEY, timezone TEXT NOT NULL);
            """)

    async def start(self):
        self.worker = asyncio.create_task(self._work(), name="welcome-card-preparation")

    async def stop(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None

    async def _work(self):
        from cyrene.workbench.projects.assistant import ASSISTANT_ID
        # Opening the app only displays persisted cards. Preparation runs separately.
        await asyncio.sleep(5)
        while True:
            try:
                await self.refresh(ASSISTANT_ID, {})
            except Exception:
                logger.exception("Welcome card preparation failed; keeping saved cards")
            await asyncio.sleep(HOME_CHECK_INTERVAL_SECONDS)

    async def _publish_due(self, owner):
        def publish():
            with self.service.store.connect() as db:
                if not db.execute("SELECT 1 FROM home_prepared WHERE owner=? AND publish_at<=?",
                                  (owner, time.time())).fetchone():
                    return
            repository = self.port().service.repository
            from cyrene.workbench.sessions.admission import session_admission
            admission = session_admission(repository._database(), owner) if hasattr(repository, '_database') else nullcontext()
            with admission:
                chat = repository.get(owner)
                version = json.dumps([chat.get("createdAt", ""), chat.get("messageGeneration", 0)]) if chat else None
                publish_due(self.service, owner, version, time.time())
        await asyncio.to_thread(publish)

    async def saved(self, owner):
        await self._publish_due(owner)
        def read():
            with self.service.store.connect() as db:
                rows = db.execute("SELECT document FROM cards WHERE owner=? AND status='active' "
                                  "AND json_extract(document,'$.spec.home_kind') IS NOT NULL", (owner,)).fetchall()
                cards = [json.loads(row["document"]) for row in rows]
                for card in cards:
                    submitted = card["input_state"].get("submitted")
                    if submitted:
                        submitted["delivery"] = self.service._delivery(db, owner, submitted["event_id"])
                return {"cards": [card for card in cards if card["spec"].get("home_visible", True)]}
        return await asyncio.to_thread(read)

    @staticmethod
    def zone(body):
        try:
            return ZoneInfo(body["timezone"]) if body.get("timezone") else datetime.now().astimezone().tzinfo
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    async def claim_display(self, owner, body):
        validate(obj({"timezone": {"type": "string", "maxLength": 100}, "client_id": ID, "started_at": {"type": "number"}}, ["client_id"]), body)
        zone = self.zone(body)
        local = datetime.fromtimestamp(time.time(), zone)
        slot = display_slot(local)
        started = datetime.fromtimestamp(body.get("started_at", time.time()), zone)
        # Remember the client timezone even before the first display is eligible,
        # so morning preparation runs at 07:30 in that timezone.
        with self.service.store.connect(write=True) as db:
            db.execute("INSERT OR REPLACE INTO home_preferences VALUES (?,?)", (owner, body.get("timezone", str(zone))))
            first = db.execute("SELECT day,client,eligible FROM home_first_start WHERE owner=?", (owner,)).fetchone()
            if started.date() == local.date() and (first is None or first['day'] != local.date().isoformat()):
                db.execute("INSERT OR REPLACE INTO home_first_start VALUES (?,?,?,?)",
                           (owner, local.date().isoformat(), body['client_id'], int(8 <= started.hour < 11)))
                first = db.execute("SELECT day,client,eligible FROM home_first_start WHERE owner=?", (owner,)).fetchone()
            if local.hour < 12 and not (8 <= local.hour < 11 and first is not None
                    and first['day'] == local.date().isoformat() and first['client'] == body['client_id'] and first['eligible']):
                slot = None
        if slot is None:
            return {"slot": None}
        if not (await self.saved(owner))["cards"]:
            return {"slot": None}
        with self.service.store.connect(write=True) as db:
            previous = db.execute("SELECT slot, client FROM home_display_receipts WHERE owner=?", (owner,)).fetchone()
            # Do not replay missed periods or repeat the current period after restart.
            show = previous is None or previous["slot"] < slot
            if show:
                db.execute("INSERT OR REPLACE INTO home_display_receipts VALUES (?,?,?)", (owner, slot, body["client_id"]))
        return {"slot": slot if show or (previous["slot"] == slot and previous["client"] == body["client_id"]) else None}

    @staticmethod
    def _save_state(db, owner, state):
        db.execute("INSERT OR REPLACE INTO home_maintenance VALUES (?,?)", (owner, json.dumps(state)))

    async def refresh(self, owner, body):
        validate(obj({"entry_id": ID, "timezone": {"type": "string", "maxLength": 100}}), body)
        async with self.lock:
            await self._publish_due(owner)
            port = self.port()
            chat = await asyncio.to_thread(port.service.repository.get, owner)
            if not chat or chat.get("kind", "personal-assistant") != "personal-assistant":
                return {"status": "unavailable"}
            version = json.dumps([chat.get("createdAt", ""), chat.get("messageGeneration", 0)])
            with self.service.store.connect() as db:
                row = db.execute("SELECT document FROM home_maintenance WHERE owner=?", (owner,)).fetchone()
                preference = db.execute("SELECT timezone FROM home_preferences WHERE owner=?", (owner,)).fetchone()
            state = json.loads(row[0]) if row else {}
            if state.get("version") != version:
                state = {"version": version}
            zone = self.zone({"timezone": body.get("timezone") or (preference[0] if preference else None)})
            now = time.time()
            local = datetime.fromtimestamp(now, zone)
            # Old foreground admission receipts cannot establish background freshness.
            state.pop("pending", None)
            if now < state.get("retry_after", 0):
                return {"status": "retry_pending"}
            cards = (await self.saved(owner))["cards"]
            repository = port.service.repository
            siblings = (await asyncio.to_thread(repository.read_summaries))["chats"] if hasattr(repository, "read_summaries") else []
            contexts = await asyncio.to_thread(task_documents, port, owner)
            source = evidence(chat, siblings, contexts)
            target = preparation_target(local)
            target_key = target.isoformat()
            scheduled_due = state.get("prepared_slot") != target_key
            reasons = []
            if scheduled_due:
                reasons.append("display period due: prepare the full headline and supporting copy for the target time")
            if source != state.get("source"):
                reasons.append("task evidence changed: reconcile confirmations, progress and delivered artifacts")
            if local.date().isoformat() != state.get("day"):
                reasons.append("new local day: reconcile today's completed items")
            weather_due = weather_expired(cards, now) and now - state.get("weather_checked", 0) >= 600
            if weather_due:
                reasons.append("weather expired (60 minute validity): fetch a current observation")
            if not reasons:
                with self.service.store.connect(write=True) as db:
                    self._save_state(db, owner, state)
                return {"status": "current"}
            prompt = (
                "[Welcome cards refresh]\n"
                f"Refresh reasons: {'; '.join(reasons)}\n"
                f"Local time: {local.isoformat()}\n"
                f"Target display time: {max(local, target).isoformat()}\n"
                "Write time-sensitive copy for the target display time, not the preparation time. "
                "Refresh outdated salutations and relative-day wording throughout the cards.\n"
                f"Existing slots: {json.dumps(cards, ensure_ascii=False)}"
            )
            try:
                await self.prepare(self.service, port, owner, version, prompt, source_snapshot(chat, siblings, contexts),
                                   publish_at=target.timestamp())
            except asyncio.CancelledError:
                raise
            except HomeModelUnavailable:
                # Configuration checks are cheap: no Agent, model request or
                # error-log loop. Recheck on the next maintenance tick so a
                # newly configured model can recover without an app restart.
                state["retry_after"] = 0
                with self.service.store.connect(write=True) as db:
                    self._save_state(db, owner, state)
                return {"status": "model_unavailable"}
            except Exception:
                state["retry_after"] = time.time() + RETRY_SECONDS
                with self.service.store.connect(write=True) as db:
                    self._save_state(db, owner, state)
                logger.exception("Background home preparation failed; retaining published cards")
                return {"status": "retry_pending"}
            state.update(source=source, day=local.date().isoformat(), prepared_slot=target_key,
                         checked=time.time(), retry_after=0)
            if weather_due:
                state["weather_checked"] = time.time()
            with self.service.store.connect(write=True) as db:
                self._save_state(db, owner, state)
            return {"status": "updated", "reasons": reasons}
