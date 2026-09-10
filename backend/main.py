"""
JESSY Backend — Entry Point

Phase 8: Wires Working Context (agent/context.py) and the global
Action History (core/action_log.py) in via two new read-only endpoints
used to verify context/memory behavior — GET /actions and
GET /debug/context/{session_id}.

Phase 9: Adds POST /voice — accepts a recorded audio clip, transcribes
it via Whisper (voice/whisper.py), feeds the resulting text through
the same Agent loop /chat uses, and (optionally) synthesizes the reply
to speech via Piper (voice/tts.py), all in one round-trip:

    MICROPHONE (browser/client) -> POST /voice -> Whisper STT -> Agent
        -> Piper TTS -> spoken audio, base64-encoded

Step 9.4 additionally tracks each session's place in that pipeline
(voice/state.py: idle/listening/thinking/speaking).

Phase 10: Adds a WebSocket endpoint, GET /ws/{session_id}, that a GUI
connects to in order to receive the Agent's on_event progress events
(agent_started/agent_thinking/capability_started/capability_completed/
capability_failed/agent_completed/agent_error) and voice_state
transitions LIVE, as they happen, instead of only after the whole
HTTP request finishes.

To make "live" actually true, agent.run() (and /voice's STT/TTS work)
now runs inside a worker thread via starlette.concurrency.run_in_threadpool.
Previously this ran directly and synchronously inside the async
endpoint, which fully blocked the event loop for the whole request —
meaning nothing could be pushed out over any WebSocket until the
request was already done. ws/manager.py bridges the callback (fired
from that worker thread) back onto the event loop thread-safely via
asyncio.run_coroutine_threadsafe.
"""

import asyncio
import base64
import logging
import os
import time

from datetime import datetime, timezone
from typing import Any, Callable

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from core.memory import get_history, append_message
from voice.tts import synthesize_speech  # noqa: E402

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("jessy.main")

import capabilities  # noqa: E402,F401
from core.executor import Executor  # noqa: E402
from core.groq_client import get_groq_client  # noqa: E402
from core.registry import registry  # noqa: E402
from core import action_log  # noqa: E402
from agent.agent import Agent  # noqa: E402
from agent.context import get_context as get_working_context  # noqa: E402
from voice.whisper import transcribe_audio  # noqa: E402
from voice.state import (  # noqa: E402
    VoiceState,
    get_state as get_voice_state,
    reset_state as reset_voice_state,
    set_state as set_voice_state,
)
from ws.manager import manager  # noqa: E402
from app.history_store import init_db, record_action, get_actions as get_history_actions, list_conversations, get_conversation, check_db_health

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

app = FastAPI(
    title="JESSY Backend",
    description="AI Personal Computer Agent — Backend API",
    version="0.4.0",
)
START_TIME = time.time()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

executor = Executor(registry)


@app.on_event("startup")
async def _bind_ws_manager_loop() -> None:
    """
    Gives ws/manager.py a handle to the running event loop, so it can
    schedule WebSocket sends from a worker thread via
    asyncio.run_coroutine_threadsafe. Must run inside an async context
    (hence the startup hook) — there is no running loop yet at plain
    module-import time.
    """
    manager.bind_loop(asyncio.get_running_loop())


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: str


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = "default"


class ChatResponse(BaseModel):
    response: str
    steps: int


class VoiceResponse(BaseModel):
    transcript: str
    response: str
    steps: int
    audio_base64: str | None = None
    audio_content_type: str | None = None


