from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json

from app.algorithms.normalization import (
    build_normalization_context,
    normalize_student_availability,
)
from app.schemas import PlanningBundle, StudentAvailability


def load_sample_bundle() -> PlanningBundle:
    path = Path(__file__).resolve().parents[2] / "fixtures" / "mvp" / "planning_bundle.sample.json"
    with path.open() as handle:
        return PlanningBundle.model_validate(json.load(handle))


def test_normalization_rounds_and_derives_windows() -> None:
    bundle = load_sample_bundle()
    context = build_normalization_context(bundle)
    entry = bundle.availability.entries[1]

    normalized = normalize_student_availability(context, entry)

    assert normalized.student_id == "s-ben"
    assert normalized.window_starts == (83, 84, 85, 86, 132)


def test_normalization_clips_blocks_to_target_week() -> None:
    bundle = load_sample_bundle()
    context = build_normalization_context(bundle)
    availability = StudentAvailability.model_validate(
        {
            **bundle.availability.entries[0].model_dump(mode="json"),
            "blocks": [
                {
                    "start_local": datetime(2026, 6, 28, 23, 0, tzinfo=ZoneInfo("America/Los_Angeles")).isoformat(),
                    "end_local": datetime(2026, 6, 29, 1, 0, tzinfo=ZoneInfo("America/Los_Angeles")).isoformat(),
                }
            ],
        }
    )

    normalized = normalize_student_availability(context, availability)

    assert normalized.window_starts == ()
    assert normalized.slot_bitmap != 0
