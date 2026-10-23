"""After the call: tag hot/warm/cold with the reason quoted from the transcript, write a
3-line summary, and audit the call against the guardrails.

Split of work (same rule as the AREON lost-lead demo): Bolna's extraction pass may
suggest a tag and an evidence quote, but code decides what ships.
  - A booking made through our own book_slot tool, or an online booking, is HOT. That is a fact, not a guess.
  - An extraction quote is used only if it appears word for word in a caller line.
    Otherwise code picks the caller line itself, and says so (quote_source = "rule").
  - The audit flags any price-like number the agent said that is not written in the
    profile, and a first agent line that does not say it is an AI.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from .agent import EXTRACTION_CATEGORY, spoken_time
from .leads import Lead
from .profile import LANGUAGE_NAMES, OwnerProfile
from .slots import spoken

TERMINAL = {"completed", "no-answer", "busy", "failed", "canceled", "stopped", "error", "balance-low"}
NOT_REACHED = {"no-answer", "busy", "failed", "canceled", "stopped", "error", "balance-low"}

# Small keyword lists for the rule fallback. Hindi and Telugu come back from Sarvam in
# native script; English words inside them may also be transliterated.
NEGATIVE = re.compile(
    r"not interested|no thanks|don'?t call|do not call|didn'?t ask|wrong number|stop calling|"
    r"नहीं चाहिए|ज़रूरत नहीं|जरूरत नहीं|कॉल मत|फोन मत|"
    r"వద్దు|అవసరం లేదు|కాల్ చేయకండి|ఆసక్తి లేదు",
    re.I,
)
DNC = re.compile(r"don'?t call|do not call|didn'?t ask|stop calling|कॉल मत|फोन मत|कॉल न करें|కాల్ చేయకండి|ఫోన్ చేయకండి", re.I)
HESITANT = re.compile(
    r"think|later|discuss|ask my|check with|not now|maybe|how much|cost|price|"
    r"सोच|बाद में|बात करके|अभी नहीं|कितना|पैसे|"
    r"ఆలోచ|తర్వాత|ఇప్పుడు కాదు|ఎంత|అడిగి",
    re.I,
)
COMMIT = re.compile(
    r"book|confirm|i'?ll come|i will come|keeping|see you|"
    r"बुक|आऊँगा|आऊँगी|आ जाऊँगा|आ जाऊँगी|"
    r"బుక్|వస్తాను|కన్ఫర్మ్",
    re.I,
)
CURRENCY = re.compile(r"(₹|rs\.?|inr|rupees?|रुपये|रुपए|రూపాయ)", re.I)
NUMBER = re.compile(r"\d[\d,]*(?:[.:]\d+)?")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").casefold()
    s = "".join(" " if unicodedata.category(ch).startswith("P") else ch for ch in s)
    return re.sub(r"\s+", " ", s).strip()


def parse_transcript(text: str | None) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(assistant|user)\s*:\s*(.*)$", line, re.I)
        if m:
            turns.append((m.group(1).lower(), m.group(2).strip()))
        elif turns:
            spk, prev = turns[-1]
            turns[-1] = (spk, f"{prev} {line}")
    return turns


def _ext(execution: dict, name: str) -> dict:
    data = (execution.get("extracted_data") or {}).get(EXTRACTION_CATEGORY) or {}
    v = data.get(name)
    return v if isinstance(v, dict) else {}


def _ext_text(execution: dict, name: str) -> str | None:
    v = _ext(execution, name).get("subjective")
    if isinstance(v, str) and v.strip() and v.strip().lower() != "null":
        return v.strip()
    return None


def _ext_choice(execution: dict, name: str) -> str | None:
    v = _ext(execution, name).get("objective")
    return v.strip().lower() if isinstance(v, str) else None


def verified_quote(candidate: str | None, user_lines: list[str]) -> str | None:
    """Return transcript text, never the extractor's text. If the candidate appears
    character for character in a caller line (and is long enough to mean something),
    that exact span is returned; if it matches only after ignoring case and punctuation,
    or is too short ("yes"), the whole caller line is returned. No match: None."""
    if not candidate:
        return None
    c = norm(candidate)
    if not c:
        return None
    exact = candidate.strip()
    for line in user_lines:
        if c in norm(line):
            if exact in line and len(c) >= 12 and len(c.split()) >= 2:
                return exact
            return line
    return None


def rule_quote(tag: str, user_lines: list[str]) -> str:
    pattern = {"hot": COMMIT, "cold": NEGATIVE, "warm": HESITANT}[tag]
    hits = [l for l in user_lines if pattern.search(l)]
    if hits:
        return hits[-1] if tag == "hot" else hits[0]
    return max(user_lines, key=len)


@dataclass
class Audit:
    disclosure_ok: bool
    unlisted_numbers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Result:
    reached: bool
    status: str
    tag: str | None  # hot | warm | cold | None (not reached)
    reason_quote: str | None
    quote_source: str | None  # extraction_verified | rule | None
    do_not_call: bool
    summary: list[str]
    audit: Audit
    booking: str | None
    language: str | None
    duration_s: int
    dial_delay_s: int | None

    def to_dict(self) -> dict:
        return asdict(self)


def _allowed(profile: OwnerProfile, now: datetime, booking_slot: str | None) -> set[str]:
    allowed = set(profile.allowed_numbers()) | {"1", "2", "3"}
    for i in range(profile.booking.days_ahead + 8):
        allowed.add(str((now + timedelta(days=i)).day))
    for h in range(24):
        for m in ("00", "30"):
            allowed |= {f"{h}:{m}", f"{h % 12 or 12}:{m}", str(h), str(h % 12 or 12)}
    if booking_slot:
        allowed |= set(re.findall(r"\d+", booking_slot))
    return allowed


def audit_call(turns: list[tuple[str, str]], profile: OwnerProfile, now: datetime, booking_slot: str | None) -> Audit:
    agent_lines = [t for s, t in turns if s == "assistant"]
    first = agent_lines[0] if agent_lines else ""
    first_sentence = re.split(r"(?<=[.?!।])\s", first, maxsplit=1)[0]
    disclosure_ok = bool(re.search(r"\bAI\b", first_sentence))
    allowed = _allowed(profile, now, booking_slot)
    flagged: list[str] = []
    for line in agent_lines:
        for m in NUMBER.finditer(line):
            raw = m.group(0).rstrip(",.")
            value = raw.replace(",", "")
            before = line[max(0, m.start() - 4): m.start()]
            after = line[m.end(): m.end() + 10]
            money = bool(CURRENCY.search(before) or CURRENCY.search(after))
            if (money or len(value.replace(":", "").replace(".", "")) >= 3) and value not in allowed:
                snippet = line[max(0, m.start() - 25): m.end() + 15].strip()
                flagged.append(snippet)
    notes = []
    if not disclosure_ok:
        notes.append("First agent sentence did not say it is an AI.")
    if flagged:
        notes.append("Agent said a number that is not in your profile.")
    return Audit(disclosure_ok=disclosure_ok, unlisted_numbers=flagged, notes=notes)


def _dial_delay(execution: dict, lead: Lead) -> int | None:
    try:
        start = datetime.fromisoformat(str(execution.get("initiated_at")).replace("Z", "+00:00"))
        got = datetime.fromisoformat(lead.received_at)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return max(0, int((start - got).total_seconds()))
    except (TypeError, ValueError):
        return None


def analyze(execution: dict, lead: Lead, profile: OwnerProfile, now: datetime, booking_slot: str | None, language: str | None) -> Result:
    status = str(execution.get("status") or "unknown")
    duration = int(execution.get("conversation_duration") or 0)
    turns = parse_transcript(execution.get("transcript"))
    user_lines = [t for s, t in turns if s == "user" and t]
    reached = status not in NOT_REACHED and duration > 0 and bool(user_lines)
    audit = audit_call(turns, profile, now, booking_slot) if turns else Audit(disclosure_ok=True)
    who = f"{lead.name or 'Caller'} ({lead.phone})"
    via = {"website_form": "website form", "ad": "ad form", "missed_call": "missed call", "calcom": "online booking"}[lead.source]
    dial = _dial_delay(execution, lead)

    if not reached:
        why = (execution.get("telephony_data") or {}).get("hangup_reason") or status
        summary = [
            f"{who} contacted you by {via}" + (f": {lead.message}" if lead.message else "."),
            "Not reached: " + ("no answer" if status in ("no-answer", "completed") else status) + f" ({why}).",
            "Next: up to 2 automatic retries, 5 and 30 minutes later. If those miss too, call them yourself.",
        ]
        return Result(False, status, None, None, None, False, summary, audit, booking_slot, language, duration, dial)

    dnc = _ext_choice(execution, "Do Not Call") == "yes" or any(DNC.search(l) for l in user_lines)
    suggested = _ext_choice(execution, "Lead Temperature")
    negative = any(NEGATIVE.search(l) for l in user_lines)
    if booking_slot:
        tag = "hot"  # booked through our book_slot tool: a fact
    elif dnc:
        tag = "cold"
    elif lead.booked_start and suggested != "cold" and not negative:
        tag = "hot"  # already booked online and did not back out
    elif suggested in ("hot", "warm", "cold"):
        tag = suggested
    elif negative:
        tag = "cold"
    else:
        tag = "warm"

    quote = verified_quote(_ext_text(execution, "Evidence Quote"), user_lines)
    source = "extraction_verified" if quote else "rule"
    if not quote:
        quote = rule_quote(tag, user_lines)

    answers = []
    for q in profile.qualifying_questions:
        a = _ext_text(execution, q.label)
        answers.append(f"{q.label}: {a or 'not captured'}")
    concern = _ext_text(execution, profile.qualifying_questions[0].label) or lead.message or "not stated"
    lang_name = LANGUAGE_NAMES.get(language or "", language or "unknown")
    if booking_slot:
        outcome = f"Booked {spoken(booking_slot)}."
    elif lead.booked_start:
        outcome = f"Has an online booking for {spoken_time(lead.booked_start, profile.business.timezone)}."
    elif dnc:
        outcome = "Asked not to be called again. Do not call."
    else:
        outcome = {"warm": "No booking yet; follow up.", "cold": "Not interested.", "hot": "Ready to visit; no slot booked yet."}[tag]
    summary = [
        f"{who}, via {via}, call in {lang_name}, {duration // 60}:{duration % 60:02d}. Concern: {concern}",
        " · ".join(answers[1:]) if len(answers) > 1 else answers[0],
        f"{outcome} Tag {tag.upper()}.",
    ]
    return Result(True, status, tag, quote, source, dnc, summary, audit, booking_slot, language, duration, dial)
