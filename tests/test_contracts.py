import pytest
from pydantic import ValidationError

from apps.api.contracts import (
    AssistantPayload,
    AvatarExpression,
    AvatarMood,
    AvatarState,
    ConversationTurnRequest,
    ConversationTurnResponse,
    HealthResponse,
    MouthCue,
    ResponseMeta,
    SessionStartResponse,
    TimingMetrics,
)


class TestAvatarState:
    def test_all_values_valid(self):
        assert AvatarState.IDLE == "idle"
        assert AvatarState.LISTENING == "listening"
        assert AvatarState.THINKING == "thinking"
        assert AvatarState.SPEAKING == "speaking"
        assert AvatarState.ERROR == "error"
        assert AvatarState.DISCONNECTED == "disconnected"

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            AvatarState("unknown")


class TestAvatarExpression:
    def test_defaults(self):
        expr = AvatarExpression()
        assert expr.state == AvatarState.SPEAKING
        assert expr.mood == AvatarMood.FRIENDLY
        assert expr.mouth == MouthCue.SMILE

    def test_explicit_values(self):
        expr = AvatarExpression(state=AvatarState.LISTENING, mood=AvatarMood.CURIOUS, mouth=MouthCue.OPEN)
        assert expr.state == AvatarState.LISTENING
        assert expr.mood == AvatarMood.CURIOUS
        assert expr.mouth == MouthCue.OPEN

    def test_ignores_extra_fields(self):
        expr = AvatarExpression(state="idle", mood="neutral", mouth="closed", unknown_field="boom")
        assert expr.state == AvatarState.IDLE


class TestAssistantPayload:
    def test_minimal(self):
        payload = AssistantPayload(text="Hello")
        assert payload.text == "Hello"
        assert payload.expression.state == AvatarState.SPEAKING
        assert payload.voice_locale == "es-ES"

    def test_empty_text_raises(self):
        with pytest.raises(ValidationError):
            AssistantPayload(text="")

    def test_text_too_long_raises(self):
        with pytest.raises(ValidationError):
            AssistantPayload(text="x" * 2001)

    def test_full_model(self):
        payload = AssistantPayload(
            text="Hola",
            expression=AvatarExpression(state=AvatarState.IDLE, mood=AvatarMood.CALM, mouth=MouthCue.CLOSED),
            action="wave",
            voice_locale="en-GB",
        )
        assert payload.voice_locale == "en-GB"
        assert payload.action == "wave"


class TestConversationTurnRequest:
    def test_minimal(self):
        req = ConversationTurnRequest(transcript="hello")
        assert req.session_id is None
        assert req.locale == "es-ES"

    def test_with_session(self):
        req = ConversationTurnRequest(session_id="abc", transcript="hello", locale="en-GB")
        assert req.session_id == "abc"
        assert req.locale == "en-GB"

    def test_empty_transcript_raises(self):
        with pytest.raises(ValidationError):
            ConversationTurnRequest(transcript="")


class TestHealthResponse:
    def test_structure(self):
        resp = HealthResponse(status="ok", provider="ollama", provider_configured=True, session_memory=True)
        data = resp.model_dump()
        assert data["provider"] == "ollama"
        assert "hermes_configured" not in data


class TestTimingMetrics:
    def test_valid(self):
        tm = TimingMetrics(request_started_at="2024-01-01T00:00:00", completed_at="2024-01-01T00:00:01", duration_ms=1000)
        assert tm.duration_ms == 1000

    def test_negative_duration_raises(self):
        with pytest.raises(ValidationError):
            TimingMetrics(request_started_at="a", completed_at="b", duration_ms=-1)


class TestResponseMeta:
    def test_fallback(self):
        meta = ResponseMeta(provider="ollama", fallback_used=True, issue="timeout")
        assert meta.fallback_used is True
        assert meta.issue == "timeout"


class TestSessionStartResponse:
    def test_structure(self):
        resp = SessionStartResponse(session_id="test-123")
        assert resp.session_id == "test-123"
