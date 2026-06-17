import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import httpx

from apps.api.config import Settings
from apps.api.contracts import AssistantPayload, ChatTurn


class ProviderError(Exception):
    """Base exception for LLM provider errors."""


class ProviderConfigurationError(ProviderError):
    """Provider is not configured."""


class ProviderProtocolError(ProviderError):
    """Provider returned an unusable response."""


TEXT_FIELD_PATTERN = re.compile(r'[{,]\s*"text"\s*:\s*"')


class BaseProvider(ABC):
    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self._client: httpx.AsyncClient | None = None

    async def get_client(self, timeout: float) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:  # pragma: no cover
            await self._client.aclose()
        self._client = None

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    async def complete_turn(
        self, transcript: str, locale: str, history: list[ChatTurn]
    ) -> AssistantPayload:
        if not self.configured:
            raise ProviderConfigurationError(f"{self.provider_name} is not configured")
        assistant = await self._complete_impl(transcript, locale, history)
        if not assistant.voice_locale:
            assistant.voice_locale = locale
        return assistant

    async def stream_turn(
        self,
        transcript: str,
        locale: str,
        history: list[ChatTurn],
        should_cancel: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncGenerator[dict, None]:
        if not self.configured:  # pragma: no cover
            raise ProviderConfigurationError(f"{self.provider_name} is not configured")
        buffer = ""
        streamed_text = ""
        async for chunk in self._stream_impl(transcript, locale, history):
            if should_cancel is not None and await should_cancel():
                self.logger.info("%s stream cancelled by client", self.provider_name)
                return
            buffer += chunk
            preview = self._extract_text_preview(buffer)
            if preview is not None and preview.startswith(streamed_text):
                delta = preview[len(streamed_text) :]
                if delta:
                    streamed_text = preview
                    yield {"type": "token", "text": delta}
        if should_cancel is not None and await should_cancel():  # pragma: no cover
            self.logger.info("%s stream cancelled by client", self.provider_name)
            return
        assistant = self._parse_content(buffer)
        if not assistant.voice_locale:  # pragma: no cover
            assistant.voice_locale = locale
        if assistant.text.startswith(streamed_text):
            delta = assistant.text[len(streamed_text) :]
            if delta:  # pragma: no cover
                yield {"type": "token", "text": delta}
        yield {
            "type": "done",
            "expression": assistant.expression.model_dump(),
            "voice_locale": assistant.voice_locale,
            "action": assistant.action,
            "full_text": assistant.text,
        }

    @abstractmethod
    async def _complete_impl(
        self, transcript: str, locale: str, history: list[ChatTurn]
    ) -> AssistantPayload: ...

    @abstractmethod
    async def _stream_impl(
        self, transcript: str, locale: str, history: list[ChatTurn]
    ) -> AsyncGenerator[str, None]: ...

    def _build_messages(
        self, transcript: str, locale: str, history: list[ChatTurn]
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.settings.system_prompt},
        ]
        if self.settings.enable_session_memory:
            for turn in history[-self.settings.session_turn_limit :]:
                messages.append({"role": "user", "content": turn.user})
                messages.append({"role": "assistant", "content": turn.assistant})
        user_payload = json.dumps(
            {"locale": locale, "transcript": transcript},
            ensure_ascii=False,
        )
        messages.append(
            {
                "role": "user",
                "content": user_payload,
            }
        )
        return messages

    def _parse_content(self, raw_content: str) -> AssistantPayload:
        cleaned = self._clean_content(raw_content)

        try:
            payload = json.loads(cleaned)
            return AssistantPayload.model_validate(payload)
        except (json.JSONDecodeError, ValueError):
            pass

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in response")
        payload = json.loads(cleaned[start : end + 1])
        return AssistantPayload.model_validate(payload)

    def _clean_content(self, raw_content: str) -> str:
        cleaned = raw_content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.removeprefix("json\n").removeprefix("json")
            cleaned = cleaned.strip()
        return cleaned

    def _extract_text_preview(self, raw_content: str) -> str | None:
        cleaned = self._clean_content(raw_content)
        object_start = cleaned.find("{")
        if object_start == -1:
            return None

        text_match = TEXT_FIELD_PATTERN.search(cleaned[object_start:])
        if text_match is None:
            return None

        value_start = object_start + text_match.end()
        value_end = value_start
        escaped = False

        while value_end < len(cleaned):
            char = cleaned[value_end]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                break
            value_end += 1

        return self._decode_json_string_prefix(cleaned[value_start:value_end])

    def _decode_json_string_prefix(self, raw_value: str) -> str:
        escape_map = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }

        decoded: list[str] = []
        index = 0
        while index < len(raw_value):
            char = raw_value[index]
            if char != "\\":
                decoded.append(char)
                index += 1
                continue

            if index + 1 >= len(raw_value):
                break

            escape_code = raw_value[index + 1]
            if escape_code in escape_map:
                decoded.append(escape_map[escape_code])
                index += 2
                continue

            if escape_code == "u":
                unicode_value = raw_value[index + 2 : index + 6]
                if len(unicode_value) < 4 or not all(
                    c in "0123456789abcdefABCDEF" for c in unicode_value
                ):
                    break
                decoded.append(chr(int(unicode_value, 16)))
                index += 6
                continue

            break  # pragma: no cover

        return "".join(decoded)


