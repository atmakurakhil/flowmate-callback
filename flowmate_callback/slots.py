"""The booking calendar for v0: open slots come from the owner's written hours, bookings live in SQLite.

The agent can only offer a time this module returned, and can only book one of those
times. Syncing to Google Calendar or Cal.com is a v1 step (README).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .profile import DAYS, OwnerProfile

SLOT_FMT = "%Y-%m-%dT%H:%M"


def _local(profile: OwnerProfile, now: datetime) -> datetime:
    return now.astimezone(ZoneInfo(profile.business.timezone))


def open_slots(profile: OwnerProfile, day: date, now: datetime, booked: set[str]) -> list[str]:
    tz = ZoneInfo(profile.business.timezone)
    local_now = _local(profile, now)
    if day < local_now.date() or day > local_now.date() + timedelta(days=profile.booking.days_ahead):
        return []
    earliest = local_now + timedelta(minutes=profile.booking.min_notice_minutes)
    step = timedelta(minutes=profile.booking.slot_minutes)
    out = []
    for start_s, end_s in profile.hours.get(DAYS[day.weekday()], []):
        t = datetime.combine(day, datetime.strptime(start_s, "%H:%M").time(), tz)
        end = datetime.combine(day, datetime.strptime(end_s, "%H:%M").time(), tz)
        while t + step <= end:
            key = t.strftime(SLOT_FMT)
            if t >= earliest and key not in booked:
                out.append(key)
            t += step
    return out


WEEKDAYS = {
    "hi": ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
    "te": ["సోమవారం", "మంగళవారం", "బుధవారం", "గురువారం", "శుక్రవారం", "శనివారం", "ఆదివారం"],
}
MONTHS = {
    "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
    "te": ["జనవరి", "ఫిబ్రవరి", "మార్చి", "ఏప్రిల్", "మే", "జూన్", "జూలై", "ఆగస్టు", "సెప్టెంబర్", "అక్టోబర్", "నవంబర్", "డిసెంబర్"],
}


def human(dt: datetime, lang: str = "en") -> str:
    """'Tuesday 20 October, 11:00 AM', with weekday and month in Hindi or Telugu when asked."""
    clock = dt.strftime("%-I:%M %p")
    if lang in WEEKDAYS:
        return f"{WEEKDAYS[lang][dt.weekday()]} {dt.day} {MONTHS[lang][dt.month - 1]}, {clock}"
    return dt.strftime("%A %-d %B, ") + clock


def spoken(slot: str, lang: str = "en") -> str:
    return human(datetime.strptime(slot, SLOT_FMT), lang)


def parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


def is_bookable(profile: OwnerProfile, slot: str, now: datetime, booked: set[str]) -> bool:
    try:
        dt = datetime.strptime((slot or "").strip()[:16], SLOT_FMT)
    except ValueError:
        return False
    return dt.strftime(SLOT_FMT) in open_slots(profile, dt.date(), now, booked)
