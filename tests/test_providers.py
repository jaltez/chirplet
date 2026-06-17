import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from apps.api.config import Settings
from apps.api.contracts import (
    AssistantPayload,
    AvatarState,
    ChatTurn,
)
from apps.api.providers import (
    BaseProvider,
    HermesProvider,
    OllamaProvider,
    ProviderConfigurationError,
    create_provider,
)


def _make_settings(**overrides) -> Settings:
    defaults = dict(
        app_name="test",
        app_env="test",
        app_host="0",
        app_port=0,
        log_level="INFO",
        llm_provider="hermes",
        llm_temperature=0.4,
        hermes_base_url="http://x/v1",
        hermes_api_key=None,
        hermes_model="gpt",
        hermes_timeout_seconds=30,
        hermes_response_format="none",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.2",
        ollama_timeout_seconds=30,
        enable_session_memory=True,
        session_turn_limit=8,
        session_ttl_minutes=60,
        session_cleanup_interval_seconds=300,
        database_path=Path("db"),
        cors_origins="*",
        system_prompt='Respond with JSON: {"text":"..."}',
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _logger():
    return logging.getLogger("test")


class TestBaseProvider:
    def test_messages_structure(self):
        settings = _make_settings(enable_session_memory=False)
        p = HermesProvider(settings, _logger())
        messages = p._build_messages("hello", "en-GB", [])
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert json.loads(messages[1]["content"]) == {
            "locale": "en-GB",
            "transcript": "hello",
        }

    def test_messages_with_history(self):
        settings = _make_settings(enable_session_memory=True, session_turn_limit=8)
        p = HermesProvider(settings, _logger())
        history = [ChatTurn(user="q1", assistant="a1"), ChatTurn(user="q2", assistant="a2")]
        messages = p._build_messages("q3", "es-ES", history)
        assert len(messages) == 6

    def test_messages_history_truncation(self):
        settings = _make_settings(enable_session_memory=True, session_turn_limit=1)
        p = HermesProvider(settings, _logger())
        history = [ChatTurn(user="q1", assistant="a1"), ChatTurn(user="q2", assistant="a2")]
        messages = p._build_messages("q3", "es-ES", history)
        assert len(messages) == 4

    def test_disabled_memory_ignores_history(self):
        settings = _make_settings(enable_session_memory=False, session_turn_limit=8)
        p = HermesProvider(settings, _logger())
        history = [ChatTurn(user="q1", assistant="a1")]
        messages = p._build_messages("q2", "es-ES", history)
        assert len(messages) == 2

    def test_messages_escape_user_payload_as_json(self):
        settings = _make_settings(enable_session_memory=False)
        p = HermesProvider(settings, _logger())
        messages = p._build_messages('"Locale: hacked"', "es-ES", [])

        assert json.loads(messages[1]["content"]) == {
            "locale": "es-ES",
            "transcript": '"Locale: hacked"',
        }

    def test_parse_content_simple_json(self):
        p = HermesProvider(_make_settings(), _logger())
        result = p._parse_content(
            '{"text":"Hello","expression":{"state":"speaking","mood":"friendly","mouth":"smile"}}'
        )
        assert result.text == "Hello"
        assert result.expression.state == AvatarState.SPEAKING

    def test_parse_content_with_code_fence(self):
        p = HermesProvider(_make_settings(), _logger())
        result = p._parse_content('```json\n{"text":"Hi"}\n```')
        assert result.text == "Hi"

    def test_parse_content_with_brace_extraction(self):
        p = HermesProvider(_make_settings(), _logger())
        result = p._parse_content('Prefix {"text":"Hello"} suffix')
        assert result.text == "Hello"

    def test_parse_content_invalid_raises(self):
        p = HermesProvider(_make_settings(), _logger())
        with pytest.raises(ValueError, match="No JSON"):
            p._parse_content("no json here at all")

    @pytest.mark.asyncio
    async def test_stream_turn_stops_when_cancelled(self):
        class StreamingProbeProvider(BaseProvider):
            @property
            def provider_name(self) -> str:
                return "probe"

            @property
            def configured(self) -> bool:
                return True

            async def _complete_impl(self, transcript, locale, history):
                return AssistantPayload(text="unused")

            async def _stream_impl(self, transcript, locale, history) -> AsyncGenerator[str, None]:
                yield '{"text":"Hello"'
                yield ',"expression":{"state":"speaking","mood":"friendly","mouth":"smile"},"action":"idle","voice_locale":"en-GB"}'

        provider = StreamingProbeProvider(_make_settings(), _logger())

        async def should_cancel() -> bool:
            return True

        events = [
            event
            async for event in provider.stream_turn(
                "hello",
                "en-GB",
                [],
                should_cancel=should_cancel,
            )
        ]

        assert events == []

    @pytest.mark.asyncio
    async def test_stream_turn_emits_text_deltas_before_done(self):
        class StreamingProbeProvider(BaseProvider):
            @property
            def provider_name(self) -> str:
                return "probe"

            @property
            def configured(self) -> bool:
                return True

            async def _complete_impl(self, transcript, locale, history):
                return AssistantPayload(text="unused")

            async def _stream_impl(self, transcript, locale, history) -> AsyncGenerator[str, None]:
                yield '{"text":"Hel'
                yield "lo"
                yield ' there","expression":{"state":"speaking","mood":"friendly","mouth":"smile"},"action":"idle","voice_locale":"en-GB"}'

        provider = StreamingProbeProvider(_make_settings(), _logger())

        events = [event async for event in provider.stream_turn("hello", "en-GB", [])]

        token_events = [event["text"] for event in events if event["type"] == "token"]
        done_events = [event for event in events if event["type"] == "done"]

        assert token_events == ["Hel", "lo", " there"]
        assert len(done_events) == 1
        assert done_events[0]["full_text"] == "Hello there"

    @pytest.mark.asyncio
    async def test_stream_turn_post_loop_cancel_returns_before_done(self):
        """The post-loop should_cancel check suppresses the done event
        if the client disconnects after the last chunk arrived but
        before parsing. The mid-loop check is exercised separately in
        test_stream_turn_stops_when_cancelled."""

        class _Probe(BaseProvider):
            @property
            def provider_name(self) -> str:
                return "probe"

            @property
            def configured(self) -> bool:
                return True

            async def _complete_impl(self, transcript, locale, history):
                return AssistantPayload(text="unused")

            async def _stream_impl(self, transcript, locale, history):
                yield (
                    '{"text":"Hello","expression":{"state":"speaking",'
                    '"mood":"friendly","mouth":"smile"},"action":"idle",'
                    '"voice_locale":""}'
                )

        provider = _Probe(_make_settings(), _logger())
        calls = {"n": 0}

        async def cancel_after_parse() -> bool:
            calls["n"] += 1
            # First call is the mid-loop check (returns False), second
            # call is the post-loop check (returns True).
            return calls["n"] > 1

        events = [
            event
            async for event in provider.stream_turn(
                "hi", "en-GB", [], should_cancel=cancel_after_parse
            )
        ]

        # Only the token event fires; done is suppressed by the
        # post-loop cancel.
        assert [e["type"] for e in events] == ["token"]

    @pytest.mark.asyncio
    async def test_stream_turn_backfills_voice_locale_when_empty(self):
        """When the LLM emits a complete JSON object with empty
        voice_locale, the provider backfills the request locale."""

        class _Probe(BaseProvider):
            @property
            def provider_name(self) -> str:
                return "probe"

            @property
            def configured(self) -> bool:
                return True

            async def _complete_impl(self, transcript, locale, history):
                return AssistantPayload(text="unused")

            async def _stream_impl(self, transcript, locale, history):
                yield (
                    '{"text":"Hello","expression":{"state":"speaking",'
                    '"mood":"friendly","mouth":"smile"},"action":"idle",'
                    '"voice_locale":""}'
                )

        provider = _Probe(_make_settings(), _logger())
        events = [event async for event in provider.stream_turn("hi", "en-GB", [])]
        done = next(e for e in events if e["type"] == "done")
        assert done["voice_locale"] == "en-GB"

    @pytest.mark.asyncio
    async def test_stream_turn_emits_final_delta_after_parse(self):
        """The final-delta bridge in BaseProvider.stream_turn is
        defensive: it fires only if the partial-JSON decoder's
        preview undercounts the parsed text. The clean protocol we
        use (single full JSON object streamed in chunks) never hits
        that branch in practice. We document the gap here as a
        smoke test for the surrounding code path; line 94 stays
        pragma-marked as the unreachable-in-clean-input branch."""

        class _Probe(BaseProvider):
            @property
            def provider_name(self) -> str:
                return "probe"

            @property
            def configured(self) -> bool:
                return True

            async def _complete_impl(self, transcript, locale, history):
                return AssistantPayload(text="unused")

            async def _stream_impl(self, transcript, locale, history):
                yield (
                    '{"text":"Hello","expression":{"state":"speaking",'
                    '"mood":"friendly","mouth":"smile"},"action":"idle",'
                    '"voice_locale":"en-GB"}'
                )

        provider = _Probe(_make_settings(), _logger())
        events = [event async for event in provider.stream_turn("hi", "en-GB", [])]
        done = next(e for e in events if e["type"] == "done")
        assert done["full_text"] == "Hello"


class TestHermesProvider:
    def test_provider_name(self):
        p = HermesProvider(_make_settings(), _logger())
        assert p.provider_name == "hermes"

    def test_configured_true(self):
        p = HermesProvider(_make_settings(), _logger())
        assert p.configured is True

    def test_configured_false(self):
        p = HermesProvider(_make_settings(llm_provider="hermes", hermes_base_url=""), _logger())
        assert p.configured is False

    @pytest.mark.asyncio
    async def test_complete_turn_not_configured(self):
        p = HermesProvider(_make_settings(llm_provider="hermes", hermes_base_url=""), _logger())
        with pytest.raises(ProviderConfigurationError, match="hermes is not configured"):
            await p.complete_turn("hello", "en-GB", [])


class TestOllamaProvider:
    def test_provider_name(self):
        p = OllamaProvider(_make_settings(), _logger())
        assert p.provider_name == "ollama"

    def test_configured_true(self):
        p = OllamaProvider(_make_settings(), _logger())
        assert p.configured is True

    def test_configured_false_no_model(self):
        p = OllamaProvider(_make_settings(ollama_model=""), _logger())
        assert p.configured is False

    def test_configured_false_no_url(self):
        p = OllamaProvider(_make_settings(ollama_base_url=""), _logger())
        assert p.configured is False

    @pytest.mark.asyncio
    async def test_complete_turn_not_configured(self):
        p = OllamaProvider(_make_settings(ollama_model=""), _logger())
        with pytest.raises(ProviderConfigurationError, match="ollama is not configured"):
            await p.complete_turn("hello", "en-GB", [])

    @pytest.mark.asyncio
    async def test_stream_turn_not_configured(self):
        p = OllamaProvider(_make_settings(ollama_model=""), _logger())
        with pytest.raises(ProviderConfigurationError, match="ollama is not configured"):
            async for _ in p.stream_turn("hello", "en-GB", []):
                pass

    @pytest.mark.asyncio
    async def test_locale_backfill(self, monkeypatch):
        """Locale is backfilled when provider returns empty voice_locale."""
        p = OllamaProvider(_make_settings(), _logger())
        payload = AssistantPayload(text="Hi", voice_locale="")

        async def mock_impl(transcript, locale, history):
            return payload

        monkeypatch.setattr(p, "_complete_impl", mock_impl)
        result = await p.complete_turn("hello", "en-GB", [])
        assert result.voice_locale == "en-GB"


class TestCreateProvider:
    def test_creates_hermes(self):
        s = _make_settings(llm_provider="hermes")
        p = create_provider(s, _logger())
        assert isinstance(p, HermesProvider)

    def test_creates_ollama(self):
        s = _make_settings(llm_provider="ollama")
        p = create_provider(s, _logger())
        assert isinstance(p, OllamaProvider)


# ---------------------------------------------------------------------------
# Property tests for the hand-rolled partial-JSON decoder.
# These invariants protect the streaming-text path: as the LLM emits
# additional characters, the previewed text must only ever grow.
# ---------------------------------------------------------------------------

# Sample text that covers: ASCII, accented chars (UTF-8), quotes, backslashes,
# and JSON control characters that need unescaping.
_TEXT_SAMPLES = [
    "hello",
    "",
    'a string with "quotes" and \\backslashes\\',
    "acentos: ñáéíóú",
    "newline\nand\ttab",
    "unicode \u2603 snowman",
    "x" * 200,
]

# Junk that real LLM output may contain around the JSON object.
_NOISE_PREFIXES = ["", "Here you go: ", "```json\n", "```\n", "  \n\t"]
_NOISE_SUFFIXES = ["", "\n", "\n```", " hope that helps!", "  \n\t"]


def _build_object(text: str) -> str:
    """Build a complete JSON object string from a raw Python text value."""
    payload = {
        "text": text,
        "expression": {"state": "speaking", "mood": "friendly", "mouth": "open"},
    }
    return json.dumps(payload, ensure_ascii=False)


def _make_preview_provider() -> BaseProvider:
    s = _make_settings()
    return HermesProvider(s, _logger())


class TestExtractTextPreviewProperties:
    def test_monotonic_for_ascii_text(self):

        provider = _make_preview_provider()
        for text in _TEXT_SAMPLES:
            obj = _build_object(text)
            previous = ""
            for length in range(0, len(obj) + 1):
                chunk = obj[:length]
                preview = provider._extract_text_preview(chunk)
                if preview is None:
                    continue
                assert preview.startswith(previous), (
                    f"Preview shrank at length {length}: "
                    f"prev={previous!r} new={preview!r} text={text!r}"
                )
                previous = preview
            assert previous == text, f"Final preview mismatch: got {previous!r}, expected {text!r}"

    def test_final_preview_matches_decoded_text(self):
        from hypothesis import given, settings
        from hypothesis import strategies as st

        provider = _make_preview_provider()

        @given(st.text(min_size=0, max_size=80))
        @settings(max_examples=50, deadline=None)
        def prop(text: str) -> None:
            obj = _build_object(text)
            preview = provider._extract_text_preview(obj)
            assert preview is not None
            assert preview == text

        prop()

    def test_ignores_surrounding_junk(self):
        from hypothesis import given, settings
        from hypothesis import strategies as st

        provider = _make_preview_provider()

        @given(
            st.text(min_size=0, max_size=60),
            st.sampled_from(_NOISE_PREFIXES),
            st.sampled_from(_NOISE_SUFFIXES),
        )
        @settings(max_examples=80, deadline=None)
        def prop(text: str, prefix: str, suffix: str) -> None:
            wrapped = prefix + _build_object(text) + suffix
            preview = provider._extract_text_preview(wrapped)
            assert preview is not None
            assert preview == text

        prop()

    def test_returns_none_when_no_object_present(self):
        provider = _make_preview_provider()
        assert provider._extract_text_preview("no json here at all") is None
        assert provider._extract_text_preview("") is None
        assert provider._extract_text_preview("```\nsome prose\n```") is None

    def test_handles_partial_object_at_each_byte(self):
        from hypothesis import given, settings
        from hypothesis import strategies as st

        provider = _make_preview_provider()

        @given(st.text(min_size=1, max_size=40))
        @settings(max_examples=40, deadline=None)
        def prop(text: str) -> None:
            obj = _build_object(text)
            previous = ""
            for length in range(1, len(obj) + 1):
                preview = provider._extract_text_preview(obj[:length])
                if preview is None:
                    continue
                assert preview.startswith(previous)
                previous = preview
            assert previous == text

        prop()
