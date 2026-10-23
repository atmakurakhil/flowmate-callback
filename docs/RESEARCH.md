# Research notes: Bolna and the buildathon submission

All sources below were read on **2026-10-17**. Bolna's docs were read as the Markdown that its docs site serves (`https://www.bolna.ai/docs/<page>.md`, indexed at <https://www.bolna.ai/docs/llms.txt>). Nothing here comes from a blog or a third party. Where a fact could not be confirmed it is listed under [Not verified](#not-verified).

## 0. Read this first

1. **Bolna accounts are for adults.** The Terms of Service (last updated 13 August 2026) say: "You represent that you are at least 18 years of age". Source: <https://www.bolna.ai/terms-of-service>.
2. **Indian phone numbers need a registered company.** To buy a dedicated +91 number you first submit a compliance application with a CIN certificate and a GST certificate. A trial or demo can use Bolna's shared default numbers instead. Sources: <https://www.bolna.ai/docs/compliance-application/introduction.md>, <https://www.bolna.ai/docs/outbound/making-calls.md>.
3. **Trial accounts get "Up to 2 concurrent calls, restricted to verified phone numbers only."** That is enough for a demo that calls your own phone. Source: <https://www.bolna.ai/docs/pricing/outbound-calling-concurrency.md>.

## 1. API basics

