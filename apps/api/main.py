import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
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
    HistoryResponse,
    ResponseMeta,
    SessionListResponse,
    SessionStartResponse,
    SessionSummary,
    TimingMetrics,
)
from apps.api.database import Database
from apps.api.logging_config import setup_logging
from apps.api.providers import (
    BaseProvider,
    ProviderConfigurationError,
    ProviderProtocolError,
    create_provider,
)
from apps.api.request_context import new_request_id, request_id_var

ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "apps" / "web"

settings: Settings = get_settings()
logger = setup_logging(settings.log_level)


def get_db(request: Request) -> Database:
    db = getattr(request.app.state, "database", None)
    if db is None:
        raise RuntimeError("Application not started")
    return db


def get_provider(request: Request) -> BaseProvider:
    p = getattr(request.app.state, "provider", None)
    if p is None:
        raise RuntimeError("Application not started")
    return p


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(path=settings.database_path, logger=logger.getChild("db"))
    provider = create_provider(settings, logger.getChild("provider"))
    app.state.database = db
    app.state.provider = provider

    Path(db.path).parent.mkdir(parents=True, exist_ok=True)
    await db.connect()
    logger.info(
        "Chirplet starting env=%s provider=%s configured=%s",
        settings.app_env,
        provider.provider_name,
        provider.configured,
    )
    cleanup_task = asyncio.create_task(_cleanup_sessions_periodically(app))
    yield
    cleanup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cleanup_task
    await provider.aclose()
    await db.close()
    app.state.database = None
    app.state.provider = None
    logger.info("Chirplet stopped")


async def _cleanup_sessions_periodically(app: FastAPI) -> None:
    while True:
        try:
            db = app.state.database
            if db is not None:
                deleted = await db.delete_expired_sessions(settings.session_ttl_minutes)
                if deleted:  # pragma: no cover
                    logger.info("Cleaned up %d expired sessions", deleted)
        except Exception:  # pragma: no cover
            logger.exception("Session cleanup failed")
        await asyncio.sleep(settings.session_cleanup_interval_seconds)


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    incoming = request.headers.get("x-request-id")
    rid = incoming.strip()[:64] if incoming else new_request_id()
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_FALLBACK_TEXT = {
    "en": {
        AvatarState.DISCONNECTED: "I can't reach the assistant right now. Please check your configuration.",
        AvatarState.ERROR: "I cannot respond right now.",
    },
    "es": {
        AvatarState.DISCONNECTED: "No puedo conectar con el asistente. Revisa la configuración.",
        AvatarState.ERROR: "No puedo responder en este momento.",
    },
}


def _fallback_response(
    locale: str, state: AvatarState, default_text: str | None = None
) -> AssistantPayload:
    mood = AvatarMood.CONCERNED if state != AvatarState.IDLE else AvatarMood.NEUTRAL
    lang_prefix = (locale or "").split("-", 1)[0].lower()
    text = (
        _FALLBACK_TEXT.get(lang_prefix, {}).get(state)
        or default_text
        or "I cannot respond right now."
    )
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
async def health(provider: BaseProvider = Depends(get_provider)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        provider=provider.provider_name,
        provider_configured=provider.configured,
        session_memory=settings.enable_session_memory,
    )


@app.post("/api/session", response_model=SessionStartResponse)
async def create_session(db: Database = Depends(get_db)) -> SessionStartResponse:
    session_id = str(uuid4())
    await db.create_session(session_id)
    logger.debug("Session created: %s", session_id)
    return SessionStartResponse(session_id=session_id)


@app.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(db: Database = Depends(get_db)) -> SessionListResponse:
    return SessionListResponse(sessions=await db.list_sessions())


@app.get("/api/sessions/{session_id}", response_model=SessionSummary)
async def get_session(session_id: str, db: Database = Depends(get_db)) -> SessionSummary:
    summary = await db.get_session(session_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return summary


@app.get("/api/sessions/{session_id}/turns", response_model=HistoryResponse)
async def get_session_turns(session_id: str, db: Database = Depends(get_db)) -> HistoryResponse:
    if await db.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return HistoryResponse(session_id=session_id, turns=await db.get_turns(session_id))


@app.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, db: Database = Depends(get_db)) -> None:
    if not await db.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")


@app.post("/api/turn", response_model=ConversationTurnResponse)
async def create_turn(
    request: ConversationTurnRequest,
    db: Database = Depends(get_db),
    provider: BaseProvider = Depends(get_provider),
) -> ConversationTurnResponse:
    started_at = _now_iso()
    started_perf = perf_counter()
    session_id = request.session_id or str(uuid4())
    meta = ResponseMeta(provider=provider.provider_name, fallback_used=False)

    await db.ensure_session(session_id)
    history = await db.get_history(session_id, settings.session_turn_limit)

    try:
        assistant = await provider.complete_turn(
            transcript=request.transcript,
            locale=request.locale,
            history=history,
        )
        await db.save_turn(session_id, request.transcript, assistant.text)
        logger.info("Turn completed session=%s provider=%s", session_id[:8], provider.provider_name)
    except ProviderConfigurationError as exc:
        logger.warning("Provider not configured: %s", exc)
        assistant = _fallback_response(
            locale=request.locale,
            state=AvatarState.DISCONNECTED,
        )
        meta = ResponseMeta(provider=provider.provider_name, fallback_used=True, issue=str(exc))
    except ProviderProtocolError as exc:
        logger.error("Provider protocol error: %s", exc)
        assistant = _fallback_response(
            locale=request.locale,
            state=AvatarState.ERROR,
        )
        meta = ResponseMeta(provider=provider.provider_name, fallback_used=True, issue=str(exc))

    completed_at = _now_iso()
    duration_ms = int((perf_counter() - started_perf) * 1000)
    logger.info(
        "Turn finished session=%s provider=%s fallback=%s duration_ms=%d",
        session_id,
        provider.provider_name,
        meta.fallback_used,
        duration_ms,
    )

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


@app.post("/api/turn/stream")
async def create_turn_stream(
    turn_request: ConversationTurnRequest,
    http_request: Request,
    db: Database = Depends(get_db),
    provider: BaseProvider = Depends(get_provider),
):
    session_id = turn_request.session_id or str(uuid4())

    await db.ensure_session(session_id)
    history = await db.get_history(session_id, settings.session_turn_limit)

    async def event_stream():
        try:
            async for event in provider.stream_turn(
                transcript=turn_request.transcript,
                locale=turn_request.locale,
                history=history,
                should_cancel=http_request.is_disconnected,
            ):
                if event["type"] == "token":
                    yield f"data: {json.dumps({'type': 'token', 'text': event['text']})}\n\n"
                elif event["type"] == "done":
                    await db.save_turn(session_id, turn_request.transcript, event["full_text"])
                    done_data = json.dumps(
                        {
                            "type": "done",
                            "session_id": session_id,
                            "text": event["full_text"],
                            "expression": event["expression"],
                            "voice_locale": event["voice_locale"],
                            "action": event["action"],
                        }
                    )
                    yield f"data: {done_data}\n\n"
                    logger.info("Stream turn finished session=%s", session_id[:8])
        except ProviderConfigurationError as exc:
            fb = _fallback_response(turn_request.locale, AvatarState.DISCONNECTED)
            yield f"data: {json.dumps({'type': 'error', 'text': fb.text, 'session_id': session_id, 'issue': str(exc)})}\n\n"
        except ProviderProtocolError as exc:
            fb = _fallback_response(turn_request.locale, AvatarState.ERROR)
            yield f"data: {json.dumps({'type': 'error', 'text': fb.text, 'session_id': session_id, 'issue': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
