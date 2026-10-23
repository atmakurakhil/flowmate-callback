"""SQLite store. Standard library only. One connection guarded by a lock is plenty for v0."""

from __future__ import annotations

import json
import sqlite3
import threading
import weakref
from pathlib import Path

from .leads import Lead

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  external_id TEXT,
  phone TEXT NOT NULL,
  data TEXT NOT NULL,
  status TEXT NOT NULL,
  execution_id TEXT,
  greeting TEXT,
  language TEXT,
  scheduled_at TEXT,
  error TEXT,
  received_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS leads_source_ext ON leads(source, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS leads_phone ON leads(phone, received_at);
CREATE TABLE IF NOT EXISTS executions (
  id TEXT PRIMARY KEY,
  lead_id TEXT,
  status TEXT NOT NULL,
  payload TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS bookings (
  slot_start TEXT PRIMARY KEY,
  lead_id TEXT NOT NULL,
  patient_name TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
  lead_id TEXT PRIMARY KEY,
  execution_id TEXT NOT NULL,
  data TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS followups (lead_id TEXT PRIMARY KEY, data TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        weakref.finalize(self, self._db.close)
        with self._lock:
            self._db.executescript(SCHEMA)

    def _q(self, sql: str, args=(), one=False):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows

    # leads
    def find_external(self, source: str, external_id: str | None):
        if not external_id:
            return None
        return self._q("SELECT * FROM leads WHERE source=? AND external_id=?", (source, external_id), one=True)

    def recent_call_to(self, phone: str, since_iso: str):
        return self._q(
            "SELECT * FROM leads WHERE phone=? AND received_at>=? AND execution_id IS NOT NULL ORDER BY received_at DESC",
            (phone, since_iso),
            one=True,
        )

    def add_lead(self, lead: Lead, status: str):
        self._q(
            "INSERT INTO leads(id, source, external_id, phone, data, status, received_at) VALUES (?,?,?,?,?,?,?)",
            (lead.id, lead.source, lead.external_id, lead.phone, lead.model_dump_json(), status, lead.received_at),
        )

    def update_lead(self, lead_id: str, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        self._q(f"UPDATE leads SET {cols} WHERE id=?", (*fields.values(), lead_id))

    def lead(self, lead_id: str):
        return self._q("SELECT * FROM leads WHERE id=?", (lead_id,), one=True)

    def lead_by_execution(self, execution_id: str):
        return self._q("SELECT * FROM leads WHERE execution_id=?", (execution_id,), one=True)

    def leads(self, limit: int = 50):
        return self._q("SELECT * FROM leads ORDER BY received_at DESC LIMIT ?", (limit,))

    # executions
    def upsert_execution(self, execution_id: str, lead_id: str | None, status: str, payload: dict | None, at: str):
        self._q(
            "INSERT INTO executions(id, lead_id, status, payload, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, payload=COALESCE(excluded.payload, executions.payload), updated_at=excluded.updated_at",
            (execution_id, lead_id, status, json.dumps(payload, ensure_ascii=False) if payload else None, at),
        )

    def execution(self, execution_id: str):
        return self._q("SELECT * FROM executions WHERE id=?", (execution_id,), one=True)

    # bookings
    def booked_slots(self) -> set[str]:
        return {r["slot_start"] for r in self._q("SELECT slot_start FROM bookings")}

    def add_booking(self, slot_start: str, lead_id: str, patient_name: str, at: str) -> bool:
        try:
            self._q("INSERT INTO bookings(slot_start, lead_id, patient_name, created_at) VALUES (?,?,?,?)", (slot_start, lead_id, patient_name, at))
            return True
        except sqlite3.IntegrityError:
            return False

    def booking_for(self, lead_id: str):
        return self._q("SELECT * FROM bookings WHERE lead_id=? ORDER BY created_at DESC", (lead_id,), one=True)

    # results
    def save_result(self, lead_id: str, execution_id: str, data: dict, at: str):
        self._q(
            "INSERT INTO results(lead_id, execution_id, data, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(lead_id) DO UPDATE SET execution_id=excluded.execution_id, data=excluded.data, created_at=excluded.created_at",
            (lead_id, execution_id, json.dumps(data, ensure_ascii=False), at),
        )

    def result(self, lead_id: str):
        r = self._q("SELECT * FROM results WHERE lead_id=?", (lead_id,), one=True)
        return json.loads(r["data"]) if r else None

    def result_execution(self, lead_id: str) -> str | None:
        r = self._q("SELECT execution_id FROM results WHERE lead_id=?", (lead_id,), one=True)
        return r["execution_id"] if r else None

    def save_followups(self, lead_id: str, data: list[dict]):
        self._q("INSERT INTO followups(lead_id,data) VALUES (?,?) ON CONFLICT(lead_id) DO UPDATE SET data=excluded.data",
                (lead_id, json.dumps(data, ensure_ascii=False)))

    def followups(self, lead_id: str):
        r = self._q("SELECT data FROM followups WHERE lead_id=?", (lead_id,), one=True)
        return json.loads(r["data"]) if r else []
