# Chirplet

Chirplet starts as a local-first desktop web app for a personal voice assistant experiment.

Phase 1 goals:
- run on a local machine without Raspberry Pi or extra hardware
- use Hermes as the conversational runtime
- provide push-to-talk voice interaction and a minimal avatar-first UI
- keep logs and persistence minimal

## Project Layout

- `apps/api`: local Python backend and Hermes bridge
- `apps/web`: lightweight browser client
- `packages/contracts`: shared JSON contracts
- `docs`: setup notes and planning docs

## Quick Start

1. Create a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Copy `.env.example` to `.env` and fill Hermes settings.
4. Run `uvicorn apps.api.main:app --reload` from the repository root.
5. Open `http://127.0.0.1:8000` in a desktop browser.

Chrome or Edge are the safest browsers for the first build because SpeechRecognition support is still uneven.

## Current Scope

The current implementation is a working MVP:
- FastAPI backend with OpenAI-compatible chat-completions integration
  (Hermes by default; Ollama is also a first-class provider for
  local dev)
- SQLite session history (in `data/chirplet.db` by default)
- avatar-first single-page UI driven by `data-*` state attributes
- browser speech recognition and speech synthesis for the first
  local demo, with a TTS voice picker in the debug panel
- minimal debug panel hidden behind a disclosure block; surfaces
  manual text input, voice selection, and a read-only session
  transcript
- request-id correlation (`X-Request-ID`) across frontend and
  backend logs
- dark mode via `prefers-color-scheme`

If the configured LLM provider is not reachable, the UI stays
available but returns a locale-aware fallback response.

## Testing and CI

- `make test` — runs the test suite.
- `make test-cov` — runs with coverage; enforces a 100% floor
  via `--cov-fail-under=100`.
- `make schema` — regenerates the JSON-Schema from Pydantic.
- A GitHub Actions workflow (`.github/workflows/ci.yml`) defines
  the full CI pipeline (lint, format-check, JSON-Schema sync,
  `make test-cov`, Docker build + smoke test). It is **disabled
  for push and pull_request events** — both jobs are gated
  with `if: github.event_name == 'workflow_dispatch'`. To run
  the pipeline manually: GitHub → Actions → CI → Run workflow.
  To re-enable on push/PR, remove the `if` lines from both jobs.

## Docs

- `docs/phase-1-spec.md` — the as-built Phase 1 contract
  (endpoints, LLM JSON schema, frontend rules, drift since v1)
- `chirplet.md` — the long-term design (Phases 1-4, Raspberry
  Pi, WebSockets, wake word)
- `docs/roadmap.md` — pointer between the two
- `docs/setup-local.md` — local run instructions
- `docs/hermes-spike.md` — notes on treating Hermes as
  production-ready
