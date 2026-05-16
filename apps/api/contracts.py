from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AvatarState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class AvatarMood(str, Enum):
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"
    CURIOUS = "curious"
    CALM = "calm"
    CHEERFUL = "cheerful"
    CONCERNED = "concerned"


class MouthCue(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    SMILE = "smile"
    ROUND = "round"


class AvatarExpression(BaseModel):
    model_config = ConfigDict(extra="ignore")

    state: AvatarState = AvatarState.SPEAKING
    mood: AvatarMood = AvatarMood.FRIENDLY
    mouth: MouthCue = MouthCue.SMILE


class AssistantPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1, max_length=2000)
    expression: AvatarExpression = Field(default_factory=AvatarExpression)
    action: str = Field(default="idle", max_length=40)
    voice_locale: str = Field(default="es-ES", max_length=16)


class ConversationTurnRequest(BaseModel):
    session_id: str | None = None
    transcript: str = Field(min_length=1, max_length=500)
    locale: str = Field(default="es-ES", max_length=16)


class SessionStartResponse(BaseModel):
    session_id: str


class TimingMetrics(BaseModel):
    request_started_at: str
    completed_at: str
    duration_ms: int = Field(ge=0)


class ResponseMeta(BaseModel):
    provider: str = "hermes"
    fallback_used: bool = False
    issue: str | None = None


class ConversationTurnResponse(BaseModel):
    session_id: str
    assistant: AssistantPayload
    timing: TimingMetrics
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class HealthResponse(BaseModel):
    status: str
    hermes_configured: bool
    session_memory: bool
