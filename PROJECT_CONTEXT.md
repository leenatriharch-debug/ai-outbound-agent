# AI Outbound Calling Agent — Project Context

This file is a handoff guide for the project owner and any AI/developer who
continues the work. Read it before changing code. It describes the current
repository, not a promise that every feature is production-ready.

## What the project does

This is a Python/FastAPI backend for an AI outbound-calling system. It manages
customers and campaigns, queues and places calls through a telephony provider,
handles provider webhooks, and supports live voice conversations.

Think of the system as a small call center:

- The API receives requests from the dashboard or another client.
- MySQL stores customers, campaigns, calls, attempts, conversations, and users.
- A background worker processes queued calls and provider events.
- Provider adapters connect the application to Vonage, Groq, and Edge TTS.
- The live-call service processes audio and keeps each call's conversation
  separate.

The project is a **modular monolith**: these parts run in one backend codebase
(the API and worker are separate processes), not as a collection of
microservices. Do not rebuild it as a different architecture without the
owner's approval.

## Current state and honest limitations

- Phases 1–4 provide customer/campaign workflows, call lifecycle handling,
  durable call dispatch, retries, and signed/idempotent Vonage webhook handling.
- Phase 5 adds Vonage WebSocket media calls and the STT → LLM → TTS conversation
  path. Audio is handled per call; transcripts are stored in the database.
- Phase 6 adds account/role checks, request logging and rate limits, readiness
  and metrics endpoints, audio limits, and worker maintenance for stale
  conversations and transcript retention.
- The latest recorded backend test run passed **49 tests**. These include
  mocked provider tests. They do **not** prove that a real Vonage call or
  production account has been tested.
- The project is **not yet production-ready**. Consent/opt-out enforcement,
  local calling-hour rules, campaign budget limits, distributed rate limiting,
  a versioned database migration system, webhook-event retention, and real
  staging/live-call verification remain open.

Do not describe mocked tests as live-provider verification.

## Main call and conversation flow

1. A client submits a direct call or works with a campaign through the API.
2. The database stores work in a durable dispatch queue. Campaign calls also
   create a `CallAttempt`.
3. `app.worker` claims queued work and asks the configured telephony adapter to
   place a call.
4. Signed provider callbacks are stored as webhook events and processed by the
   call-event/lifecycle services. Event IDs prevent duplicate processing.
5. For a live Vonage conversation, the answer flow connects the call to the
   WebSocket media route. Audio is segmented in memory; completed utterances
   go through STT, LLM, and TTS. TTS audio is converted to Vonage's expected
   PCM format and sent back to that call.
6. Conversation history and transcript rows are associated with that call; a
   campaign transcript is linked to its `CallAttempt`.
7. The worker also runs periodic stale-conversation recovery and transcript
   cleanup.

Useful implementation files:

- API/startup and health endpoints: `backend/app/main.py`
- Call and campaign dispatch: `backend/app/services/call_orchestrator.py`,
  `backend/app/services/call_dispatch_service.py`
- Provider callback processing and lifecycle: `backend/app/services/call_event_service.py`,
  `backend/app/services/call_lifecycle_service.py`
- Background worker: `backend/app/worker.py`
- Live audio/conversation processing: `backend/app/services/live_call_service.py`
- Voice, WebSocket, and signed webhook routes: `backend/app/routes/voice_routes.py`
- Webhook signature and callback helpers:
  `backend/app/services/vonage_webhook_security.py`

## Architecture and provider switching

Routes handle HTTP/WebSocket requests. Services contain application workflows.
Provider interfaces define the operations the application expects, and
`ProviderRegistry` creates the selected adapter. Keep provider-specific SDK
calls inside adapters; do not put them directly into conversation or campaign
logic.

Current registry choices are:

| Capability | Registered choice today |
| --- | --- |
| LLM | `groq` |
| STT | `groq` |
| TTS | `edge_tts` |
| Telephony | `vonage` |

These settings are in `backend/app/config/settings.py`; registration is in
`backend/app/services/provider_registry.py`. There is currently only one
registered implementation for each capability. Changing a setting to an
unregistered name fails; a setting alone cannot install or implement a new
provider.

To add another provider:

1. Implement the existing capability interface (for example `AIProvider`,
   `SpeechToTextProvider`, `TextToSpeechProvider`, or `TelephonyProvider`).
2. Add the adapter to `ProviderRegistry` under a clear provider name.
3. Add only the required provider configuration to settings/environment; never
   hardcode credentials.
4. Add focused mocked tests for request mapping, responses, and errors.
5. Run the relevant tests and the complete backend suite.
6. Verify real-provider behavior separately using safe staging credentials.

After an adapter is registered, selecting among registered providers should
use configuration and should not require changing conversation business logic.
Adding a provider still requires adapter code. Telephony providers may have
different webhook-signature and live-media protocols, so changing telephony
providers can require more work than swapping an LLM.

Relevant interfaces and adapters:

