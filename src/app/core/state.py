"""In-memory session state for the group scheduling app.

Each active scheduling group is a separate AppState stored in `_sessions`,
keyed by its unique URL token. Multiple groups can run concurrently in a
single deployment without any interference.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.schemas import (
    AvailabilityBlock,
    ClassConfig,
    PlanningBundle,
    PlanOutput,
    StudentAvailability,
    StudentRecord,
    WeeklyConfig,
)

DEFAULT_GROUP_ID = "group"
SLUG_PATTERN = re.compile(r"[^a-z0-9]+")

_sessions: dict[str, "AppState"] = {}


@dataclass
class AppState:
    """Mutable state for one active scheduling group."""

    title: str
    class_config: ClassConfig
    week_start_local: date
    week_end_local: date
    students: dict[str, StudentRecord] = field(default_factory=dict)
    availability: dict[str, StudentAvailability] = field(default_factory=dict)
    last_bundle: PlanningBundle | None = None
    last_plan: PlanOutput | None = None
    expected_student_ids: list[str] = field(default_factory=list)


    def is_session_complete(self) -> bool:
        """True once every pre-loaded participant has submitted availability."""

        return bool(self.expected_student_ids) and len(self.availability) >= len(
            self.expected_student_ids
        )

    def replace_student_availability(
        self,
        student_id: str,
        *,
        blocks: list[AvailabilityBlock],
    ) -> None:
        """Overwrite (not merge) one participant's availability.

        Resubmitting via the same link replaces the previous answer entirely
        and clears any cached plan so the next Generate reflects the change.
        """

        self.availability[student_id] = StudentAvailability(
            student_id=student_id,
            blocks=blocks,
        )
        self.last_plan = None
        self.last_bundle = None

    def load_bundle(self, bundle: PlanningBundle) -> list[str]:
        """Replace state with pre-parsed availability from a PlanningBundle file."""

        self.students.clear()
        self.availability.clear()
        self.last_bundle = None
        self.last_plan = None
        self.expected_student_ids = []
        self.class_config = bundle.class_config
        self.week_start_local = bundle.availability.week_start_local
        self.week_end_local = bundle.availability.week_end_local

        roster_by_id = {s.student_id: s for s in bundle.roster.students}
        skipped: list[str] = []
        for entry in bundle.availability.entries:
            if not entry.blocks:
                skipped.append(entry.student_id)
                continue
            student = roster_by_id.get(entry.student_id)
            if student is None:
                continue
            self.students[entry.student_id] = student
            self.availability[entry.student_id] = entry

        skipped.extend(
            sid for sid in roster_by_id
            if sid not in self.availability and sid not in skipped
        )
        return skipped


def _upcoming_monday(today: date) -> date:
    """Return today if it is already Monday, otherwise the next Monday."""

    days_ahead = (0 - today.weekday()) % 7
    return today + timedelta(days=days_ahead)


def _base_config(title: str, min_group_size: int, session_duration: int) -> ClassConfig:
    """Build a ClassConfig from environment variables for a new session."""

    return ClassConfig(
        class_id=DEFAULT_GROUP_ID,
        name=title or os.environ.get("APP_TITLE", "Group Scheduler"),
        timezone=os.environ.get("CLASS_TIMEZONE", "America/Los_Angeles"),
        weekly_config=WeeklyConfig(minimum_group_size=min_group_size, session_duration_minutes=session_duration),
    )


def create_session(title: str, names: list[str], min_group_size: int, session_duration:int) -> tuple[str, AppState]:
    """Create a new scheduling group session and return its token + state."""

    token = secrets.token_urlsafe(8)
    week_start = _upcoming_monday(date.today())
    state = AppState(
        title=title.strip() or "Group",
        class_config=_base_config(title, min_group_size, session_duration),
        week_start_local=week_start,
        week_end_local=week_start + timedelta(days=6),
    )
    existing_ids: list[str] = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        student_id = slugify_student_id(name, existing_ids=existing_ids)
        existing_ids.append(student_id)
        state.students[student_id] = StudentRecord(student_id=student_id, name=name)
        state.expected_student_ids.append(student_id)

    _sessions[token] = state
    return token, state


def get_session(token: str) -> AppState | None:
    """Return the live state for a token, or None if unknown."""

    return _sessions.get(token)


def delete_session(token: str) -> None:
    """Remove a session entirely."""

    _sessions.pop(token, None)


def all_sessions() -> dict[str, AppState]:
    """Return a snapshot of all active sessions."""

    return dict(_sessions)


def restore_sessions_from(loaded: dict[str, AppState]) -> None:
    """Populate the session dict from a database restore on startup."""

    _sessions.clear()
    _sessions.update(loaded)


def slugify_student_id(name: str, existing_ids: list[str] | set[str] | None = None) -> str:
    """Derive a stable, collision-free participant id from a display name."""

    base = SLUG_PATTERN.sub("-", name.strip().lower()).strip("-") or "participant"
    existing = set(existing_ids or ())
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"
