"""Settings, read from environment variables only. No secret ever lives in a file in this repo.

Two modes:
  mock  (default)  no Bolna key needed; calls are replayed from fixtures/executions.
  live             real Bolna API. Refuses to start without every token it needs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
IST = ZoneInfo("Asia/Kolkata")

# Bolna sends webhooks and tool calls from these IPs (docs.bolna.ai, security page, read 2026-10-17).
BOLNA_SOURCE_IPS = frozenset({"13.203.39.153", "13.126.9.249", "13.202.133.53"})

# Mock mode runs on a fixed clock so the demo is the same every time:
# Monday 19 Oct 2026, 10:02 IST. Set FLOWMATE_MOCK_NOW=real to use the wall clock.
MOCK_NOW = datetime(2026, 10, 19, 10, 2, 0, tzinfo=IST)


class ConfigError(RuntimeError):
    pass


@dataclass
class Settings:
    mode: str = "mock"
    bolna_api_key: str | None = None
    bolna_agent_id: str | None = None
    bolna_base_url: str = "https://api.bolna.ai"
    from_number: str | None = None
    public_base_url: str | None = None
    inbound_token: str | None = None
    calcom_secret: str | None = None
    meta_app_secret: str | None = None
    webhook_token: str | None = None
    tool_token: str | None = None
    admin_token: str | None = None
    enforce_bolna_ips: bool = False
    trust_proxy: bool = False
    live_allowlist: frozenset[str] | None = None  # None = nobody; {"*"} = anyone with consent
    allowed_country_codes: tuple[str, ...] = ("91",)
    db_path: str = ":memory:"
    profile_path: str = str(ROOT / "profiles" / "neelagiri-dental-hyderabad.FICTIONAL.json")
    fixtures_dir: str = str(ROOT / "fixtures")
    calcom_consent_field: str = "callback_consent"
    recall_window_hours: int = 24
    fixed_now: datetime | None = None
    extra: dict = field(default_factory=dict)

    @property
    def live(self) -> bool:
        return self.mode == "live"

    def now(self) -> datetime:
        return self.fixed_now or datetime.now(IST)

    def check(self) -> "Settings":
        if self.mode not in ("mock", "live"):
            raise ConfigError(f"FLOWMATE_MODE must be mock or live, got {self.mode!r}")
        if self.live:
            needed = {
                "BOLNA_API_KEY": self.bolna_api_key,
                "BOLNA_AGENT_ID": self.bolna_agent_id,
                "FLOWMATE_PUBLIC_BASE_URL": self.public_base_url,
                "FLOWMATE_INBOUND_TOKEN": self.inbound_token,
                "FLOWMATE_WEBHOOK_TOKEN": self.webhook_token,
                "FLOWMATE_TOOL_TOKEN": self.tool_token,
                "FLOWMATE_ADMIN_TOKEN": self.admin_token,
            }
            missing = [k for k, v in needed.items() if not v]
            if missing:
                raise ConfigError("live mode needs: " + ", ".join(missing))
            if not str(self.public_base_url).startswith("https://"):
                raise ConfigError("FLOWMATE_PUBLIC_BASE_URL must be https:// (Bolna needs a public HTTPS endpoint)")
            short = [k for k, v in needed.items() if k.endswith("TOKEN") and len(v) < 24]
            if short:
                raise ConfigError("tokens must be at least 24 characters: " + ", ".join(short))
        return self


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _allowlist(raw: str | None) -> frozenset[str] | None:
    if raw is None or raw.strip() == "":
        return None
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def from_env() -> Settings:
    env = os.environ.get
    mode = (env("FLOWMATE_MODE") or "mock").strip().lower()
    live = mode == "live"
    # Mock mode gets fixed development tokens so auth is still exercised end to end.
    dev = None if live else "mock-dev-token-not-a-secret"
    fixed_now = None
    if not live and (env("FLOWMATE_MOCK_NOW") or "").lower() != "real":
        fixed_now = MOCK_NOW
    s = Settings(
        mode=mode,
        bolna_api_key=env("BOLNA_API_KEY"),
        bolna_agent_id=env("BOLNA_AGENT_ID") or (None if live else "mock-agent"),
        bolna_base_url=env("BOLNA_BASE_URL") or "https://api.bolna.ai",
        from_number=env("BOLNA_FROM_NUMBER") or None,
        public_base_url=(env("FLOWMATE_PUBLIC_BASE_URL") or ("" if live else "https://mock.flowmate.local")).rstrip("/") or None,
        inbound_token=env("FLOWMATE_INBOUND_TOKEN") or dev,
        calcom_secret=env("CALCOM_WEBHOOK_SECRET") or dev,
        meta_app_secret=env("META_APP_SECRET") or dev,
        webhook_token=env("FLOWMATE_WEBHOOK_TOKEN") or dev,
        tool_token=env("FLOWMATE_TOOL_TOKEN") or dev,
        admin_token=env("FLOWMATE_ADMIN_TOKEN") or dev,
        enforce_bolna_ips=_bool("FLOWMATE_ENFORCE_BOLNA_IPS", live),
        trust_proxy=_bool("FLOWMATE_TRUST_PROXY", False),
        live_allowlist=_allowlist(env("FLOWMATE_LIVE_ALLOWLIST")),
        db_path=env("FLOWMATE_DB") or (str(ROOT / "data" / "callback.db") if live else ":memory:"),
        profile_path=env("FLOWMATE_PROFILE") or Settings.profile_path,
        calcom_consent_field=env("CALCOM_CONSENT_FIELD") or "callback_consent",
        fixed_now=fixed_now,
    )
    return s.check()
