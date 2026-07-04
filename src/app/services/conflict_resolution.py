from __future__ import annotations

from app.algorithms.normalization import contiguous_range_mask, normalize_weekly_availability
from app.algorithms.planner import build_plan
from app.schemas import ExcludedStudent, PlanOutput, PlanningBundle


def resolve_conflicts(
    bundle: PlanningBundle, plan_output: PlanOutput
) -> tuple[PlanOutput, list[str]]:
    """Attempt a same-window merge first, then a relaxed re-plan, in that order."""

    notes: list[str] = []

    merged_plan = _attempt_merge(bundle, plan_output)
    if merged_plan is not None:
        plan_output = merged_plan
        notes.append(
            "Merged previously excluded student(s) into an existing group with a "
            "shared window."
        )

    if plan_output.groups and not plan_output.excluded_students:
        return plan_output, notes

    relaxed_plan = _attempt_relaxed_replan(bundle)
    if relaxed_plan is not None and relaxed_plan.groups and (
        len(relaxed_plan.excluded_students) < len(plan_output.excluded_students)
        or not plan_output.groups
    ):
        notes.append(
            "Re-ran the planner with minimum_group_size temporarily relaxed to 2 "
            "to find a valid plan."
        )
        return relaxed_plan, notes

    notes.append("Automatic conflict resolution could not improve on the current plan.")
    return plan_output, notes


def _attempt_merge(bundle: PlanningBundle, plan_output: PlanOutput) -> PlanOutput | None:
    """Fold excluded students into an existing group if their availability covers it."""

    if not plan_output.excluded_students or not plan_output.groups:
        return None

    context, normalized_by_student = normalize_weekly_availability(bundle)

    updated_groups = [group.model_copy(deep=True) for group in plan_output.groups]
    still_excluded: list[ExcludedStudent] = []
    changed = False

    for excluded in plan_output.excluded_students:
        normalized = normalized_by_student.get(excluded.student_id)
        if normalized is None or normalized.slot_bitmap == 0:
            still_excluded.append(excluded)
            continue

        merged_into = None
        for group in updated_groups:
            local_start = group.chosen_window.start_local.astimezone(context.timezone)
            start_slot = round(
                (local_start - context.week_start_at).total_seconds()
                / 60
                / context.slot_minutes
            )
            required_mask = contiguous_range_mask(
                start_slot, start_slot + context.session_slot_count
            )
            if normalized.slot_bitmap & required_mask == required_mask:
                merged_into = group
                break

        if merged_into is None:
            still_excluded.append(excluded)
            continue

        merged_into.student_ids.append(excluded.student_id)
        if normalized.response_mode == "zoom":
            merged_into.participation_summary.zoom += 1
        elif normalized.response_mode == "in_person":
            merged_into.participation_summary.in_person += 1
        else:
            merged_into.participation_summary.unknown += 1
        merged_into.notes.append(
            f"Merged in after conflict resolution: {excluded.student_id}."
        )
        changed = True

    if not changed:
        return None

    covered_student_ids = {
        student_id for group in updated_groups for student_id in group.student_ids
    }
    summary = plan_output.summary.model_copy(
        update={
            "covered_student_count": len(covered_student_ids),
            "excluded_student_count": len(still_excluded),
        }
    )
    return plan_output.model_copy(
        update={
            "groups": updated_groups,
            "excluded_students": sorted(still_excluded, key=lambda student: student.student_id),
            "summary": summary,
        }
    )


def _attempt_relaxed_replan(bundle: PlanningBundle) -> PlanOutput | None:
    """Re-run the planner with the minimum group size temporarily lowered to 2."""

    current_minimum = bundle.class_config.weekly_config.minimum_group_size
    if current_minimum <= 2:
        return None

    relaxed_weekly_config = bundle.class_config.weekly_config.model_copy(
        update={"minimum_group_size": 2}
    )
    relaxed_class_config = bundle.class_config.model_copy(
        update={"weekly_config": relaxed_weekly_config}
    )
    relaxed_bundle = bundle.model_copy(update={"class_config": relaxed_class_config})
    return build_plan(relaxed_bundle)
