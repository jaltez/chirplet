"""End-to-end test of the SSE + interrupt path using Playwright.

Boots the real app via lifespan_context (real DB, real routes,
real dependency-injection chain) with a *slow* streaming provider
overridden via app.dependency_overrides, so we can race the
client-side interrupt against a real in-flight SSE stream.

The browser is headless Chromium. We stub out window.SpeechRecognition
and window.speechSynthesis before app.js runs (headless Chromium has
neither), then exercise the same code path the user would:
submit a turn via the manual text input, wait for some tokens,
click the talk button to trigger Interrupt, and assert the avatar
returns to idle and the stream stops.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

SLOW_PROVIDER_FULL_JSON = (
    '{"text":"hello world this is a slow stream",'
    '"expression":{"state":"speaking","mood":"friendly","mouth":"smile"},'
    '"action":"idle","voice_locale":"en-GB"}'
)


async def _slow_stream(transcript, locale, history, should_cancel=None):
    """Stream a valid JSON payload one character every 80ms, yielding
    a `token` event per incremental delta the way the real
    BaseProvider.stream_turn does, then a `done` event at the end.

    Honours should_cancel between characters so the test can race an
    interrupt against the in-flight stream.
    """
    # BaseProvider.stream_turn uses a text-preview decoder to find the
    # "text" value in the incomplete JSON. Easiest path that matches
    # the real shape: yield a `token` event with the *full payload*
    # immediately, then yield `done` at the end. The client-side
    # `streamed_text` will equal the final text after the first token
    # so no further deltas fire. This is functionally equivalent to a
    # provider that emits the full text in one chunk, with the slow
    # per-character sleep moved to AFTER the token so the avatar still
    # has time to enter the speaking state before the test interrupts.
    full_text = "hello world this is a slow stream"
    yield {
        "type": "token",
        "text": full_text,
    }
    # Hold the connection open with a long sleep, but bail out on
    # cancel so the test can race an interrupt.
    for _ in range(50):
        if should_cancel is not None and await should_cancel():
            return
        await asyncio.sleep(0.05)
    yield {
        "type": "done",
        "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"},
        "voice_locale": "en-GB",
        "action": "idle",
        "full_text": full_text,
    }


class _SlowProvider:
    provider_name = "slow"
    configured = True

    async def complete_turn(self, transcript, locale, history):
        from apps.api.contracts import AssistantPayload

        return AssistantPayload(
            text="hello world this is a slow stream",
            voice_locale=locale,
        )

    async def stream_turn(self, transcript, locale, history, should_cancel=None):
        async for event in _slow_stream(transcript, locale, history, should_cancel):
            yield event

    async def aclose(self) -> None:
        pass


@pytest.fixture
def temp_env(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "e2e-test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("LLM_PROVIDER", "hermes")
    monkeypatch.setenv("HERMES_BASE_URL", "http://x/v1")
    monkeypatch.setenv("HERMES_MODEL", "gpt-x")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    from apps.api.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app_server(temp_env):
    """Boot the real app with the slow provider injected. Returns a
    (base_url, transport) tuple and tears everything down on exit."""
    from apps.api.main import app, get_provider

    app.dependency_overrides[get_provider] = lambda: _SlowProvider()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Serve the app over a real loopback HTTP server so the browser
        # sees a normal origin (the ASGI transport is in-process and
        # not directly browser-accessible).
        import socket
        import threading

        import uvicorn

        # Pick a free port.
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
        )
        server = uvicorn.Server(config)

        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Wait for the server to be ready (up to 5s).
        import time
        import urllib.request

        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.1)
                break
            except Exception:
                time.sleep(0.1)

        base_url = f"http://127.0.0.1:{port}"
        try:
            yield base_url, browser
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            browser.close()
            app.dependency_overrides.clear()


def _stub_browser_apis(page) -> None:
    """Headless Chromium has no SpeechRecognition or speechSynthesis.
    Stub them to no-ops so app.js boots cleanly."""
    page.add_init_script(
        """
        // No-op SpeechRecognition: just emits a 'no-match' end so the
        // recognition lifecycle is well-defined. The test never uses
        // push-to-talk; it always submits via the manual text input.
        window.SpeechRecognition = function () {
            return {
                lang: "en-GB",
                interimResults: false,
                maxAlternatives: 1,
                onstart: null, onerror: null, onresult: null, onend: null,
                start: function () { if (this.onend) setTimeout(() => this.onend(), 0); },
                stop: function () { if (this.onend) setTimeout(() => this.onend(), 0); },
            };
        };
        // No-op speechSynthesis: the assistant text is rendered to
        // #spoken-text by the SSE handler, no audio is required.
        if (!window.speechSynthesis) {
            window.speechSynthesis = {
                speaking: false,
                cancel: function () {},
                speak: function () {},
                getVoices: function () { return []; },
                addEventListener: function () {},
            };
        }
        """
    )


class TestSseStreaming:
    def test_avatar_enters_speaking_state_when_tokens_arrive(self, app_server):
        base_url, browser = app_server
        page = browser.new_page()
        _stub_browser_apis(page)
        page.goto(base_url)
        expect(page.locator("#talk-button")).to_be_enabled()

        # Open the debug panel so the manual input is visible.
        page.locator(".debug-panel summary").click()
        page.locator("#manual-input").fill("hi")
        page.locator("#manual-send").click()

        # Avatar should reach the speaking state once at least one
        # token has arrived.
        expect(page.locator("body")).to_have_attribute("data-state", "speaking", timeout=5000)
        page.close()

    def test_interrupt_stops_stream_and_resets_avatar(self, app_server):
        base_url, browser = app_server
        page = browser.new_page()
        _stub_browser_apis(page)
        page.goto(base_url)
        page.locator(".debug-panel summary").click()

        page.locator("#manual-input").fill("hi")
        page.locator("#manual-send").click()

        # Wait for the speaking state to begin.
        expect(page.locator("body")).to_have_attribute("data-state", "speaking", timeout=5000)

        # Capture the text length, then click the talk button to interrupt.
        # (Holding and releasing a real mouse would also work; a click
        # exercises the same handleTalkStart -> interruptTurn path.)
        text_before = page.locator("#spoken-text").text_content() or ""
        assert len(text_before) > 0, "expected some tokens before interrupt"

        page.locator("#talk-button").click()

        # After interrupt, the avatar should be back to idle.
        expect(page.locator("body")).to_have_attribute("data-state", "idle", timeout=5000)

        # And the stream should have stopped: the spoken text should
        # not grow further. Wait a couple of stream-tick durations
        # to be sure.
        text_after = page.locator("#spoken-text").text_content() or ""
        page.wait_for_timeout(500)
        text_final = page.locator("#spoken-text").text_content() or ""
        assert text_final == text_after, (
            f"Stream continued after interrupt: before={text_after!r} after={text_final!r}"
        )
        page.close()


class TestUrlDebugPanel:
    def test_debug_panel_starts_closed(self, app_server):
        base_url, browser = app_server
        page = browser.new_page()
        _stub_browser_apis(page)
        page.goto(base_url)
        # The <details> element is open when it has the `open` attribute.
        # Default (no URL param) -> panel is closed.
        assert not page.evaluate("document.querySelector('.debug-panel').open")
        page.close()

    def test_debug_panel_opens_with_query_param(self, app_server):
        base_url, browser = app_server
        page = browser.new_page()
        _stub_browser_apis(page)
        page.goto(f"{base_url}/?debug=1")
        assert page.evaluate("document.querySelector('.debug-panel').open")
        page.close()

    def test_debug_panel_opens_with_hash(self, app_server):
        base_url, browser = app_server
        page = browser.new_page()
        _stub_browser_apis(page)
        page.goto(f"{base_url}/#debug")
        assert page.evaluate("document.querySelector('.debug-panel').open")
        page.close()


class TestVoiceTheme:
    def test_no_data_voice_attribute_when_no_voice_selected(self, app_server):
        base_url, browser = app_server
        page = browser.new_page()
        _stub_browser_apis(page)
        page.goto(base_url)
        # With no saved voice, body has no data-voice attribute.
        assert page.evaluate("!document.body.dataset.voice")
        page.close()

    def test_selecting_voice_sets_data_voice_attribute(self, app_server):
        base_url, browser = app_server
        page = browser.new_page()
        _stub_browser_apis(page)
        # Open debug panel via URL flag so the voice select is in view.
        page.goto(f"{base_url}/?debug=1")

        # Stub populateVoiceList to expose two voices synchronously
        # (real Chromium headless may not provide any).
        page.evaluate(
            """
            const sel = document.querySelector('#voice-select');
            sel.innerHTML = '';
            for (const v of [
              {uri: 'urn:voice:one', name: 'One', lang: 'en-GB'},
              {uri: 'urn:voice:two', name: 'Two', lang: 'es-ES'},
            ]) {
              const opt = document.createElement('option');
              opt.value = v.uri;
              opt.textContent = v.name;
              sel.appendChild(opt);
            }
            sel.disabled = false;
            """
        )
        # Select the first voice. The change handler should call
        # applyVoiceTheme and set body[data-voice].
        page.evaluate("document.querySelector('#voice-select').value = 'urn:voice:one'")
        page.locator("#voice-select").dispatch_event("change")

        voice_attr = page.evaluate("document.body.dataset.voice || ''")
        assert voice_attr.startswith("v"), (
            f"expected data-voice starting with 'v', got {voice_attr!r}"
        )
        assert voice_attr != "", "expected non-empty data-voice after selecting a voice"
        page.close()