class HermesProvider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "hermes"

    @property
    def configured(self) -> bool:
        return self.settings.hermes_configured

    async def _complete_impl(
        self, transcript: str, locale: str, history: list[ChatTurn]
    ) -> AssistantPayload:
        messages = self._build_messages(transcript, locale, history)
        payload: dict[str, Any] = {
            "model": self.settings.hermes_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
        }
        if self.settings.hermes_response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.settings.hermes_api_key:
            headers["Authorization"] = f"Bearer {self.settings.hermes_api_key}"

        self.logger.debug(
            "Hermes request model=%s messages=%d", self.settings.hermes_model, len(messages)
        )

        try:
            async with httpx.AsyncClient(timeout=self.settings.hermes_timeout_seconds) as client:
                response = await client.post(
                    self.settings.hermes_chat_url, headers=headers, json=payload
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self.logger.error("Hermes HTTP error: %s", exc)
            raise ProviderProtocolError(f"Hermes request failed: {exc}") from exc

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                chunks = [
                    item["text"]
                    for item in content
                    if item.get("type") == "text" and item.get("text")
                ]
                content = "".join(chunks) if chunks else ""
            if not isinstance(content, str) or not content:
                raise ValueError("Empty or unsupported content format")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            self.logger.error("Hermes response extraction failed: %s", exc)
            raise ProviderProtocolError("Hermes response structure unexpected") from exc

        return self._parse_content(content)

    async def _stream_impl(
        self, transcript: str, locale: str, history: list[ChatTurn]
    ) -> AsyncGenerator[str, None]:
        messages = self._build_messages(transcript, locale, history)
        payload: dict[str, Any] = {
            "model": self.settings.hermes_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "stream": True,
        }
        headers = {"Content-Type": "application/json"}
        if self.settings.hermes_api_key:
            headers["Authorization"] = f"Bearer {self.settings.hermes_api_key}"

        self.logger.debug("Hermes stream request model=%s", self.settings.hermes_model)

        try:
            async with httpx.AsyncClient(timeout=self.settings.hermes_timeout_seconds) as client:  # noqa: SIM117
                async with client.stream(
                    "POST", self.settings.hermes_chat_url, headers=headers, json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except httpx.HTTPError as exc:
            self.logger.error("Hermes stream error: %s", exc)
            raise ProviderProtocolError(f"Hermes stream failed: {exc}") from exc


class OllamaProvider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def configured(self) -> bool:
        return bool(self.settings.ollama_base_url and self.settings.ollama_model)

    async def _complete_impl(
        self, transcript: str, locale: str, history: list[ChatTurn]
    ) -> AssistantPayload:
        messages = self._build_messages(transcript, locale, history)
        payload: dict[str, Any] = {
            "model": self.settings.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.settings.llm_temperature},
            "format": "json",
        }

        self.logger.debug(
            "Ollama request model=%s messages=%d", self.settings.ollama_model, len(messages)
        )

        try:
            async with httpx.AsyncClient(timeout=self.settings.ollama_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.ollama_base_url}/api/chat",
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self.logger.error("Ollama HTTP error: %s", exc)
            raise ProviderProtocolError(f"Ollama request failed: {exc}") from exc

        try:
            body = response.json()
            content = body["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty Ollama response content")
        except (KeyError, TypeError, ValueError) as exc:
            self.logger.error("Ollama response extraction failed: %s", exc)
            raise ProviderProtocolError("Ollama response structure unexpected") from exc

        return self._parse_content(content)

    async def _stream_impl(
        self, transcript: str, locale: str, history: list[ChatTurn]
    ) -> AsyncGenerator[str, None]:
        messages = self._build_messages(transcript, locale, history)
        payload: dict[str, Any] = {
            "model": self.settings.ollama_model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": self.settings.llm_temperature},
            "format": "json",
        }

        self.logger.debug("Ollama stream request model=%s", self.settings.ollama_model)

        try:
            client = await self.get_client(self.settings.ollama_timeout_seconds)
            async with client.stream(
                "POST", f"{self.settings.ollama_base_url}/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
                        if chunk.get("done", False):
                            break
                    except json.JSONDecodeError:
                        continue
        except httpx.HTTPError as exc:
            self.logger.error("Ollama stream error: %s", exc)
            raise ProviderProtocolError(f"Ollama stream failed: {exc}") from exc


def create_provider(settings: Settings, logger: logging.Logger) -> BaseProvider:
    if settings.llm_provider == "ollama":
        return OllamaProvider(settings, logger)
    return HermesProvider(settings, logger)
