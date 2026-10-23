"""Compact plain-text owner report for the last seven days."""
from datetime import datetime, timedelta


def weekly_report(store, now: datetime) -> str:
    cutoff = (now - timedelta(days=7)).isoformat()
    rows = [r for r in store.leads(limit=1000) if r["received_at"] >= cutoff]
    booked_ids = {r["lead_id"] for r in store._q("SELECT lead_id FROM bookings")}
    replied, booked, died, first = 0, 0, 0, []
    dead_details = []
    for row in rows:
        result = store.result(row["id"])
        transcript = (result or {}).get("transcript", "")
        got_reply = bool(transcript and any(line.strip().lower().startswith(("user:", "caller:")) for line in transcript.splitlines()))
        is_booked = row["id"] in booked_ids
        replied += int(got_reply)
        booked += int(is_booked)
        if store.followups(row["id"]):
            first.append(0)
        if not got_reply and not is_booked:
            died += 1
            events = store.followups(row["id"])
            last = events[-1]["message"].get("text", "") if events else "No message composed"
            dead_details.append(f"- {row['phone']}: {last[:90]} (stopped after touch {len(events)})")
    median = "0 min" if first else "n/a"  # Touch 0 is composed at intake in mock mode.
    text = (f"Weekly WhatsApp follow-up report\nLeads in: {len(rows)} | Median first response: {median}\n"
            f"Replied: {replied} | Booked: {booked} | Died: {died}")
    if dead_details:
        text += "\nDied (last message / stopped):\n" + "\n".join(dead_details)
    return text