@app.get("/", tags=["root"])
async def root():
    return {"message": "JESSY backend is running."}


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check():
    return HealthResponse(
        status="online",
        service="jessy-backend",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# --- TEMPORARY DEBUG ROUTE — remove once the spawn issue is diagnosed ---
@app.get("/debug/spawn-test", tags=["debug"])
def debug_spawn_test():
    import subprocess
    import getpass
    import os as _os

    proc = subprocess.Popen(["cmd.exe"])
    return {
        "status": "launched",
        "pid": proc.pid,
        "running_as_user": getpass.getuser(),
        "session_name": _os.environ.get("SESSIONNAME"),
    }
# -------------------------------------------------------------------------


# --- TEMPORARY DEBUG ROUTE — inspect a session's Working Context (Phase 8) ---
@app.get("/debug/context/{session_id}", tags=["debug"])
def debug_context(session_id: str):
    return get_working_context(session_id).to_dict()
# -------------------------------------------------------------------------


# --- TEMPORARY DEBUG ROUTE — inspect a session's Voice State (Phase 9.4) ---
@app.get("/debug/voice-state/{session_id}", tags=["debug"])
def debug_voice_state(session_id: str):
    """
    Superseded by GET /ws/{session_id} (Phase 10) for anything live —
    a GUI should watch the socket instead of polling this. Kept as a
    quick manual sanity check.
    """
    return get_voice_state(session_id).to_dict()
# -------------------------------------------------------------------------


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """
    Live event stream for a single session. Connect here from the GUI
    to receive, in real time, everything the Agent loop and voice
    pipeline are doing for this session_id — no polling required.

    Message shape sent to the client (JSON):
        {
          "type": "agent_started" | "agent_thinking" |
                   "capability_started" | "capability_completed" |
                   "capability_failed" | "agent_completed" |
                   "agent_error" | "voice_state",
          "session_id": "...",
          "payload": {...},
          "timestamp": "<ISO 8601 UTC>"
        }

    The final response text still always comes back over the original
    HTTP request (/chat or /voice) too — this socket is a side-channel
    for live progress, not a replacement for the HTTP response.

    The client isn't expected to send anything meaningful; this simply
    keeps the connection open (and lets us detect disconnects) by
    waiting on inbound messages that are otherwise ignored.
    """
    await manager.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)
    except Exception:
        logger.exception("WebSocket error for session %s", session_id)
        manager.disconnect(session_id, websocket)


@app.get("/actions", tags=["agent"])
async def recent_actions(limit: int = 10):
    """
    Return the most recent capability actions across all sessions, for
    the future Action Feed (Phase 11). Statuses: executed / proposed /
    failed / cancelled. Never fabricated — this reads straight from
    core/action_log.py, which is only ever written to by the Agent
    loop right after a real Executor call (or a real user decision).
    """
    return {"actions": action_log.get_recent(limit=limit)}


# Friendly, user-facing message shown whenever the agent fails to run at
# all (rate limits, provider outage, unexpected exception, etc). Keeps
# the API returning a normal 200 with a short one-liner instead of a
# raw 500 + stack trace, which is nicer for the frontend/CLI to display.
RATE_LIMIT_MESSAGE = (
    "I'm a bit overloaded right now (hit my API rate limit) — "
    "please try again in a few minutes."
)
GENERIC_FAILURE_MESSAGE = (
    "Something went wrong on my end while processing that. Please try again."
)


