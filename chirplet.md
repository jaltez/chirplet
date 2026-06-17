

```markdown
# CHIRPLET: Voice Assistant Project with Hermes Agent, VPS, and Raspberry Pi


> **Author:** Javier Altez
> **Date:** 2026-05-14
> **Version:** 1.0 (revised 2026-06-17 to mark phases against the
> current implementation; see `docs/phase-1-spec.md` for the
> shipped MVP and `docs/roadmap.md` for the live plan)

## Current state

| Phase | Scope                                  | Status      | Doc                          |
| ----- | -------------------------------------- | ----------- | ---------------------------- |
| 1     | Functional prototype (desktop browser) | **shipped** | `docs/phase-1-spec.md`       |
| 2     | Visual interface (avatar states)       | partial     | this file, §"Phase 2"        |
| 3     | Optimisation (WebSockets, wake word)   | deferred    | this file, §"Phase 3"        |
| 4     | Polish and hardware (Raspberry Pi)      | deferred    | this file, §"Phase 4"        |

Everything in the body of this document below is the long-term
design. For what is in the repo *today*, read `phase-1-spec.md`
first, then come back here for context.

---

## Table of Contents

1. [Overview](#overview)
2. [System Architecture](#system-architecture)
3. [Main Components](#main-components)
   - [The Brain: Hermes Agent as the API Server](#the-brain-hermes-agent-as-the-api-server)
   - [Fast Language Model](#fast-language-model)
   - [Raspberry Pi Client](#raspberry-pi-client)
   - [Display and Animated Face](#display-and-animated-face)
4. [Communication Flow](#communication-flow)
   - [Flow Diagram](#flow-diagram)
   - [Structured JSON Handling](#structured-json-handling)
5. [Estimated Latency](#estimated-latency)
6. [Technology Stack](#technology-stack)
7. [Security](#security)
8. [Options and Considerations](#options-and-considerations)
9. [Proposed Roadmap](#proposed-roadmap)

---

## Overview

The goal is to build a conversational voice assistant made up of:

- **Hermes Agent** as the cloud-hosted brain on a VPS
- **A fast language model** (DeepSeek Flash, GLM Turbo) for responsive answers
- **A Raspberry Pi** acting as the local client with microphone, screen, and speakers
- **A complete voice interface** (STT + TTS)
- **An animated face** based on a pixel grid, controlled by numeric sequences produced by the agent

Communication happens in real time through WebSockets or HTTP, with Hermes generating structured JSON responses that include both the text to speak and the facial expression data for the avatar.

---

## System Architecture
```

┌──────────────────────────────────────────────────────┐
│ Cloud VPS                                            │
│                                                      │
│  ┌─────────────────────┐   ┌──────────────────────┐ │
│  │   Hermes Agent      │   │   Fast Model         │ │
│  │   (API Server +     │◄──┤  (DeepSeek Flash /   │ │
│  │    WebSocket)       │   │   GLM Turbo)         │ │
│  └─────────┬───────────┘   └──────────────────────┘ │
│            │                                         │
└────────────┼─────────────────────────────────────────┘
             │ WebSocket / HTTP (REST API)
             │ Structured JSON
             │
┌────────────┼─────────────────────────────────────────┐
│ Raspberry Pi (Local Client)                          │
│            │                                         │
│  ┌─────────▼───────────┐   ┌──────────────────────┐ │
│  │   Audio Capture     │   │   Virtual Display    │ │
│  │  (PyAudio /         │   │   (Pygame / LVGL)    │ │
│  │   sounddevice)      │   │                      │ │
│  └─────────┬───────────┘   │  ┌────────────────┐  │ │
│            │               │  │ Animated Face  │  │ │
│  ┌─────────▼───────────┐   │  │ (pixel grid +  │  │ │
│  │   Local STT         │   │  │ presets)       │  │ │
│  │  (faster-whisper /  │   │  └────────────────┘  │ │
│  │   vosk)             │   └──────────────────────┘ │
│  └─────────┬───────────┘                            │
│            │                                         │
│  ┌─────────▼───────────┐                             │
│  │   Local TTS         │                             │
│  │  (pyttsx3 / piper / │                             │
│  │   edge-tts)         │                             │
│  └─────────────────────┘                             │
└──────────────────────────────────────────────────────┘

```
---

## Main Components

### The Brain: Hermes Agent as the API Server

Hermes Agent is not only a terminal application. It can operate as a full **API server** compatible with the OpenAI standard. That makes it a strong backend choice for distributed architectures.

**Key features:**
- **OpenAI-compatible REST API:** endpoint `http://<vps-ip>:<port>/v1/chat/completions`
- **Native WebSockets:** used in official integrations such as Feishu and WeCom, supporting low-latency persistent channels
- **Structured JSON generation:** you can define schemas so the agent returns fields like `text`, `expression`, `action`, and more
- **Context handling:** conversation memory for more complex dialogues

