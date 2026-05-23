import json
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

import httpx

from apps.api.config import Settings
from apps.api.contracts import AssistantPayload, ChatTurn


class ProviderError(Exception):
    """Base exception for LLM provider errors."""


class ProviderConfigurationError(ProviderError):
    """Provider is not configured."""


class ProviderProtocolError(ProviderError):
    """Provider returned an unusable response."""


class BaseProvider(ABC):
    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger

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
        self, transcript: str, locale: str, history: list[ChatTurn]
    ) -> AsyncGenerator[dict, None]:
        if not self.configured:
            raise ProviderConfigurationError(f"{self.provider_name} is not configured")
        buffer = ""
        async for chunk in self._stream_impl(transcript, locale, history):
            buffer += chunk
            yield {"type": "token", "text": chunk}
        assistant = self._parse_content(buffer)
        if not assistant.voice_locale:
            assistant.voice_locale = locale
        yield {"type": "done", "expression": assistant.expression.model_dump(),
               "voice_locale": assistant.voice_locale,
               "action": assistant.action, "full_text": assistant.text}

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
        messages.append({
            "role": "user",
            "content": f"Locale: {locale}\nUser message: {transcript}",
        })
        return messages

    def _parse_content(self, raw_content: str) -> AssistantPayload:
        cleaned = raw_content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.removeprefix("json\n").removeprefix("json")
            cleaned = cleaned.strip()

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

        self.logger.debug("Hermes request model=%s messages=%d", self.settings.hermes_model, len(messages))

        try:
            async with httpx.AsyncClient(timeout=self.settings.hermes_timeout_seconds) as client:
                response = await client.post(self.settings.hermes_chat_url, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self.logger.error("Hermes HTTP error: %s", exc)
            raise ProviderProtocolError(f"Hermes request failed: {exc}") from exc

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                chunks = [item["text"] for item in content if item.get("type") == "text" and item.get("text")]
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
            async with httpx.AsyncClient(timeout=self.settings.hermes_timeout_seconds) as client:
                async with client.stream("POST", self.settings.hermes_chat_url, headers=headers, json=payload) as response:
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

        self.logger.debug("Ollama request model=%s messages=%d", self.settings.ollama_model, len(messages))

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
            async with httpx.AsyncClient(timeout=self.settings.ollama_timeout_seconds) as client:
                async with client.stream("POST", f"{self.settings.ollama_base_url}/api/chat", json=payload) as response:
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
