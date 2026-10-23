"""Compose-only WhatsApp follow-ups. No provider client or send operation exists here."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .leads import Lead, first_name
from .profile import OwnerProfile

OPT_OUT = {"stop", "no", "not interested", "नहीं", "रोकें", "వద్దు", "ఆపండి"}


def is_opt_out(text: str) -> bool:
    normalized = " ".join(text.casefold().strip().split())
    return normalized in OPT_OUT


def next_window(dt: datetime, profile: OwnerProfile) -> datetime:
    zone = ZoneInfo(profile.business.timezone)
    local = dt.astimezone(zone)
    start, end = profile.calling_window.start_hour, profile.calling_window.end_hour
    if start <= local.hour < end:
        return dt
    day = local.date() if local.hour < start else local.date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, start, tzinfo=zone)


def compose_template(lead: Lead, profile: OwnerProfile, touch: int) -> dict:
    lang = lead.language if lead.language in profile.languages else profile.default_language
    name = first_name(lead.name) or "there"
    enquiry = (lead.message or "").strip()
    # All factual copy comes from the profile. The touch-up is deliberately generic.
    clinic = profile.business.name
    if lang == "hi":
        text = f"नमस्ते {name}, मैं {clinic} का सहायक हूँ। आपकी पूछताछ मिली है। क्या आप अपॉइंटमेंट के बारे में बात करना चाहेंगे?" if touch == 0 else f"नमस्ते {name}, {clinic} से आपकी पूछताछ के बारे में फिर से संपर्क कर रहे हैं। क्या हम आपकी मदद कर सकते हैं?"
    elif lang == "te":
        text = f"నమస్కారం {name}, నేను {clinic} క్లినిక్ సహాయకుడిని. మీ విచారణ అందింది. అపాయింట్‌మెంట్ గురించి మాట్లాడాలనుకుంటున్నారా?" if touch == 0 else f"నమస్కారం {name}, {clinic} నుంచి మీ విచారణ గురించి మళ్లీ సంప్రదిస్తున్నాం. సహాయం చేయాలా?"
    else:
        text = f"Hi {name}, I’m the assistant for {clinic}. We received your enquiry. Would you like to discuss an appointment?" if touch == 0 else f"Hi {name}, following up from {clinic} about your enquiry. Can we help?"
    return {"messaging_product": "whatsapp", "to": lead.phone, "type": "template",
            "template": {"name": "clinic_enquiry_followup", "language": {"code": lang},
                         "components": [{"type": "body", "parameters": [{"type": "text", "text": name}]}]},
            "text": text, "enquiry_context": enquiry}


def sequence(lead: Lead, profile: OwnerProfile, now: datetime, *, reply: str | None = None, booked: bool = False) -> list[dict]:
    if not lead.consent_basis:
        return []
    if should_stop(reply=reply, booked=booked):
        return []
    out = []
    for touch, offset in enumerate((0, 2, 24, 72)):
        due = next_window(now + timedelta(hours=offset), profile)
        out.append({"touch": touch, "at": due.isoformat(), "message": compose_template(lead, profile, touch)})
    return out


def should_stop(reply: str | None = None, booked: bool = False) -> bool:
    """A reply ends automation; opt-outs are also detectable for suppression."""
    return booked or bool(reply and reply.strip()) or bool(reply and is_opt_out(reply))
