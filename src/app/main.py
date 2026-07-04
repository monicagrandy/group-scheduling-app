"""FastAPI entrypoint for the group scheduling app.

Local dev:  uvicorn app.main:app --reload --app-dir src
Railway:    Procfile handles the start command automatically.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes.scheduling import router as scheduling_router
from app.core.database import load_all_sessions
from app.core.state import restore_sessions_from

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    sessions = load_all_sessions()
    if sessions:
        restore_sessions_from(sessions)
        print(f"Restored {len(sessions)} session(s) from database.")
    yield


app = FastAPI(title="Group Scheduler", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(scheduling_router)
