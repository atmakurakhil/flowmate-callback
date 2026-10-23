"""The owner profile: the only source of facts the agent may say on a call.

Rule enforced here and in agent.py: a price exists only if the owner wrote it.
A service with no written price gets the owner's price_policy line, or a stock
"the clinic will confirm" line. Nothing is inferred.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

SUPPORTED_LANGUAGES = ("en", "hi", "te")
LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "te": "Telugu"}
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Business(BaseModel):
    name: str
    name_spoken: dict[str, str] = Field(default_factory=dict)  # per language, for the voice
    city: str
    address: str
    phone_display: str | None = None
    timezone: str = "Asia/Kolkata"

    def spoken(self, lang: str) -> str:
        return self.name_spoken.get(lang) or self.name


class Service(BaseModel):
    name: str
    price: str | None = None  # exactly as the owner wrote it, e.g. "₹1,200"
    price_policy: str | None = None  # what to say when there is no price
    notes: str | None = None


class FAQ(BaseModel):
    q: str
    a: str


class Booking(BaseModel):
    slot_minutes: int = 30
    days_ahead: int = 6
    min_notice_minutes: int = 60
    link: str | None = None
    what_to_bring: str | None = None

    @field_validator("link")
    @classmethod
    def https_only(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("https://"):
            raise ValueError("booking.link must start with https://")
        return v


class CallingWindow(BaseModel):
    start_hour: int = 9
    end_hour: int = 21

    @model_validator(mode="after")
    def trai_window(self) -> "CallingWindow":
        # Bolna's calling-guardrails page lists TRAI's window as 9:00 to 21:00 IST.
        if not (9 <= self.start_hour < self.end_hour <= 21):
            raise ValueError("calling_window must sit inside 09:00-21:00 IST")
        return self


class Question(BaseModel):
    id: str
    label: str
    text: dict[str, str]


class Voices(BaseModel):
    # Sarvam bulbul:v3 speaker ids listed on Bolna's Sarvam voice page (read 2026-10-17).
    en: str = "ritu"
    hi: str = "priya"
    te: str = "kavya"


class OwnerProfile(BaseModel):
    fictional: bool = False
    notice: str | None = None
    business: Business
    languages: list[str]
    default_language: str
    hours: dict[str, list[list[str]]]
    calling_window: CallingWindow = Field(default_factory=CallingWindow)
    services: list[Service]
    not_offered: list[str] = Field(default_factory=list)
    faq: list[FAQ] = Field(default_factory=list)
    booking: Booking = Field(default_factory=Booking)
    qualifying_questions: list[Question]
    voices: Voices = Field(default_factory=Voices)
    owner_whatsapp: str | None = None

    @model_validator(mode="after")
    def validate_all(self) -> "OwnerProfile":
        bad = [l for l in self.languages if l not in SUPPORTED_LANGUAGES]
        if bad:
            raise ValueError(f"unsupported languages {bad}; v0 supports {SUPPORTED_LANGUAGES}")
        if not self.languages:
            raise ValueError("at least one language is required")
        if self.default_language not in self.languages:
            raise ValueError("default_language must be one of languages")
        if not 2 <= len(self.qualifying_questions) <= 3:
            raise ValueError("qualifying_questions must have 2 or 3 entries")
        for q in self.qualifying_questions:
            missing = [l for l in self.languages if not q.text.get(l)]
            if missing:
                raise ValueError(f"question {q.id!r} has no text for {missing}")
        for day, ranges in self.hours.items():
            if day not in DAYS:
                raise ValueError(f"unknown day {day!r} in hours")
            for r in ranges:
                if len(r) != 2 or not all(HHMM.match(x) for x in r) or r[0] >= r[1]:
                    raise ValueError(f"bad hours range {r!r} on {day}")
        braces = [s for s in _strings(self.model_dump()) if "{" in s or "}" in s]
        if braces:
            raise ValueError(f"profile text may not contain braces (Bolna reads them as variables): {braces[:3]}")
        for s in self.services:
            if s.price is not None and not re.search(r"\d", s.price):
                raise ValueError(f"service {s.name!r}: price must be the written amount, got {s.price!r}")
        return self

    # --- helpers used by the prompt and the post-call audit ---

    def hours_text(self) -> str:
        parts = []
        for day in DAYS:
            ranges = self.hours.get(day, [])
            label = day.capitalize()
            parts.append(f"{label}: closed" if not ranges else f"{label}: " + ", ".join(f"{a}-{b}" for a, b in ranges))
        return "; ".join(parts)

    def allowed_numbers(self) -> set[str]:
        """Every number written anywhere in the profile, normalised (1,200 -> 1200)."""
        blob = self.model_dump_json()
        found = {n.replace(",", "") for n in re.findall(r"\d[\d,]*", blob)}
        for ranges in self.hours.values():
            for r in ranges:
                for t in r:
                    h, m = int(t[:2]), int(t[3:])
                    found |= {str(h), str(h % 12 or 12), str(m), f"{h}:{t[3:]}", f"{h % 12 or 12}:{t[3:]}"}
        return found


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def load_profile(path: str | Path) -> OwnerProfile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return OwnerProfile.model_validate(data)
