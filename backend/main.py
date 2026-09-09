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
(voice/state.py: idle/listening/thinking/speaking) — set here around
the STT and TTS phases, and inside agent/agent.py around the Agent
loop itself — via GET /debug/voice-state/{session_id}. This lays the
groundwork for Phase 10's WebSocket, which will push these same
transitions to the GUI live.
"""

import base64
import logging
import os

from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

app = FastAPI(
    title="JESSY Backend",
    description="AI Personal Computer Agent — Backend API",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

executor = Executor(registry)


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
    Note: since /voice is a single synchronous request/response, this
    will almost always read back "idle" if checked between requests —
    the listening/thinking/speaking transitions happen and resolve
    entirely within one /voice call. This becomes genuinely useful for
    live status once Phase 10's WebSocket pushes each transition as it
    happens, instead of only being checkable after the fact.
    """
    return get_voice_state(session_id).to_dict()
# -------------------------------------------------------------------------


@app.get("/actions", tags=["agent"])
async def get_actions(limit: int = 10):
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


def _run_agent_turn(message: str, session_id: str) -> tuple[str, int]:
    """
    Shared helper used by both /chat and /voice: runs a plain-text
    message through the Agent loop, degrades gracefully on rate
    limits / unexpected failures exactly the way /chat always has,
    and persists the turn to per-session memory either way.

    Note: agent.run() itself sets this session's voice state to
    THINKING for the duration of the call and back to IDLE afterward
    (via try/finally), regardless of which branch below is taken.

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
            user_message=message, history=history, session_id=session_id
        )
    except RuntimeError as exc:
        # Raised by groq_client.chat() when every configured API key has
        # hit its rate limit. Degrade gracefully instead of a raw 500.
        if "rate limit" in str(exc).lower():
            logger.warning("Groq rate limit hit for session %s: %s", session_id, exc)
            append_message(session_id, "user", message)
            append_message(session_id, "assistant", RATE_LIMIT_MESSAGE)
            return RATE_LIMIT_MESSAGE, 0

        logger.exception("Agent run failed with RuntimeError")
        append_message(session_id, "user", message)
        append_message(session_id, "assistant", GENERIC_FAILURE_MESSAGE)
        return GENERIC_FAILURE_MESSAGE, 0
    except Exception:
        # Catch-all: never let an unexpected agent failure surface as a
        # raw 500 with a stack trace to the caller. Log full details
        # server-side, but return a clean one-liner to the client.
        logger.exception("Agent run failed")
        append_message(session_id, "user", message)
        append_message(session_id, "assistant", GENERIC_FAILURE_MESSAGE)
        return GENERIC_FAILURE_MESSAGE, 0

    append_message(session_id, "user", message)
    append_message(session_id, "assistant", final_text)

    return final_text, len(step_logs)


@app.post("/chat", response_model=ChatResponse, tags=["agent"])
async def chat(request: ChatRequest):
    """
    Send a message to the JESSY agent and get back its final response
    after it has run its full tool-calling loop.
    """
    session_id = request.session_id or "default"
    final_text, steps = _run_agent_turn(request.message, session_id)
    return ChatResponse(response=final_text, steps=steps)


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

    Voice state (Phase 9.4): this session is marked LISTENING while
    transcribing, THINKING while the Agent loop runs (set inside
    agent.run() itself), SPEAKING while synthesizing the reply (if
    `speak` is true), and IDLE once the whole request is done or if it
    fails at any point.
    """
    session_id = session_id or "default"
    audio_bytes = await audio.read()

    try:
        set_voice_state(session_id, VoiceState.LISTENING)
        transcription = transcribe_audio(
            audio_bytes=audio_bytes,
            filename=audio.filename or "input.wav",
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
        final_text, steps = _run_agent_turn(transcription.text, session_id)

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
