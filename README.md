# AI Outbound Calling Agent

For a plain-language architecture overview, current limitations, key files,
test commands, and an AI handoff prompt, see
[PROJECT_CONTEXT.md](./PROJECT_CONTEXT.md).

## Run the backend

In VS Code, choose **Terminal → Run Task → Run IVR backend (port 8002)**.
The Swagger UI is at `http://127.0.0.1:8002/docs`. Port 8000 is left for the
other local assistant project.

## IVR setup

IVR menus are managed through the `/api/ivr/menus/` endpoints. Create a menu
with keypad options that each have a label and an international destination
phone number. The active menu is returned by `/api/voice/answer`; selected
options connect the caller to their configured destination.

Set `PUBLIC_BASE_URL` in the backend environment to the public HTTPS base URL
of the API (for example, the HTTPS URL of your development tunnel). Vonage
must be able to reach the `/api/voice/ivr/input` webhook. Configure
`VONAGE_NUMBER` as the authorized Vonage caller ID when transferring calls.

## Provider selection

Provider selection is currently configured with environment settings; a
provider configuration UI is not part of this phase. Supported selectors are:

| Capability | Setting | Current provider |
| --- | --- | --- |
| LLM | `LLM_PROVIDER` | `groq` |
| Speech-to-text | `STT_PROVIDER` | `groq` |
| Text-to-speech | `TTS_PROVIDER` | `edge_tts` |
| Telephony | `TELEPHONY_PROVIDER` | `vonage` |

Groq requires `GROQ_API_KEY`; speech-to-text uses `GROQ_STT_MODEL`. Edge TTS
uses `EDGE_TTS_VOICE`. `av` decodes the TTS audio in memory and resamples it
to Vonage's 16-kHz mono PCM format. Vonage voice calls require `VONAGE_API_KEY`,
`VONAGE_SIGNATURE_SECRET`, `VONAGE_APPLICATION_ID`,
`VONAGE_PRIVATE_KEY_PATH`, `VONAGE_NUMBER`, and an HTTPS `PUBLIC_BASE_URL`.
Enable **Use signed webhooks** in the Vonage Voice application settings and
set `VONAGE_SIGNATURE_SECRET` to that application's webhook signing secret.

## Outbound call dispatch

`POST /api/calls/outbound` preserves the existing phone-number API and creates a
durable dispatch job. Campaign contacts are selected by the existing queue and
orchestrator, which create a `CallAttempt` and its dispatch job in one database
transaction. Both job types use the configured telephony provider and the same
worker.

Run the API and worker in separate terminals. From `backend`, run:

```powershell
.\venv\Scripts\python.exe -m app.worker
```

The worker processes pending direct calls, eligible campaign contacts, and
scheduled retries. It stores provider UUIDs and applies signed Vonage event
webhooks to the matching call or `CallAttempt`. `busy` and unanswered outcomes
use the existing campaign retry policy.

Before using an existing database, run
`python -m app.database.init_db` from `backend` to create the new
`user_accounts`, `call_dispatch_jobs`, `vonage_webhook_events`, and
`conversation_attempt_links` tables. This project does not currently use a
versioned migration framework.

Provider rejections are recorded as failed dispatches; Vonage rate limits can
be retried under the existing retry rules. A network timeout or other
ambiguous provider outcome is marked `dispatch_unknown` and is not
automatically retried, because Vonage may have accepted the call before the
connection failed. Reconcile such calls with Vonage before taking action.

## Live outbound conversations (Phase 5)

For outbound dispatches, `/api/voice/answer` returns a Vonage `connect` action
with a `wss://` media endpoint. The media endpoint requires both the signed
dispatch reference and Vonage's signed WebSocket authorization. It receives
live 16-kHz L16 audio, segments speech in memory using voice activity and
silence detection, and connects each utterance to the configured STT, LLM, and
TTS providers. TTS output is decoded and sent back to Vonage as paced PCM audio.
After the initial greeting, speech during response generation/playback cancels
the stale response and sends Vonage's `clear` command. Conversation messages
are stored separately per call and campaign transcripts are linked to their
`CallAttempt`.

The initial greeting is synthesized and sent before the media handler starts
processing inbound audio, preventing an early audio frame from cancelling the
greeting task. WebSocket JSON control messages are handled separately from
binary PCM frames. Audio pipeline logs include greeting completion, generated
TTS PCM size/duration, transmitted frame counts, received audio frame counts,
transcription length, and LLM response duration. TTS, LLM, STT, or playback
timeouts/failures are logged and terminate the call rather than leaving a
silent session; voice processing stages have a 45-second timeout.

The configured Groq Whisper adapter transcribes completed utterances through
its existing request/response transcription API; it does not provide partial
streaming transcripts. Audio transport to and from Vonage remains a live
WebSocket stream; only VAD-delimited utterances are sent to STT, in memory,
without writing audio files. A provider with native incremental streaming STT
can be selected later by implementing the existing provider interface.

