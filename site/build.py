"""Export the six-lead mock demo to site/data.json for the static demo page.

Runs the same pipeline as `python3 -m flowmate_callback demo` (cmd_demo): the real
FastAPI app in mock mode, each fixture lead posted to its real handler, then the
fixture call replayed through MockBolna, the tool endpoints and the webhook sequence.
Nothing here re-implements tagging, quoting, auditing or message composition; it
only reads what the app produced.

    python3 site/build.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ["FLOWMATE_MODE"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from flowmate_callback.app import create_app  # noqa: E402
from flowmate_callback.config import ROOT, from_env  # noqa: E402

LANGS = {"te": ("Telugu", "te-IN"), "hi": ("Hindi", "hi-IN"), "en": ("English", "en-IN")}
CHANNELS = {"website_form": "Website form", "ad": "Ad lead", "missed_call": "Missed call", "calcom": "Cal.com booking"}


def post_lead(c: TestClient, s, fx: dict):
    """Same request cmd_demo makes for one fixture lead."""
    body = json.dumps(fx["body"], ensure_ascii=False).encode()
    if fx["endpoint"] == "/leads/calcom":
        sig = hmac.new(s.calcom_secret.encode(), body, hashlib.sha256).hexdigest()
        return c.post("/leads/calcom", content=body, headers={"x-cal-signature-256": sig, "content-type": "application/json"})
    return c.post("/leads/form", content=body, headers={"X-FlowMate-Token": s.inbound_token, "content-type": "application/json"})


def turns(transcript: str) -> list[dict]:
    out = []
    for line in (transcript or "").splitlines():
        m = re.match(r"^(assistant|user):\s?(.*)$", line)
        if m:
            out.append({"who": "agent" if m.group(1) == "assistant" else "caller", "text": m.group(2)})
        elif out and line.strip():
            out[-1]["text"] += " " + line.strip()
    return out


def main() -> None:
    s = from_env()
    app = create_app(s)
    svc = app.state.service
    c = TestClient(app)
    leads = []
    for f in sorted(Path(ROOT, "fixtures", "leads").glob("*.json")):
        fx = json.loads(f.read_text(encoding="utf-8"))
        r = post_lead(c, s, fx)
        out = r.json()
        body = fx["body"]
        title = re.sub(r"^\d+\.\s*", "", fx["label"])
        title = re.sub(r"\s*\(expected[^)]*\)\s*$", "", title)
        entry = {
            "id": f.stem,
            "label": fx["label"],
            "title": title,
            "http": {"endpoint": fx["endpoint"], "status": r.status_code, "result": out.get("status"), "reason": out.get("reason")},
        }
        if not out.get("execution_id"):
            lang = body.get("language")
            entry.update({
                "name": body.get("name"), "channel": CHANNELS.get(body.get("source"), body.get("source")),
                "language": LANGS[lang][0] if lang in LANGS else None, "lang_code": LANGS[lang][1] if lang in LANGS else None,
                "message": body.get("message"), "tag": "REJECTED", "greeting": None, "turns": [], "tool_calls": [],
                "why_quote": None, "owner_message": None, "whatsapp": None, "audit": None, "seconds_to_callback": None,
            })
            leads.append(entry)
            continue
        replay = c.post(f"/mock/replay/{out['execution_id']}").json()
        detail = c.get(f"/leads/{out['lead_id']}", headers={"X-FlowMate-Token": s.admin_token}).json()
        res, lead = detail["result"], detail["lead"]
        lang = res.get("language") or lead.get("language") or "en"
        tag = "NOT REACHED" if not res["reached"] else res["tag"].upper()
        audit = res.get("audit") or {}
        entry.update({
            "name": lead.get("name"),
            "channel": CHANNELS.get(lead.get("source"), lead.get("source")),
            "language": LANGS.get(lang, (lang, "en-IN"))[0],
            "lang_code": LANGS.get(lang, (lang, "en-IN"))[1],
            "message": lead.get("message"),
            "tag": tag,
            "greeting": detail["greeting"],
            "turns": turns(res.get("transcript")),
            "tool_calls": [{"tool": t["tool"], "args": t["args"], "response": t["response"]} for t in replay.get("tool_calls", [])],
            "why_quote": res.get("reason_quote") if res["reached"] else None,
            "quote_source": res.get("quote_source"),
            "summary": res.get("summary"),
            "owner_message": res["owner_message"],
            "whatsapp": (res.get("whatsapp") or {}).get("text"),
            "audit": {
                "disclosure_ok": audit.get("disclosure_ok"),
                "unlisted_numbers": audit.get("unlisted_numbers") or [],
                "notes": audit.get("notes") or [],
                "check": bool(audit.get("unlisted_numbers")),
                "check_line": next((ln for ln in res["owner_message"].splitlines() if ln.startswith("CHECK:")), None),
            } if res["reached"] else None,
            "seconds_to_callback": res.get("dial_delay_s"),
            "duration_s": res.get("duration_s"),
            "call_status": res.get("status"),
        })
        leads.append(entry)
    data = {
        "business": f"{svc.profile.business.name} (fictional)",
        "clock": f"{s.now():%a %d %b %Y %H:%M} IST",
        "mode": "mock",
        "leads": leads,
    }
    (HERE / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {HERE / 'data.json'}: {len(leads)} leads, tags " + ", ".join(l["tag"] for l in leads))


if __name__ == "__main__":
    main()