### Fast Language Model

To achieve smooth conversational behavior, the recommended options are:

| Model | Advantages | TTFT (approx.) |
|--------|----------|---------------|
| **DeepSeek V4 Flash** | Excellent speed-to-quality ratio, optimized for chat | 0.6s - 1.6s |
| **GLM Turbo** | Very fast, good multilingual understanding | 0.5s - 1.2s |

Both are compatible with Hermes Agent and respond quickly enough for interactive voice use.

### Raspberry Pi Client

The client is a Python application running on the Raspberry Pi and handling the following tasks:

1. Continuous **audio capture** from the microphone
2. **Wake word detection** (optional, for example, "Hey, Hermes")
3. **STT (Speech-to-Text):** local transcription from audio to text
4. **Communication with the VPS:** sending text and receiving structured JSON
5. **TTS (Text-to-Speech):** synthesizing the answer into audio
6. **Animated face rendering:** interpreting the visual commands returned in JSON

### Display and Animated Face

**Concept:**
- A low-resolution pixel grid (for example, 32x32 or 64x64) for a retro or minimalist look
- Animations based on **predefined presets** such as smile, surprise, speaking, listening, and more
- Control through **numeric coded sequences** inside the JSON response

**Example of the expected JSON schema from the agent:**
```json
{
  "text": "Hello. How can I help you?",
  "expression": {
    "type": "happy",
    "duration_ms": 2000,
    "mouth": "speaking",
    "eyes": "open"
  },
  "action": "wave",
  "anim_sequence": [1, 2, 3, 3, 2, 1]
}
```

---

## Communication Flow

### Flow Diagram

```
User speaks
     │
     ▼
[Microphone] ──► [Wake Word Detection] (optional)
                       │
                       ▼
                  [Local STT]
               (faster-whisper / vosk)
                       │
                       ▼
                Transcribed text
                       │
                       ▼
[Pi Client] ───── WebSocket ─────► [Hermes Agent on VPS]
                                       │
                                       ▼
                                 Model processes
                                 (DeepSeek Flash)
                                       │
                                       ▼
                                  JSON response:
                                { text, expression, ... }
                                       │
                       ◄────────────────┘
                       │
           ┌───────────┴───────────┐
           ▼                       ▼
      [Local TTS]            [Face Rendering]
     (pyttsx3/piper)         (Pygame + grid)
           │                       │
           ▼                       ▼
       Speakers                  Display
```

### Structured JSON Handling

Hermes Agent **can generate JSON output** using defined schemas. In the agent configuration or system prompt, you can specify the exact format you need. That allows a single response from the agent to control all of the following at once:

- The **text** to synthesize with TTS
- The avatar's **facial expression**
- The visual **animations** or actions

---

## Estimated Latency

The total perceived latency is the sum of each stage:

| Stage              | Component                 | Typical latency   |
| ------------------ | ------------------------- | ----------------- |
| Capture + STT      | faster-whisper (local)    | 100 - 300 ms      |
| Network (send)     | WebSocket                 | 1 - 2 ms          |
| Inference          | DeepSeek Flash / GLM Turbo | 500 - 1600 ms    |
| Network (reply)    | WebSocket                 | 1 - 2 ms          |
| TTS                | pyttsx3 / piper (local)   | 50 - 200 ms       |
| Rendering          | Pygame                    | < 10 ms           |
| **Estimated total** |                           | **~800 ms - 2.5 s** |

> **Note:** With a fast model and local STT/TTS, latency stays within a range that is generally acceptable for natural conversation. Using WebSockets avoids the overhead of opening a new HTTP connection for every message.

---

## Technology Stack

### On the VPS (Cloud)

| Component        | Technology                  | Description                                 |
| ---------------- | --------------------------- | ------------------------------------------- |
| **Orchestrator** | Hermes Agent                | Main server with REST API and WebSocket     |
| **LLM model**    | DeepSeek Flash / GLM Turbo  | Fast inference                              |
| **HTTP server**  | Hermes internal server      | OpenAI-compatible API exposure              |
| **Secure tunnel**| Cloudflare Tunnel / Tailscale | Access without opening direct ports      |

### On the Raspberry Pi (Client)

| Component        | Technology                     | Description                        |
| ---------------- | ------------------------------ | ---------------------------------- |
| **Language**     | Python 3.10+                   | Base for the full client app       |
| **Audio I/O**    | `pyaudio` / `sounddevice`      | Audio capture and playback         |
| **STT**          | `faster-whisper` / `vosk`      | Local speech-to-text transcription |
| **TTS**          | `pyttsx3` / `piper` / `edge-tts` | Text-to-speech synthesis        |
| **Communication**| `websockets` (Python)          | Persistent channel to the VPS      |
| **UI**           | `pygame` / `LVGL` / `tkinter`  | Face rendering on screen           |
| **HTTP fallback**| `aiohttp` / `requests`         | Simple alternative via REST API    |
| **Secure network** | Cloudflare Tunnel / Tailscale | Encrypted connection to the VPS  |

