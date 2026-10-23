import hashlib
import hmac
import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from flowmate_callback.app import Service, create_app
from flowmate_callback.config import MOCK_NOW, ROOT, Settings
from flowmate_callback.store import Store

TOKEN = "test-token-0123456789abcdefghij"
LEADS = Path(ROOT, "fixtures", "leads")


def settings(**kw) -> Settings:
    base = Settings(
        mode="mock",
        bolna_agent_id="mock-agent",
        public_base_url="https://mock.flowmate.local",
        inbound_token=TOKEN,
        calcom_secret=TOKEN,
        webhook_token=TOKEN,
        tool_token=TOKEN,
        admin_token=TOKEN,
        fixed_now=MOCK_NOW,
    )
    return replace(base, **kw)


def client(**kw):
    s = settings(**kw)
    svc = Service(s, store=Store(":memory:"))
    return TestClient(create_app(s, svc)), svc


def lead_fixture(name: str) -> dict:
    return json.loads((LEADS / name).read_text(encoding="utf-8"))


def post_lead(c: TestClient, fx: dict):
    body = json.dumps(fx["body"], ensure_ascii=False).encode()
    if fx["endpoint"] == "/leads/calcom":
        sig = hmac.new(TOKEN.encode(), body, hashlib.sha256).hexdigest()
        return c.post("/leads/calcom", content=body, headers={"x-cal-signature-256": sig, "content-type": "application/json"})
    return c.post("/leads/form", content=body, headers={"X-FlowMate-Token": TOKEN, "content-type": "application/json"})


def run_fixture(c: TestClient, name: str) -> dict:
    """Post a fixture lead, replay its mock call, return the stored lead detail."""
    r = post_lead(c, lead_fixture(name))
    out = r.json()
    if out.get("execution_id"):
        c.post(f"/mock/replay/{out['execution_id']}")
    return c.get(f"/leads/{out['lead_id']}", headers={"X-FlowMate-Token": TOKEN}).json()