def _run_agent_turn(
    message: str,
    session_id: str,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> tuple[str, int]:
    """
    Shared helper used by both /chat and /voice: runs a plain-text
    message through the Agent loop, degrades gracefully on rate
    limits / unexpected failures exactly the way /chat always has,
    and persists the turn to per-session memory either way.

    This function is synchronous and is always invoked via
    starlette.concurrency.run_in_threadpool from the async endpoints
    below, so it runs in a worker thread — freeing the main event loop
    to actually deliver WebSocket events pushed by on_event (and by
    agent.run()'s own voice-state changes) while this is still running.

    on_event: forwarded straight into agent.run(). If provided, it is
    called (from this worker thread) for every agent-loop event; the
    caller is expected to have wired it to
    ws.manager.manager.emit_from_thread, which is itself thread-safe.

    Returns (final_response_text, step_count).
    """
    try:
        groq_client = get_groq_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    agent = Agent(groq_client=groq_client, registry=registry, executor=executor)
    history = get_history(session_id)

    try:
        final_text, step_logs = agent.run(
            user_message=message,
            history=history,
            session_id=session_id,
            on_event=on_event,
        )
    except RuntimeError as exc:
        # Raised by groq_client.chat() when every configured API key has
        # hit its rate limit. Degrade gracefully instead of a raw 500.
        if "rate limit" in str(exc).lower():
            logger.warning("Groq rate limit hit for session %s: %s", session_id, exc)
            if on_event:
                on_event("agent_error", {"error": "rate_limited"})
            append_message(session_id, "user", message)
            append_message(session_id, "assistant", RATE_LIMIT_MESSAGE)
            return RATE_LIMIT_MESSAGE, 0

        logger.exception("Agent run failed with RuntimeError")
        if on_event:
            on_event("agent_error", {"error": "internal_error"})
        append_message(session_id, "user", message)
        append_message(session_id, "assistant", GENERIC_FAILURE_MESSAGE)
        return GENERIC_FAILURE_MESSAGE, 0
    except Exception:
        # Catch-all: never let an unexpected agent failure surface as a
        # raw 500 with a stack trace to the caller. Log full details
        # server-side, but return a clean one-liner to the client.
        logger.exception("Agent run failed")
        if on_event:
            on_event("agent_error", {"error": "internal_error"})
        append_message(session_id, "user", message)
        append_message(session_id, "assistant", GENERIC_FAILURE_MESSAGE)
        return GENERIC_FAILURE_MESSAGE, 0

    append_message(session_id, "user", message)
    append_message(session_id, "assistant", final_text)

    return final_text, len(step_logs)


@app.post("/chat", response_model=ChatResponse, tags=["agent"])
async def chat(request: ChatRequest):
    session_id = request.session_id or "default"

    record_action(session_id=session_id, type="chat_received", detail=request.message)

    def on_event(event_type: str, payload: dict[str, Any]) -> None:
        manager.emit_from_thread(session_id, event_type, payload)
        record_action(
            session_id=session_id,
            type=event_type,
            capability=payload.get("capability"),
            detail=payload.get("detail") or payload.get("message"),
            payload=payload,
        )

    final_text, steps = await run_in_threadpool(
        _run_agent_turn, request.message, session_id, on_event
    )

    record_action(session_id=session_id, type="chat_response", detail=final_text)  # <-- new

    return ChatResponse(response=final_text, steps=steps)

def _handle_voice_sync(
    audio_bytes: bytes,
    filename: str,
    session_id: str,
    language: str | None,
    speak: bool,
    on_event: Callable[[str, dict[str, Any]], None] | None,
) -> VoiceResponse:
    """
    Synchronous body of /voice. Run inside a worker thread via
    run_in_threadpool (see the voice() endpoint below) so the main
    event loop stays free to actually deliver WebSocket events (voice
    state changes, agent progress) live while this is still running.
    """
    session_id = session_id or "default"
    try:
        set_voice_state(session_id, VoiceState.LISTENING)
        transcription = transcribe_audio(
            audio_bytes=audio_bytes,
            filename=filename,
            language=language,
        )

        if not transcription.success:
            logger.warning(
                "Voice transcription failed for session %s: %s",
                session_id, transcription.error,
            )
            return VoiceResponse(
                transcript="",
                response=f"I couldn't understand that audio: {transcription.error}",
                steps=0,
            )

        # agent.run() (called inside _run_agent_turn) sets this session's
        # voice state to THINKING for the duration and back to IDLE when
        # it returns — no need to set it here.
        final_text, steps = _run_agent_turn(transcription.text, session_id, on_event)

        audio_base64 = None
        audio_content_type = None
        if speak:
            set_voice_state(session_id, VoiceState.SPEAKING)
            speech = synthesize_speech(final_text)
            if speech.success:
                audio_base64 = base64.b64encode(speech.audio_bytes).decode("ascii")
                audio_content_type = speech.content_type
            else:
                logger.warning(
                    "TTS failed for session %s (continuing with text-only reply): %s",
                    session_id, speech.error,
                )

        return VoiceResponse(
            transcript=transcription.text,
            response=final_text,
            steps=steps,
            audio_base64=audio_base64,
            audio_content_type=audio_content_type,
        )
    finally:
        # Guarantees the session never gets stuck showing "listening" or
        # "speaking" if something above raises unexpectedly.
        reset_voice_state(session_id)


@app.post("/voice", response_model=VoiceResponse, tags=["agent"])
async def voice(
    audio: UploadFile = File(...),
    session_id: str = Form("default"),
    language: str | None = Form(None),
    speak: bool = Form(True),
):
    """
    MICROPHONE -> POST /voice -> Whisper STT -> Agent -> response
        -> (optional) Piper TTS -> spoken audio, base64-encoded

    `speak` (default true) controls whether the reply is also
    synthesized to audio. TTS failure never fails the whole request —
    you still get the text `response`, just with audio_base64=None
    and a warning logged server-side.

    Voice state (Phase 9.4 / live via Phase 10): this session is marked
    LISTENING while transcribing, THINKING while the Agent loop runs
    (set inside agent.run() itself), SPEAKING while synthesizing the
    reply (if `speak` is true), and IDLE once the whole request is
    done or if it fails at any point. Every transition is pushed live
    to GET /ws/{session_id} if a GUI is connected.
    """
    session_id = session_id or "default"
    audio_bytes = await audio.read()
    filename = audio.filename or "input.wav"

    def on_event(event_type: str, payload: dict[str, Any]) -> None:
        manager.emit_from_thread(session_id, event_type, payload)

    return await run_in_threadpool(
        _handle_voice_sync, audio_bytes, filename, session_id, language, speak, on_event
    )


@app.post("/speak", tags=["agent"])
async def speak_text(text: str = Form(...), voice: str | None = Form(None)):
    """
    Standalone TTS test endpoint — no transcription, no agent loop.
    Returns raw audio bytes directly (not JSON/base64), so you can
    pipe curl's output straight to a .wav file and play it.
    """
    result = synthesize_speech(text, voice=voice)
    if not result.success:
        raise HTTPException(status_code=502, detail=result.error)
    return Response(content=result.audio_bytes, media_type=result.content_type)

# Call once at startup (alongside any other startup init you already have):
@app.on_event("startup")
def _startup():
    init_db()


# --- Hook into your event emitter ---
# Find the function in your capability-execution / WS-manager code that
# currently does something like: `await websocket.send_json(event)` or
# `manager.broadcast(session_id, event)`. Wrap every call site (or the
# function itself, if it's centralized) with a matching record_action call:

# Example, if you have a centralized emit function:
async def emit_event(session_id: str, event: dict):
    await ws_manager.broadcast(session_id, event)   # your existing line
    record_action(                                   # <-- add this
        session_id=session_id,
        type=event.get("type", "unknown"),
        capability=event.get("capability"),
        detail=event.get("detail") or event.get("message"),
        payload=event,
    )


# --- New endpoint ---
from fastapi import Query

@app.get("/history")
def history(
    session_id: str | None = Query(default=None),
    type: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    return get_history_actions(session_id=session_id, type=type, limit=limit, offset=offset)
    
@app.get("/conversations", tags=["agent"])
def conversations(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    return list_conversations(limit=limit, offset=offset)


@app.get("/conversations/{session_id}", tags=["agent"])
def conversation_detail(session_id: str):
    messages = get_conversation(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="No conversation found for this session.")
    return {"session_id": session_id, "messages": messages}

@app.get("/status", tags=["system"])
def status():
    db_ok = check_db_health()
    return {
        "status": "ok" if db_ok else "degraded",
        "database_ok": db_ok,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "server_time": time.time(),
    }
