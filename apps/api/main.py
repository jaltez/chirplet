from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.api.config import Settings, get_settings
from apps.api.contracts import (
    AssistantPayload,
    AvatarExpression,
    AvatarMood,
    AvatarState,
    ConversationTurnRequest,
    ConversationTurnResponse,
    HealthResponse,
    ResponseMeta,
    SessionStartResponse,
    TimingMetrics,
)
from apps.api.hermes import ChatTurn, HermesClient, HermesConfigurationError, HermesProtocolError

ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "apps" / "web"


@dataclass
class SessionState:
    history: deque[ChatTurn] = field(default_factory=deque)


settings: Settings = get_settings()
hermes_client = HermesClient(settings=settings)
session_store: dict[str, SessionState] = {}

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_or_create_session(session_id: str | None) -> tuple[str, SessionState]:
    current_session_id = session_id or str(uuid4())
    session = session_store.get(current_session_id)
    if session is None:
        session = SessionState()
        session_store[current_session_id] = session
    return current_session_id, session


def _trim_history(session: SessionState) -> None:
    while len(session.history) > settings.session_turn_limit:
        session.history.popleft()


def _fallback_response(locale: str, state: AvatarState, text: str) -> AssistantPayload:
    mood = AvatarMood.CONCERNED if state != AvatarState.IDLE else AvatarMood.NEUTRAL
    return AssistantPayload(
        text=text,
        voice_locale=locale,
        action="idle",
        expression=AvatarExpression(state=state, mood=mood, mouth="closed"),
    )


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        hermes_configured=settings.hermes_configured,
        session_memory=settings.enable_session_memory,
    )


@app.post("/api/session", response_model=SessionStartResponse)
async def create_session() -> SessionStartResponse:
    session_id, _session = _get_or_create_session(None)
    return SessionStartResponse(session_id=session_id)


@app.post("/api/turn", response_model=ConversationTurnResponse)
async def create_turn(request: ConversationTurnRequest) -> ConversationTurnResponse:
    started_at = _now_iso()
    started_perf = perf_counter()
    session_id, session = _get_or_create_session(request.session_id)

    meta = ResponseMeta(provider="hermes", fallback_used=False)

    try:
        assistant = await hermes_client.complete_turn(
            transcript=request.transcript,
            locale=request.locale,
            history=list(session.history),
        )
        session.history.append(ChatTurn(user=request.transcript, assistant=assistant.text))
        _trim_history(session)
    except HermesConfigurationError as exc:
        assistant = _fallback_response(
            locale=request.locale,
            state=AvatarState.DISCONNECTED,
            text="Hermes is not configured yet.",
        )
        meta = ResponseMeta(provider="hermes", fallback_used=True, issue=str(exc))
    except HermesProtocolError as exc:
        assistant = _fallback_response(
            locale=request.locale,
            state=AvatarState.ERROR,
            text="I cannot respond right now.",
        )
        meta = ResponseMeta(provider="hermes", fallback_used=True, issue=str(exc))

    completed_at = _now_iso()
    duration_ms = int((perf_counter() - started_perf) * 1000)

    return ConversationTurnResponse(
        session_id=session_id,
        assistant=assistant,
        timing=TimingMetrics(
            request_started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        ),
        meta=meta,
    )
