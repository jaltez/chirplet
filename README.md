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

The current implementation is an MVP skeleton:
- FastAPI backend with Hermes chat-completions integration
- in-memory session history
- avatar-first single page UI
- browser speech recognition and speech synthesis for the first local demo
- minimal debug panel hidden behind a disclosure block

Hermes remains the required runtime for conversation. If Hermes is not configured, the UI stays available but returns a safe fallback response.

## Docs

- `docs/phase-1-spec.md`
- `docs/setup-local.md`
- `docs/hermes-spike.md`