| Item | What the docs say | Source |
|---|---|---|
| Base URL | `https://api.bolna.ai` | [API quickstart](https://www.bolna.ai/docs/quickstarts/api.md) |
| Auth | `Authorization: Bearer <key>`. Keys are made in the dashboard under Developers, then API Keys (<https://platform.bolna.ai/developers>), and are shown only once. | [Authentication](https://www.bolna.ai/docs/api-reference/authentication.md) |
| Key check | `GET /user/me` returns wallet and `concurrency {max, current}` | [API quickstart](https://www.bolna.ai/docs/quickstarts/api.md) |
| Rate limits | `/call`, `/v2/agent/{id}` and `/v2/agent/{id}/executions` allow 500 requests a minute; everything else allows 1,000. Over the limit you get HTTP 429, so back off and retry. | [Rate limiting](https://www.bolna.ai/docs/api-reference/rate-limiting.md), [Limits](https://www.bolna.ai/docs/api-reference/limits.md) |
| SDKs | There is no server SDK. The only SDK is the browser Web Call SDK, which is enabled on request. The REST examples are in curl, Python standard library and Node `fetch`. | [SDKs](https://www.bolna.ai/docs/developer-resources/sdks/overview.md), [Web Call SDK](https://www.bolna.ai/docs/developer-resources/sdks/web-call.md) |

**Why Python + FastAPI.** Bolna has no server SDK, so either stack talks to the same REST API. Python fits it more closely. The quickstart's end-to-end harness is a dependency-free Python script, the webhook-handling example in the payload reference is Python, and custom tools map parameters with Python `%(name)s` format specifiers. FastAPI adds typed request validation, and its test client lets the whole flow run in tests with no network.

## 2. Creating an agent: `POST /v2/agent`

Source: [Create agent](https://www.bolna.ai/docs/api-reference/agent/v2/create.md). It returns HTTP 201 `{"agent_id", "state": "created"}`.

- **Required:** `agent_config` (with `agent_name` and `tasks`) and `agent_prompts.task_1.system_prompt`.
- **`tasks[0]`:** `task_type: "conversation"`. `toolchain.pipelines` must be an array of arrays: `[["transcriber","llm","synthesizer"]]`.
- **`tools_config` needs** `llm_agent`, `transcriber`, `synthesizer`, `input` and `output`.
  - `llm_agent.agent_type` takes one of `simple_llm_agent`, `knowledgebase_agent`, `graph_agent` or `realtime_agent`.
  - GPT-5-series models require `temperature: 1`.
- **Telephony:** set `input.provider` and `output.provider` to `twilio`, `plivo`, `exotel`, `vobiz` or `trunk`, with `format: "wav"` for the first four.
- **Validation at creation:** the transcriber and synthesizer `provider` values are checked against a list. The STT model and language are checked as a pair, and the TTS model and voice are checked against the live catalogue. The LLM provider is *not* checked until a call runs.
- **Sarvam TTS** needs `language` inside `provider_config`. The [Sarvam voice page](https://www.bolna.ai/docs/sarvam-voice.md) says `voice` (display name) and `voice_id` are both required.
- **Other `agent_config` fields used:**
  - `webhook_url`.
  - `calling_guardrails {call_start_hour, call_end_hour}`, in the recipient's timezone.
  - `call_summary_enabled`, off by default.
- **`task_config` fields used:** `call_terminate`, `hangup_after_silence`, `hangup_after_LLMCall`, `call_hangup_message` (a string, or a map of language to message), `voicemail` and `check_if_user_online`.
- **Variables:** prompts and the welcome message take `{name}` (canonical) or `{{name}}`, filled from `user_data` on each call. An unresolved `{{path}}` renders as `{path}`, which the agent would say aloud. Source: [Using context](https://www.bolna.ai/docs/prompting/using-context.md).
- **Welcome message per language:** the welcome message is a single string. When a call starts in another language, the docs say to "put a variable in it (for example `{{greeting}}`) and pass the localized text with the call". Source: [Start a call in a chosen language](https://www.bolna.ai/docs/customizations/start-call-in-language.md). FlowMate uses this to put the AI disclosure in the first sentence, in the caller's language.

## 3. Placing a call: `POST /call`

Source: [Make call](https://www.bolna.ai/docs/api-reference/calls/make.md).

- **Required:** `agent_id` and `recipient_phone_number` (E.164). It returns `{"message":"done","status":"queued","execution_id"}`.
- **Optional:**
  - `from_phone_number`: must be a number bought on your Bolna account. Leave it out to use the default number.
  - `scheduled_at`: ISO 8601 with a timezone.
  - `user_data`: prompt variables.
  - `agent_data.language`: the language a multilingual agent starts in. It cannot be combined with `voice_id`.
  - `retry_config`: `max_retries` 1 to 3; `retry_on_statuses` from `no-answer`, `busy`, `failed`, `error`; and `retry_intervals_minutes`.
  - `bypass_call_guardrails`.
- **Lifecycle:** `queued → initiated → ringing → in-progress → call-disconnected → completed`. The quickstart says "Your phone should ring within a few seconds."
- **Default numbers:** callers in India see a `+91` number, and callers in most other countries see a `+1`. Source: [Making calls](https://www.bolna.ai/docs/outbound/making-calls.md).

## 4. After the call: webhook and execution data

Sources: [Execution payload](https://www.bolna.ai/docs/post-call/execution-payload.md), [Webhooks](https://www.bolna.ai/docs/post-call/polling-call-status-webhooks.md), [Call statuses](https://www.bolna.ai/docs/post-call/list-phone-call-status.md), [Webhook not received](https://www.bolna.ai/docs/troubleshooting/webhook-not-received.md), [Security](https://www.bolna.ai/docs/concepts/security.md).

**How delivery works**
- Bolna POSTs the full execution to the agent's `webhook_url` every time the status changes, so one call produces several deliveries with the same `id`. Make the handler idempotent on `id`.
- Act only on terminal statuses: `completed`, `no-answer`, `busy`, `failed`, `canceled`, `stopped`, `error`, `balance-low`.
- `call-disconnected` arrives first, before the fields are filled in. `completed` follows "a few seconds later" (the status page says it can take about 2 to 3 minutes).
- `completed` does not prove anyone spoke. Check `conversation_duration > 0` and look for a `user:` line.

**What the payload contains**
- `transcript`: newline-separated `assistant: …` and `user: …` lines.
- `extracted_data`: grouped as `{Category: {Extraction: {subjective, objective, confidence, confidence_label, reasoning_subjective, reasoning_objective, validation}}}`.
- `summary`: filled only when `call_summary_enabled` is on.
- `telephony_data`: `recording_url`, `hangup_reason`, `provider`, and more.
- `context_details.recipient_data`: echoes the `user_data` you sent.
- `cost_breakdown`: in cents.

**Security**
- Webhooks come from `13.203.39.153`, `13.126.9.249` and `13.202.133.53`. Whitelist all three.
- The security page says: "There is no HMAC signature on webhook payloads or tool call requests in the current version." FlowMate therefore checks the source IP and a token in the URL, then re-reads the execution with `GET /executions/{id}` before trusting it.
- Calls are processed in AWS us-east-1 by default. Indian data residency is an enterprise option.

**Extractions over the API**
- They are called "dispositions". `POST /dispositions/bulk` takes `{agent_id, dispositions:[{name, question, category, model, is_subjective, is_objective, objective_options}]}`.
- Each category runs as one LLM pass, and the default model is `gpt-4.1-mini`.
- Sources: [Dispositions](https://www.bolna.ai/docs/api-reference/dispositions/overview.md), [Bulk create](https://www.bolna.ai/docs/api-reference/dispositions/bulk-create.md), [Extraction output](https://www.bolna.ai/docs/prompting/extraction-output.md).

## 5. Tools during the call (booking)

Sources: [Custom functions](https://www.bolna.ai/docs/tool-calling/custom-function-calls.md), [Schema](https://www.bolna.ai/docs/tool-calling/custom-function-schema.md), [Variables](https://www.bolna.ai/docs/tool-calling/custom-function-variables.md), [Cal.com book](https://www.bolna.ai/docs/tool-calling/book-calendar-slots.md), [Cal.com check](https://www.bolna.ai/docs/tool-calling/fetch-calendar-slots.md).

- **Custom functions** follow OpenAI function calling, with `"key": "custom_task"` and `value {method, url, param, api_token, headers}`. The `param` field maps values with `%(name)s`.
- Variables defined in the prompt are filled into a tool's parameters automatically, so the LLM does not ask the caller for them. FlowMate uses this for `lead_id`.
- Tool calls come from the same three IPs.
- **Bolna's built-in Cal.com tools** (Check Calendar Slots and Book Appointment) are set up in the dashboard's Tools tab with a Cal.com API key. FlowMate v0 uses its own two tools instead, `check_slots` and `book_slot`, backed by the owner's written hours. That way the agent can only offer real slots, and mock mode can test the whole flow.

## 6. Languages and voices

Sources: [Supported languages](https://www.bolna.ai/docs/customizations/supported-languages.md), [Multilingual setup](https://www.bolna.ai/docs/customizations/multilingual-languages-support.md), [Multilingual config](https://www.bolna.ai/docs/customizations/multilingual-config-reference.md), [Multilingual quickstart](https://www.bolna.ai/docs/quickstarts/multilingual.md), [Sarvam STT](https://www.bolna.ai/docs/sarvam-transcriber.md), [Sarvam TTS](https://www.bolna.ai/docs/sarvam-voice.md).

- **Language codes:** English `en`, Hindi `hi` and Telugu `te` are all on the list. All three switch automatically mid-call, along with eight other Indian languages.
- **`multilingual_config`** sits under `tools_config`. It needs `enabled: true`, at least 2 `languages`, and an `active_language` that is one of the keys. Each language entry takes its own transcriber, synthesizer, `system_prompt`, `handoff_message` and `agent_name`. `language_switch_trigger` is either `requested_or_auto_detected` (the default) or `caller_requested`. To add it to an existing agent you need `PUT /v2/agent/{id}`, because PATCH does not cover it.
- **Speech to text:** Sarvam `saaras:v4` (recommended) or `saaras:v3`. It covers `en`, `hi`, `te` and 9 more through Bolna. Bolna transcribes in Sarvam's `transcribe` mode, so English words inside a Hindi call can come back in Devanagari.
- **Text to speech:** Sarvam `bulbul:v3` has 16 voices that work in every language, including `priya`, `kavya`, `ritu`, `simran` and `shubh`. `bulbul:v4-flash` (shipped 15 Oct 2026) has 222 voices, each tied to one language, such as `kavitha_te_conversation`.
- **Prompts:** the docs recommend writing each language's prompt in its native script.

## 7. Telephony in India

Sources: [Supported providers](https://www.bolna.ai/docs/supported-telephony-providers.md), [Choosing telephony](https://www.bolna.ai/docs/concepts/choosing-telephony.md), [Buying numbers](https://www.bolna.ai/docs/buying-phone-numbers.md), [Regulated numbers](https://www.bolna.ai/docs/obtaining-regulated-phone-numbers.md), [Compliance](https://www.bolna.ai/docs/compliance-application/introduction.md), [Submitting compliance](https://www.bolna.ai/docs/compliance-application/how-to-submit-guide.md), [Exotel connect](https://www.bolna.ai/docs/exotel/connect-provider.md), [Plivo connect](https://www.bolna.ai/docs/plivo/connect-provider.md), [Twilio connect](https://www.bolna.ai/docs/twilio/connect-provider.md).

- **Providers for India:** Plivo, Exotel and Vobiz. Twilio is the option "outside India". Plivo is "Default for most Bolna deployments", and Exotel has "DLT compliance built in". You can also bring your own SIP trunk.
- **Buying numbers:** a number costs $5 a month from the wallet. Indian numbers come through Plivo (Karnataka 80 and Maharashtra 22 regions) or through Vobiz, which needs KYC first. Both require an approved compliance application (CIN + GST, PDFs up to 10 MB each, reviewed in 12 to 24 business hours). The compliance API is `POST /compliance/create`, added 12 Oct 2026.
- **Regulated numbers:**

  | Series | Use | Provider | Requirements |
  |---|---|---|---|
  | 140 | Telemarketing | Vobiz | DLT Principal Entity registration on the Tata Teleservices portal, with company documents and a ₹5,900 fee |
  | 160 | Transactional | Plivo | DLT registration plus an RBI or SEBI certificate (i.e. banking and finance) |

- **Bring your own account:** Exotel needs `API_KEY`, `API_TOKEN`, `ACCOUNT_SID`, `DOMAIN`, the outbound and inbound app IDs, and an ExoPhone. Plivo and Twilio connect in the dashboard under Settings, then Integrations.
- **Calling hours:** Bolna's guardrails page gives TRAI's window as 9:00 to 21:00 IST and says "No calls on national Do Not Disturb (DND) registered numbers without consent." It labels this "not legal advice". FlowMate's calling window is held inside 9 to 21, and out-of-hours leads are scheduled for the next 09:00. Source: [Calling guardrails](https://www.bolna.ai/docs/outbound/calling-guardrails.md).
- **Who is responsible for consent:** the ToS says the business deploying Bolna acts as Data Fiduciary under the DPDP Act and is "solely responsible for obtaining all consents". FlowMate's consent record (wording and time) is kept for exactly this reason.

## 8. Pricing for a demo

Sources: [Bolna pricing](https://www.bolna.ai/pricing), [What it costs](https://www.bolna.ai/docs/get-started/what-it-costs.md), [Call pricing](https://www.bolna.ai/docs/pricing/call-pricing.md), [Preferred models](https://www.bolna.ai/docs/pricing/preferred-models.md), [Pilot plan](https://www.bolna.ai/docs/pricing/pilot-plan.md), [FAQ](https://www.bolna.ai/docs/frequently-asked-questions.md).

- **Free credit:** "We offer $5 in free credits when you sign up."
- **Per-minute rate:** pay-as-you-go top-ups run from $10 to $5,000. The flat Voice AI rate is $0.06 a minute (₹5.52) and falls to about $0.045 on larger top-ups. It covers STT, LLM and TTS only when every model is a "preferred" one.
- **Preferred models** include `saaras:v4`, `gpt-4.1-mini` and `bulbul:v3`, the three FlowMate uses.
- **Telephony and platform fee:** the pricing page FAQ says these are "added per minute", and the docs FAQ gives the platform fee as "$0.02/min … (plus provider charges)". The India telephony rate is not published on the pages read.
- **Pilot plan:** $300 for 6,500 minutes or $500 for 12,000 minutes. It is non-refundable and makes call recording mandatory.
- **Demo budget:** $5 at $0.06 plus $0.02 a minute is at most about 60 minutes before telephony. That is plenty for a recorded demo of 2-minute calls.

## 9. WhatsApp after the call

Bolna's native route is an AiSensy node inside a Bolna workflow. It sends Meta-approved templates, and the page notes that "business-initiated messages *must* use an approved template". The other route is Make.com. FlowMate v0 only composes the confirmation, as a template body with `{{1}}`…`{{5}}` plus the filled text. Sources: [AiSensy](https://www.bolna.ai/docs/workflows/aisensy-whatsapp.md), [Make.com WhatsApp](https://www.bolna.ai/docs/tutorials/make-com/send-whatsapp-after-bolna-call.md).

## 10. Cal.com webhook (lead source)

Source: <https://cal.com/docs/developing/guides/automation/webhooks>, read through a summarising fetch rather than the raw page.

- The `BOOKING_CREATED` payload is `{triggerEvent, createdAt, payload{uid, startTime, endTime, attendees[], responses{name, email, attendeePhoneNumber, notes, …}}}`.
- The phone number sits in `responses.attendeePhoneNumber`.
- The signature is HMAC-SHA256 of the raw body with the webhook secret, sent in `x-cal-signature-256`.

## 11. The buildathon submit page

The page is server-rendered, so it was read from its raw HTML: <https://buildathon.bolna-ai.lovable.app/symphony/buildathon/submit>. Heading: "Submit your project". Subheading: "Share what you built. Selected teams move to interview."

| Field (as labelled) | Required | Input | Notes on the page |
|---|---|---|---|
| Team size | yes | select: Just me, 2, 3, 4 | |
| Full name | yes | text, max 100 | |
| Email | yes | email, max 255 | "Use the same email you registered with." |
| Project name | yes | text, max 150 | |
| Project description | yes | textarea, max 200 characters | "1–2 lines: what it does and who it's for." |
| Submission link | yes | URL, max 500 | "GitHub repo, live app (Vercel etc.) or demo link." |
| Pitch deck or pitch video link | yes | URL, max 500, placeholder `https://drive.google.com/...` | "Google Drive or Luma link. Make sure link sharing is set to 'Anyone with the link'." |

- Nothing is uploaded; every item is a link.
- The page states no deadline. The main page gives Oct 31 as the "Deadline to submit your project", then the shortlist on Nov 3, interviews Nov 5 to 7, and the finale on Nov 20. Source: <https://buildathon.bolna-ai.lovable.app/symphony/buildathon>.
- The form also has a hidden field, `contact_pref_2` (tabindex -1, not shown to people). It reads as a bot trap. Fill the form by hand in a normal browser.

## Not verified

- **Custom tools through the API.** The API schema documents `api_tools.tools` plus `tools_params` (with `param` as stringified JSON) only for `transfer_call`. Combining that shape with the dashboard's `custom_task` fields is an inference. After `create-agent`, open the agent's Tools tab and confirm `check_slots` and `book_slot` are there. If they are not, paste the two tool definitions in by hand with "Write manually" (`python3 -m flowmate_callback agent` prints them).
- **Webhook headers.** The dashboard has an "Add headers" toggle for webhooks, but the API exposes only `webhook_url`. FlowMate therefore puts its webhook token in the URL query.
- **Retries.** It is not stated whether an auto-retry keeps the same execution id. The handler copes with both: it maps a new id back to the lead through `context_details.recipient_data.lead_id`.
- **Trial "verified phone numbers".** The pages read do not say how to verify a number on a trial account.
- **India telephony rate.** The per-minute rate on Bolna's default +91 numbers is not published, and the two pricing pages describe the platform fee differently ("6¢/min" with telephony and platform added, against "$0.02/min platform fee").
- **TRAI category.** Whether a callback to someone who just asked for one counts as a service or a promotional call is not settled. Bolna's docs map only the 140-series (telemarketing) and the 160-series (BFSI transactional). Ask <compliance@bolna.ai> before a client goes live on a dedicated number.
- **Cal.com signature encoding.** The docs do not say hex or base64, so both are accepted.
- **Buildathon FAQ.** The answers ("What kind of builds are allowed?", "How does the selection process work?", etc.) are collapsed and not in the HTML. The browser extension did not respond, so they were not read. Judging criteria are not published on either page.
- **Live behaviour.** Nothing here was tested against the live API: no account was created and no call was placed.
