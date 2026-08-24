"""Exact search planner that builds balanced groups with shared meeting windows."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from math import floor

from app.algorithms.normalization import (
    NormalizationContext,
    SLOTS_PER_DAY,
    iter_set_bits,
    normalize_weekly_availability,
)
from app.schemas import (
    ChosenWindow,
    ExcludedStudent,
    PlanOutput,
    PlannedGroup,
    PlanningBundle,
    PlanSummary,
)


@dataclass(frozen=True)
class EligibleStudent:
    """Planner-facing projection of a student who still has usable availability."""

    student_id: str
    name: str
    slot_bitmap: int
    window_bitmap: int
    window_starts: tuple[int, ...]


@dataclass(frozen=True)
class CandidateGroup:
    """One fully specified group candidate plus its preferred meeting window."""

    student_indices: tuple[int, ...]
    student_ids: tuple[str, ...]
    member_mask: int
    chosen_start_slot: int
    candidate_window_count: int
    buffer_floor: int
    buffer_score: float


@dataclass(frozen=True)
class SearchOutcome:
    """Exact-search result containing planned groups and excluded students."""

    groups: tuple[CandidateGroup, ...]
    excluded_indices: tuple[int, ...]


@dataclass(frozen=True)
class PlannerSearchResult:
    """One valid plan found for a requested max group count."""

    requested_max_groups: int
    covered_count: int
    group_count: int
    outcome: SearchOutcome



def build_plan(bundle: PlanningBundle) -> PlanOutput:
    """Build the highest-ranked draft schedule for one planning bundle."""

    return build_plan_options(bundle)[0]


def build_plan_options(bundle: PlanningBundle) -> list[PlanOutput]:
    """Build every maximum-coverage arrangement, ranked best-first.

    A group arrangement is a partition of the covered students. Each group is
    paired with its best shared meeting window; alternate windows for the same
    membership do not create duplicate arrangements.
    """

    context, normalized_by_student = normalize_weekly_availability(bundle)
    active_students = [student for student in bundle.roster.students if student.active]
    responded_students = [
        student for student in active_students if student.student_id in normalized_by_student
    ]

    eligible_students: list[EligibleStudent] = []
    excluded_students: list[ExcludedStudent] = []

    for student in active_students:
        normalized = normalized_by_student.get(student.student_id)
        if normalized is None:
            excluded_students.append(
                ExcludedStudent(
                    student_id=student.student_id,
                    reason="No availability submitted for the planning week.",
                )
            )
            continue
        if normalized.slot_bitmap == 0:
            excluded_students.append(
                ExcludedStudent(
                    student_id=student.student_id,
                    reason="Availability produced no valid 30-minute slots after normalization.",
                    notes=list(normalized.notes),
                )
            )
            continue
        if not normalized.window_starts:
            excluded_students.append(
                ExcludedStudent(
                    student_id=student.student_id,
                    reason="Availability does not contain a shared 2-hour window candidate.",
                    notes=list(normalized.notes),
                )
            )
            continue
        eligible_students.append(
            EligibleStudent(
                student_id=student.student_id,
                name=student.name,
                slot_bitmap=normalized.slot_bitmap,
                window_bitmap=normalized.window_bitmap,
                window_starts=normalized.window_starts,
            )
        )

    # Search the hardest-to-place students first to prune the exact search quickly.
    eligible_students.sort(key=lambda student: (len(student.window_starts), student.student_id))

    requested_max_groups = resolve_max_groups(
        configured_max_groups=bundle.class_config.weekly_config.max_groups,
        active_student_count=len(active_students),
    )
    minimum_group_size = bundle.class_config.weekly_config.minimum_group_size

    search_results = find_all_plans(
        eligible_students=eligible_students,
        requested_max_groups=requested_max_groups,
        minimum_group_size=minimum_group_size,
        context=context,
    )

    generated_at = datetime.now(context.timezone)
    if not search_results:
        for student in eligible_students:
            excluded_students.append(
                ExcludedStudent(
                    student_id=student.student_id,
                    reason="No valid group could be formed under the current constraints.",
                )
            )
        return [
            _build_plan_output(
                bundle=bundle,
                context=context,
                active_student_count=len(active_students),
                responded_student_count=len(responded_students),
                eligible_student_count=len(eligible_students),
                requested_max_groups=requested_max_groups,
                base_excluded_students=excluded_students,
                eligible_students=eligible_students,
                search_result=None,
                arrangement_number=1,
                arrangement_count=1,
                generated_at=generated_at,
            )
        ]

    return [
        _build_plan_output(
            bundle=bundle,
            context=context,
            active_student_count=len(active_students),
            responded_student_count=len(responded_students),
            eligible_student_count=len(eligible_students),
            requested_max_groups=requested_max_groups,
            base_excluded_students=excluded_students,
            eligible_students=eligible_students,
            search_result=search_result,
            arrangement_number=index,
            arrangement_count=len(search_results),
            generated_at=generated_at,
        )
        for index, search_result in enumerate(search_results, start=1)
    ]


def _build_plan_output(
    *,
    bundle: PlanningBundle,
    context: NormalizationContext,
    active_student_count: int,
    responded_student_count: int,
    eligible_student_count: int,
    requested_max_groups: int,
    base_excluded_students: list[ExcludedStudent],
    eligible_students: list[EligibleStudent],
    search_result: PlannerSearchResult | None,
    arrangement_number: int,
    arrangement_count: int,
    generated_at: datetime,
) -> PlanOutput:
    """Convert one search result into the public planner response."""

    planned_groups: list[PlannedGroup] = []
    excluded_students = list(base_excluded_students)
    if search_result is not None:
        excluded_id_set = {
            eligible_students[index].student_id
            for index in search_result.outcome.excluded_indices
        }
        for student_id in excluded_id_set:
            excluded_students.append(
                ExcludedStudent(
                    student_id=student_id,
                    reason="Excluded to preserve valid groups and maximize total coverage.",
                )
            )

        sorted_groups = sorted(
            search_result.outcome.groups,
            key=lambda group: (group.chosen_start_slot, group.student_ids),
        )
        for group_number, candidate in enumerate(sorted_groups, start=1):
            planned_groups.append(
                PlannedGroup(
                    group_number=group_number,
                    student_ids=list(candidate.student_ids),
                    chosen_window=ChosenWindow(
                        window_id=context.window_id_from_start_slot(candidate.chosen_start_slot),
                        start_local=context.slot_index_to_datetime(candidate.chosen_start_slot),
                        end_local=context.window_end_datetime(candidate.chosen_start_slot),
                        timezone=context.timezone_name,
                    ),
                    candidate_window_count=candidate.candidate_window_count,
                    buffer_score=round(candidate.buffer_score, 3),
                    notes=[],
                )
            )

    covered_student_ids = {
        student_id for group in planned_groups for student_id in group.student_ids
    }
    summary = PlanSummary(
        requested_student_count=active_student_count,
        responded_student_count=responded_student_count,
        covered_student_count=len(covered_student_ids),
        excluded_student_count=len(excluded_students),
        group_count=len(planned_groups),
    )

    planner_notes = [
        f"Resolved max_groups={requested_max_groups} for {active_student_count} active students.",
        f"Received availability from {responded_student_count} of {active_student_count} active students.",
        f"{eligible_student_count} students produced at least one valid meeting window after normalization.",
    ]
    if search_result is None:
        planner_notes.append("No valid group configuration was found.")
    else:
        planner_notes.append(
            f"Arrangement {arrangement_number} of {arrangement_count}: "
            f"{search_result.group_count} group(s) covering {search_result.covered_count} student(s)."
        )

    return PlanOutput(
        class_id=bundle.class_config.class_id,
        week_start_local=bundle.availability.week_start_local,
        week_end_local=bundle.availability.week_end_local,
        timezone=bundle.class_config.timezone,
        requested_max_groups=requested_max_groups,
        generated_at=generated_at,
        summary=summary,
        groups=planned_groups,
        excluded_students=sorted(excluded_students, key=lambda student: student.student_id),
        planner_notes=planner_notes,
    )



def resolve_max_groups(configured_max_groups: int | None, active_student_count: int) -> int:
    """Resolve the effective max group count for this planning run."""

    if configured_max_groups is not None:
        return configured_max_groups
    return max(1, floor(active_student_count / 2))



def find_best_plan(
    eligible_students: list[EligibleStudent],
    requested_max_groups: int,
    minimum_group_size: int,
    context: NormalizationContext,
) -> PlannerSearchResult | None:
    """Return the highest-ranked maximum-coverage arrangement."""

    plans = find_all_plans(
        eligible_students=eligible_students,
        requested_max_groups=requested_max_groups,
        minimum_group_size=minimum_group_size,
        context=context,
    )
    return plans[0] if plans else None


def find_all_plans(
    eligible_students: list[EligibleStudent],
    requested_max_groups: int,
    minimum_group_size: int,
    context: NormalizationContext,
) -> list[PlannerSearchResult]:
    """Enumerate every arrangement at the highest achievable coverage.

    Ranking favors fewer, larger groups before window flexibility and buffer.
    This prevents the configured maximum from being treated as a target that
    unnecessarily fragments six compatible students into three pairs.
    """

    eligible_count = len(eligible_students)
    if eligible_count < minimum_group_size:
        return []

    for covered_count in range(eligible_count, minimum_group_size - 1, -1):
        results: list[PlannerSearchResult] = []
        max_group_count = min(requested_max_groups, covered_count // minimum_group_size)
        for group_count in range(1, max_group_count + 1):
            for size_distribution in group_size_distributions(
                covered_count, group_count, minimum_group_size
            ):
                for outcome in solve_all_exact_groupings(
                    eligible_students=eligible_students,
                    size_distribution=size_distribution,
                    exclusion_budget=eligible_count - covered_count,
                    context=context,
                ):
                    results.append(
                        PlannerSearchResult(
                            requested_max_groups=requested_max_groups,
                            covered_count=covered_count,
                            group_count=group_count,
                            outcome=outcome,
                        )
                    )
        if results:
            results.sort(key=lambda result: plan_sort_key(result, eligible_students))
            return results
    return []


def group_size_distributions(
    covered_count: int,
    group_count: int,
    minimum_group_size: int,
) -> tuple[tuple[int, ...], ...]:
    """Return every non-increasing group-size partition meeting the minimum."""

    distributions: list[tuple[int, ...]] = []

    def backtrack(remaining: int, groups_left: int, maximum_size: int, sizes: list[int]) -> None:
        if groups_left == 0:
            if remaining == 0:
                distributions.append(tuple(sizes))
            return

        largest_possible = min(maximum_size, remaining - minimum_group_size * (groups_left - 1))
        for size in range(largest_possible, minimum_group_size - 1, -1):
            next_remaining = remaining - size
            if next_remaining < minimum_group_size * (groups_left - 1):
                continue
            backtrack(next_remaining, groups_left - 1, size, sizes + [size])

    if group_count > 0 and covered_count >= group_count * minimum_group_size:
        backtrack(covered_count, group_count, covered_count, [])
    return tuple(distributions)



def balanced_group_sizes(covered_count: int, group_count: int) -> tuple[int, ...] | None:
    """Distribute students as evenly as possible across `group_count` groups."""

    if group_count <= 0 or covered_count < group_count:
        return None
    small = covered_count // group_count
    large = small + 1 if covered_count % group_count else small
    large_group_count = covered_count % group_count
    sizes = [large] * large_group_count + [small] * (group_count - large_group_count)
    return tuple(sorted(sizes, reverse=True))



def solve_exact_grouping(
    eligible_students: list[EligibleStudent],
    size_distribution: tuple[int, ...],
    exclusion_budget: int,
    context: NormalizationContext,
) -> SearchOutcome | None:
    """Return the best outcome for one fixed size distribution."""

    outcomes = solve_all_exact_groupings(
        eligible_students=eligible_students,
        size_distribution=size_distribution,
        exclusion_budget=exclusion_budget,
        context=context,
    )
    if not outcomes:
        return None

    best_outcome: SearchOutcome | None = None
    for outcome in outcomes:
        best_outcome = choose_better_outcome(
            current=best_outcome,
            proposed=outcome,
            eligible_students=eligible_students,
        )
    return best_outcome


def solve_all_exact_groupings(
    eligible_students: list[EligibleStudent],
    size_distribution: tuple[int, ...],
    exclusion_budget: int,
    context: NormalizationContext,
) -> tuple[SearchOutcome, ...]:
    """Enumerate all exact groupings for one fixed size distribution."""

    candidates_by_size = {
        size: generate_candidate_groups(eligible_students, size, context)
        for size in set(size_distribution)
    }
    if any(not candidates_by_size[size] for size in set(size_distribution)):
        return ()

    candidates_by_size_and_member: dict[int, dict[int, tuple[CandidateGroup, ...]]] = {}
    for size, candidates in candidates_by_size.items():
        index: dict[int, list[CandidateGroup]] = {}
        for candidate in candidates:
            for member in candidate.student_indices:
                index.setdefault(member, []).append(candidate)
        candidates_by_size_and_member[size] = {
            member: tuple(groups)
            for member, groups in index.items()
        }

    size_values = tuple(sorted(set(size_distribution), reverse=True))
    size_counts = Counter(size_distribution)
    initial_remaining_counts = tuple(size_counts[size] for size in size_values)

    @lru_cache(maxsize=None)
    def search(
        resolved_mask: int,
        exclusion_remaining: int,
        remaining_counts: tuple[int, ...],
    ) -> tuple[SearchOutcome, ...]:
        """Backtracking search over unresolved students.

        `resolved_mask` tracks both grouped students and deliberate exclusions.
        """

        unresolved_indices = tuple(
            index
            for index in range(len(eligible_students))
            if not resolved_mask & (1 << index)
        )
        remaining_group_slots = sum(
            size_values[idx] * remaining_counts[idx]
            for idx in range(len(size_values))
        )
        if len(unresolved_indices) != exclusion_remaining + remaining_group_slots:
            return ()
        if remaining_group_slots == 0:
            if len(unresolved_indices) != exclusion_remaining:
                return ()
            return (SearchOutcome(groups=(), excluded_indices=unresolved_indices),)

        pivot = unresolved_indices[0]
        outcomes: list[SearchOutcome] = []

        for size_index, group_size in enumerate(size_values):
            if remaining_counts[size_index] == 0:
                continue
            for candidate in candidates_by_size_and_member[group_size].get(pivot, ()):
                if candidate.member_mask & resolved_mask:
                    continue
                next_counts = list(remaining_counts)
                next_counts[size_index] -= 1
                branch_outcomes = search(
                    resolved_mask | candidate.member_mask,
                    exclusion_remaining,
                    tuple(next_counts),
                )
                outcomes.extend(
                    SearchOutcome(
                        groups=(candidate,) + branch_outcome.groups,
                        excluded_indices=branch_outcome.excluded_indices,
                    )
                    for branch_outcome in branch_outcomes
                )

        if exclusion_remaining > 0:
            # If no group placement works for the pivot, try consuming one exclusion.
            branch_outcomes = search(
                resolved_mask | (1 << pivot),
                exclusion_remaining - 1,
                remaining_counts,
            )
            outcomes.extend(
                SearchOutcome(
                    groups=branch_outcome.groups,
                    excluded_indices=(pivot,) + branch_outcome.excluded_indices,
                )
                for branch_outcome in branch_outcomes
            )

        return tuple(outcomes)

    return search(0, exclusion_budget, initial_remaining_counts)



def generate_candidate_groups(
    eligible_students: list[EligibleStudent],
    group_size: int,
    context: NormalizationContext,
) -> list[CandidateGroup]:
    """Enumerate all student combinations of `group_size` with a shared window."""

    candidates: list[CandidateGroup] = []

    def backtrack(
        next_index: int,
        chosen_indices: list[int],
        member_mask: int,
        common_windows: int,
    ) -> None:
        if len(chosen_indices) == group_size:
            window_starts = tuple(iter_set_bits(common_windows))
            if not window_starts:
                return
            chosen_start_slot, buffer_floor, buffer_score = choose_best_window(
                eligible_students=eligible_students,
                member_indices=tuple(chosen_indices),
                window_starts=window_starts,
                context=context,
            )
            candidate = CandidateGroup(
                student_indices=tuple(chosen_indices),
                student_ids=tuple(
                    sorted(eligible_students[index].student_id for index in chosen_indices)
                ),
                member_mask=member_mask,
                chosen_start_slot=chosen_start_slot,
                candidate_window_count=len(window_starts),
                buffer_floor=buffer_floor,
                buffer_score=buffer_score,
            )
            candidates.append(candidate)
            return

        remaining_needed = group_size - len(chosen_indices)
        max_start = len(eligible_students) - remaining_needed
        for student_index in range(next_index, max_start + 1):
            student = eligible_students[student_index]
            # Intersect window bitmaps incrementally so dead branches fail early.
            next_common_windows = (
                student.window_bitmap
                if not chosen_indices
                else common_windows & student.window_bitmap
            )
            if next_common_windows == 0:
                continue
            backtrack(
                student_index + 1,
                chosen_indices + [student_index],
                member_mask | (1 << student_index),
                next_common_windows,
            )

    backtrack(0, [], 0, 0)
    candidates.sort(
        key=lambda candidate: (
            -candidate.candidate_window_count,
            -candidate.buffer_floor,
            -candidate.buffer_score,
            candidate.chosen_start_slot,
            candidate.student_ids,
        )
    )
    return candidates



def choose_best_window(
    eligible_students: list[EligibleStudent],
    member_indices: tuple[int, ...],
    window_starts: tuple[int, ...],
    context: NormalizationContext,
) -> tuple[int, int, float]:
    """Pick the shared meeting window with the most slack around it."""

    best_window: tuple[int, int, float] | None = None
    for start_slot in window_starts:
        slack_values = [
            slack_around_window(
                slot_bitmap=eligible_students[index].slot_bitmap,
                start_slot=start_slot,
                session_slot_count=context.session_slot_count,
            )
            for index in member_indices
        ]
        buffer_floor = min(slack_values)
        buffer_score = sum(slack_values) / len(slack_values)
        candidate = (buffer_floor, buffer_score, -start_slot)
        if best_window is None or candidate > (
            best_window[1],
            best_window[2],
            -best_window[0],
        ):
            best_window = (start_slot, buffer_floor, buffer_score)
    assert best_window is not None
    return best_window



def slack_around_window(slot_bitmap: int, start_slot: int, session_slot_count: int) -> int:
    """Measure how much extra contiguous availability surrounds a chosen window."""

    end_slot = start_slot + session_slot_count - 1
    left = 0
    cursor = start_slot - 1
    while cursor >= 0 and cursor // SLOTS_PER_DAY == start_slot // SLOTS_PER_DAY:
        if not slot_bitmap & (1 << cursor):
            break
        left += 1
        cursor -= 1

    right = 0
    cursor = end_slot + 1
    while cursor // SLOTS_PER_DAY == end_slot // SLOTS_PER_DAY:
        if not slot_bitmap & (1 << cursor):
            break
        right += 1
        cursor += 1

    return left + right



def choose_better_outcome(
    current: SearchOutcome | None,
    proposed: SearchOutcome,
    eligible_students: list[EligibleStudent],
) -> SearchOutcome:
    """Compare two outcomes using window richness, slack, then deterministic tie-breaks."""

    if current is None:
        return proposed

    current_score = outcome_score(current)
    proposed_score = outcome_score(proposed)
    if proposed_score > current_score:
        return proposed
    if proposed_score < current_score:
        return current

    current_token = outcome_token(current, eligible_students)
    proposed_token = outcome_token(proposed, eligible_students)
    if proposed_token < current_token:
        return proposed
    return current



def outcome_score(outcome: SearchOutcome) -> tuple[int, int, float]:
    """Score an outcome by window flexibility first, then by extra buffer."""

    return (
        sum(group.candidate_window_count for group in outcome.groups),
        sum(group.buffer_floor for group in outcome.groups),
        round(sum(group.buffer_score for group in outcome.groups), 6),
    )



def outcome_token(
    outcome: SearchOutcome,
    eligible_students: list[EligibleStudent],
) -> tuple[tuple[tuple[int, tuple[str, ...]], ...], tuple[str, ...]]:
    """Create a deterministic token so equal-scoring outcomes sort consistently."""

    group_token = tuple(
        sorted((group.chosen_start_slot, group.student_ids) for group in outcome.groups)
    )
    excluded_token = tuple(
        sorted(eligible_students[index].student_id for index in outcome.excluded_indices)
    )
    return group_token, excluded_token


def plan_sort_key(
    result: PlannerSearchResult,
    eligible_students: list[EligibleStudent],
) -> tuple:
    """Rank maximum-coverage plans with deterministic, user-facing priorities."""

    group_sizes = [len(group.student_indices) for group in result.outcome.groups]
    size_spread = max(group_sizes) - min(group_sizes)
    flexibility, buffer_floor, buffer_score = outcome_score(result.outcome)
    return (
        result.group_count,
        size_spread,
        -flexibility,
        -buffer_floor,
        -buffer_score,
        outcome_token(result.outcome, eligible_students),
    )
