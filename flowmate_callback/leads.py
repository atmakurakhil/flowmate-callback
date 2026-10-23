"""Inbound leads: normalise two shapes into one Lead, and refuse anything without consent.

Guardrail 1 (from the buildathon application): it calls only people who asked to be contacted.
Here that means every lead must carry explicit, recorded consent, or it is rejected
before any call is placed:

  form / ad lead   consent.given is the boolean true AND consent.text is the exact
                   wording the person agreed to (stored as evidence)
  missed call      consent.basis == "called_us" (the person phoned the business)
  cal.com booking  the booking question named CALCOM_CONSENT_FIELD was ticked
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import uuid
from datetime import datetime

from pydantic import BaseModel

from .profile import SUPPORTED_LANGUAGES


class LeadRejected(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Lead(BaseModel):
    id: str
    source: str  # website_form | ad | missed_call | calcom
    context: str  # enquiry | booking | missed_call  (picks the greeting)
    name: str | None
    phone: str  # E.164
    language: str | None
    message: str | None
    consent_basis: str  # form_checkbox | called_us | booking_question
    consent_text: str | None
    consent_at: str
    external_id: str | None
    received_at: str
    booked_start: str | None = None  # set for cal.com bookings (ISO, UTC as sent)


_SPACES = re.compile(r"[\s\-().]")


def normalize_phone(raw: str, allowed_cc: tuple[str, ...] = ("91",)) -> str:
    """Return E.164. Indian mobiles accept 98xxxxxxxx, 098xxxxxxxx, 9198xxxxxxxx, +91 98xxx xxxxx."""
    if not isinstance(raw, str) or not raw.strip():
        raise LeadRejected("bad_phone", "phone is required")
    s = _SPACES.sub("", raw.strip())
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        digits = s.lstrip("0")
        if re.fullmatch(r"[6-9]\d{9}", digits):
            s = "+91" + digits
        elif re.fullmatch(r"91[6-9]\d{9}", digits):
            s = "+" + digits
        else:
            raise LeadRejected("bad_phone", f"cannot read phone number {raw!r}")
    if not re.fullmatch(r"\+\d{8,15}", s):
        raise LeadRejected("bad_phone", f"cannot read phone number {raw!r}")
    if not any(s.startswith("+" + cc) for cc in allowed_cc):
        raise LeadRejected("country_not_allowed", f"{s} is outside the allowed country codes {allowed_cc}")
    if s.startswith("+91") and not re.fullmatch(r"\+91[6-9]\d{9}", s):
        raise LeadRejected("bad_phone", f"{s} is not a valid Indian mobile number")
    return s


def _clean_text(v, limit: int) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        v = str(v)
    # Lead text is untrusted: it reaches the agent's prompt. No braces (Bolna variable
    # syntax), no newlines, bounded length.
    v = re.sub(r"[{}<>\[\]\r\n\t]", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v[:limit] or None


def _language(v, default: str | None = None) -> str | None:
    if isinstance(v, str):
        code = v.strip().lower()[:2]
        if code in SUPPORTED_LANGUAGES:
            return code
    return default


def parse_form(payload: dict, now: datetime, allowed_cc: tuple[str, ...] = ("91",)) -> Lead:
    if not isinstance(payload, dict):
        raise LeadRejected("bad_payload", "body must be a JSON object")
    source = payload.get("source") or "website_form"
    if source not in ("website_form", "ad", "missed_call"):
        raise LeadRejected("bad_source", "source must be website_form, ad or missed_call")
    consent = payload.get("consent")
    if not isinstance(consent, dict):
        raise LeadRejected("no_consent", "consent object is required; this lead will not be called")
    if source == "missed_call":
        if consent.get("basis") != "called_us":
            raise LeadRejected("no_consent", "missed-call leads need consent.basis = 'called_us'")
        basis, text = "called_us", None
    else:
        if consent.get("given") is not True:
            raise LeadRejected("no_consent", "consent.given must be the boolean true; this lead will not be called")
        text = _clean_text(consent.get("text"), 300)
        if not text:
            raise LeadRejected("no_consent", "consent.text (the exact wording they agreed to) is required")
        basis = "form_checkbox"
    phone = normalize_phone(payload.get("phone", ""), allowed_cc)
    return Lead(
        id=str(uuid.uuid4()),
        source=source,
        context="missed_call" if source == "missed_call" else "enquiry",
        name=_clean_text(payload.get("name"), 60),
        phone=phone,
        language=_language(payload.get("language")),
        message=_clean_text(payload.get("message"), 200),
        consent_basis=basis,
        consent_text=text,
        consent_at=str(consent.get("at") or now.isoformat()),
        external_id=_clean_text(payload.get("external_id"), 100),
        received_at=now.isoformat(),
    )


def _truthy(v) -> bool:
    if v is True:
        return True
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "on", "checked", "1")
    if isinstance(v, list):
        return len(v) > 0
    return False


def parse_calcom(payload: dict, now: datetime, consent_field: str, allowed_cc: tuple[str, ...] = ("91",)) -> Lead | None:
    """Cal.com BOOKING_CREATED webhook (shape per cal.com webhook docs, read 2026-10-17).
    Returns None for other trigger events (they are acknowledged and ignored)."""
    if not isinstance(payload, dict) or payload.get("triggerEvent") != "BOOKING_CREATED":
        return None
    p = payload.get("payload") or {}
    responses = p.get("responses") or {}

    def resp(key):
        r = responses.get(key)
        return r.get("value") if isinstance(r, dict) else None

    consent = responses.get(consent_field)
    if not isinstance(consent, dict) or not _truthy(consent.get("value")):
        raise LeadRejected("no_consent", f"booking question {consent_field!r} was not ticked; this lead will not be called")
    attendees = p.get("attendees") or [{}]
    phone_raw = resp("attendeePhoneNumber") or resp("phone") or attendees[0].get("phoneNumber") or ""
    locale = ((attendees[0].get("language") or {}).get("locale")) if isinstance(attendees[0], dict) else None
    return Lead(
        id=str(uuid.uuid4()),
        source="calcom",
        context="booking",
        name=_clean_text(resp("name") or attendees[0].get("name"), 60),
        phone=normalize_phone(phone_raw, allowed_cc),
        language=_language(resp("language")) or _language(locale),
        message=_clean_text(resp("notes"), 200),
        consent_basis="booking_question",
        consent_text=_clean_text(consent.get("label") or consent_field, 300),
        consent_at=str(payload.get("createdAt") or now.isoformat()),
        external_id=_clean_text(p.get("uid"), 100),
        received_at=now.isoformat(),
        booked_start=p.get("startTime"),
    )


def verify_calcom_signature(body: bytes, header: str | None, secret: str | None) -> bool:
    """HMAC-SHA256 of the raw body with the webhook secret, sent in x-cal-signature-256.
    Cal.com's docs do not say hex or base64, so both are accepted (unverified, see RESEARCH.md)."""
    if not header or not secret:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256)
    candidates = {digest.hexdigest(), base64.b64encode(digest.digest()).decode()}
    header = header.strip()
    if header.startswith("sha256="):
        header = header[7:]
    return any(hmac.compare_digest(header, c) for c in candidates)


