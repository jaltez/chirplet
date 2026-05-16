import json
from dataclasses import dataclass
from typing import Any

import httpx

from apps.api.config import Settings
from apps.api.contracts import AssistantPayload


class HermesError(Exception):
    """Base exception for Hermes integration errors."""


class HermesConfigurationError(HermesError):
    """Raised when Hermes connection settings are incomplete."""


class HermesProtocolError(HermesError):
    """Raised when Hermes returns an unusable response."""


@dataclass(frozen=True)
class ChatTurn:
    user: str
    assistant: str


class HermesClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def complete_turn(
        self,
        transcript: str,
        locale: str,
        history: list[ChatTurn],
    ) -> AssistantPayload:
        if not self.settings.hermes_configured:
            raise HermesConfigurationError("Hermes is not configured")

        payload = self._build_payload(transcript=transcript, locale=locale, history=history)
        headers = {"Content-Type": "application/json"}
        if self.settings.hermes_api_key:
            headers["Authorization"] = f"Bearer {self.settings.hermes_api_key}"

        try:
            async with httpx.AsyncClient(timeout=self.settings.hermes_timeout_seconds) as client:
                response = await client.post(self.settings.hermes_chat_url, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HermesProtocolError(f"Hermes request failed: {exc}") from exc

        try:
            content = self._extract_message_content(response.json())
            assistant = self._parse_content(content)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HermesProtocolError("Hermes response could not be parsed") from exc

        if not assistant.voice_locale:
            assistant.voice_locale = locale
        return assistant

    def _build_payload(self, transcript: str, locale: str, history: list[ChatTurn]) -> dict[str, Any]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.settings.system_prompt},
        ]

        if self.settings.enable_session_memory:
            for turn in history[-self.settings.session_turn_limit :]:
                messages.append({"role": "user", "content": turn.user})
                messages.append({"role": "assistant", "content": turn.assistant})

        messages.append(
            {
                "role": "user",
                "content": f"Locale: {locale}\nUser message: {transcript}",
            }
        )

        payload: dict[str, Any] = {
            "model": self.settings.hermes_model,
            "messages": messages,
            "temperature": 0.4,
        }

        if self.settings.hermes_response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        return payload

    def _extract_message_content(self, payload: dict[str, Any]) -> str:
        message = payload["choices"][0]["message"]
        content = message["content"]

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if item.get("type") == "text" and item.get("text"):
                    chunks.append(item["text"])
            if chunks:
                return "".join(chunks)

        raise ValueError("Unsupported message content format")

    def _parse_content(self, raw_content: str) -> AssistantPayload:
        cleaned = raw_content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in Hermes response")

        payload = json.loads(cleaned[start : end + 1])
        return AssistantPayload.model_validate(payload)