- `backend/app/services/ai_provider.py`
- `backend/app/services/speech_provider.py`
- `backend/app/services/telephony_provider.py`
- `backend/app/services/provider_registry.py`
- `backend/app/services/groq_provider.py`
- `backend/app/services/stt.py`
- `backend/app/services/edge_tts_provider.py`
- `backend/app/services/vonage_provider.py`

## Authentication and operations

- In `APP_ENV=production`, management/AI API routes use bearer tokens and
  `admin`/`operator` roles. The initial admin is created with
  `python -m app.bootstrap_admin`.
- In non-production mode, authentication intentionally has a local-development
  bypass. Never expose that mode as a public production service.
- `/health` is liveness; `/ready` checks the database and, in production, an
  active admin; `/metrics` returns aggregate metrics and is admin-protected in
  production.
- The in-process limiter allows 10 login requests and 120 other API requests
  per client IP per minute. It is not shared across multiple processes.
- Logs are structured and attempt to redact bearer tokens and phone-number-like
  values. This is not a guarantee that every kind of personal data is removed;
  do not log secrets or customer conversation contents.
- The maintenance worker marks old active conversations failed and deletes
  inactive conversation sessions, messages, and attempt links after the
  configured transcript-retention period (90 days by default). Vonage webhook
  event rows are not covered by this cleanup.
- Database table creation currently uses SQLAlchemy `create_all`; there is no
  versioned migration framework. `create_all` creates missing tables but is
  not a substitute for migrations when changing existing table columns.

## Configuration and running locally

The application reads environment values from `backend/.env` when commands are
run from the `backend` directory. Keep real credentials out of source code,
documentation, screenshots, and chat. Do not print or copy the existing
`backend/.env` contents into an AI conversation.

Important settings include:

- Database: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- Environment/auth: `APP_ENV`, `AUTH_JWT_SECRET`,
  `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_PASSWORD`
- Vonage: `VONAGE_API_KEY`, `VONAGE_SIGNATURE_SECRET`,
  `VONAGE_APPLICATION_ID`, `VONAGE_PRIVATE_KEY_PATH`, `VONAGE_NUMBER`,
  `PUBLIC_BASE_URL`
- Groq: `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_STT_MODEL`
- Provider selection: `LLM_PROVIDER`, `STT_PROVIDER`, `TTS_PROVIDER`,
  `TELEPHONY_PROVIDER`
- Conversation/audio/maintenance: `CALL_*`, `MAX_AUDIO_UPLOAD_BYTES`,
  `CALL_MAX_AUDIO_FRAME_BYTES`, `TRANSCRIPT_RETENTION_DAYS`,
  `STALE_CONVERSATION_MINUTES`, `MAINTENANCE_INTERVAL_SECONDS`

Production startup requires valid secrets/provider configuration and an HTTPS
public base URL. Vonage must be able to reach the application's public webhook
and WebSocket endpoints. A local `localhost` address is not reachable by
Vonage.

Run commands in PowerShell from `backend`:

```powershell
.\venv\Scripts\python.exe -m app.database.init_db
.\venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8002 --no-access-log
.\venv\Scripts\python.exe -m app.worker
```

The API and worker should run in separate terminals. The API docs are available
at `http://127.0.0.1:8002/docs` while the local API is running. Do not start a
real outbound call until credentials, consent, and safe test numbers are
confirmed.

## Tests

- `backend/tests/test_call_lifecycle.py`: campaign/call status and retry rules.
- `backend/tests/test_outbound_lifecycle.py`: dispatch, provider callbacks,
  signatures, idempotency, retry behavior, and worker regression cases.
- `backend/tests/test_live_call_service.py`: audio segmentation, transcription
  flow, interruptions, silence, and per-call transcript behavior.
- `backend/tests/test_provider_architecture.py`: provider interfaces/registry
  and voice/STT adapter behavior.
- `backend/tests/test_phase6_security.py`: auth/RBAC, rate limits, readiness,
  logging, retention, and stale-session recovery.
- Provider and phone-call tests are mocked unless a test explicitly says it
  uses a live service. No live Vonage call has been verified as of this
  handoff.

## Change safely

Before editing:

1. Read this file and `README.md`.
2. Inspect the specific route, service, model, and tests involved.
3. Preserve the existing modular-monolith structure and direct/campaign call
   behavior unless the owner explicitly requests a behavior change.
4. Never read aloud, paste, commit, or expose secrets from `backend/.env` or
   private-key files.
5. Make the smallest change that satisfies the request and add focused tests.
6. Run targeted tests, then the full test suite when practical.
7. Clearly report what was mocked versus what was verified with real providers.
8. Ask the owner before introducing paid providers, changing call behavior, or
   deleting/purging additional data.

## Prompt for a future AI assistant

> Read `PROJECT_CONTEXT.md` and `README.md` before changing anything. First
> explain the relevant existing flow in plain language. Do not expose or ask me
> to paste secrets. Keep the existing architecture and APIs unless I approve a
> change. Implement my requested change directly, add focused tests, run them,
> and clearly distinguish mocked verification from a real-provider test.
>
> My next request: [write the requested change here]
