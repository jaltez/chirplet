import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

from apps.api.prompting import DEFAULT_SYSTEM_PROMPT

load_dotenv()


def _read_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_base_url(raw_value: str) -> str:
    value = raw_value.strip().rstrip("/")
    if not value:
        return ""
    if value.endswith("/v1"):
        return value
    return f"{value}/v1"


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    app_host: str
    app_port: int
    hermes_base_url: str
    hermes_api_key: str | None
    hermes_model: str
    hermes_timeout_seconds: float
    hermes_response_format: str
    enable_session_memory: bool
    session_turn_limit: int
    system_prompt: str

    @property
    def hermes_configured(self) -> bool:
        return bool(self.hermes_base_url and self.hermes_model)

    @property
    def hermes_chat_url(self) -> str:
        if not self.hermes_configured:
            raise RuntimeError("Hermes endpoint is not configured")
        return f"{self.hermes_base_url}/chat/completions"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "Chirplet"),
        app_env=os.getenv("APP_ENV", "development"),
        app_host=os.getenv("APP_HOST", "127.0.0.1"),
        app_port=int(os.getenv("APP_PORT", "8000")),
        hermes_base_url=_normalize_base_url(os.getenv("HERMES_BASE_URL", "")),
        hermes_api_key=os.getenv("HERMES_API_KEY") or None,
        hermes_model=os.getenv("HERMES_MODEL", "").strip(),
        hermes_timeout_seconds=float(os.getenv("HERMES_TIMEOUT_SECONDS", "45")),
        hermes_response_format=os.getenv("HERMES_RESPONSE_FORMAT", "none").strip().lower(),
        enable_session_memory=_read_bool("ENABLE_SESSION_MEMORY", True),
        session_turn_limit=max(1, int(os.getenv("SESSION_TURN_LIMIT", "8"))),
        system_prompt=os.getenv("HERMES_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT).strip(),
    )