Set `CALL_GREETING`, `CALL_SILENCE_TIMEOUT_SECONDS`,
`CALL_SILENCE_REPROMPTS`, `CALL_VAD_THRESHOLD`, `CALL_VAD_SILENCE_MS`, and
`CALL_MAX_UTTERANCE_SECONDS` as needed. Ensure the public HTTPS host supports
WebSocket upgrades to WSS and can reach `/api/voice/answer`,
`/api/voice/events`, and `/api/voice/media`.

### Real-call setup

1. Install backend dependencies and create/update the database tables:

   ```powershell
   cd backend
   .\venv\Scripts\python.exe -m pip install -r requirements.txt
   .\venv\Scripts\python.exe -m app.database.init_db
   ```

2. Configure the provider credentials and Vonage Voice application settings
   described above. Set `PUBLIC_BASE_URL` to the externally reachable HTTPS
   address (not `http://localhost`); confirm its WSS upgrade works.
3. Start the API and dispatch worker in separate backend terminals:

   ```powershell
   .\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --no-access-log
   .\venv\Scripts\python.exe -m app.worker
   ```

4. Submit a call through `POST /api/calls/outbound` or activate a campaign
   through the existing campaign endpoints. Use a phone number you control and
   follow applicable consent and calling rules.
5. Confirm the Vonage event webhook reports the final call status and inspect
   `conversation_sessions`, `conversation_messages`, and (for campaign calls)
   `conversation_attempt_links` for the transcript.

The automated tests use mocked providers and WebSocket frames; they do not
prove that a live phone receiver heard the audio. During a controlled live
test, verify both the `voice_audio_transmitted` and `media_audio_received`
events, then confirm the greeting and replies are audible on the phone.

## Security and operations (Phase 6)

### Accounts and roles

When `APP_ENV=production`, management and AI endpoints require a bearer access
token. Create the first admin after initializing the database by setting
`BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD` (at least 12
characters), then run this once from `backend`:

```powershell
.\venv\Scripts\python.exe -m app.bootstrap_admin
```

Remove the bootstrap password from the environment after provisioning. Admins
can create and manage accounts at `/api/auth/users`; operators can manage
calls, campaigns, customers, conversations, and AI operations. Only admins can
manage IVR menus and access `/metrics`. Login at `POST /api/auth/login` with
JSON `{"username":"...","password":"..."}`; use the returned `access_token` as
`Authorization: Bearer <token>`. Access tokens expire after 30 minutes by
default. Keep `AUTH_JWT_SECRET` secret and at least 32 bytes. Non-production
environments intentionally allow local API access without a token and must
never be exposed publicly.

Production startup also requires the Vonage signature secret (at least 32
bytes), Vonage application credentials and readable private key, `GROQ_API_KEY`,
and an HTTPS `PUBLIC_BASE_URL`. Set secrets in an ignored local `.env` file or
deployment secret manager; do not commit them. The API and worker both validate
required production settings on startup.

### Monitoring, rate limits, and retention

- `GET /health` is a liveness check. `GET /ready` checks database connectivity
  and, in production, that an active admin account exists.
- Admin-only `GET /metrics` returns aggregate call, attempt, dispatch queue,
  pending webhook, and active-conversation metrics in Prometheus text format.
- Application logs are JSON and redact bearer tokens and phone-number-shaped
  values. This is a safeguard, not a guarantee that arbitrary PII is removed;
  avoid logging customer content or credentials. Start Uvicorn with
  `--no-access-log` to avoid logging request details.
- The in-process limiter allows up to 10 login requests and 120 other API
  requests per client IP per minute. It is not shared across worker processes;
  enforce limits at the trusted reverse proxy/API gateway for multi-worker
  deployments. Do not rely on arbitrary forwarded-IP headers.
- The worker marks active conversations older than
  `STALE_CONVERSATION_MINUTES` (150 by default) as failed and purges inactive
  conversation sessions, messages, and attempt links older than
  `TRANSCRIPT_RETENTION_DAYS` (90 by default). Maintenance runs every
  `MAINTENANCE_INTERVAL_SECONDS` (3600 by default). Vonage webhook event rows
  are not currently covered by this retention job and need a separately
  approved retention policy.

Phase 6 adds security and operational safeguards, but this is not yet a
production-ready deployment: rate limiting is process-local, the project has
no versioned database migration framework, webhook-event retention is
unresolved, and consent/opt-out, local calling-hour, and campaign budget
controls are not implemented. Complete those controls and validate with real
staging credentials and controlled live calls before production use.

Run the backend tests from `backend` with:

```powershell
.\venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Example menu request:

```json
{
  "name": "Main menu",
  "welcome_message": "Press 1 for Sales or 2 for Support.",
  "max_attempts": 3,
  "timeout_seconds": 10,
  "is_active": true,
  "options": [
    {
      "digit": "1",
      "label": "Sales",
      "destination_phone": "+14155550123"
    },
    {
      "digit": "2",
      "label": "Support",
      "destination_phone": "+14155550124"
    }
  ]
}
```

Create database tables with `python -m app.database.init_db` from the backend
directory. Run all backend tests with
`python -m unittest discover -s tests -v`.