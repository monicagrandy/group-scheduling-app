from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrictModel(BaseModel):

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class WeeklyConfig(StrictModel):
    """Planning constraints that shape a single week's scheduling search."""

    max_groups: int | None = Field(default=None, ge=1)
    session_duration_minutes: int = Field(default=120, ge=30, multiple_of=30)
    minimum_group_size: int = Field(default=2, ge=2)


class ClassConfig(StrictModel):
    """Top-level class metadata used by every planning run."""

    class_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    weekly_config: WeeklyConfig = Field(default_factory=WeeklyConfig)


class StudentRecord(StrictModel):
    """Roster entry for a single student."""

    student_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    active: bool = True


class RosterInput(StrictModel):
    """Active class roster referenced by the planner."""

    class_id: str = Field(min_length=1)
    students: list[StudentRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_student_ids(self) -> "RosterInput":
        """Prevent ambiguous cross-references during planning."""

        student_ids = [student.student_id for student in self.students]
        if len(student_ids) != len(set(student_ids)):
            raise ValueError("roster contains duplicate student_id values")
        return self


class AvailabilityBlock(StrictModel):
    """One positive local-time availability interval for a student."""

    start_local: datetime
    end_local: datetime

    @model_validator(mode="after")
    def validate_range(self) -> "AvailabilityBlock":
        """Require strictly increasing time ranges."""

        if self.end_local <= self.start_local:
            raise ValueError("availability block end_local must be after start_local")
        return self


class StudentAvailability(StrictModel):
    """Planner-ready weekly availability for a single student."""

    student_id: str = Field(min_length=1)
    raw_text: str | None = None
    notes: list[str] = Field(default_factory=list)
    blocks: list[AvailabilityBlock] = Field(default_factory=list)


class WeeklyAvailabilityInput(StrictModel):
    """All submitted availability for one class and target week."""

    class_id: str = Field(min_length=1)
    week_start_local: date
    week_end_local: date
    entries: list[StudentAvailability] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_entries(self) -> "WeeklyAvailabilityInput":
        """Enforce a valid week range and unique student submissions."""

        if self.week_end_local < self.week_start_local:
            raise ValueError("week_end_local must be on or after week_start_local")
        student_ids = [entry.student_id for entry in self.entries]
        if len(student_ids) != len(set(student_ids)):
            raise ValueError("weekly availability contains duplicate student_id values")
        return self


class PlanningBundle(StrictModel):
    """Single payload consumed by the planner."""

    class_config: ClassConfig
    roster: RosterInput
    availability: WeeklyAvailabilityInput

    @model_validator(mode="after")
    def validate_cross_references(self) -> "PlanningBundle":
        """Verify that roster and availability belong to the same class."""

        class_id = self.class_config.class_id
        if self.roster.class_id != class_id:
            raise ValueError("roster.class_id must match class_config.class_id")
        if self.availability.class_id != class_id:
            raise ValueError("availability.class_id must match class_config.class_id")
        roster_ids = {student.student_id for student in self.roster.students}
        unknown_ids = [
            entry.student_id
            for entry in self.availability.entries
            if entry.student_id not in roster_ids
        ]
        if unknown_ids:
            raise ValueError(
                "weekly availability contains student_id values not present in roster: "
                + ", ".join(sorted(unknown_ids))
            )
        return self


class ChosenWindow(StrictModel):
    """Selected meeting window for a planned group."""

    window_id: str = Field(min_length=1)
    start_local: datetime
    end_local: datetime
    timezone: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> "ChosenWindow":
        """Require the chosen meeting window to have positive duration."""

        if self.end_local <= self.start_local:
            raise ValueError("chosen window end_local must be after start_local")
        return self


class ParticipationSummary(StrictModel):
    """Counts of participation modes inside one planned group."""

    zoom: int = Field(default=0, ge=0)
    in_person: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)


class PlannedGroup(StrictModel):
    """Planner output for a single group."""

    group_number: int = Field(ge=1)
    student_ids: list[str] = Field(min_length=1)
    chosen_window: ChosenWindow
    candidate_window_count: int = Field(ge=1)
    buffer_score: float = Field(ge=0)
    participation_summary: ParticipationSummary = Field(default_factory=ParticipationSummary)
    notes: list[str] = Field(default_factory=list)


class ExcludedStudent(StrictModel):
    """Student omitted from the final draft plus the reason why."""

    student_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)


class PlanSummary(StrictModel):
    """Aggregate counts for the generated draft schedule."""

    requested_student_count: int = Field(ge=0)
    responded_student_count: int = Field(ge=0)
    covered_student_count: int = Field(ge=0)
    excluded_student_count: int = Field(ge=0)
    group_count: int = Field(ge=0)


class PlanOutput(StrictModel):
    """Top-level planner response returned to scripts and future APIs."""

    class_id: str = Field(min_length=1)
    week_start_local: date
    week_end_local: date
    timezone: str = Field(min_length=1)
    requested_max_groups: int = Field(ge=1)
    generated_at: datetime
    summary: PlanSummary
    groups: list[PlannedGroup] = Field(default_factory=list)
    excluded_students: list[ExcludedStudent] = Field(default_factory=list)
    planner_notes: list[str] = Field(default_factory=list)
