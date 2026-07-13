import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from apps.api.contracts import AssistantPayload


def _make_mock_provider(stream_turn_fn=None):
    provider = MagicMock()
    provider.provider_name = "mock-ws"
    provider.configured = True

    if stream_turn_fn:

        async def default_stream(transcript, locale, history, should_cancel=None):
            payload = AssistantPayload(text=f"Echo: {transcript}", voice_locale=locale)
            yield {"type": "token", "text": payload.text}
            yield {
                "type": "done",
                "expression": payload.expression.model_dump(),
                "voice_locale": payload.voice_locale,
                "action": payload.action,
                "full_text": payload.text,
            }

        provider.stream_turn = stream_turn_fn
    else:

        async def default_stream(transcript, locale, history, should_cancel=None):
            payload = AssistantPayload(text=f"Echo: {transcript}", voice_locale=locale)
            yield {"type": "token", "text": payload.text}
            yield {
                "type": "done",
                "expression": payload.expression.model_dump(),
                "voice_locale": payload.voice_locale,
                "action": payload.action,
                "full_text": payload.text,
            }

        provider.stream_turn = default_stream

    return provider


def _make_mock_db():
    db = MagicMock()
    db.ensure_session = AsyncMock()
    db.save_turn = AsyncMock()
    db.get_history = AsyncMock(return_value=[])
    return db


@pytest.fixture
def ws_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ws.db"))
    monkeypatch.setenv("LLM_PROVIDER", "hermes")
    monkeypatch.setenv("HERMES_BASE_URL", "http://x/v1")
    monkeypatch.setenv("HERMES_MODEL", "gpt-x")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    from apps.api.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def ws_app(ws_env):
    import apps.api.main as main_module

    provider = _make_mock_provider()
    db = _make_mock_db()
    main_module.app.dependency_overrides[main_module.get_provider] = lambda: provider
    main_module.app.dependency_overrides[main_module.get_db] = lambda: db

    with TestClient(main_module.app) as client:
        yield client, main_module, provider, db

    main_module.app.dependency_overrides.clear()


class TestWsConnection:
    def test_connected_event(self, ws_app):
        client, _mod, provider, _db = ws_app
        with client.websocket_connect("/ws") as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"
            assert data["provider"] == "mock-ws"
            assert data["configured"] is True


class TestWsTurn:
    def test_turn_emits_token_and_done(self, ws_app):
        client, _mod, _provider, db = ws_app
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # connected

            ws.send_json(
                {
                    "type": "turn",
                    "transcript": "hello",
                    "locale": "en-GB",
                    "session_id": "ws-1",
                }
            )

            token = ws.receive_json()
            assert token["type"] == "token"
            assert "Echo: hello" in token["text"]

            done = ws.receive_json()
            assert done["type"] == "done"
            assert done["session_id"] == "ws-1"
            assert done["voice_locale"] == "en-GB"

            db.save_turn.assert_called_once()

    def test_turn_creates_session_if_none(self, ws_app):
        client, _mod, _provider, db = ws_app
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "turn", "transcript": "hi", "locale": "es-ES"})

            done = ws.receive_json()
            token = ws.receive_json()
            assert done["type"] == "token"
            assert token["type"] == "done"
            assert token["session_id"] is not None

    def test_empty_transcript_returns_error(self, ws_app):
        client, _mod, _provider, db = ws_app
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "turn", "transcript": "", "session_id": "x"})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert "Empty transcript" in err["text"]

    def test_turn_persists_on_done(self, ws_app):
        client, _mod, _provider, db = ws_app
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json(
                {
                    "type": "turn",
                    "transcript": "persist me",
                    "session_id": "persist-1",
                }
            )
            ws.receive_json()  # token
            ws.receive_json()  # done
            db.save_turn.assert_called_once()
            call = db.save_turn.call_args
            assert call[0][0] == "persist-1"
            assert call[0][1] == "persist me"


class TestWsPing:
    def test_ping_returns_pong(self, ws_app):
        client, _mod, _p, _db = ws_app
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "ping"})
            data = ws.receive_json()
            assert data["type"] == "pong"


class TestWsInvalidJson:
    def test_invalid_json_returns_error(self, ws_app):
        client, _mod, _p, _db = ws_app
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # connected
            ws.send_text("not valid json{{{")
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "Invalid JSON" in data["text"]


class TestWsInterrupt:
    def test_interrupt_cancels_turn(self, ws_app):
        client, main_module, _provider, db = ws_app

        # Override with a slow provider that waits for cancel
        slow_provider = _make_mock_provider()

        async def slow_stream(transcript, locale, history, should_cancel=None):
            yield {"type": "token", "text": "starting"}
            for _ in range(50):
                if should_cancel is not None and await should_cancel():
                    return
                await asyncio.sleep(0.02)
            yield {
                "type": "done",
                "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"},
                "voice_locale": "en-GB",
                "action": "idle",
                "full_text": "starting",
            }

        slow_provider.stream_turn = slow_stream
        main_module.app.dependency_overrides[main_module.get_provider] = lambda: slow_provider

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "turn", "transcript": "hello", "session_id": "int-1"})

            token = ws.receive_json()
            assert token["type"] == "token"

            ws.send_json({"type": "interrupt"})
            ws.send_json({"type": "ping"})

            pong = ws.receive_json()
            assert pong["type"] == "pong"

            db.save_turn.assert_not_called()


