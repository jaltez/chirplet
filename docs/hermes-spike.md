# Hermes Spike Checklist

Before treating Hermes as stable for the product, validate these points:

1. The endpoint accepts OpenAI-compatible chat completions.
2. The selected model can follow a strict JSON-only system prompt.
3. Session memory behavior is clear: either Hermes owns it or the local backend does.
4. Tool support is confirmed for later phases.
5. Timeout and error behavior are acceptable for a voice UX.

Minimum acceptance:

- 20 consecutive requests without malformed payloads
- JSON parse success above 95 percent
- acceptable average latency on a local desktop flow
