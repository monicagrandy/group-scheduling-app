"""Routes for the group scheduling app."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError

from app.algorithms.planner import build_plan_options
from app.core.database import clear_session, load_all_sessions, save_session
from app.core.state import (
    AppState,
    create_session,
    delete_session,
    get_session,
    slugify_student_id,
)
from app.schemas import (
    AvailabilityBlock,
    PlanningBundle,
    PlanOutput,
    RosterInput,
    StudentAvailability,
    StudentRecord,
    WeeklyAvailabilityInput,
)
from app.services.conflict_resolution import resolve_conflicts

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _time_12h(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str)
    return dt.strftime("%I:%M %p").lstrip("0")


def _friendly_date(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str[:10])
    return dt.strftime("%b %-d")


templates.env.filters["time_12h"] = _time_12h
templates.env.filters["friendly_date"] = _friendly_date

router = APIRouter()


def _static_version(filename: str) -> int:
    return int((STATIC_DIR / filename).stat().st_mtime)


def _build_bundle(state: AppState) -> PlanningBundle:
    roster = RosterInput(
        class_id=state.class_config.class_id,
        students=list(state.students.values()),
    )
    availability = WeeklyAvailabilityInput(
        class_id=state.class_config.class_id,
        week_start_local=state.week_start_local,
        week_end_local=state.week_end_local,
        entries=list(state.availability.values()),
    )
    return PlanningBundle(class_config=state.class_config, roster=roster, availability=availability)


def _compute_all_plans(state: AppState) -> list[dict]:
    """Return every maximum-coverage arrangement, ranked best-first."""

    options = build_plan_options(_build_bundle(state))
    plans: list[dict] = []
    for index, plan in enumerate(options):
        group_sizes = sorted((len(group.student_ids) for group in plan.groups), reverse=True)
        group_word = "group" if len(group_sizes) == 1 else "groups"
        size_summary = " + ".join(str(size) for size in group_sizes) or "no valid groups"
        prefix = "Optimal arrangement" if index == 0 else f"Alternative {index}"
        plans.append(
            {
                "label": f"{prefix} — {len(group_sizes)} {group_word} ({size_summary})",
                "is_optimal": index == 0,
                "plan": plan.model_dump(mode="json"),
            }
        )
    return plans


def _existing_availability_json(state: AppState) -> str:
    students = sorted(state.students.values(), key=lambda s: s.name.lower())
    payload = [
        {
            "student_id": s.student_id,
            "name": s.name,
            "blocks": [
                {"start_local": b.start_local.isoformat(), "end_local": b.end_local.isoformat()}
                for b in state.availability[s.student_id].blocks
            ],
        }
        for s in students
        if s.student_id in state.availability
    ]
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Home page — create a new group link
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_js_version": _static_version("app.js"),
            "css_version": _static_version("styles.css"),
        },
    )


@router.post("/session/init")
async def init_session(
    title: str = Form(""),
    names: str = Form(...),
    min_group_size: str = Form(""),
    session_duration: str = Form(""),
    week_start_date: str = Form(""),
) -> RedirectResponse:
    """Create a new session and send the creator straight to the group link."""

    parsed_names = [
        part.strip()
        for line in names.splitlines()
        for part in line.split(",")
        if part.strip()
    ]
    min_size = int(min_group_size) if min_group_size.strip() else 3
    duration = int(session_duration) if session_duration.strip() else 120
    anchor = date.fromisoformat(week_start_date) if week_start_date.strip() else None
    token, state = create_session(title, parsed_names, min_size, duration, anchor_date=anchor)
    save_session(token, state)
    return RedirectResponse(url=f"/submit/{token}", status_code=303)


# ---------------------------------------------------------------------------
# Public group link — submit availability + view results
# ---------------------------------------------------------------------------

@router.get("/submit/{token}", response_class=HTMLResponse)
async def submit_form(request: Request, token: str) -> HTMLResponse:
    state = get_session(token)
    if state is None:
        return templates.TemplateResponse(
            request, "submit_invalid.html", {"css_version": _static_version("styles.css")}, status_code=404
        )

    roster_options = sorted(
        (
            {
                "student_id": sid,
                "name": state.students[sid].name,
                "submitted": sid in state.availability,
            }
            for sid in state.expected_student_ids
        ),
        key=lambda o: o["name"].lower(),
    )
    share_url = str(request.url_for("submit_form", token=token))

    return templates.TemplateResponse(
        request,
        "submit.html",
        {
            "token": token,
            "title": state.title,
            "week_start_local": state.week_start_local,
            "week_end_local": state.week_end_local,
            "session_minutes": state.class_config.weekly_config.session_duration_minutes,
            "roster_options": roster_options,
            "submitted_count": len(state.availability),
            "expected_count": len(state.expected_student_ids),
            "existing_availability_json": _existing_availability_json(state),
            "all_plans": state.all_plans,
            "all_plans_json": json.dumps(state.all_plans),
            "students_by_id": {s.student_id: s.name for s in state.students.values()},
            "share_url": share_url,
            "just_submitted_name": request.query_params.get("submitted"),
            "app_js_version": _static_version("app.js"),
            "css_version": _static_version("styles.css"),
        },
    )


@router.post("/submit/{token}")
async def submit_availability(
    token: str,
    student_id: str = Form(...),
    viewer_timezone: str = Form(""),
    block_start: list[str] = Form([]),
    block_end: list[str] = Form([]),
) -> RedirectResponse:
    """Replace one participant's availability; auto-run planner once everyone's in."""

    state = get_session(token)
    if state is None or student_id not in state.students:
        return RedirectResponse(url=f"/submit/{token}", status_code=303)

    try:
        tz = ZoneInfo(viewer_timezone) if viewer_timezone else ZoneInfo(state.class_config.timezone)
    except Exception:
        tz = ZoneInfo(state.class_config.timezone)

    blocks: list[AvailabilityBlock] = []
    for start_raw, end_raw in zip(block_start, block_end):
        if not start_raw or not end_raw:
            continue
        start_local = datetime.fromisoformat(start_raw).replace(tzinfo=tz)
        end_local = datetime.fromisoformat(end_raw).replace(tzinfo=tz)
        if end_local <= start_local:
            continue
        blocks.append(AvailabilityBlock(start_local=start_local, end_local=end_local))

    state.replace_student_availability(student_id, blocks=blocks)

    if state.is_session_complete() and state.last_plan is None:
        state.all_plans = _compute_all_plans(state)
        state.last_plan = PlanOutput.model_validate(state.all_plans[0]["plan"]) if state.all_plans else None

    save_session(token, state)
    submitted_name = state.students[student_id].name
    return RedirectResponse(url=f"/submit/{token}?submitted={submitted_name}", status_code=303)


@router.post("/submit/{token}/generate")
async def generate_groups(token: str) -> RedirectResponse:
    """Manually trigger the planner for a session (accessible to anyone with the link)."""

    state = get_session(token)
    if state is not None and state.students:
        state.all_plans = _compute_all_plans(state)
        state.last_plan = PlanOutput.model_validate(state.all_plans[0]["plan"]) if state.all_plans else None
        save_session(token, state)
    return RedirectResponse(url=f"/submit/{token}", status_code=303)


@router.post("/submit/{token}/clear")
async def clear_group(token: str) -> RedirectResponse:
    """Delete a session entirely — the link stops working."""

    delete_session(token)
    clear_session(token)
    return RedirectResponse(url="/", status_code=303)


# ---------------------------------------------------------------------------
# Dev-only fixture loader (local use only, not linked from any UI)
# ---------------------------------------------------------------------------

class LoadFixtureRequest(BaseModel):
    path: str
    token: str | None = None


@router.post("/dev/load-fixture")
async def load_fixture(payload: LoadFixtureRequest) -> JSONResponse:
    file_path = Path(payload.path)
    if not file_path.is_file():
        return JSONResponse({"status": "error", "message": f"File not found: {payload.path}"})
    try:
        raw = json.loads(file_path.read_text())
        bundle = PlanningBundle.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        return JSONResponse({"status": "error", "message": f"Invalid planning bundle: {exc}"})

    use_token = payload.token or "fixture"
    existing = get_session(use_token)
    if existing is None:
        from app.core.state import AppState
        state = AppState(
            title="Fixture",
            class_config=bundle.class_config,
            week_start_local=bundle.availability.week_start_local,
            week_end_local=bundle.availability.week_end_local,
        )
        from app.core.state import _sessions
        _sessions[use_token] = state
    else:
        state = existing

    skipped = state.load_bundle(bundle)
    save_session(use_token, state)

    return JSONResponse({
        "status": "loaded",
        "token": use_token,
        "loaded_student_count": len(state.students),
        "skipped_no_availability": sorted(skipped),
    })
