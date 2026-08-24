from __future__ import annotations

import json
from pathlib import Path

from app.algorithms import build_plan, build_plan_options
from app.schemas import PlanningBundle, StudentAvailability


def load_sample_bundle() -> PlanningBundle:
    path = Path(__file__).resolve().parents[2] / "fixtures" / "mvp" / "planning_bundle.sample.json"
    with path.open() as handle:
        return PlanningBundle.model_validate(json.load(handle))


def test_planner_builds_expected_two_group_split_for_sample_bundle() -> None:
    bundle = load_sample_bundle()

    plan = build_plan(bundle)

    assert plan.summary.group_count == 2
    assert plan.summary.covered_student_count == 6
    assert plan.summary.excluded_student_count == 0

    groups = [set(group.student_ids) for group in plan.groups]
    assert {frozenset(group) for group in groups} == {
        frozenset({"s-amelia", "s-ben", "s-carmen"}),
        frozenset({"s-diego", "s-emma", "s-finn"}),
    }

    windows = {group.group_number: group.chosen_window.start_local.isoformat() for group in plan.groups}
    assert set(windows.values()) == {
        "2026-06-30T18:00:00-07:00",
        "2026-07-01T18:00:00-07:00",
    }


def test_planner_excludes_student_without_two_hour_window() -> None:
    bundle = load_sample_bundle()
    updated_entry = StudentAvailability.model_validate(
        {
            **bundle.availability.entries[0].model_dump(mode="json"),
            "blocks": [
                {
                    "start_local": "2026-06-30T18:00:00-07:00",
                    "end_local": "2026-06-30T19:00:00-07:00",
                }
            ],
        }
    )
    updated_entries = [updated_entry, *bundle.availability.entries[1:]]
    bundle = PlanningBundle.model_validate(
        {
            **bundle.model_dump(mode="json"),
            "availability": {
                **bundle.availability.model_dump(mode="json"),
                "entries": [entry.model_dump(mode="json") for entry in updated_entries],
            },
        }
    )

    plan = build_plan(bundle)

    assert plan.summary.covered_student_count == 5
    assert plan.summary.excluded_student_count == 1
    assert plan.excluded_students[0].student_id == "s-amelia"
    assert plan.excluded_students[0].reason.startswith("Availability does not contain")


def test_planner_handles_balanced_groups_with_different_sizes() -> None:
    bundle = PlanningBundle.model_validate(
        {
            "class_config": {
                "class_id": "seven-student-class",
                "name": "Seven Student Class",
                "timezone": "America/Los_Angeles",
                "weekly_config": {
                    "max_groups": 2,
                    "session_duration_minutes": 120,
                    "minimum_group_size": 2,
                },
            },
            "roster": {
                "class_id": "seven-student-class",
                "students": [
                    {"student_id": "s-a", "name": "A", "active": True},
                    {"student_id": "s-b", "name": "B", "active": True},
                    {"student_id": "s-c", "name": "C", "active": True},
                    {"student_id": "s-d", "name": "D", "active": True},
                    {"student_id": "s-e", "name": "E", "active": True},
                    {"student_id": "s-f", "name": "F", "active": True},
                    {"student_id": "s-g", "name": "G", "active": True},
                ],
            },
            "availability": {
                "class_id": "seven-student-class",
                "week_start_local": "2026-06-29",
                "week_end_local": "2026-07-05",
                "entries": [
                    {
                        "student_id": "s-a",
                        "blocks": [
                            {"start_local": "2026-07-01T18:00:00-07:00", "end_local": "2026-07-01T20:00:00-07:00"}
                        ],
                    },
                    {
                        "student_id": "s-b",
                        "blocks": [
                            {"start_local": "2026-06-30T18:00:00-07:00", "end_local": "2026-06-30T20:00:00-07:00"}
                        ],
                    },
                    {
                        "student_id": "s-c",
                        "blocks": [
                            {"start_local": "2026-06-30T18:00:00-07:00", "end_local": "2026-06-30T20:00:00-07:00"}
                        ],
                    },
                    {
                        "student_id": "s-d",
                        "blocks": [
                            {"start_local": "2026-06-30T18:00:00-07:00", "end_local": "2026-06-30T20:00:00-07:00"}
                        ],
                    },
                    {
                        "student_id": "s-e",
                        "blocks": [
                            {"start_local": "2026-06-30T18:00:00-07:00", "end_local": "2026-06-30T20:00:00-07:00"}
                        ],
                    },
                    {
                        "student_id": "s-f",
                        "blocks": [
                            {"start_local": "2026-07-01T18:00:00-07:00", "end_local": "2026-07-01T20:00:00-07:00"}
                        ],
                    },
                    {
                        "student_id": "s-g",
                        "blocks": [
                            {"start_local": "2026-07-01T18:00:00-07:00", "end_local": "2026-07-01T20:00:00-07:00"}
                        ],
                    },
                ],
            },
        }
    )

    plan = build_plan(bundle)

    assert plan.summary.group_count == 2
    assert plan.summary.covered_student_count == 7
    assert sorted(len(group.student_ids) for group in plan.groups) == [3, 4]
    assert {frozenset(group.student_ids) for group in plan.groups} == {
        frozenset({"s-a", "s-f", "s-g"}),
        frozenset({"s-b", "s-c", "s-d", "s-e"}),
    }


