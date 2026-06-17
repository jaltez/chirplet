# Phase 1 Spec

**Status: shipped.** The current implementation matches the
scope described below, plus a few additions driven by real usage
(see "Drift since v1" at the end).

## Goal

Deliver a local-first desktop MVP that proves the core loop:

1. user presses a talk button
2. browser captures speech
3. backend sends the turn to Hermes (or Ollama, in the local dev
   case)
4. the LLM returns a structured response
5. browser speaks the answer and updates the avatar state

## Included

- desktop browser only
- single user
- persistent session history in SQLite (so the LLM can keep
  context across turns)
- push-to-talk, with a press-and-hold-to-Interrupt client flow
- Spanish and English locales, with locale-aware fallback text
- avatar states: disconnected, listening, thinking, speaking,
  error, idle
- request-id correlation across frontend and backend logs
- a debug panel that exposes: manual text input, TTS voice
  selection, and a read-only session transcript
- dark mode via `prefers-color-scheme`

## Excluded (deferred to later phases)

- Raspberry Pi and physical peripherals (Phase 4 in
  `chirplet.md`)
- continuous listening
- wake word
- mobile and tablet layouts
- advanced lip sync
- WebSockets (the current MVP uses HTTP + Server-Sent Events)
- multi-user / authentication
- deployment beyond a single-user Docker container
- a private / persistent store of conversation transcripts on
  the client (the debug panel re-fetches from the backend on
  demand; nothing is cached locally)

## Backend Contract

### Endpoints

| Method | Path                                  | Purpose                                |
| ------ | ------------------------------------- | -------------------------------------- |
| GET    | `/api/health`                         | Service status + provider configured   |
| POST   | `/api/session`                        | Create a session, return its id        |
| POST   | `/api/turn`                           | Non-streaming turn                      |
| POST   | `/api/turn/stream`                    | SSE streaming turn                     |
| GET    | `/api/sessions`                       | List sessions (most recent first)      |
| GET    | `/api/sessions/{session_id}`          | Session metadata (404 if missing)      |
| GET    | `/api/sessions/{session_id}/turns`    | Per-session turn log (404 if missing)  |
| DELETE | `/api/sessions/{session_id}`          | Delete a session (404 if missing)      |

### Turn request

`POST /api/turn` and `POST /api/turn/stream` accept:

```json
{
  "session_id": "optional, generates a new one if absent",
  "transcript": "user input, 1-500 chars",
  "locale": "es-ES or en-GB"
}
```

### Turn response (non-streaming)

```json
{
  "session_id": "...",
  "assistant": {
    "text": "Hello there",
    "expression": { "state": "speaking", "mood": "friendly", "mouth": "smile" },
    "action": "idle",
    "voice_locale": "en-GB"
  },
  "timing": { "request_started_at": "...", "completed_at": "...", "duration_ms": 123 },
  "meta": { "provider": "hermes", "fallback_used": false, "issue": null }
}
```

### SSE events (`/api/turn/stream`)

- `data: {"type":"token","text":"..."}`  — incremental text
- `data: {"type":"done", "session_id":"...", "text":"...", "expression":..., "voice_locale":"...", "action":"..."}`
- `data: {"type":"error","text":"...","session_id":"...","issue":"..."}`

`issue` is the underlying provider exception message and is only
present on error events; it's also logged to the backend with
the request id.

## LLM Contract

The backend calls an OpenAI-compatible chat-completions
endpoint. Two providers are first-class:

- `hermes` (default): any OpenAI-compatible endpoint
  (`HERMES_BASE_URL`)
- `ollama`: a local Ollama server (`OLLAMA_BASE_URL`,
  `OLLAMA_MODEL`)

The system prompt mandates a single JSON object with:

- `text`
- `expression.state` (idle | listening | thinking | speaking | error | disconnected)
- `expression.mood`  (neutral | friendly | curious | calm | cheerful | concerned)
- `expression.mouth` (closed | open | smile | round)
- `action`           (free-form, e.g. "idle" | "wave" | "nod" | "blink")
- `voice_locale`     ("es-ES" or "en-GB")

`response_format: json_object` is supported on Hermes providers
that accept the OpenAI response_format field.

## Frontend Rules

- UI is avatar-first and almost textless
- browser speech synthesis (`window.speechSynthesis`) for the
  first local demo, with a voice picker in the debug panel
- browser speech recognition (`window.SpeechRecognition`) when
  available
- a debug panel provides manual text input for unsupported
  browsers and surfaces the session transcript
- the app is keyboard-accessible (spacebar to talk)

## Drift since v1

- **SQLite session history was added.** The original spec said
  "persistent database" was excluded. We needed turn history to
  feed the LLM context, so a minimal SQLite store was added in
  `apps/api/database.py`. The data is server-side only; the
  client fetches via the `/api/sessions` endpoints on demand.
- **A debug-panel transcript view was added.** The original spec
  said "visible conversation history" was excluded. We
  restricted it to a hidden `<details>` section so the main UI
  is still avatar-first, but the data is reachable for debugging.
- **SSE streaming replaced the non-streaming turn as the
  primary path.** The non-streaming `/api/turn` is still
  available for clients that prefer it.
- **Request-id correlation was added** for log tracing
  (`X-Request-ID` header on every request/response).
- **Dark mode was added** via `prefers-color-scheme`.

See `chirplet.md` for the long-term vision that this MVP
serves, and `docs/roadmap.md` for what is in each future phase.