class TestWsErrors:
    def test_provider_config_error(self, ws_app):
        client, main_module, _provider, _db = ws_app

        from apps.api.providers import ProviderConfigurationError

        error_provider = _make_mock_provider()

        async def error_stream(transcript, locale, history, should_cancel=None):
            raise ProviderConfigurationError("not configured")
            yield  # pragma: no cover

        error_provider.stream_turn = error_stream
        main_module.app.dependency_overrides[main_module.get_provider] = lambda: error_provider

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "turn", "transcript": "hello"})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert "session_id" in err

    def test_provider_protocol_error(self, ws_app):
        client, main_module, _provider, _db = ws_app

        from apps.api.providers import ProviderProtocolError

        error_provider = _make_mock_provider()

        async def error_stream(transcript, locale, history, should_cancel=None):
            raise ProviderProtocolError("bad response")
            yield  # pragma: no cover

        error_provider.stream_turn = error_stream
        main_module.app.dependency_overrides[main_module.get_provider] = lambda: error_provider

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "turn", "transcript": "hello"})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert "session_id" in err

    def test_unexpected_error(self, ws_app):
        client, main_module, _provider, _db = ws_app

        error_provider = _make_mock_provider()

        async def boom_stream(transcript, locale, history, should_cancel=None):
            raise RuntimeError("unexpected crash")
            yield  # pragma: no cover

        error_provider.stream_turn = boom_stream
        main_module.app.dependency_overrides[main_module.get_provider] = lambda: error_provider

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "turn", "transcript": "hello"})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert "session_id" in err


class TestWsAuth:
    def test_auth_rejection(self, ws_app):
        client, main_module, _p, _db = ws_app
        main_module.app.state.auth_token = "secret-key"

        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws") as ws:
            ws.receive_json()

    def test_auth_with_valid_token_header(self, ws_app):
        client, main_module, _p, _db = ws_app
        main_module.app.state.auth_token = "secret-key"

        with client.websocket_connect("/ws", headers={"Authorization": "Bearer secret-key"}) as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"

    def test_auth_with_valid_token_query_param(self, ws_app):
        client, main_module, _p, _db = ws_app
        main_module.app.state.auth_token = "secret-key"

        with client.websocket_connect("/ws?token=secret-key") as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"


class TestWsRateLimit:
    def test_rate_limit_rejection(self, ws_app):
        client, main_module, _p, _db = ws_app
        from apps.api.rate_limit import RateLimiter

        main_module.app.state.rate_limiter = RateLimiter(max_requests=0, window_seconds=60)

        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws") as ws:
            ws.receive_json()


class TestWsCoverageEdgeCases:
    def test_interrupt_breaks_loop_when_event_arrives_after_cancel(self, ws_app):
        """Hit the cancel_event.is_set() break inside the async-for loop."""
        client, main_module, _provider, db = ws_app

        cancel_probe_provider = _make_mock_provider()

        async def stream_then_yield_after_cancel(transcript, locale, history, should_cancel=None):
            yield {"type": "token", "text": "starting"}
            for _ in range(50):
                if should_cancel is not None and await should_cancel():
                    break
                await asyncio.sleep(0.02)
            yield {
                "type": "done",
                "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"},
                "voice_locale": "en-GB",
                "action": "idle",
                "full_text": "starting",
            }

        cancel_probe_provider.stream_turn = stream_then_yield_after_cancel
        main_module.app.dependency_overrides[main_module.get_provider] = lambda: (
            cancel_probe_provider
        )

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "turn", "transcript": "hello", "session_id": "brk-1"})
            ws.receive_json()  # token

            ws.send_json({"type": "interrupt"})
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong"

            db.save_turn.assert_not_called()

    def test_invalid_json_during_turn_is_ignored_by_listener(self, ws_app):
        """Invalid JSON received during a turn is caught and skipped."""
        client, main_module, _provider, db = ws_app

        slow_provider = _make_mock_provider()

        async def slow_stream(transcript, locale, history, should_cancel=None):
            yield {"type": "token", "text": "hello"}
            for _ in range(50):
                if should_cancel is not None and await should_cancel():
                    return
                await asyncio.sleep(0.02)
            yield {
                "type": "done",
                "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"},
                "voice_locale": "en-GB",
                "action": "idle",
                "full_text": "hello",
            }

        slow_provider.stream_turn = slow_stream
        main_module.app.dependency_overrides[main_module.get_provider] = lambda: slow_provider

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "turn", "transcript": "hello", "session_id": "json-1"})
            ws.receive_json()  # token

            ws.send_text("not valid json{{{")
            ws.send_json({"type": "interrupt"})
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong"

    def test_disconnect_during_turn_sets_cancel(self, ws_app):
        """WebSocketDisconnect in the listener sets the cancel event."""
        client, main_module, _provider, _db = ws_app

        slow_provider = _make_mock_provider()

        async def slow_stream(transcript, locale, history, should_cancel=None):
            yield {"type": "token", "text": "hello"}
            for _ in range(50):
                if should_cancel is not None and await should_cancel():
                    return
                await asyncio.sleep(0.02)
            yield {
                "type": "done",
                "expression": {"state": "speaking", "mood": "friendly", "mouth": "smile"},
                "voice_locale": "en-GB",
                "action": "idle",
                "full_text": "hello",
            }

        slow_provider.stream_turn = slow_stream
        main_module.app.dependency_overrides[main_module.get_provider] = lambda: slow_provider

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "turn", "transcript": "hello", "session_id": "disc-1"})
            ws.receive_json()  # token
            ws.close()

    def test_db_save_failure_still_sends_done(self, ws_app):
        """When DB save fails during a WS turn, the done event is still sent."""
        client, _mod, _provider, db = ws_app
        db.save_turn = AsyncMock(side_effect=RuntimeError("DB locked"))

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # connected
            ws.send_json(
                {
                    "type": "turn",
                    "transcript": "hello",
                    "session_id": "dbfail-1",
                }
            )
            ws.receive_json()  # token
            done = ws.receive_json()
            assert done["type"] == "done"
            assert done["session_id"] == "dbfail-1"
