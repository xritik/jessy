from contextlib import asynccontextmanager
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


load_dotenv()


APP_NAME = os.getenv("APP_NAME", "JESSY")
APP_ENV = os.getenv("APP_ENV", "development")
FRONTEND_ORIGIN = os.getenv(
    "FRONTEND_ORIGIN",
    "http://localhost:5173",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"Starting {APP_NAME}...")
    print(f"Environment: {APP_ENV}")

    yield

    print(f"Stopping {APP_NAME}...")


app = FastAPI(
    title=APP_NAME,
    description="JESSY AI Personal Computer Agent",
    version="0.1.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/")
async def root():
    return {
        "success": True,
        "application": APP_NAME,
        "version": "0.1.0",
        "message": "JESSY backend is running.",
    }


@app.get("/health")
async def health():
    return {
        "success": True,
        "status": "healthy",
        "service": "backend",
    }


@app.get("/status")
async def status():
    return {
        "agent": "online",
        "groq": "not_configured",
        "whisper": "not_configured",
        "tts": "not_configured",
        "filesystem": "not_configured",
        "browser": "not_configured",
        "terminal": "not_configured",
    }