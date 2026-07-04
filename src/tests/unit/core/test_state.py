from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.state import create_session
from app.schemas import AvailabilityBlock, StudentAvailability


def _make_block(start: str, end: str, tz: str = "America/Los_Angeles") -> AvailabilityBlock:
    z = ZoneInfo(tz)
    return AvailabilityBlock(
        start_local=datetime.fromisoformat(start).replace(tzinfo=z),
        end_local=datetime.fromisoformat(end).replace(tzinfo=z),
    )


class TestCreateSession:
    def test_stores_custom_min_group_size(self):
        _, state = create_session("Test", ["Alice", "Bob", "Carol"], 4, 120)
        assert state.class_config.weekly_config.minimum_group_size == 4

    def test_stores_custom_session_duration(self):
        _, state = create_session("Test", ["Alice", "Bob"], 3, 90)
        assert state.class_config.weekly_config.session_duration_minutes == 90

    def test_uses_default_min_group_size(self):
        _, state = create_session("Test", ["Alice", "Bob", "Carol"], 3, 120)
        assert state.class_config.weekly_config.minimum_group_size == 3

    def test_slugifies_names_to_student_ids(self):
        _, state = create_session("Test", ["Alice Smith", "Bob O'Brien"], 3, 120)
        assert "alice-smith" in state.students
        assert "bob-o-brien" in state.students

    def test_deduplicates_colliding_slugs(self):
        _, state = create_session("Test", ["Ali", "Ali"], 3, 120)
        ids = list(state.students.keys())
        assert len(ids) == 2
        assert len(set(ids)) == 2

    def test_expected_student_ids_matches_roster(self):
        names = ["Alice", "Bob", "Carol"]
        _, state = create_session("Test", names, 3, 120)
        assert len(state.expected_student_ids) == 3
        assert set(state.expected_student_ids) == set(state.students.keys())

    def test_token_is_unique_per_call(self):
        token1, _ = create_session("A", ["Alice"], 3, 120)
        token2, _ = create_session("B", ["Bob"], 3, 120)
        assert token1 != token2


class TestReplaceStudentAvailability:
    def test_replaces_existing_blocks(self):
        _, state = create_session("Test", ["Alice"], 3, 120)
        sid = state.expected_student_ids[0]
        block1 = _make_block("2026-06-30T18:00:00", "2026-06-30T20:00:00")
        state.replace_student_availability(sid, blocks=[block1])
        assert len(state.availability[sid].blocks) == 1

        block2 = _make_block("2026-07-01T18:00:00", "2026-07-01T20:00:00")
        state.replace_student_availability(sid, blocks=[block2])
        assert len(state.availability[sid].blocks) == 1
        assert state.availability[sid].blocks[0].start_local.day == 1

    def test_clears_last_plan_on_resubmit(self):
        from app.schemas import PlanOutput, PlanSummary
        from datetime import date
        _, state = create_session("Test", ["Alice"], 3, 120)
        sid = state.expected_student_ids[0]
        state.last_plan = PlanOutput(
            class_id="x", week_start_local=date.today(), week_end_local=date.today(),
            timezone="UTC", requested_max_groups=1,
            generated_at=datetime.now(ZoneInfo("UTC")),
            summary=PlanSummary(
                requested_student_count=1, responded_student_count=1,
                covered_student_count=1, excluded_student_count=0, group_count=1,
            ),
            groups=[], excluded_students=[], planner_notes=[],
        )
        block = _make_block("2026-06-30T18:00:00", "2026-06-30T20:00:00")
        state.replace_student_availability(sid, blocks=[block])
        assert state.last_plan is None


class TestIsSessionComplete:
    def test_false_when_no_submissions(self):
        _, state = create_session("Test", ["Alice", "Bob"], 3, 120)
        assert not state.is_session_complete()

    def test_false_when_partial_submissions(self):
        _, state = create_session("Test", ["Alice", "Bob"], 3, 120)
        sid = state.expected_student_ids[0]
        block = _make_block("2026-06-30T18:00:00", "2026-06-30T20:00:00")
        state.replace_student_availability(sid, blocks=[block])
        assert not state.is_session_complete()

    def test_true_when_all_submitted(self):
        _, state = create_session("Test", ["Alice", "Bob"], 3, 120)
        block = _make_block("2026-06-30T18:00:00", "2026-06-30T20:00:00")
        for sid in state.expected_student_ids:
            state.replace_student_availability(sid, blocks=[block])
        assert state.is_session_complete()
