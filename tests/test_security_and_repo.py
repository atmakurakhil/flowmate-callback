import importlib.metadata as md
import re
import subprocess
import unittest
from pathlib import Path

from flowmate_callback.app import Service
from flowmate_callback.config import ROOT, ConfigError, Settings
from flowmate_callback.store import Store
from tests.helpers import TOKEN, client, lead_fixture, post_lead, settings


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.c, self.svc = client()

    def test_inbound_token_required(self):
        fx = lead_fixture("1-form-ravi-telugu.json")
        r = self.c.post("/leads/form", json=fx["body"], headers={"X-FlowMate-Token": "wrong"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.c.post("/leads/form", json=fx["body"]).status_code, 401)
        self.assertEqual(self.svc.bolna.calls, [])

    def test_calcom_signature_required(self):
        fx = lead_fixture("5-calcom-sameer-english.json")
        r = self.c.post("/leads/calcom", json=fx["body"], headers={"x-cal-signature-256": "00" * 32})
        self.assertEqual(r.status_code, 401)

    def test_webhook_tool_and_admin_tokens(self):
        self.assertEqual(self.c.post("/webhooks/bolna?token=nope", json={"id": "x", "status": "completed"}).status_code, 401)
        self.assertEqual(self.c.post("/tools/slots", json={"date": "2026-10-20"}).status_code, 401)
        self.assertEqual(self.c.post("/tools/book", json={}, headers={"Authorization": "Bearer nope"}).status_code, 401)
        self.assertEqual(self.c.get("/leads").status_code, 401)
        self.assertEqual(self.c.get("/leads", headers={"X-FlowMate-Token": TOKEN}).status_code, 200)

    def test_bolna_ip_enforcement(self):
        c, _ = client(enforce_bolna_ips=True)
        r = c.post(f"/webhooks/bolna?token={TOKEN}", json={"id": "x", "status": "completed"})
        self.assertEqual(r.status_code, 403)  # the test client is not a Bolna IP
        c2, _ = client(enforce_bolna_ips=True, trust_proxy=True)
        r2 = c2.post(f"/webhooks/bolna?token={TOKEN}", json={"id": "x", "status": "completed"}, headers={"x-forwarded-for": "13.203.39.153"})
        self.assertEqual(r2.status_code, 200)


class LiveSafetyTests(unittest.TestCase):
    def live(self, **kw):
        s = settings(mode="live", bolna_api_key="bn-test", **kw)

        class NoNetwork:
            calls = []

            def place_call(self, *a, **k):
                self.calls.append(a)
                return {"status": "queued", "execution_id": "live-1"}

        return Service(s, client=NoNetwork(), store=Store(":memory:"))

    def test_live_refuses_without_allowlist_and_for_fixture_numbers(self):
        from flowmate_callback.config import MOCK_NOW
        from flowmate_callback.leads import parse_form

        lead = parse_form({"phone": "9876500001", "consent": {"given": True, "text": "ok"}}, MOCK_NOW)
        self.assertEqual(self.live().take_lead(lead)["status"], "blocked")
        fixture = parse_form({"phone": "9000000001", "consent": {"given": True, "text": "ok"}}, MOCK_NOW)
        self.assertEqual(self.live(live_allowlist=frozenset({"*"})).take_lead(fixture)["status"], "blocked")
        svc = self.live(live_allowlist=frozenset({"+919876500001"}))
        self.assertEqual(svc.take_lead(lead)["status"], "calling")

    def test_live_config_demands_tokens_and_https(self):
        with self.assertRaises(ConfigError):
            Settings(mode="live", bolna_api_key="bn-x").check()
        with self.assertRaises(ConfigError):
            Settings(mode="live", bolna_api_key="k", bolna_agent_id="a", public_base_url="http://x", inbound_token=TOKEN,
                     webhook_token=TOKEN, tool_token=TOKEN, admin_token=TOKEN).check()
        with self.assertRaises(ConfigError):
            Settings(mode="live", bolna_api_key="k", bolna_agent_id="a", public_base_url="https://x", inbound_token="short",
                     webhook_token=TOKEN, tool_token=TOKEN, admin_token=TOKEN).check()


class RepoHygiene(unittest.TestCase):
    def tracked_files(self):
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True)
        return [ROOT / f for f in out.stdout.split() if f]

    def test_no_secrets_in_files(self):
        pattern = re.compile(r"bn-[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY")
        for f in self.tracked_files():
            if f.is_file() and f.suffix in (".py", ".json", ".md", ".txt", ".example", ".toml", ".sh", ""):
                with self.subTest(f.name):
                    self.assertIsNone(pattern.search(f.read_text(encoding="utf-8", errors="ignore")))

    def test_env_file_is_ignored(self):
        self.assertIn(".env", (ROOT / ".gitignore").read_text().split())

    def test_dependencies_are_pinned_and_not_agpl(self):
        reqs = [l.strip() for l in (ROOT / "requirements.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
        self.assertTrue(reqs)
        for r in reqs:
            name, _, version = r.partition("==")
            with self.subTest(name):
                self.assertTrue(version, f"{name} is not pinned")
                try:
                    meta = md.metadata(name)
                except md.PackageNotFoundError:
                    self.skipTest(f"{name} not installed here")
                lic = " ".join(filter(None, [meta.get("License-Expression"), meta.get("License") or "", *(meta.get_all("Classifier") or [])]))
                self.assertNotRegex(lic, r"AGPL|Affero")
