"""Command line.

  python3 -m flowmate_callback demo            whole flow in mock mode, no key, no network
  python3 -m flowmate_callback agent           print the Bolna agent + extractions JSON (secrets redacted)
  python3 -m flowmate_callback serve           run the HTTP server (mock unless FLOWMATE_MODE=live)
  python3 -m flowmate_callback secrets         print fresh random tokens to paste into your .env
  python3 -m flowmate_callback check           live: GET /user/me (key works? wallet? concurrency?)
  python3 -m flowmate_callback create-agent --yes   live: create the agent and its extractions on your Bolna account
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
from pathlib import Path

from . import agent
from .config import ROOT, from_env
from .profile import load_profile


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_agent(_):
    s = from_env()
    profile = load_profile(s.profile_path)
    base = s.public_base_url or "https://YOUR-PUBLIC-URL"
    body = agent.agent_payload(profile, base, s.webhook_token or "WEBHOOK_TOKEN", s.tool_token or "TOOL_TOKEN")
    _print({"POST /v2/agent": agent.redact(body, s.webhook_token, s.tool_token),
            "POST /dispositions/bulk (dispositions)": agent.dispositions(profile)})


def cmd_secrets(_):
    for k in ("FLOWMATE_INBOUND_TOKEN", "FLOWMATE_WEBHOOK_TOKEN", "FLOWMATE_TOOL_TOKEN", "FLOWMATE_ADMIN_TOKEN", "CALCOM_WEBHOOK_SECRET"):
        print(f"{k}={secrets.token_urlsafe(32)}")


def cmd_check(_):
    s = from_env()
    if not s.live:
        sys.exit("check talks to Bolna: set FLOWMATE_MODE=live and BOLNA_API_KEY first")
    from .bolna import BolnaClient

    me = BolnaClient(s.bolna_api_key, s.bolna_base_url).me()
    _print({k: me.get(k) for k in ("name", "email", "wallet", "concurrency")})


def cmd_create_agent(args):
    os.environ.setdefault("BOLNA_AGENT_ID", "pending-create")
    s = from_env()
    if not s.live:
        sys.exit("create-agent talks to Bolna: set FLOWMATE_MODE=live first")
    if not args.yes:
        sys.exit("This creates an agent on your Bolna account. Re-run with --yes when you mean it.")
    from .bolna import BolnaClient

    profile = load_profile(s.profile_path)
    c = BolnaClient(s.bolna_api_key, s.bolna_base_url)
    r = c.create_agent(agent.agent_payload(profile, s.public_base_url, s.webhook_token, s.tool_token))
    agent_id = r["agent_id"]
    c.create_dispositions(agent_id, agent.dispositions(profile))
    print(f"Created agent {agent_id}. Put BOLNA_AGENT_ID={agent_id} in your .env.")
    print("Then open it at https://platform.bolna.ai and check the Tools tab lists check_slots and book_slot.")


def cmd_serve(args):
    import uvicorn

    from .app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)


def cmd_demo(_):
    os.environ["FLOWMATE_MODE"] = "mock"
    from fastapi.testclient import TestClient

    from .app import create_app

    s = from_env()
    app = create_app(s)
    svc = app.state.service
    c = TestClient(app)
    print(f"FlowMate Callback v0, MOCK MODE. Profile: {svc.profile.business.name} (FICTIONAL). Clock: {s.now():%a %d %b %Y %H:%M} IST\n")
    for f in sorted(Path(ROOT, "fixtures", "leads").glob("*.json")):
        fx = json.loads(f.read_text(encoding="utf-8"))
        body = json.dumps(fx["body"], ensure_ascii=False).encode()
        if fx["endpoint"] == "/leads/calcom":
            sig = hmac.new(s.calcom_secret.encode(), body, hashlib.sha256).hexdigest()
            r = c.post("/leads/calcom", content=body, headers={"x-cal-signature-256": sig, "content-type": "application/json"})
        else:
            r = c.post("/leads/form", content=body, headers={"X-FlowMate-Token": s.inbound_token, "content-type": "application/json"})
        out = r.json()
        print("=" * 78)
        print(f"{fx['label']}\n  POST {fx['endpoint']} -> {r.status_code} {out.get('status')}" + (f" ({out.get('reason')})" if out.get("reason") else ""))
        if out.get("execution_id"):
            c.post(f"/mock/replay/{out['execution_id']}")
            detail = c.get(f"/leads/{out['lead_id']}", headers={"X-FlowMate-Token": s.admin_token}).json()
            res = detail["result"]
            print(f"  greeting (first words on the call): {detail['greeting']}")
            print("\n  OWNER NOTIFICATION (composed, not sent)")
            print("  " + res["owner_message"].replace("\n", "\n  "))
            wa = res.get("whatsapp")
            print("\n  CALLER WHATSAPP (composed, not sent)")
            print("  " + (wa["text"] if wa else "none: no booking on this call"))
        print()
    print("Nothing was sent and no real call was placed. See README for live mode.")


def cmd_whatsapp_demo(_):
    os.environ["FLOWMATE_MODE"] = "mock"
    os.environ["FLOWMATE_PROFILE"] = str(ROOT / "profiles" / "glow-derm-hyderabad.FICTIONAL.json")
    from fastapi.testclient import TestClient
    from .app import create_app
    from .report import weekly_report
    s = from_env()
    app = create_app(s)
    svc = app.state.service
    c = TestClient(app)
    print(f"WhatsApp follow-up DEMO (mock only) — {svc.profile.business.name}; {s.now():%Y-%m-%d %H:%M} IST")
    for i, (name, phone, lang, service) in enumerate([
        ("Ananya Rao", "9000000101", "en", "acne consult"), ("Kabir Shah", "9000000102", "hi", "hair-fall consult"),
        ("Meera Reddy", "9000000103", "te", "chemical peel"), ("Ishaan Das", "9000000104", "en", "laser hair removal"),
        ("Sana Ali", "9000000105", "hi", "acne consult")]):
        payload = {"entry": [{"changes": [{"field": "leadgen", "value": {"leadgen_id": f"demo-{i}", "form_id": "glow-form", "page_id": "glow-page"}}]}],
                   "field_data": [{"name": "full_name", "values": [name]}, {"name": "phone_number", "values": [phone]},
                                  {"name": "language", "values": [lang]}, {"name": "message", "values": [service]},
                                  {"name": "consent", "values": ["yes"]}]}
        body = json.dumps(payload, ensure_ascii=False).encode()
        sig = "sha256=" + hmac.new((s.meta_app_secret or "").encode(), body, hashlib.sha256).hexdigest()
        response = c.post("/leads/meta", content=body, headers={"X-Hub-Signature-256": sig, "content-type": "application/json"})
        out = response.json()
        if response.status_code != 202:
            print("Lead rejected:", out)
            continue
        row = svc.store.lead(out["lead_id"])
        print(f"\n{name} ({out['status']})")
        for item in svc.store.followups(out["lead_id"]):
            print(f"  {item['at']} — {item['message']['text']}")
    print("\n" + weekly_report(svc.store, s.now()))
    print("\nMock mode: composed only; nothing sent.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="flowmate_callback")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    sub.add_parser("whatsapp-demo").set_defaults(fn=cmd_whatsapp_demo)
    sub.add_parser("agent").set_defaults(fn=cmd_agent)
    sub.add_parser("secrets").set_defaults(fn=cmd_secrets)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    ca = sub.add_parser("create-agent")
    ca.add_argument("--yes", action="store_true")
    ca.set_defaults(fn=cmd_create_agent)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8787)
    sv.set_defaults(fn=cmd_serve)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
