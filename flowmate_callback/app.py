"""HTTP surface.

  POST /leads/form          generic form / ad / missed-call JSON      header X-FlowMate-Token
  POST /leads/calcom        Cal.com BOOKING_CREATED webhook           header x-cal-signature-256
  POST /webhooks/bolna      Bolna execution webhook                   ?token=  (+ Bolna source IPs in live mode)
  POST /tools/slots         Bolna custom function: open slots          Authorization: Bearer <tool token>
  POST /tools/book          Bolna custom function: book a slot         Authorization: Bearer <tool token>
  GET  /leads, /leads/{id}  owner view (JSON)                         header X-FlowMate-Token: <admin token>
  POST /mock/replay/{id}    mock mode only: play a fixture call through the same handlers
  GET  /health
"""

from __future__ import annotations

import hmac
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from . import agent, analysis, messages, slots
from .bolna import BolnaClient, BolnaError, MockBolna
from .config import BOLNA_SOURCE_IPS, Settings, from_env
from .leads import Lead, LeadRejected, parse_calcom, parse_form, parse_meta_lead, verify_calcom_signature, verify_meta_signature
from .followup import sequence as followup_sequence
from .report import weekly_report
from .profile import OwnerProfile, load_profile
from .store import Store


def _eq(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(str(a), str(b))


class Service:
    def __init__(self, settings: Settings, profile: OwnerProfile | None = None, client=None, store: Store | None = None):
        self.s = settings
        self.profile = profile or load_profile(settings.profile_path)
        self.store = store or Store(settings.db_path)
        if client is not None:
            self.bolna = client
        elif settings.live:
            self.bolna = BolnaClient(settings.bolna_api_key, settings.bolna_base_url)
        else:
            self.bolna = MockBolna(settings)
        self.fixture_phones = set(MockBolna(settings).fixtures) if settings.live else set()

    # ---------- leads ----------

    def next_call_time(self, now: datetime) -> datetime | None:
        """None if we may call now; else the start of the next calling window."""
        w = self.profile.calling_window
        local = now.astimezone(ZoneInfo(self.profile.business.timezone))
        if w.start_hour <= local.hour < w.end_hour:
            return None
        day = local.date() if local.hour < w.start_hour else local.date() + timedelta(days=1)
        return datetime(day.year, day.month, day.day, w.start_hour, 0, tzinfo=local.tzinfo)

    def call_permitted(self, phone: str) -> str | None:
        """Live-mode safety net. Returns a reason if the call must not be placed."""
        if not self.s.live:
            return None
        if phone in self.fixture_phones:
            return "number belongs to the fictional fixtures"
        allow = self.s.live_allowlist
        if allow is None:
            return "FLOWMATE_LIVE_ALLOWLIST is empty; set it to your own test number(s), or * for a real client"
        if "*" not in allow and phone not in allow:
            return "number is not in FLOWMATE_LIVE_ALLOWLIST"
        return None

    def take_lead(self, lead: Lead) -> dict:
        now = self.s.now()
        dup = self.store.find_external(lead.source, lead.external_id)
        if dup:
            return {"lead_id": dup["id"], "status": "duplicate"}
        since = (now - timedelta(hours=self.s.recall_window_hours)).isoformat()
        recent = self.store.recent_call_to(lead.phone, since)
        self.store.add_lead(lead, "received")
        self.store.save_followups(lead.id, followup_sequence(lead, self.profile, now))
        if recent:
            self.store.update_lead(lead.id, status="not_called_recent", error=f"already called for lead {recent['id']} in the last {self.s.recall_window_hours}h")
            return {"lead_id": lead.id, "status": "not_called_recent"}
        blocked = self.call_permitted(lead.phone)
        if blocked:
            self.store.update_lead(lead.id, status="blocked", error=blocked)
            return {"lead_id": lead.id, "status": "blocked", "reason": blocked}
        lang = agent.call_language(self.profile, lead)
        data = agent.user_data(self.profile, lead)
        later = self.next_call_time(now)
        scheduled_at = later.isoformat() if later else None
        try:
            r = self.bolna.place_call(
                self.s.bolna_agent_id, lead.phone, data,
                language=lang if len(self.profile.languages) > 1 else None,
                from_number=self.s.from_number, scheduled_at=scheduled_at,
            )
        except BolnaError as e:
            self.store.update_lead(lead.id, status="call_failed", error=str(e), greeting=data["greeting"], language=lang)
            return {"lead_id": lead.id, "status": "call_failed", "reason": str(e)}
        status = "scheduled" if scheduled_at else "calling"
        self.store.update_lead(lead.id, status=status, execution_id=r["execution_id"], greeting=data["greeting"], language=lang, scheduled_at=scheduled_at)
        self.store.upsert_execution(r["execution_id"], lead.id, r.get("status", "queued"), None, now.isoformat())
        out = {"lead_id": lead.id, "status": status, "execution_id": r["execution_id"]}
        if scheduled_at:
            out["scheduled_at"] = scheduled_at
        return out

    # ---------- post-call ----------

    def handle_execution_event(self, payload: dict) -> dict:
        execution_id = str(payload.get("id") or payload.get("execution_id") or "")
        if not execution_id:
            return {"ok": False, "reason": "no execution id"}
        status = str(payload.get("status") or "unknown")
        now = self.s.now().isoformat()
        row = self.store.lead_by_execution(execution_id)
        lead_id = row["id"] if row else None
        if not row:
            # A retry may arrive under a new execution id; map it through the lead_id we passed in user_data.
            hinted = ((payload.get("context_details") or {}).get("recipient_data") or {}).get("lead_id")
            row = self.store.lead(hinted) if hinted else None
            if not row:
                return {"ok": False, "reason": "unknown execution"}
            lead_id = row["id"]
            self.store.update_lead(lead_id, execution_id=execution_id)
        self.store.upsert_execution(execution_id, lead_id, status, None, now)
        if status not in analysis.TERMINAL:
            return {"ok": True, "status": status, "processed": False}
        if self.store.result_execution(lead_id) == execution_id:
            return {"ok": True, "status": status, "processed": False, "reason": "already processed"}
        # Webhooks carry no signature (Bolna security page), so the payload is only a trigger.
        # The execution is re-read from Bolna's API before anything is stored.
        execution = self.bolna.get_execution(execution_id)
        if str(execution.get("status")) not in analysis.TERMINAL:
            return {"ok": True, "status": execution.get("status"), "processed": False}
        self.store.upsert_execution(execution_id, lead_id, str(execution.get("status")), execution, now)
        result = self.process(lead_id, execution)
        return {"ok": True, "status": execution.get("status"), "processed": True, "tag": result["tag"]}

    def process(self, lead_id: str, execution: dict) -> dict:
        row = self.store.lead(lead_id)
        lead = Lead.model_validate_json(row["data"])
        booking = self.store.booking_for(lead_id)
        res = analysis.analyze(execution, lead, self.profile, self.s.now(), booking["slot_start"] if booking else None, row["language"])
        url = f"{self.s.public_base_url}/leads/{lead_id}" if self.s.public_base_url else None
        out = res.to_dict()
        out["owner_message"] = messages.owner_notification(lead, res, self.profile, url)
        out["whatsapp"] = messages.whatsapp_confirmation(lead, res, self.profile)
        out["transcript"] = execution.get("transcript") or ""
        out["recording_url"] = (execution.get("telephony_data") or {}).get("recording_url")
        self.store.save_result(lead_id, execution["id"], out, self.s.now().isoformat())
        self.store.update_lead(lead_id, status="done" if res.reached else "not_reached")
        return out

    # ---------- tools (called by the Bolna agent mid-call) ----------

    def tool_slots(self, body: dict) -> dict:
        day = slots.parse_day(str(body.get("date", "")))
        if not day:
            return {"slots": [], "message": "Date not understood. Ask the caller which day, then call again with YYYY-MM-DD."}
        free = slots.open_slots(self.profile, day, self.s.now(), self.store.booked_slots())[:3]
        if not free:
            return {"slots": [], "message": "No open times that day. Ask the caller for another day."}
        return {"slots": free, "spoken": [slots.spoken(x) for x in free], "message": "Offer only these times."}

    def tool_book(self, body: dict) -> dict:
        slot = str(body.get("slot_start", "")).strip()[:16]
        lead_id = str(body.get("lead_id", ""))
        name = str(body.get("patient_name", ""))[:60]
        if not self.store.lead(lead_id):
            return {"booked": False, "message": "Unknown lead. Do not confirm a booking; say the clinic will call back."}
        if not slots.is_bookable(self.profile, slot, self.s.now(), self.store.booked_slots()):
            return {"booked": False, "message": "That time is not available. Call check_slots again and offer only what it returns."}
        if not self.store.add_booking(slot, lead_id, name, self.s.now().isoformat()):
            return {"booked": False, "message": "That time was just taken. Call check_slots again."}
        return {"booked": True, "slot": slot, "spoken": slots.spoken(slot), "message": "Booked. Read back the day and time, and say a WhatsApp confirmation will follow."}

    # ---------- mock replay ----------

    def replay(self, execution_id: str) -> dict:
        """Play one fixture call: run its tool calls, then send Bolna's webhook sequence."""
        if not isinstance(self.bolna, MockBolna):
            raise HTTPException(404, "replay exists only in mock mode")
        script = self.bolna.script(execution_id)
        lead_id = script["execution"]["context_details"]["recipient_data"]["lead_id"]
        tool_log = []
        for call in script["tool_calls"]:
            args = {k: self._resolve(v, lead_id) for k, v in call["args"].items()}
            fn = self.tool_slots if call["tool"] == "check_slots" else self.tool_book
            tool_log.append({"tool": call["tool"], "args": args, "response": fn(args)})
        final = script["execution"]
        events = []
        if final["status"] == "completed":
            events = [{"id": execution_id, "status": "in-progress"}, {"id": execution_id, "status": "call-disconnected", "conversation_duration": 0}, {"id": execution_id, "status": "completed"}]
        else:
            events = [{"id": execution_id, "status": final["status"]}]
        handled = [self.handle_execution_event(e) for e in events]
        return {"tool_calls": tool_log, "webhook_events": handled}

    def _resolve(self, value, lead_id: str):
        if not isinstance(value, str):
            return value
        if value == "@lead_id":
            return lead_id
        if value.startswith("@tomorrow"):
            day = (self.s.now() + timedelta(days=1)).date().isoformat()
            rest = value[len("@tomorrow"):].strip()
            return f"{day}T{rest}" if rest else day
        return value


def create_app(settings: Settings | None = None, service: Service | None = None) -> FastAPI:
    svc = service or Service(settings or from_env())
    s = svc.s
    app = FastAPI(title="FlowMate Callback", version="0.1.0")
    app.state.service = svc

    def client_ip(request: Request) -> str:
        if s.trust_proxy and request.headers.get("x-forwarded-for"):
            return request.headers["x-forwarded-for"].split(",")[0].strip()
        return request.client.host if request.client else ""

    def require_bolna(request: Request):
        if s.enforce_bolna_ips and client_ip(request) not in BOLNA_SOURCE_IPS:
            raise HTTPException(403, "source IP is not a Bolna IP")

    async def json_body(request: Request) -> dict:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            raise HTTPException(400, "body must be JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be a JSON object")
        return body

    @app.exception_handler(LeadRejected)
    async def rejected(_: Request, e: LeadRejected):
        return JSONResponse({"status": "rejected", "code": e.code, "reason": e.message, "called": False}, status_code=422)

    @app.get("/health")
    def health():
        return {"ok": True, "mode": s.mode, "business": svc.profile.business.name, "fictional_profile": svc.profile.fictional}

    @app.get("/reports/weekly")
    def report_weekly(x_flowmate_token: str | None = Header(default=None)):
        if not _eq(x_flowmate_token, s.admin_token):
            raise HTTPException(401, "bad or missing X-FlowMate-Token")
        return {"text": weekly_report(svc.store, s.now())}

    @app.post("/leads/form", status_code=202)
    async def lead_form(request: Request, x_flowmate_token: str | None = Header(default=None)):
        if not _eq(x_flowmate_token, s.inbound_token):
            raise HTTPException(401, "bad or missing X-FlowMate-Token")
        lead = parse_form(await json_body(request), s.now(), s.allowed_country_codes)
        return svc.take_lead(lead)

    @app.post("/leads/calcom", status_code=202)
    async def lead_calcom(request: Request, x_cal_signature_256: str | None = Header(default=None)):
        raw = await request.body()
        if not verify_calcom_signature(raw, x_cal_signature_256, s.calcom_secret):
            raise HTTPException(401, "bad or missing x-cal-signature-256")
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(400, "body must be JSON")
        lead = parse_calcom(payload, s.now(), s.calcom_consent_field, s.allowed_country_codes)
        if lead is None:
            return JSONResponse({"status": "ignored", "reason": "only BOOKING_CREATED starts a callback"}, status_code=200)
        return svc.take_lead(lead)

    @app.post("/leads/meta", status_code=202)
    async def lead_meta(request: Request, x_hub_signature_256: str | None = Header(default=None)):
        raw = await request.body()
        if not verify_meta_signature(raw, x_hub_signature_256, s.meta_app_secret):
            raise HTTPException(401, "bad or missing X-Hub-Signature-256")
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(400, "body must be JSON")
        lead = parse_meta_lead(payload.get("webhook", payload), payload.get("field_data", []), s.now(), s.allowed_country_codes)
        return svc.take_lead(lead)

    @app.post("/webhooks/bolna")
    async def bolna_webhook(request: Request, token: str | None = None):
        require_bolna(request)
        if not _eq(token, s.webhook_token):
            raise HTTPException(401, "bad webhook token")
        return svc.handle_execution_event(await json_body(request))

    def require_tool(authorization: str | None):
        if not _eq(authorization, f"Bearer {s.tool_token}"):
            raise HTTPException(401, "bad tool token")

    @app.post("/tools/slots")
    async def tools_slots(request: Request, authorization: str | None = Header(default=None)):
        require_bolna(request)
        require_tool(authorization)
        return svc.tool_slots(await json_body(request))

    @app.post("/tools/book")
    async def tools_book(request: Request, authorization: str | None = Header(default=None)):
        require_bolna(request)
        require_tool(authorization)
        return svc.tool_book(await json_body(request))

    def require_admin(token: str | None):
        if not _eq(token, s.admin_token):
            raise HTTPException(401, "bad or missing X-FlowMate-Token")

    @app.get("/leads")
    def list_leads(x_flowmate_token: str | None = Header(default=None)):
        require_admin(x_flowmate_token)
        out = []
        for r in svc.store.leads():
            res = svc.store.result(r["id"])
            out.append({"lead_id": r["id"], "status": r["status"], "phone": r["phone"], "source": r["source"], "received_at": r["received_at"], "tag": (res or {}).get("tag")})
        return out

    @app.get("/leads/{lead_id}")
    def get_lead(lead_id: str, x_flowmate_token: str | None = Header(default=None)):
        require_admin(x_flowmate_token)
        r = svc.store.lead(lead_id)
        if not r:
            raise HTTPException(404, "no such lead")
        return {"lead": json.loads(r["data"]), "status": r["status"], "error": r["error"], "execution_id": r["execution_id"],
                "greeting": r["greeting"], "scheduled_at": r["scheduled_at"], "result": svc.store.result(lead_id)}

    if not s.live:
        @app.post("/mock/replay/{execution_id}")
        def mock_replay(execution_id: str):
            return svc.replay(execution_id)

    return app
