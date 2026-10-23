import hashlib
import hmac
import json
import unittest

from flowmate_callback.config import MOCK_NOW
from flowmate_callback.leads import LeadRejected, normalize_phone, parse_calcom, parse_form, verify_calcom_signature
from tests.helpers import lead_fixture

OK_CONSENT = {"given": True, "text": "I agree to a call back."}


class PhoneTests(unittest.TestCase):
    def test_indian_formats(self):
        for raw in ("9000000001", "09000000001", "919000000001", "+91 90000 00001", "+91-900-000-0001", "0091 9000000001"):
            with self.subTest(raw):
                self.assertEqual(normalize_phone(raw), "+919000000001")

    def test_rejects(self):
        for raw in ("", "12345", "+91 5000000001", "+14155550100", "abc"):
            with self.subTest(raw), self.assertRaises(LeadRejected):
                normalize_phone(raw)


class ConsentTests(unittest.TestCase):
    def form(self, **kw):
        body = {"name": "A", "phone": "9000000001"}
        body.update(kw)
        return parse_form(body, MOCK_NOW)

    def test_needs_explicit_consent(self):
        bad = [
            {},
            {"consent": None},
            {"consent": {"given": False, "text": "x"}},
            {"consent": {"given": "true", "text": "x"}},
            {"consent": {"given": True}},
            {"consent": {"given": True, "text": "   "}},
            {"source": "missed_call", "consent": {"given": True, "text": "x"}},
        ]
        for kw in bad:
            with self.subTest(kw), self.assertRaises(LeadRejected) as e:
                self.form(**kw)
            self.assertEqual(e.exception.code, "no_consent")

    def test_good_consent_is_kept_as_evidence(self):
        l = self.form(consent=OK_CONSENT, language="hi")
        self.assertEqual(l.consent_basis, "form_checkbox")
        self.assertEqual(l.consent_text, "I agree to a call back.")
        self.assertEqual(l.language, "hi")

    def test_missed_call_basis(self):
        l = self.form(source="missed_call", consent={"basis": "called_us"})
        self.assertEqual((l.context, l.consent_basis), ("missed_call", "called_us"))

    def test_bad_source(self):
        with self.assertRaises(LeadRejected):
            self.form(source="scraped_list", consent=OK_CONSENT)


class CalcomTests(unittest.TestCase):
    def test_booking_with_consent(self):
        body = lead_fixture("5-calcom-sameer-english.json")["body"]
        l = parse_calcom(body, MOCK_NOW, "callback_consent")
        self.assertEqual((l.phone, l.context, l.language, l.name), ("+919000000005", "booking", "en", "Sameer Khan"))
        self.assertEqual(l.booked_start, "2026-10-24T11:00:00.000Z")

    def test_booking_without_consent_rejected(self):
        body = json.loads(json.dumps(lead_fixture("5-calcom-sameer-english.json")["body"]))
        body["payload"]["responses"]["callback_consent"]["value"] = False
        with self.assertRaises(LeadRejected):
            parse_calcom(body, MOCK_NOW, "callback_consent")
        del body["payload"]["responses"]["callback_consent"]
        with self.assertRaises(LeadRejected):
            parse_calcom(body, MOCK_NOW, "callback_consent")

    def test_other_events_ignored(self):
        self.assertIsNone(parse_calcom({"triggerEvent": "BOOKING_CANCELLED"}, MOCK_NOW, "callback_consent"))

    def test_signature(self):
        body = b'{"a":1}'
        sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_calcom_signature(body, sig, "secret"))
        self.assertFalse(verify_calcom_signature(body, sig, "other"))
        self.assertFalse(verify_calcom_signature(body + b" ", sig, "secret"))
        self.assertFalse(verify_calcom_signature(body, None, "secret"))
