"""
JESSY Backend — Entry Point

Step 2: Wires the Core Agent (Groq client, Capability Registry,
Executor, Agent loop) into a real /chat endpoint.
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
from agent.agent import Agent  # noqa: E402

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
        final_text, step_logs = agent.run(user_message=request.message, history=history)
    except Exception as exc:
        logger.exception("Agent run failed")
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    append_message(session_id, "user", request.message)
    append_message(session_id, "assistant", final_text)

    return ChatResponse(response=final_text, steps=len(step_logs))
