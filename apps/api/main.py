import asyncio
import contextlib
import hmac
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from apps.api.config import Settings, get_settings
from apps.api.contracts import (
    AssistantPayload,
    AvatarExpression,
    AvatarMood,
    AvatarState,
    BulkExportResponse,
    ConversationTurnRequest,
    ConversationTurnResponse,
    ExportTurn,
    HealthResponse,
    HistoryResponse,
    ImportResponse,
    ResponseMeta,
    SessionExport,
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
from apps.api.rate_limit import RateLimiter
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
    app.state.auth_token = settings.auth_token
    app.state.rate_limiter = (
        RateLimiter(settings.rate_limit_per_minute) if settings.rate_limit_per_minute > 0 else None
    )

    Path(db.path).parent.mkdir(parents=True, exist_ok=True)
    await db.connect()
    logger.info(
        "Chirplet starting env=%s provider=%s configured=%s auth=%s rate_limit=%s",
        settings.app_env,
        provider.provider_name,
        provider.configured,
        bool(settings.auth_token),
        settings.rate_limit_per_minute,
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
            limiter = getattr(app.state, "rate_limiter", None)
            if limiter is not None:
                limiter.cleanup()
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
_allow_credentials = _cors_origins != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ---------------------------------------------------------------------------
# Auth + rate-limiting guard
# ---------------------------------------------------------------------------


async def api_guard(request: Request) -> None:
    """Combined bearer-token auth and per-IP rate limiter.

    When ``AUTH_TOKEN`` is unset, auth is disabled. When
    ``RATE_LIMIT_PER_MINUTE`` is 0, rate limiting is disabled.
    Both are no-ops by default so local-first deployments keep
    zero-friction defaults.
    """
    auth_token = getattr(request.app.state, "auth_token", "")
    if auth_token:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        if not hmac.compare_digest(token, auth_token):
            raise HTTPException(status_code=401, detail="Invalid or missing authentication token")

    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is not None:
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.check(client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sse_event(payload: dict) -> str:
    """Format a single Server-Sent Event frame.

    The trailing blank line is required by the SSE spec to
    terminate a frame; without it, the client keeps reading
    the previous event.
    """
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
    mood = AvatarMood.CONCERNED
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


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


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
async def create_session(
    db: Database = Depends(get_db),
    _: None = Depends(api_guard),
) -> SessionStartResponse:
    session_id = str(uuid4())
    await db.ensure_session(session_id)
    logger.debug("Session created: %s", session_id)
    return SessionStartResponse(session_id=session_id)


@app.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(
    db: Database = Depends(get_db),
    _: None = Depends(api_guard),
) -> SessionListResponse:
    return SessionListResponse(sessions=await db.list_sessions())


@app.get("/api/sessions/{session_id}", response_model=SessionSummary)
async def get_session(
    session_id: str,
    db: Database = Depends(get_db),
    _: None = Depends(api_guard),
) -> SessionSummary:
    summary = await db.get_session(session_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return summary


@app.get("/api/sessions/{session_id}/turns", response_model=HistoryResponse)
async def get_session_turns(
    session_id: str,
    db: Database = Depends(get_db),
    _: None = Depends(api_guard),
) -> HistoryResponse:
    if await db.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return HistoryResponse(session_id=session_id, turns=await db.get_turns(session_id))


@app.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    db: Database = Depends(get_db),
    _: None = Depends(api_guard),
) -> None:
    if not await db.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")


@app.get("/api/sessions/{session_id}/export", response_model=SessionExport)
async def export_session(
    session_id: str,
    db: Database = Depends(get_db),
    _: None = Depends(api_guard),
) -> SessionExport:
    summary = await db.get_session(session_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Session not found")
    turns = await db.get_turns(session_id)
    return SessionExport(
        session_id=summary.session_id,
        created_at=summary.created_at,
        last_active_at=summary.last_active_at,
        turn_count=summary.turn_count,
        turns=[ExportTurn(**t.model_dump()) for t in turns],
    )


@app.get("/api/export/all", response_model=BulkExportResponse)
async def export_all(
    db: Database = Depends(get_db),
    _: None = Depends(api_guard),
) -> BulkExportResponse:
    sessions = await db.list_sessions(limit=100000)
    result: list[SessionExport] = []
    for s in sessions:
        turns = await db.get_turns(s.session_id)
        result.append(
            SessionExport(
                session_id=s.session_id,
                created_at=s.created_at,
                last_active_at=s.last_active_at,
                turn_count=s.turn_count,
                turns=[ExportTurn(**t.model_dump()) for t in turns],
            )
        )
    return BulkExportResponse(
        exported_at=_now_iso(),
        schema_version=1,
        sessions=result,
    )


@app.post("/api/import", response_model=ImportResponse)
async def import_sessions(
    payload: BulkExportResponse,
    db: Database = Depends(get_db),
    _: None = Depends(api_guard),
) -> ImportResponse:
    imported_sessions = 0
    imported_turns = 0
    skipped_sessions = 0
    for session in payload.sessions:
        turns = [{"user": t.user, "assistant": t.assistant} for t in session.turns]
        if await db.import_session(session.session_id, turns):
            imported_sessions += 1
            imported_turns += len(turns)
        else:
            skipped_sessions += 1
    return ImportResponse(
        imported_sessions=imported_sessions,
        imported_turns=imported_turns,
        skipped_sessions=skipped_sessions,
    )


@app.post("/api/turn", response_model=ConversationTurnResponse)
async def create_turn(
    request: ConversationTurnRequest,
    db: Database = Depends(get_db),
    provider: BaseProvider = Depends(get_provider),
    _: None = Depends(api_guard),
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
        try:
            await db.save_turn(session_id, request.transcript, assistant.text)
        except Exception:
            logger.exception("Failed to persist turn; response is still returned")
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
    _: None = Depends(api_guard),
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
                    yield _sse_event({"type": "token", "text": event["text"]})
                elif event["type"] == "done":
                    try:
                        await db.save_turn(session_id, turn_request.transcript, event["full_text"])
                    except Exception:
                        logger.exception("Failed to persist streamed turn")
                    yield _sse_event(
                        {
                            "type": "done",
                            "session_id": session_id,
                            "text": event["full_text"],
                            "expression": event["expression"],
                            "voice_locale": event["voice_locale"],
                            "action": event["action"],
                        }
                    )
                    logger.info("Stream turn finished session=%s", session_id[:8])
        except ProviderConfigurationError as exc:
            fb = _fallback_response(turn_request.locale, AvatarState.DISCONNECTED)
            yield _sse_event(
                {
                    "type": "error",
                    "text": fb.text,
                    "session_id": session_id,
                    "issue": str(exc),
                }
            )
        except ProviderProtocolError as exc:
            fb = _fallback_response(turn_request.locale, AvatarState.ERROR)
            yield _sse_event(
                {
                    "type": "error",
                    "text": fb.text,
                    "session_id": session_id,
                    "issue": str(exc),
                }
            )
        except Exception:
            logger.exception("Unexpected error in stream")
            fb = _fallback_response(turn_request.locale, AvatarState.ERROR)
            yield _sse_event(
                {
                    "type": "error",
                    "text": fb.text,
                    "session_id": session_id,
                }
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    db: Database = Depends(get_db),
    provider: BaseProvider = Depends(get_provider),
):
    auth_token = getattr(websocket.app.state, "auth_token", "")
    if auth_token:
        token = ""
        auth_header = websocket.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        elif "token" in websocket.query_params:
            token = websocket.query_params["token"]
        if not hmac.compare_digest(token, auth_token):
            await websocket.close(code=4001, reason="Unauthorized")
            return

    limiter = getattr(websocket.app.state, "rate_limiter", None)
    if limiter is not None:
        client_ip = websocket.client.host if websocket.client else "unknown"
        if not limiter.check(client_ip):
            await websocket.close(code=4002, reason="Rate limited")
            return

    await websocket.accept()
    await websocket.send_json(
        {
            "type": "connected",
            "provider": provider.provider_name,
            "configured": provider.configured,
        }
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "text": "Invalid JSON message"})
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type == "turn":
                await _handle_ws_turn(websocket, msg, db, provider)
    except WebSocketDisconnect:
        pass


async def _handle_ws_turn(
    websocket: WebSocket,
    msg: dict,
    db: Database,
    provider: BaseProvider,
) -> None:
    session_id = msg.get("session_id") or str(uuid4())
    transcript = (msg.get("transcript") or "").strip()[:500]
    locale = msg.get("locale", "es-ES")

    if not transcript:
        await websocket.send_json(
            {"type": "error", "text": "Empty transcript", "session_id": session_id}
        )
        return

    await db.ensure_session(session_id)
    history = await db.get_history(session_id, settings.session_turn_limit)

    cancel_event = asyncio.Event()

    async def listen_for_interrupt() -> None:
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if parsed.get("type") == "interrupt":
                    cancel_event.set()
                    return
        except WebSocketDisconnect:
            cancel_event.set()

    listener = asyncio.create_task(listen_for_interrupt())

    try:

        async def should_cancel() -> bool:
            return cancel_event.is_set()

        async for event in provider.stream_turn(
            transcript=transcript,
            locale=locale,
            history=history,
            should_cancel=should_cancel,
        ):
            if cancel_event.is_set():
                break

            if event["type"] == "token":
                await websocket.send_json({"type": "token", "text": event["text"]})
            elif event["type"] == "done":
                try:
                    await db.save_turn(session_id, transcript, event["full_text"])
                except Exception:
                    logger.exception("Failed to persist WS turn")
                await websocket.send_json(
                    {
                        "type": "done",
                        "session_id": session_id,
                        "text": event["full_text"],
                        "expression": event["expression"],
                        "voice_locale": event["voice_locale"],
                        "action": event["action"],
                    }
                )
                logger.info("WS turn finished session=%s", session_id[:8])
    except ProviderConfigurationError as exc:
        fb = _fallback_response(locale, AvatarState.DISCONNECTED)
        await websocket.send_json(
            {
                "type": "error",
                "text": fb.text,
                "session_id": session_id,
                "issue": str(exc),
            }
        )
    except ProviderProtocolError as exc:
        fb = _fallback_response(locale, AvatarState.ERROR)
        await websocket.send_json(
            {
                "type": "error",
                "text": fb.text,
                "session_id": session_id,
                "issue": str(exc),
            }
        )
    except Exception:
        logger.exception("Unexpected error in WS turn")
        fb = _fallback_response(locale, AvatarState.ERROR)
        await websocket.send_json(
            {
                "type": "error",
                "text": fb.text,
                "session_id": session_id,
            }
        )
    finally:
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener
