# Phase 1 Spec

## Goal

Deliver a local-first desktop MVP that proves the core loop:

1. user presses a talk button
2. browser captures speech
3. backend sends the turn to Hermes
4. Hermes returns a structured response
5. browser speaks the answer and updates the avatar state

## Included

- desktop browser only
- single user
- single active session
- push-to-talk
- Spanish and English locales
- avatar states: disconnected, listening, thinking, speaking, error, idle
- minimal technical logging

## Excluded

- Raspberry Pi and physical peripherals
- continuous listening
- wake word
- mobile and tablet layouts
- persistent database
- visible conversation history
- advanced lip sync

## Backend Contract

`POST /api/session`
- returns a local session id

`GET /api/health`
- returns service status and whether Hermes is configured

`POST /api/turn`
- accepts transcript and locale
- returns text, expression, timing, and fallback metadata

## Hermes Contract

Hermes is called through an OpenAI-compatible chat-completions endpoint.
The backend instructs Hermes to return one JSON object with:

- `text`
- `expression.state`
- `expression.mood`
- `expression.mouth`
- `action`
- `voice_locale`

## Frontend Rules

- UI stays avatar-first and almost textless
- browser speech synthesis is used for the first local demo
- browser speech recognition is used when available
- a hidden debug panel provides manual text input for unsupported browsers
