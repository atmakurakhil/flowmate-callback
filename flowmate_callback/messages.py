"""Compose the owner's notification and the caller's WhatsApp confirmation. Compose only.

Nothing in this repo sends a message. WhatsApp business-initiated messages need a
Meta-approved template (Bolna's AiSensy page says so), so the confirmation is built as
a template body with numbered variables plus the filled text for preview.
"""

from __future__ import annotations

from .agent import spoken_time
from .analysis import Result
from .leads import Lead, first_name
from .profile import LANGUAGE_NAMES, OwnerProfile
from .slots import spoken

TEMPLATE = {
    "en": "Hi {{1}}, your appointment at {{2}} is confirmed for {{3}}. Address: {{4}}. To change it: {{5}}. Sent by the clinic's AI assistant.",
    "hi": "नमस्ते {{1}}, {{2}} में आपकी अपॉइंटमेंट {{3}} के लिए पक्की हो गई है। पता: {{4}}। बदलने के लिए: {{5}}। यह संदेश क्लिनिक की AI असिस्टेंट ने भेजा है।",
    "te": "నమస్కారం {{1}}, {{2}} లో మీ అపాయింట్‌మెంట్ {{3}} కి కన్ఫర్మ్ అయింది. చిరునామా: {{4}}. మార్చడానికి: {{5}}. ఈ మెసేజ్ క్లినిక్ AI అసిస్టెంట్ పంపింది.",
}


def whatsapp_confirmation(lead: Lead, result: Result, profile: OwnerProfile) -> dict | None:
    lang = result.language if result.language in TEMPLATE else "en"
    if result.booking:
        when = spoken(result.booking, lang)
    elif lead.booked_start and result.tag == "hot":
        when = spoken_time(lead.booked_start, profile.business.timezone, lang)
    else:
        return None
    change = profile.booking.link or profile.business.phone_display or "reply to this message"
    params = [first_name(lead.name) or "there", profile.business.name, when, profile.business.address, change]
    text = TEMPLATE[lang]
    for i, v in enumerate(params, 1):
        text = text.replace("{{" + str(i) + "}}", v)
    return {"to": lead.phone, "language": lang, "template_body": TEMPLATE[lang], "params": params, "text": text, "sent": False}


def owner_notification(lead: Lead, result: Result, profile: OwnerProfile, transcript_url: str | None) -> str:
    if result.tag:
        head = f"[{result.tag.upper()}] {lead.name or 'New lead'} {lead.phone}"
    else:
        head = f"[NOT REACHED] {lead.name or 'New lead'} {lead.phone}"
    lines = [head]
    if result.dial_delay_s is not None:
        lines.append(f"Called back {result.dial_delay_s}s after the enquiry, in {LANGUAGE_NAMES.get(result.language or '', result.language)}.")
    lines += [f"{i}. {s}" for i, s in enumerate(result.summary, 1)]
    if result.reason_quote:
        src = "caller's words" if result.quote_source == "extraction_verified" else "caller's words, picked by rule"
        lines.append(f'Why {result.tag.upper()}: "{result.reason_quote}" ({src})')
    if result.do_not_call:
        lines.append("They asked not to be called again. Do not call them unless they contact you again.")
    if result.audit.unlisted_numbers:
        lines.append("CHECK: the agent said a number that is not in your profile: " + " | ".join(f'"{x}"' for x in result.audit.unlisted_numbers) + ". Correct it with the caller.")
    if not result.audit.disclosure_ok:
        lines.append("CHECK: the first agent sentence did not say it is an AI.")
    if result.reached and not result.audit.notes:
        lines.append("Checks passed: said it is an AI first; quoted nothing outside your profile.")
    if transcript_url:
        lines.append(f"Transcript, recording and drafts: {transcript_url} (owner token required)")
    return "\n".join(lines)