def verify_meta_signature(body: bytes, header: str | None, secret: str | None) -> bool:
    if not header or not secret or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header, expected)


def parse_meta_lead(payload: dict, field_data: list[dict], now: datetime, allowed_cc=("91",)) -> Lead:
    """Map a Meta leadgen webhook notification plus fetched fixture field_data to Lead."""
    try:
        change = next(change for entry in payload["entry"] for change in entry["changes"]
                      if change.get("field") == "leadgen")
        value = change["value"]
        leadgen_id = str(value["leadgen_id"])
        form_id, page_id = str(value["form_id"]), str(value["page_id"])
    except (KeyError, TypeError, StopIteration):
        raise LeadRejected("bad_meta_payload", "expected entry[].changes[].value leadgen_id, form_id and page_id")
    fields = {str(x.get("name", "")).lower(): x.get("values", [x.get("value")])[0]
              for x in field_data if isinstance(x, dict) and (x.get("values") or x.get("value") is not None)}
    consent_value = str(fields.get("consent", fields.get("callback_consent", ""))).strip().lower()
    if consent_value not in ("yes", "true", "1", "on", "checked"):
        raise LeadRejected("no_consent", "Meta lead form must record callback consent")
    phone = fields.get("phone_number") or fields.get("phone") or ""
    return Lead(id=str(uuid.uuid4()), source="ad", context="enquiry",
        name=_clean_text(fields.get("full_name") or fields.get("name"), 60),
        phone=normalize_phone(phone, allowed_cc), language=_language(fields.get("language")),
        message=_clean_text(fields.get("message") or fields.get("what_service"), 200),
        consent_basis="meta_lead_form", consent_text=_clean_text(str(fields.get("consent")), 300),
        consent_at=str(fields.get("created_time") or now.isoformat()),
        external_id=f"meta:{page_id}:{form_id}:{leadgen_id}", received_at=now.isoformat())


def first_name(name: str | None) -> str | None:
    if not name:
        return None
    return name.split()[0][:30]
