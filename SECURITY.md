# Security

Chirplet's MVP is a **local-first single-user desktop app**. The
default configuration assumes that and is not safe to expose on an
untrusted network without additional work. This document spells
out the trust model and the known gaps.

## Trust model (default)

- The process binds to `APP_HOST=127.0.0.1` by default (see
  `apps/api/config.py` / `.env.example`). On `127.0.0.1` only the
  local user can reach the API.
- `APP_ENV=development` is the default. Switching to `production`
  does not change any security behaviour today; it's a label
  used in the startup log.
- There is **no authentication** on any endpoint. Anyone who can
  reach the port can:
  - create sessions and submit turns (`/api/turn`, `/api/turn/stream`),
    which will spend the operator's LLM tokens / API budget,
  - read the operator's saved session history
    (`/api/sessions`, `/api/sessions/{id}/turns`),
  - delete sessions (`DELETE /api/sessions/{id}`).
- There is **no rate limiting**. A hostile local user (or a
  network attacker if the bind is changed) can drive the LLM at
  unbounded rate up to the upstream provider's quota.
- There is **no transport security**. The app speaks plain HTTP
  on the local port; do not put it behind a public domain name
  without TLS termination (a reverse proxy).
- The browser CORS configuration is `allow_origins=["*"]` with
  `allow_credentials=True`. Browsers reject credentialed
  cross-origin requests to `*` per the Fetch spec, so this is
  effectively a non-issue today, but it's misleading and should
  be tightened if/when a real frontend origin is added.

## Trust model when bound to a non-loopback interface

If you change `APP_HOST` to `0.0.0.0` (or any non-loopback
address, e.g. for a Raspberry Pi Phase 4 deployment), the above
caveats apply to **anyone on the network** that can reach the
host:port. Before doing that, you should add at least:

1. A reverse proxy with TLS (Caddy / nginx / Cloudflare Tunnel).
2. An authentication layer (a single-user bearer token checked
   by a dependency, or HTTP basic auth in front).
3. Rate limiting (e.g. `slowapi` middleware).

These are out of scope for the MVP. None of them are implemented
today.

## Data at rest

- `data/chirplet.db` (SQLite, configurable via `DATABASE_PATH`)
  holds every session id, every turn, and every assistant
  response. It is **not encrypted at rest**. The directory is
  bind-mounted into the container in `docker-compose.yml`.
- Provider API keys come from `HERMES_API_KEY` / `OLLAMA_*` in
  `.env`. The `.env` file is gitignored. The app does not
  persist the key itself; it is read from the environment on
  every request and never written to the database or logs.

## Data echoed to the client

- The SSE `error` event includes the underlying provider
  exception string in `issue` (e.g. `HTTP 401 Unauthorized` if
  the operator's API key has been revoked). The string is also
  logged server-side with the request id. This is intentional
  for the local-dev case (a clear error in the debug panel is
  better than a vague fallback), but in a multi-user or
  network-exposed deployment you may want to strip this field
  and keep it server-side only.

## Reporting issues

This is a personal project; there is no formal security
disclosure process. Open an issue on the project's issue
tracker.
