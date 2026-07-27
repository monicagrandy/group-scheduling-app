"""SQLite-backed persistence for concurrent scheduling group sessions.

Each active group is a separate row in the `sessions` table, keyed by its
URL token. Snapshots older than TTL_DAYS are automatically discarded on
startup so stale data never bleeds across weeks.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from app.schemas import ClassConfig, PlanOutput, StudentAvailability, StudentRecord

TTL_DAYS = 8
_DB_PATH = Path(os.environ.get("DATABASE_PATH", "./data.db"))


@contextmanager
def _connection():
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token    TEXT PRIMARY KEY,
                payload  TEXT NOT NULL,
                saved_at TEXT NOT NULL
            )
        """)
        conn.commit()
        yield conn
    finally:
        conn.close()


def _state_to_payload(token: str, state) -> dict:
    return {
        "token": token,
        "title": state.title,
        "class_config": state.class_config.model_dump(mode="json"),
        "week_start_local": state.week_start_local.isoformat(),
        "week_end_local": state.week_end_local.isoformat(),
        "expected_student_ids": state.expected_student_ids,
        "students": {sid: r.model_dump(mode="json") for sid, r in state.students.items()},
        "availability": {sid: e.model_dump(mode="json") for sid, e in state.availability.items()},
        "last_plan": state.last_plan.model_dump(mode="json") if state.last_plan else None,
    }


def _payload_to_state(data: dict):
    from app.core.state import AppState

    title = data.get("title", "Group")
    state = AppState(
        title=title,
        class_config=ClassConfig.model_validate(data["class_config"]),
        week_start_local=date.fromisoformat(data["week_start_local"]),
        week_end_local=date.fromisoformat(data["week_end_local"]),
    )
    state.expected_student_ids = data.get("expected_student_ids", [])
    state.students = {
        sid: StudentRecord.model_validate(r) for sid, r in data.get("students", {}).items()
    }
    state.availability = {
        sid: StudentAvailability.model_validate(e) for sid, e in data.get("availability", {}).items()
    }
    if data.get("last_plan"):
        state.last_plan = PlanOutput.model_validate(data["last_plan"])
    return state


def save_session(token: str, state) -> None:
    """Persist one session to SQLite."""

    payload = json.dumps(_state_to_payload(token, state))
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as conn:
        conn.execute(
            """INSERT INTO sessions (token, payload, saved_at) VALUES (?, ?, ?)
               ON CONFLICT(token) DO UPDATE SET payload=excluded.payload, saved_at=excluded.saved_at""",
            (token, payload, now),
        )
        conn.commit()


def load_all_sessions() -> dict:
    """Load all non-expired sessions from SQLite. Returns {token: AppState}."""

    from app.core.state import AppState

    cutoff = datetime.now(timezone.utc)
    result: dict[str, AppState] = {}

    with _connection() as conn:
        rows = conn.execute("SELECT token, payload, saved_at FROM sessions").fetchall()
        expired_tokens = []
        for token, payload_json, saved_at_str in rows:
            saved_at = datetime.fromisoformat(saved_at_str)
            age_days = (cutoff - saved_at).days
            if age_days >= TTL_DAYS:
                expired_tokens.append(token)
                continue
            try:
                state = _payload_to_state(json.loads(payload_json))
                result[token] = state
            except Exception:
                expired_tokens.append(token)

        if expired_tokens:
            placeholders = ",".join("?" for _ in expired_tokens)
            conn.execute(f"DELETE FROM sessions WHERE token IN ({placeholders})", expired_tokens)
            conn.commit()

    return result


def clear_session(token: str) -> None:
    """Delete one session from SQLite."""

    with _connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
