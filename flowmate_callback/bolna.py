"""Bolna API client, and a mock that replays fixture calls with no key and no network.

Endpoints used (Bolna API reference, read 2026-10-17), base https://api.bolna.ai, Bearer auth:
  GET  /user/me                  key check, wallet, concurrency
  POST /v2/agent                 create agent -> {"agent_id", "state"}
  POST /dispositions/bulk        create the post-call extractions for an agent
  POST /call                     place a call -> {"status": "queued", "execution_id"}
  GET  /executions/{id}          the full execution (transcript, extracted_data, telephony_data)
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from datetime import timedelta
from pathlib import Path

import httpx

from .config import Settings


class BolnaError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Bolna API {status}: {message}")
        self.status = status


class BolnaClient:
    def __init__(self, api_key: str, base_url: str = "https://api.bolna.ai", http: httpx.Client | None = None):
        self._http = http or httpx.Client(base_url=base_url, timeout=15.0)
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        for attempt in range(3):
            r = self._http.request(method, path, headers=self._headers, json=body)
            if r.status_code == 429 and attempt < 2:  # docs: back off and retry on 429
                time.sleep(2**attempt)
                continue
            if r.status_code >= 400:
                try:
                    msg = r.json().get("message") or r.text
                except ValueError:
                    msg = r.text
                raise BolnaError(r.status_code, str(msg)[:300])
            return r.json() if r.content else {}
        raise BolnaError(429, "rate limited")

    def me(self) -> dict:
        return self._req("GET", "/user/me")

    def create_agent(self, body: dict) -> dict:
        return self._req("POST", "/v2/agent", body)

    def create_dispositions(self, agent_id: str, items: list[dict]) -> dict:
        return self._req("POST", "/dispositions/bulk", {"agent_id": agent_id, "dispositions": items})

    def place_call(self, agent_id: str, to: str, user_data: dict, language: str | None = None,
                   from_number: str | None = None, scheduled_at: str | None = None) -> dict:
        body = {
            "agent_id": agent_id,
            "recipient_phone_number": to,
            "user_data": user_data,
            # Two retries on no-answer/busy, 5 and 30 minutes apart (auto-retry fields from the /call reference).
            "retry_config": {"enabled": True, "max_retries": 2, "retry_on_statuses": ["no-answer", "busy"], "retry_intervals_minutes": [5, 30]},
        }
        if language:
            body["agent_data"] = {"language": language}
        if from_number:
            body["from_phone_number"] = from_number
        if scheduled_at:
            body["scheduled_at"] = scheduled_at
        return self._req("POST", "/call", body)

    def get_execution(self, execution_id: str) -> dict:
        return self._req("GET", f"/executions/{execution_id}")


class MockBolna:
    """Same interface. place_call picks the fixture whose match_phone is the recipient,
    and get_execution returns that fixture as a completed Bolna execution."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls: list[dict] = []
        self.executions: dict[str, dict] = {}
        self.fixtures = {}
        for f in sorted(Path(settings.fixtures_dir, "executions").glob("*.json")):
            fx = json.loads(f.read_text(encoding="utf-8"))
            self.fixtures[fx["match_phone"]] = fx

    def me(self) -> dict:
        return {"name": "mock", "wallet": 0, "concurrency": {"max": 2, "current": 0}}

    def create_agent(self, body: dict) -> dict:
        return {"agent_id": "mock-agent", "state": "created"}

    def create_dispositions(self, agent_id: str, items: list[dict]) -> dict:
        return {"created": len(items)}

    def place_call(self, agent_id: str, to: str, user_data: dict, language: str | None = None,
                   from_number: str | None = None, scheduled_at: str | None = None) -> dict:
        execution_id = str(uuid.uuid4())
        self.calls.append({"execution_id": execution_id, "to": to, "user_data": user_data, "language": language, "scheduled_at": scheduled_at})
        fx = self.fixtures.get(to)
        now = self.settings.now()
        base = copy.deepcopy(fx["execution"]) if fx else {"status": "no-answer", "conversation_duration": 0, "transcript": ""}
        base.update({
            "id": execution_id,
            "agent_id": agent_id,
            "user_number": to,
            "agent_number": "+910000000000",
            "created_at": now.isoformat(),
            "initiated_at": (now + timedelta(seconds=(fx or {}).get("dial_delay_seconds", 8))).isoformat(),
            "context_details": {"recipient_phone_number": to, "recipient_data": user_data},
        })
        if fx and "{greeting}" in base.get("transcript", ""):
            base["transcript"] = base["transcript"].replace("{greeting}", user_data["greeting"])
        self.executions[execution_id] = {"execution": base, "tool_calls": (fx or {}).get("tool_calls", [])}
        return {"message": "done", "status": "scheduled" if scheduled_at else "queued", "execution_id": execution_id}

    def get_execution(self, execution_id: str) -> dict:
        if execution_id not in self.executions:
            raise BolnaError(404, "execution not found")
        return copy.deepcopy(self.executions[execution_id]["execution"])

    def script(self, execution_id: str) -> dict:
        return self.executions[execution_id]
