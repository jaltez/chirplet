import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from apps.api.prompting import DEFAULT_SYSTEM_PROMPT

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

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


def _resolve_db_path(raw_path: str) -> Path:
    path = Path(raw_path.strip())
    if path.is_absolute():
        return path
    return (_PROJECT_ROOT / path).resolve()


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    app_host: str
    app_port: int
    log_level: str
    llm_provider: str
    llm_temperature: float
    hermes_base_url: str
    hermes_api_key: str | None
    hermes_model: str
    hermes_timeout_seconds: float
    hermes_response_format: str
    ollama_base_url: str
    ollama_model: str
    ollama_timeout_seconds: float
    enable_session_memory: bool
    session_turn_limit: int
    session_ttl_minutes: int
    session_cleanup_interval_seconds: int
    database_path: Path
    cors_origins: str
    system_prompt: str

    @property
    def hermes_configured(self) -> bool:
        return bool(self.hermes_base_url and self.hermes_model)

    @property
    def provider_configured(self) -> bool:
        if self.llm_provider == "ollama":
            return bool(self.ollama_base_url and self.ollama_model)
        return self.hermes_configured

    @property
    def hermes_chat_url(self) -> str:
        if not self.hermes_base_url:
            raise RuntimeError("Hermes endpoint is not configured")
        return f"{self.hermes_base_url}/chat/completions"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "Chirplet"),
        app_env=os.getenv("APP_ENV", "development"),
        app_host=os.getenv("APP_HOST", "127.0.0.1"),
        app_port=int(os.getenv("APP_PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        llm_provider=os.getenv("LLM_PROVIDER", "hermes").strip().lower(),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.4")),
        hermes_base_url=_normalize_base_url(os.getenv("HERMES_BASE_URL", "")),
        hermes_api_key=os.getenv("HERMES_API_KEY") or None,
        hermes_model=os.getenv("HERMES_MODEL", "").strip(),
        hermes_timeout_seconds=float(os.getenv("HERMES_TIMEOUT_SECONDS", "45")),
        hermes_response_format=os.getenv("HERMES_RESPONSE_FORMAT", "none").strip().lower(),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "").strip(),
        ollama_timeout_seconds=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60")),
        enable_session_memory=_read_bool("ENABLE_SESSION_MEMORY", True),
        session_turn_limit=max(1, int(os.getenv("SESSION_TURN_LIMIT", "8"))),
        session_ttl_minutes=max(1, int(os.getenv("SESSION_TTL_MINUTES", "60"))),
        session_cleanup_interval_seconds=max(30, int(os.getenv("SESSION_CLEANUP_INTERVAL_SECONDS", "300"))),
        database_path=_resolve_db_path(os.getenv("DATABASE_PATH", "data/chirplet.db")),
        cors_origins=os.getenv("CORS_ORIGINS", "*"),
        system_prompt=os.getenv("HERMES_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT).strip(),
    )
