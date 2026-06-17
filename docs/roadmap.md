# Chirplet Roadmap

A pointer to the right document for each question about where the
project is and where it's going.

## Where we are

- **Phase 1 (functional prototype)** — **shipped.** Read
  `phase-1-spec.md` for the as-built MVP contract: endpoints,
  LLM JSON schema, frontend rules, and the list of features
  that drifted into the implementation since v1.
- **Phase 2 (visual interface)** — **partial** in the browser;
  the Raspberry Pi Pygame renderer is not started. See
  `chirplet.md` §"Phase 2" for the long-term design.

## Where we're going

- **Phase 3 (optimisation)** — WebSockets, wake word, secure
  tunnel. Not started.
- **Phase 4 (polish and hardware)** — Raspberry Pi client,
  physical display, autostart service. Not started.

Both deferred phases are sketched in `chirplet.md`.

## How this document is maintained

Whenever the implementation drifts from this doc (or `chirplet.md`),
update the doc in the same commit. The convention is:

- `docs/phase-1-spec.md` describes **what the code does today**.
- `chirplet.md` describes the **long-term design**.
- `docs/roadmap.md` (this file) is the **index** between them.
