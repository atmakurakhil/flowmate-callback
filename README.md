# FlowMate Callback v0

An AI voice agent on [Bolna](https://www.bolna.ai) that phones back every enquiry an Indian small business gets (form fill, ad lead, missed call, online booking) within a minute, in Telugu, Hindi or English.

On the call, the agent:
- answers only from the owner's written details;
- asks two or three qualifying questions;
- books a slot in the owner's hours.

After the call, the owner gets a transcript, a three-line summary, and a hot, warm or cold tag. The tag's reason is quoted from the caller's own words. The caller's WhatsApp confirmation is drafted too.

v0 composes messages but never sends them. Mock mode runs the whole flow with no Bolna key.

**Live demo:** [flowmate-callback.vercel.app](https://flowmate-callback.vercel.app) (six simulated calls on a fictional clinic). **Pitch deck:** [deck/deck.html](deck/deck.html).

This is the FlowMate entry for the Bolna Buildathon (submissions close 2026-10-31). The research behind every Bolna field used here is in [docs/RESEARCH.md](docs/RESEARCH.md).

## The three guardrails, and where the code enforces them

| Guardrail (as stated on the application) | Enforced in |
|---|---|
| **Calls only people who asked to be contacted** | `leads.py` rejects any lead that lacks recorded consent, before a call is placed. That means `consent.given: true` plus the wording they agreed to, `consent.basis: "called_us"` for a missed call, or a ticked consent question on a Cal.com booking. Other limits: no more than one call per number in 24 hours, calls only between 09:00 and 21:00 IST (a lead that arrives outside those hours is scheduled for the next 09:00), and live mode calls only the numbers in `FLOWMATE_LIVE_ALLOWLIST`. |
| **Says it is an AI in its first sentence** | `agent.py` sets the welcome message to `{greeting}`, and the greeting text is built in code, not by the owner. Its first sentence says "AI" in every language and situation, and a test checks every combination. After each call, `analysis.audit_call()` checks the transcript's first agent sentence again. |
| **Quotes nothing the owner has not written down** | The prompt's BUSINESS FACTS come only from the profile. A service with no written price gets the owner's policy line instead of a number. Off-script questions get a fixed "I won't guess" line, and Bolna's web-search tool is never added. After each call, `analysis.audit_call()` flags any price-like number the agent said that is not in the profile, and the owner sees it as `CHECK:`. |

## Run it in mock mode (no key, no network, about 1 minute)

Needs Python 3.10+ on macOS or Linux.

```bash
cd ~/Work/flowmate-callback
python3 -m venv .venv && source .venv/bin/activate     # optional; skip if fastapi/httpx/uvicorn/pydantic are already installed
pip install -r requirements.txt
python3 -m flowmate_callback demo                      # six fictional leads, end to end
python3 -m unittest discover -s tests -t .             # 46 tests
```

## WhatsApp follow-up

`python3 -m flowmate_callback whatsapp-demo` runs five fictional Meta lead-form records through the compose-only WhatsApp sequence and prints the weekly owner report. It makes no network requests and sends nothing. Touches are scheduled at intake, +2h, +24h and +72h within the profile's 09:00–21:00 IST window. `POST /leads/meta` accepts a Meta `leadgen` webhook plus fixture `field_data`, validates `X-Hub-Signature-256` with `META_APP_SECRET`, and requires an affirmative consent field. Set `FLOWMATE_PROFILE` to `profiles/glow-derm-hyderabad.FICTIONAL.json` for the dermatology profile. `GET /reports/weekly` returns the owner report (admin token required).

`demo` posts six fictional leads through the real HTTP handlers. Each one triggers a mock Bolna call that replays a fixture transcript, including the mid-call `check_slots` and `book_slot` tool calls and Bolna's webhook sequence (`in-progress`, then `call-disconnected`, then `completed`). For each lead it prints the greeting, the owner notification and the WhatsApp draft:

1. Telugu, tooth pain: books Tuesday 11:00, tagged **HOT**.
2. Hindi, price question: **WARM**. Both prices quoted are written in the profile, so the audit passes.
3. English, wants whitening, which the clinic doesn't offer: **COLD**. The fixture's AI-written quote is not what the caller said, so the verifier rejects it and quotes the caller's real line.
4. Missed call, no answer: **NOT REACHED**.
5. Cal.com booking, where the agent invents a root-canal price: **HOT** plus a `CHECK:` flag.
6. A form with no consent: **rejected**, no call placed.

Mock mode runs on a fixed clock, Monday 19 Oct 2026 at 10:02 IST, so the output is the same every run. Set `FLOWMATE_MOCK_NOW=real` to use the real clock.

Other commands:

```bash
python3 -m flowmate_callback agent      # print the exact POST /v2/agent body and the extractions (tokens redacted)
python3 -m flowmate_callback serve      # HTTP server on 127.0.0.1:8787 (mock unless FLOWMATE_MODE=live)
python3 -m flowmate_callback secrets    # print fresh random tokens for your .env
```

## Layout

```
flowmate_callback/
  config.py     settings from env vars only; live mode refuses to start without every token
  profile.py    owner profile model + validation (prices only if written, calling window inside 09-21)
  leads.py      form/ad/missed-call JSON and Cal.com BOOKING_CREATED -> Lead; consent; phone -> E.164
  agent.py      Bolna agent JSON: AI-disclosure greeting, per-language prompts, voices, tools, extractions
  bolna.py      Bolna REST client (agent, dispositions, call, execution) + MockBolna replaying fixtures
  slots.py      open slots from the owner's hours; the agent can only offer and book these
  analysis.py   hot/warm/cold + quote verified against the transcript, 3-line summary, guardrail audit
  messages.py   owner notification + caller WhatsApp template (compose only)
  store.py      SQLite (leads, executions, bookings, results)
  app.py        FastAPI routes (below)
  __main__.py   CLI: demo, agent, serve, secrets, check, create-agent
profiles/neelagiri-dental-hyderabad.FICTIONAL.json   sample owner profile, clearly fictional
fixtures/leads/        six fictional inbound leads
fixtures/executions/   five fictional calls shaped like Bolna execution payloads
tests/                 unittest suite (standard library; no extra dev dependencies)
docs/RESEARCH.md       Bolna API, languages, telephony, pricing, the submit page; what is unverified
```

| Route | Who calls it | Auth |
|---|---|---|
| `POST /leads/form` | your website form, ad tool (via Zapier/Make/n8n), missed-call system | header `X-FlowMate-Token` |
| `POST /leads/calcom` | Cal.com webhook, `BOOKING_CREATED` | `x-cal-signature-256` HMAC |
| `POST /webhooks/bolna` | Bolna, after status changes | `?token=` in the URL + Bolna's 3 source IPs |
| `POST /tools/slots`, `POST /tools/book` | the Bolna agent, mid-call | `Authorization: Bearer` + Bolna IPs |
| `GET /leads`, `GET /leads/{id}` | the owner (JSON: summary, tag, transcript, drafts) | header `X-FlowMate-Token` (admin token) |
| `POST /mock/replay/{execution_id}` | mock mode only | none |

Generic form lead:

```json
{
  "source": "website_form",
  "external_id": "form-123",
  "name": "Ravi Teja",
  "phone": "+91 90000 00001",
  "language": "te",
  "message": "Tooth pain since 2 days",
  "consent": {"given": true, "text": "I agree to get a call back from <business> about my enquiry.", "at": "2026-10-19T10:01:40+05:30"}
}
```

- `source` is one of `website_form`, `ad` or `missed_call`. A missed call sends `"consent": {"basis": "called_us"}` instead.
- For Cal.com, add a required checkbox booking question with the identifier `callback_consent`. You can rename it with `CALCOM_CONSENT_FIELD`.

## Going live

1. **Bolna account and API key.** Sign up at [platform.bolna.ai](https://platform.bolna.ai/sign-up) (account holders must be 18+, per Bolna's [terms](https://www.bolna.ai/terms-of-service)), create a key under Developers → API Keys, and put it in `.env` as `BOLNA_API_KEY` (git-ignored).
2. **Phone number.** Demos use Bolna's default +91 number (leave `BOLNA_FROM_NUMBER` empty). Trial accounts call verified numbers only, two at a time. A dedicated Indian number needs the client's company KYC (CIN + GST) and the right DLT series.
3. **Public HTTPS address.** Set `FLOWMATE_PUBLIC_BASE_URL`; Bolna must reach `/webhooks/bolna` and `/tools/*` from `13.203.39.153`, `13.126.9.249`, `13.202.133.53`. Webhooks are unsigned, so the server checks the source IP and a URL token, then re-reads the call from the API.
4. **Fill `.env`** from `.env.example` (`python3 -m flowmate_callback secrets` prints tokens) and set `FLOWMATE_LIVE_ALLOWLIST`.
5. **Create the agent:** `python3 -m flowmate_callback check`, then `create-agent --yes`; put the id in `BOLNA_AGENT_ID`.
6. **First call:** `python3 -m flowmate_callback serve`, post one form lead with your own number:

```bash
curl -X POST "$FLOWMATE_PUBLIC_BASE_URL/leads/form" -H "X-FlowMate-Token: $FLOWMATE_INBOUND_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"name":"Test","phone":"<your number>","language":"te","message":"test enquiry","consent":{"given":true,"text":"I agree to a call back (my own test)."}}'
```

Next after v0: send the WhatsApp drafts through a WhatsApp Business provider with a Meta-approved template (for example AiSensy, which Bolna integrates).

## Known limits of v0

- The calendar is FlowMate's own (SQLite, built from the owner's written hours). Syncing to Google Calendar or Cal.com comes next. Bolna's built-in Cal.com tools are an alternative, set up in the dashboard.
- Nothing is sent. The owner notification and the WhatsApp confirmation are drafts.
- Each agent serves one owner profile; for a second business, create a second agent.
- The rule fallback for quotes uses small Hindi, Telugu and English keyword lists. Bolna's extraction quote is preferred whenever it matches the transcript word for word.
- The price audit checks numbers written as digits. A price spoken in words ("four thousand") would not be caught.
- Hindi and Telugu fixture lines were written for the demo. Have a native speaker read the prompts and the greeting before a real client goes live.
- None of this has run against the live Bolna API yet; see "Not verified" in [docs/RESEARCH.md](docs/RESEARCH.md).
