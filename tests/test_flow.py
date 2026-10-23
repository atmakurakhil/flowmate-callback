"""End to end in mock mode: lead in -> call placed -> fixture call replayed -> results out."""

import json
import unittest
from datetime import datetime

from flowmate_callback import analysis
from flowmate_callback.config import IST
from tests.helpers import TOKEN, client, lead_fixture, post_lead, run_fixture


class FixtureOutcomes(unittest.TestCase):
    def setUp(self):
        self.c, self.svc = client()

    def user_lines(self, detail):
        return [t for s, t in analysis.parse_transcript(detail["result"]["transcript"]) if s == "user"]

    def assert_quote_is_real(self, detail):
        q = detail["result"]["reason_quote"]
        self.assertTrue(any(analysis.norm(q) in analysis.norm(l) for l in self.user_lines(detail)), q)

    def test_telugu_hot_books_and_confirms(self):
        d = run_fixture(self.c, "1-form-ravi-telugu.json")
        r = d["result"]
        self.assertEqual(r["tag"], "hot")
        self.assertEqual(r["booking"], "2026-10-20T11:00")
        self.assertEqual(r["quote_source"], "extraction_verified")
        self.assert_quote_is_real(d)
        self.assertEqual(len(r["summary"]), 3)
        self.assertTrue(r["audit"]["disclosure_ok"])
        self.assertEqual(r["audit"]["unlisted_numbers"], [])
        wa = r["whatsapp"]
        self.assertFalse(wa["sent"])
        self.assertEqual(wa["language"], "te")
        self.assertIn("మంగళవారం 20 అక్టోబర్", wa["text"])
        self.assertIn("[HOT]", r["owner_message"])

    def test_hindi_warm(self):
        d = run_fixture(self.c, "2-form-pooja-hindi.json")
        self.assertEqual(d["result"]["tag"], "warm")
        self.assert_quote_is_real(d)
        self.assertIsNone(d["result"]["whatsapp"])
        self.assertEqual(d["result"]["audit"]["unlisted_numbers"], [])  # ₹1,200 and ₹1,500 are written in the profile

    def test_english_cold_rejects_made_up_quote(self):
        d = run_fixture(self.c, "3-ad-arjun-english.json")
        r = d["result"]
        self.assertEqual(r["tag"], "cold")
        self.assertEqual(r["quote_source"], "rule")  # the fixture's extraction quote is not what he said
        self.assertEqual(r["reason_quote"], "Oh. Then I'm not interested, thanks.")

    def test_no_answer(self):
        d = run_fixture(self.c, "4-missed-call-lakshmi.json")
        r = d["result"]
        self.assertFalse(r["reached"])
        self.assertIsNone(r["tag"])
        self.assertEqual(d["status"], "not_reached")
        self.assertIn("[NOT REACHED]", r["owner_message"])

    def test_breach_is_flagged(self):
        d = run_fixture(self.c, "5-calcom-sameer-english.json")
        r = d["result"]
        self.assertEqual(r["tag"], "hot")
        self.assertEqual(len(r["audit"]["unlisted_numbers"]), 1)
        self.assertIn("4,000", r["audit"]["unlisted_numbers"][0])
        self.assertIn("CHECK:", r["owner_message"])
        self.assertIn("Saturday 24 October, 4:30 PM", r["whatsapp"]["text"])

    def test_no_consent_no_call(self):
        r = post_lead(self.c, lead_fixture("6-form-no-consent.json"))
        self.assertEqual(r.status_code, 422)
        self.assertFalse(r.json()["called"])
        self.assertEqual(self.svc.bolna.calls, [])


