import hashlib
import hmac
import json
import unittest
from datetime import timedelta

from flowmate_callback.config import MOCK_NOW, ROOT
from flowmate_callback.followup import is_opt_out, sequence, should_stop
from flowmate_callback.leads import LeadRejected, parse_form
from flowmate_callback.profile import load_profile
from flowmate_callback.report import weekly_report
from tests.helpers import TOKEN, client


class WhatsAppTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile(ROOT / "profiles/glow-derm-hyderabad.FICTIONAL.json")
        self.lead = parse_form({"phone": "9000000001", "name": "Ravi", "message": "acne consult",
                                "consent": {"given": True, "text": "Yes"}}, MOCK_NOW)

    def test_consent_gate(self):
        with self.assertRaises(LeadRejected):
            parse_form({"phone": "9000000001"}, MOCK_NOW)

    def test_window(self):
        seq = sequence(self.lead, self.profile, MOCK_NOW.replace(hour=20))
        self.assertEqual([x["at"][11:16] for x in seq], ["20:02", "09:00", "20:02", "20:02"])

    def test_stop_on_reply_and_opt_out_languages(self):
        self.assertTrue(is_opt_out("STOP"))
        self.assertTrue(is_opt_out("नहीं"))
        self.assertTrue(is_opt_out("వద్దు"))
        self.assertFalse(is_opt_out("yes"))
        self.assertTrue(should_stop(reply="Thanks"))
        self.assertTrue(should_stop(booked=True))
        self.assertTrue(should_stop(reply="STOP"))
        self.assertFalse(should_stop())
        self.assertEqual(sequence(self.lead, self.profile, MOCK_NOW, reply="Thanks"), [])
        self.assertEqual(sequence(self.lead, self.profile, MOCK_NOW, booked=True), [])
        self.assertEqual(sequence(self.lead, self.profile, MOCK_NOW, reply="STOP"), [])

    def test_no_price_invention(self):
        message = sequence(self.lead, self.profile, MOCK_NOW)[0]["message"]["text"]
        self.assertNotIn("₹", message)
        self.assertIn("assistant", message)

    def test_meta_hmac_reject(self):
        c, _ = client()
        body = json.dumps({"entry": []}).encode()
        response = c.post("/leads/meta", content=body, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64})
        self.assertEqual(response.status_code, 401)

    def test_report_numbers(self):
        c, svc = client()
        lead = self.lead
        svc.store.add_lead(lead, "received")
        svc.store.save_followups(lead.id, sequence(lead, self.profile, MOCK_NOW))
        report = weekly_report(svc.store, MOCK_NOW + timedelta(minutes=1))
        self.assertIn("Leads in: 1", report)
        self.assertIn("Died: 1", report)


if __name__ == "__main__":
    unittest.main()
