"""Durable home drafts stay private until their scheduled publication time."""
import json

from .schemas import CardError


def ensure_pending_table(db):
    db.execute("CREATE TABLE IF NOT EXISTS home_prepared (owner TEXT PRIMARY KEY, publish_at REAL NOT NULL, document TEXT NOT NULL)")


def documents(db, owner):
    return {row["id"]: json.loads(row["document"]) for row in db.execute(
        "SELECT id,document FROM cards WHERE owner=? AND status='active' "
        "AND json_extract(document,'$.spec.home_kind') IS NOT NULL", (owner,))}


def publish_documents(service, db, owner, baseline, prepared):
    if documents(db, owner) != baseline:
        raise CardError("revision_conflict", "Home changed during preparation; retry with current data")
    for card_id, card in prepared.items():
        if card != baseline.get(card_id):
            service._save(db, card, version=True)


def publish_due(service, owner, version, now):
    """Atomically publish once, or discard a draft superseded by foreground edits."""
    with service.store.connect(write=True) as db:
        ensure_pending_table(db)
        row = db.execute("SELECT document FROM home_prepared WHERE owner=? AND publish_at<=?", (owner, now)).fetchone()
        if row is None:
            return
        draft = json.loads(row[0])
        valid = draft['version'] == version and documents(db, owner) == draft['baseline']
        if valid:
            publish_documents(service, db, owner, draft['baseline'], draft['prepared'])
        else:
            # Permit the scheduler to regenerate this slot without overwriting user edits.
            state_row = db.execute("SELECT document FROM home_maintenance WHERE owner=?", (owner,)).fetchone()
            if state_row:
                state = json.loads(state_row[0])
                state.pop('prepared_slot', None)
                db.execute("UPDATE home_maintenance SET document=? WHERE owner=?", (json.dumps(state), owner))
        db.execute("DELETE FROM home_prepared WHERE owner=?", (owner,))