class FlowRules(unittest.TestCase):
    def test_call_is_placed_immediately_with_disclosure_greeting(self):
        c, svc = client()
        r = post_lead(c, lead_fixture("1-form-ravi-telugu.json"))
        self.assertEqual((r.status_code, r.json()["status"]), (202, "calling"))
        call = svc.bolna.calls[0]
        self.assertIsNone(call["scheduled_at"])
        self.assertEqual(call["language"], "te")
        self.assertIn("AI", call["user_data"]["greeting"].split(",")[1])

    def test_late_night_lead_waits_for_9am(self):
        c, svc = client(fixed_now=datetime(2026, 10, 19, 22, 40, tzinfo=IST))
        r = post_lead(c, lead_fixture("1-form-ravi-telugu.json")).json()
        self.assertEqual(r["status"], "scheduled")
        self.assertEqual(r["scheduled_at"], "2026-10-20T09:00:00+05:30")
        early = client(fixed_now=datetime(2026, 10, 19, 6, 15, tzinfo=IST))[0]
        self.assertEqual(post_lead(early, lead_fixture("1-form-ravi-telugu.json")).json()["scheduled_at"], "2026-10-19T09:00:00+05:30")

    def test_duplicate_and_repeat_phone(self):
        c, svc = client()
        fx = lead_fixture("1-form-ravi-telugu.json")
        post_lead(c, fx)
        self.assertEqual(post_lead(c, fx).json()["status"], "duplicate")
        fx2 = json.loads(json.dumps(fx))
        fx2["body"]["external_id"] = "form-0001-b"
        self.assertEqual(post_lead(c, fx2).json()["status"], "not_called_recent")
        self.assertEqual(len(svc.bolna.calls), 1)

    def test_webhook_is_idempotent_and_waits_for_completed(self):
        c, svc = client()
        out = post_lead(c, lead_fixture("2-form-pooja-hindi.json")).json()
        eid = out["execution_id"]
        url = f"/webhooks/bolna?token={TOKEN}"
        first = c.post(url, json={"id": eid, "status": "call-disconnected", "conversation_duration": 0}).json()
        self.assertFalse(first["processed"])
        self.assertIsNone(svc.store.result(out["lead_id"]))
        done = c.post(url, json={"id": eid, "status": "completed"}).json()
        again = c.post(url, json={"id": eid, "status": "completed"}).json()
        self.assertTrue(done["processed"])
        self.assertFalse(again["processed"])

    def test_tools_offer_and_book_only_real_slots(self):
        c, svc = client()
        lead_id = post_lead(c, lead_fixture("1-form-ravi-telugu.json")).json()["lead_id"]
        h = {"Authorization": f"Bearer {TOKEN}"}
        today = c.post("/tools/slots", json={"date": "2026-10-19", "lead_id": lead_id}, headers=h).json()
        self.assertEqual(today["slots"][0], "2026-10-19T11:30")  # 10:02 now + 60 min notice
        sunday = c.post("/tools/slots", json={"date": "2026-10-25", "lead_id": lead_id}, headers=h).json()
        self.assertEqual(sunday["slots"], [])
        self.assertEqual(c.post("/tools/slots", json={"date": "next tuesday"}, headers=h).json()["slots"], [])
        bad = c.post("/tools/book", json={"slot_start": "2026-10-20T14:00", "patient_name": "R", "lead_id": lead_id}, headers=h).json()
        self.assertFalse(bad["booked"])  # 14:00 is the lunch break
        good = c.post("/tools/book", json={"slot_start": "2026-10-20T10:00", "patient_name": "R", "lead_id": lead_id}, headers=h).json()
        self.assertTrue(good["booked"])
        taken = c.post("/tools/book", json={"slot_start": "2026-10-20T10:00", "patient_name": "S", "lead_id": lead_id}, headers=h).json()
        self.assertFalse(taken["booked"])
        unknown = c.post("/tools/book", json={"slot_start": "2026-10-20T10:30", "patient_name": "S", "lead_id": "nope"}, headers=h).json()
        self.assertFalse(unknown["booked"])


class AnalysisUnits(unittest.TestCase):
    def test_verified_quote(self):
        lines = ["Oh. Then I'm not interested, thanks.", "Yes"]
        self.assertEqual(analysis.verified_quote("Then I'm not interested", lines), "Then I'm not interested")
        self.assertEqual(analysis.verified_quote("then I'm NOT interested", lines), lines[0])  # case differs: quote the real line
        self.assertIsNone(analysis.verified_quote("I am not interested at all", lines))
        self.assertEqual(analysis.verified_quote("yes", lines), "Yes")  # too short, but real: whole line

    def test_do_not_call_is_cold(self):
        from flowmate_callback.leads import Lead
        from flowmate_callback.profile import load_profile
        from tests.helpers import settings

        profile = load_profile(settings().profile_path)
        lead = Lead(id="x", source="website_form", context="enquiry", name="A", phone="+919000000009", language="en", message=None,
                    consent_basis="form_checkbox", consent_text="ok", consent_at="t", external_id=None, received_at="2026-10-19T10:02:00+05:30")
        ex = {"id": "e", "status": "completed", "conversation_duration": 20,
              "transcript": "assistant: Hello A, I'm an AI assistant calling from X.\nuser: I didn't ask for this, please don't call me again.\n"}
        r = analysis.analyze(ex, lead, profile, settings().now(), None, "en")
        self.assertEqual((r.tag, r.do_not_call), ("cold", True))
        self.assertIn("don't call me again", r.reason_quote)

    def test_missing_disclosure_flagged(self):
        from flowmate_callback.profile import load_profile
        from tests.helpers import settings

        profile = load_profile(settings().profile_path)
        a = analysis.audit_call([("assistant", "Hello, this is Priya from the clinic."), ("user", "hi")], profile, settings().now(), None)
        self.assertFalse(a.disclosure_ok)