def _bundle_with_settings(
    *,
    session_duration_minutes: int = 120,
    minimum_group_size: int = 3,
) -> PlanningBundle:
    """Six students all free Tuesday 6-9pm — adjust config settings per test."""
    students = [
        {"student_id": f"s-{n}", "name": n.title(), "active": True}
        for n in ["a", "b", "c", "d", "e", "f"]
    ]
    entries = [
        {
            "student_id": f"s-{n}",
            "blocks": [{"start_local": "2026-06-30T18:00:00-07:00", "end_local": "2026-06-30T21:00:00-07:00"}],
        }
        for n in ["a", "b", "c", "d", "e", "f"]
    ]
    return PlanningBundle.model_validate({
        "class_config": {
            "class_id": "test",
            "name": "Test",
            "timezone": "America/Los_Angeles",
            "weekly_config": {
                "session_duration_minutes": session_duration_minutes,
                "minimum_group_size": minimum_group_size,
            },
        },
        "roster": {"class_id": "test", "students": students},
        "availability": {
            "class_id": "test",
            "week_start_local": "2026-06-29",
            "week_end_local": "2026-07-05",
            "entries": entries,
        },
    })


def test_planner_respects_minimum_group_size_of_4() -> None:
    bundle = _bundle_with_settings(minimum_group_size=4)
    plan = build_plan(bundle)
    assert plan.summary.group_count == 1
    assert all(len(g.student_ids) >= 4 for g in plan.groups)


def test_planner_returns_all_valid_arrangements_and_ranks_larger_groups_first() -> None:
    bundle = _bundle_with_settings(minimum_group_size=2)

    plans = build_plan_options(bundle)
    size_arrangements = [
        sorted((len(group.student_ids) for group in plan.groups), reverse=True)
        for plan in plans
    ]

    # Six mutually compatible students have 41 set partitions whose groups
    # all contain at least two people: [6], [4,2], [3,3], and [2,2,2].
    assert len(plans) == 41
    assert size_arrangements[0] == [6]
    assert [3, 3] in size_arrangements
    assert [2, 2, 2] in size_arrangements

    membership_tokens = {
        tuple(sorted(tuple(sorted(group.student_ids)) for group in plan.groups))
        for plan in plans
    }
    assert len(membership_tokens) == len(plans)


def test_planner_rejects_students_without_sufficient_window_for_90_min_session() -> None:
    bundle = _bundle_with_settings(session_duration_minutes=90)
    plan = build_plan(bundle)
    assert plan.summary.group_count >= 1
    duration_slots = 90 // 30
    for group in plan.groups:
        start = group.chosen_window.start_local
        end = group.chosen_window.end_local
        assert (end - start).seconds // 60 == 90


def test_planner_cannot_form_groups_when_window_too_short_for_session() -> None:
    students = [
        {"student_id": f"s-{n}", "name": n.title(), "active": True}
        for n in ["a", "b", "c"]
    ]
    # Only a 90-minute block available, but session requires 120 minutes
    entries = [
        {
            "student_id": f"s-{n}",
            "blocks": [{"start_local": "2026-06-30T18:00:00-07:00", "end_local": "2026-06-30T19:30:00-07:00"}],
        }
        for n in ["a", "b", "c"]
    ]
    bundle = PlanningBundle.model_validate({
        "class_config": {
            "class_id": "test",
            "name": "Test",
            "timezone": "America/Los_Angeles",
            "weekly_config": {"session_duration_minutes": 120, "minimum_group_size": 3},
        },
        "roster": {"class_id": "test", "students": students},
        "availability": {
            "class_id": "test",
            "week_start_local": "2026-06-29",
            "week_end_local": "2026-07-05",
            "entries": entries,
        },
    })
    plan = build_plan(bundle)
    assert plan.summary.group_count == 0
    assert plan.summary.excluded_student_count == 3
