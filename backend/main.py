"""
JESSY Backend — Entry Point

Phase 8: Wires Working Context (agent/context.py) and the global
Action History (core/action_log.py) in via two new read-only endpoints
used to verify context/memory behavior — GET /actions and
GET /debug/context/{session_id}.
"""

import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from core.memory import get_history, append_message

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

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

app = FastAPI(
    title="JESSY Backend",
    description="AI Personal Computer Agent — Backend API",
    version="0.2.0",
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


@app.post("/chat", response_model=ChatResponse, tags=["agent"])
async def chat(request: ChatRequest):
    """
    Send a message to the JESSY agent and get back its final response
    after it has run its full tool-calling loop.
    """
    try:
        groq_client = get_groq_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    agent = Agent(groq_client=groq_client, registry=registry, executor=executor)

    session_id = request.session_id or "default"
    history = get_history(session_id)

    try:
        final_text, step_logs = agent.run(user_message=request.message, history=history, session_id=session_id)
    except RuntimeError as exc:
        # Raised by groq_client.chat() when every configured API key has
        # hit its rate limit. Degrade gracefully instead of a raw 500.
        if "rate limit" in str(exc).lower():
            logger.warning("Groq rate limit hit for session %s: %s", session_id, exc)
            append_message(session_id, "user", request.message)
            append_message(session_id, "assistant", RATE_LIMIT_MESSAGE)
            return ChatResponse(response=RATE_LIMIT_MESSAGE, steps=0)

        logger.exception("Agent run failed with RuntimeError")
        append_message(session_id, "user", request.message)
        append_message(session_id, "assistant", GENERIC_FAILURE_MESSAGE)
        return ChatResponse(response=GENERIC_FAILURE_MESSAGE, steps=0)
    except Exception:
        # Catch-all: never let an unexpected agent failure surface as a
        # raw 500 with a stack trace to the caller. Log full details
        # server-side, but return a clean one-liner to the client.
        logger.exception("Agent run failed")
        append_message(session_id, "user", request.message)
        append_message(session_id, "assistant", GENERIC_FAILURE_MESSAGE)
        return ChatResponse(response=GENERIC_FAILURE_MESSAGE, steps=0)

    append_message(session_id, "user", request.message)
    append_message(session_id, "assistant", final_text)

    return ChatResponse(response=final_text, steps=len(step_logs))
