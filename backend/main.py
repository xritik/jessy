"""
JESSY Backend — Entry Point
Step 1: Foundation server.

This file will grow as we add the agent, capabilities, voice,
and websocket layers in later steps. For now it exposes a health
endpoint so the frontend can verify a real connection exists.
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Load environment variables from backend/.env
load_dotenv()

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

app = FastAPI(
    title="JESSY Backend",
    description="AI Personal Computer Agent — Backend API",
    version="0.1.0",
)

# Allow the React dev server to call this API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: str


@app.get("/", tags=["root"])
async def root():
    """Basic root route to confirm the server is alive."""
    return {"message": "JESSY backend is running."}


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check():
    """
    Reports that the backend process is alive and reachable.

    This is intentionally simple in Step 1 — it does not yet report
    on Groq, Whisper, TTS, or capability subsystems. Those checks
    are added in later phases as those subsystems are built.
    """
    return HealthResponse(
        status="online",
        service="jessy-backend",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
