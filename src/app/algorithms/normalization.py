"""Helpers that convert raw availability blocks into fixed-size planner bitmaps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import math
from typing import Iterable
from zoneinfo import ZoneInfo

from app.schemas import PlanningBundle, StudentAvailability

SLOT_MINUTES = 30
SLOTS_PER_DAY = 48
SLOTS_PER_WEEK = 336


@dataclass(frozen=True)
class NormalizationContext:
    """Derived week-level values reused by every normalization helper."""

    timezone_name: str
    timezone: ZoneInfo
    week_start_local: date
    week_end_local: date
    week_start_at: datetime
    week_end_exclusive_at: datetime
    slot_minutes: int
    session_duration_minutes: int
    session_slot_count: int

    def slot_index_to_datetime(self, slot_index: int) -> datetime:
        """Translate a slot index back into a local datetime for reporting."""

        return self.week_start_at + timedelta(minutes=slot_index * self.slot_minutes)

    def window_end_datetime(self, start_slot: int) -> datetime:
        """Compute the end datetime for a session that starts at `start_slot`."""

        return self.slot_index_to_datetime(start_slot + self.session_slot_count)

    def window_id_from_start_slot(self, start_slot: int) -> str:
        """Build a stable identifier for one candidate meeting window."""

        start = self.slot_index_to_datetime(start_slot)
        end = self.window_end_datetime(start_slot)
        return f"{start.isoformat()}/{end.isoformat()}"


@dataclass(frozen=True)
class NormalizedStudentAvailability:
    """Bitmap representation of one student's availability for fast search."""

    student_id: str
    raw_text: str | None
    notes: tuple[str, ...]
    slot_bitmap: int
    window_bitmap: int
    window_starts: tuple[int, ...]



def build_normalization_context(bundle: PlanningBundle) -> NormalizationContext:
    """Precompute the week boundaries and slot sizing for one planning run."""

    timezone = ZoneInfo(bundle.class_config.timezone)
    week_start_at = datetime.combine(
        bundle.availability.week_start_local,
        time.min,
        tzinfo=timezone,
    )
    week_end_exclusive_at = datetime.combine(
        bundle.availability.week_end_local + timedelta(days=1),
        time.min,
        tzinfo=timezone,
    )
    session_duration_minutes = bundle.class_config.weekly_config.session_duration_minutes
    return NormalizationContext(
        timezone_name=bundle.class_config.timezone,
        timezone=timezone,
        week_start_local=bundle.availability.week_start_local,
        week_end_local=bundle.availability.week_end_local,
        week_start_at=week_start_at,
        week_end_exclusive_at=week_end_exclusive_at,
        slot_minutes=SLOT_MINUTES,
        session_duration_minutes=session_duration_minutes,
        session_slot_count=session_duration_minutes // SLOT_MINUTES,
    )



def normalize_weekly_availability(
    bundle: PlanningBundle,
) -> tuple[NormalizationContext, dict[str, NormalizedStudentAvailability]]:
    """Normalize every student submission into the bitmap form used by the planner."""

    context = build_normalization_context(bundle)
    normalized = {
        entry.student_id: normalize_student_availability(context, entry)
        for entry in bundle.availability.entries
    }
    return context, normalized



def normalize_student_availability(
    context: NormalizationContext,
    availability: StudentAvailability,
) -> NormalizedStudentAvailability:
    """Convert one student's availability blocks into slot and window bitmaps."""

    slot_bitmap = 0
    for block in availability.blocks:
        slot_range = block_to_slot_range(context, block.start_local, block.end_local)
        if slot_range is None:
            continue
        start_slot, end_slot = slot_range
        slot_bitmap |= contiguous_range_mask(start_slot, end_slot)

    window_bitmap = build_window_bitmap(context, slot_bitmap)
    window_starts = tuple(iter_set_bits(window_bitmap))
    return NormalizedStudentAvailability(
        student_id=availability.student_id,
        raw_text=availability.raw_text,
        notes=tuple(availability.notes),
        slot_bitmap=slot_bitmap,
        window_bitmap=window_bitmap,
        window_starts=window_starts,
    )



def block_to_slot_range(
    context: NormalizationContext,
    start_local: datetime,
    end_local: datetime,
) -> tuple[int, int] | None:
    """Clip a block to the target week and snap it onto the slot grid.

    Starts round up and ends round down so we never claim availability the student
    did not explicitly offer.
    """

    local_start = start_local.astimezone(context.timezone)
    local_end = end_local.astimezone(context.timezone)

    clipped_start = max(local_start, context.week_start_at)
    clipped_end = min(local_end, context.week_end_exclusive_at)
    if clipped_end <= clipped_start:
        return None

    start_minutes = math.ceil(
        (clipped_start - context.week_start_at).total_seconds() / 60 / context.slot_minutes
    ) * context.slot_minutes
    end_minutes = math.floor(
        (clipped_end - context.week_start_at).total_seconds() / 60 / context.slot_minutes
    ) * context.slot_minutes
    if end_minutes <= start_minutes:
        return None

    start_slot = int(start_minutes // context.slot_minutes)
    end_slot = int(end_minutes // context.slot_minutes)
    start_slot = max(0, min(start_slot, SLOTS_PER_WEEK))
    end_slot = max(0, min(end_slot, SLOTS_PER_WEEK))
    if end_slot <= start_slot:
        return None
    return start_slot, end_slot



def build_window_bitmap(context: NormalizationContext, slot_bitmap: int) -> int:
    """Return every valid same-day meeting window implied by `slot_bitmap`."""

    if context.session_slot_count <= 0:
        return 0

    window_bitmap = 0
    for start_slot in range(0, SLOTS_PER_WEEK - context.session_slot_count + 1):
        end_slot = start_slot + context.session_slot_count - 1
        # Sessions cannot spill across midnight because the coordinator reviews
        # single same-day practice windows.
        if start_slot // SLOTS_PER_DAY != end_slot // SLOTS_PER_DAY:
            continue
        required = contiguous_range_mask(start_slot, start_slot + context.session_slot_count)
        if slot_bitmap & required == required:
            window_bitmap |= 1 << start_slot
    return window_bitmap



def contiguous_range_mask(start_slot: int, end_slot: int) -> int:
    """Build a bitmap with bits set for the half-open range `[start_slot, end_slot)`."""

    if end_slot <= start_slot:
        return 0
    width = end_slot - start_slot
    return ((1 << width) - 1) << start_slot



def iter_set_bits(mask: int) -> Iterable[int]:
    """Yield set bit positions from least significant to most significant."""

    remaining = mask
    while remaining:
        least_significant_bit = remaining & -remaining
        yield least_significant_bit.bit_length() - 1
        remaining ^= least_significant_bit