---

## Security

Hermes Agent includes protection mechanisms you should take advantage of:

1. **API key (`API_SERVER_KEY`):** protects the HTTP endpoint
2. **WebSocket authentication:** use authentication tokens during the initial connection
3. **Encrypted tunnel:** use Cloudflare Tunnel or Tailscale so you do not expose ports directly to the internet
4. **VPS firewall:** restrict access to the tunnel or known IPs only
5. **HTTPS/WSS:** use encrypted connections whenever possible

---

## Options and Considerations

### STT: Local or cloud?

| Option                               | Advantages                                       | Drawbacks                                    |
| ------------------------------------ | ------------------------------------------------ | -------------------------------------------- |
| **Local** (faster-whisper, vosk)     | No network latency, full privacy, no extra cost  | Requires resources on the Pi, variable quality |
| **Cloud** (Azure, Google, Whisper API) | Higher accuracy, no local compute cost         | Network latency, cost, external dependency   |

**Recommendation:** Start with local `faster-whisper`, which offers good quality with smaller models optimized for Raspberry Pi.

### TTS: Local or cloud?

| Option                            | Advantages                             | Drawbacks                                  |
| --------------------------------- | -------------------------------------- | ------------------------------------------ |
| **Local** (pyttsx3, piper)        | No network latency, works offline      | Less natural voices                        |
| **Cloud** (OpenAI TTS, ElevenLabs) | Excellent voice quality               | Network latency, cost, requires internet   |

**Recommendation:** `piper-tts` offers fairly natural voices and is optimized for ARM. Hermes can also delegate TTS if you prefer premium voice quality.

### Communication: WebSocket or HTTP?

| Method            | Advantages                                       | Drawbacks                                  |
| ----------------- | ------------------------------------------------ | ------------------------------------------ |
| **WebSocket**     | Persistent channel, lower latency, bidirectional | More complex to implement                  |
| **HTTP (REST API)** | Simple, easy to debug                          | Higher latency due to per-message handshake |

**Recommendation:** WebSocket for production, HTTP for fast prototyping.

---

## Proposed Roadmap

### Phase 1 - Functional Prototype

> **Status: shipped.** See `docs/phase-1-spec.md` for the as-built
> contract.

- [x] Define architecture and stack
- [x] Backend: HTTP + SSE turn endpoint, OpenAI-compatible
- [x] Browser client: Web Speech API for STT/TTS, push-to-talk
- [x] Avatar: pure-CSS state machine driven by `data-*` attributes
- [x] Spanish + English locales
- [x] SQLite session history (drift — see `phase-1-spec.md` §"Drift")
- [x] Latency smoke test in CI via the Playwright e2e test

### Phase 2 - Visual Interface

> **Status: partial.** The avatar exists in the browser and the
> LLM-driven expressions are wired end-to-end. The Raspberry Pi
> pixel-grid + Pygame renderer is not started.

- [x] Avatar state machine (`idle | listening | thinking |
  speaking | error | disconnected`)
- [x] Avatar mood + mouth cues (driven by LLM JSON)
- [x] JSON parsing for expression commands
- [x] Synchronise audio with facial movement (browser-side;
  via `mouth=open` on the speaking state)
- [ ] Design the pixel grid and expression presets
- [ ] Build rendering with Pygame (Raspberry Pi)

### Phase 3 - Optimization

> **Status: deferred.**

- [ ] Migrate from HTTP/SSE to WebSockets
- [ ] Add wake word detection with `pvporcupine` or similar
- [ ] Configure a secure tunnel (Cloudflare/Tailscale)
- [ ] Tune latency and robustness

### Phase 4 - Polish and Hardware

> **Status: deferred.**

- [ ] Move from the virtual display to a physical screen (HDMI/SPI)
- [ ] Integrate optional LEDs or a physical matrix
- [ ] Package the Pi app as an auto-starting service
- [ ] Run long-duration usage tests

---

## Conclusion

This distributed architecture makes it possible to build a private, responsive voice assistant with an appealing visual interface, separating compute power in the cloud from physical interaction on the Raspberry Pi.

Hermes Agent as the central brain, with structured JSON and WebSocket support, fits this design well. Combined with a fast model and local audio processing, it can provide a smooth conversational experience with latency in the 1-2 second range.

Ready to start Phase 1?

```

This document captures the full set of topics covered so far: architecture, components, communication flow, detailed technology stack, latency estimates, security, and a roadmap for development. You can store it as `README.md` or `DOCS.md` in your project.

If you want to adjust any section or go deeper on a technical point, say which area you want to refine.
