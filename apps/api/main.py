import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from apps.api.config import Settings, get_settings
from apps.api.contracts import (
    AssistantPayload,
    AvatarExpression,
    AvatarMood,
    AvatarState,
    ChatTurn,
    ConversationTurnRequest,
    ConversationTurnResponse,
    HealthResponse,
    ResponseMeta,
    SessionStartResponse,
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

ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "apps" / "web"

settings: Settings = get_settings()
logger = setup_logging(settings.log_level)
database = Database(path=str(settings.database_path), logger=logger.getChild("db"))
provider: BaseProvider = create_provider(settings, logger.getChild("provider"))


async def _cleanup_sessions_periodically() -> None:
    while True:
        try:
            deleted = await database.delete_expired_sessions(settings.session_ttl_minutes)
            if deleted:
                logger.info("Cleaned up %d expired sessions", deleted)
        except Exception:
            logger.exception("Session cleanup failed")
        await asyncio.sleep(settings.session_cleanup_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.path.parent.mkdir(parents=True, exist_ok=True)
    await database.connect()
    logger.info("Chirplet starting env=%s provider=%s configured=%s",
                settings.app_env, provider.provider_name, provider.configured)
    cleanup_task = asyncio.create_task(_cleanup_sessions_periodically())
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    await database.close()
    logger.info("Chirplet stopped")


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        provider=provider.provider_name,
        provider_configured=provider.configured,
        session_memory=settings.enable_session_memory,
    )


@app.post("/api/session", response_model=SessionStartResponse)
async def create_session() -> SessionStartResponse:
    session_id = str(uuid4())
    await database.create_session(session_id)
    logger.debug("Session created: %s", session_id)
    return SessionStartResponse(session_id=session_id)


@app.post("/api/turn", response_model=ConversationTurnResponse)
async def create_turn(request: ConversationTurnRequest) -> ConversationTurnResponse:
    started_at = _now_iso()
    started_perf = perf_counter()
    session_id = request.session_id or str(uuid4())
    meta = ResponseMeta(provider=provider.provider_name, fallback_used=False)

    await database.create_session(session_id)
    await database.touch_session(session_id)
    history = await database.get_history(session_id, settings.session_turn_limit)

    try:
        assistant = await provider.complete_turn(
            transcript=request.transcript,
            locale=request.locale,
            history=history,
        )
        await database.save_turn(session_id, request.transcript, assistant.text)
        logger.info("Turn completed session=%s provider=%s",
                     session_id[:8], provider.provider_name)
    except ProviderConfigurationError as exc:
        logger.warning("Provider not configured: %s", exc)
        assistant = _fallback_response(
            locale=request.locale,
            state=AvatarState.DISCONNECTED,
            text=f"LLM provider '{provider.provider_name}' is not configured yet. Set it in .env",
        )
        meta = ResponseMeta(provider=provider.provider_name, fallback_used=True, issue=str(exc))
    except ProviderProtocolError as exc:
        logger.error("Provider protocol error: %s", exc)
        assistant = _fallback_response(
            locale=request.locale,
            state=AvatarState.ERROR,
            text="I cannot respond right now.",
        )
        meta = ResponseMeta(provider=provider.provider_name, fallback_used=True, issue=str(exc))

    completed_at = _now_iso()
    duration_ms = int((perf_counter() - started_perf) * 1000)
    logger.info("Turn finished session=%s provider=%s fallback=%s duration_ms=%d",
                session_id, provider.provider_name, meta.fallback_used, duration_ms)

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
async def create_turn_stream(request: ConversationTurnRequest):
    session_id = request.session_id or str(uuid4())

    await database.create_session(session_id)
    await database.touch_session(session_id)
    history = await database.get_history(session_id, settings.session_turn_limit)

    async def event_stream():
        full_text = ""
        try:
            async for event in provider.stream_turn(
                transcript=request.transcript,
                locale=request.locale,
                history=history,
            ):
                if event["type"] == "token":
                    full_text += event["text"]
                    yield f"data: {json.dumps({'type': 'token', 'text': event['text']})}\n\n"
                elif event["type"] == "done":
                    await database.save_turn(session_id, request.transcript, event["full_text"])
                    done_data = json.dumps({
                        "type": "done",
                        "session_id": session_id,
                        "expression": event["expression"],
                        "voice_locale": event["voice_locale"],
                        "action": event["action"],
                    })
                    yield f"data: {done_data}\n\n"
                    logger.info("Stream turn finished session=%s", session_id[:8])
        except ProviderConfigurationError as exc:
            fb = _fallback_response(request.locale, AvatarState.DISCONNECTED,
                f"LLM provider '{provider.provider_name}' is not configured yet.")
            yield f"data: {json.dumps({'type': 'error', 'text': fb.text, 'session_id': session_id})}\n\n"
        except ProviderProtocolError as exc:
            fb = _fallback_response(request.locale, AvatarState.ERROR, "I cannot respond right now.")
            yield f"data: {json.dumps({'type': 'error', 'text': fb.text, 'session_id': session_id})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
