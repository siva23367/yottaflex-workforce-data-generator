"""Typed workforce records and their relational metadata."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, time
from enum import Enum
from functools import lru_cache
from typing import ClassVar, get_type_hints


@lru_cache(maxsize=None)
def _type_hints(model: type) -> dict[str, type]:
    return get_type_hints(model)


class LeaveStatus(str, Enum):
    APPROVED = "Approved"
    REJECTED = "Rejected"
    PENDING = "Pending"


class SwapStatus(str, Enum):
    APPROVED = "Approved"
    PENDING = "Pending"
    DENIED = "Denied"


class Record:
    primary_key: ClassVar[tuple[str, ...]] = ()

    def __post_init__(self) -> None:
        annotations = _type_hints(type(self))
        for model_field in fields(self):
            value = getattr(self, model_field.name)
            expected_type = annotations[model_field.name]
            if not isinstance(value, expected_type):
                raise TypeError(
                    f"{type(self).__name__}.{model_field.name} must be "
                    f"{getattr(expected_type, '__name__', str(expected_type))}, "
                    f"got {type(value).__name__}"
                )
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"{type(self).__name__}.{model_field.name} is required")
        if getattr(self, "weekly_hours_cap", 1) <= 0:
            raise ValueError("weekly_hours_cap must be positive")
        if getattr(self, "hours", 1) <= 0:
            raise ValueError("hours must be positive")
        if getattr(self, "min_staff_required", 1) <= 0:
            raise ValueError("min_staff_required must be positive")
        if hasattr(self, "start_date") and self.end_date < self.start_date:
            raise ValueError("end_date cannot precede start_date")

    def to_record(self) -> dict[str, object]:
        values = asdict(self)
        for key, value in values.items():
            if isinstance(value, Enum):
                values[key] = value.value
            elif isinstance(value, datetime):
                values[key] = value.isoformat(timespec="seconds")
            elif isinstance(value, time):
                values[key] = value.strftime("%H:%M")
            elif isinstance(value, date):
                values[key] = value.isoformat()
        return values


@dataclass(frozen=True, slots=True)
class Employee(Record):
    emp_id: str = field(metadata={"primary_key": True})
    name: str
    team_id: str = field(metadata={"foreign_key": ("Team", "team_id")})
    primary_skill_id: str = field(metadata={"foreign_key": ("Skill", "skill_id")})
    weekly_hours_cap: int = 40
    primary_key: ClassVar[tuple[str, ...]] = ("emp_id",)


@dataclass(frozen=True, slots=True)
class EmployeeSkill(Record):
    emp_id: str = field(
        metadata={"primary_key": True, "foreign_key": ("Employee", "emp_id")}
    )
    skill_id: str = field(
        metadata={"primary_key": True, "foreign_key": ("Skill", "skill_id")}
    )
    proficiency: str
    primary_key: ClassVar[tuple[str, ...]] = ("emp_id", "skill_id")


@dataclass(frozen=True, slots=True)
class Team(Record):
    team_id: str = field(metadata={"primary_key": True})
    team_name: str
    primary_key: ClassVar[tuple[str, ...]] = ("team_id",)


@dataclass(frozen=True, slots=True)
class Skill(Record):
    skill_id: str = field(metadata={"primary_key": True})
    skill_name: str
    primary_key: ClassVar[tuple[str, ...]] = ("skill_id",)


@dataclass(frozen=True, slots=True)
class ShiftTemplate(Record):
    template_id: str = field(metadata={"primary_key": True})
    shift_name: str
    start_time: time
    end_time: time
    hours: int
    primary_key: ClassVar[tuple[str, ...]] = ("template_id",)


@dataclass(frozen=True, slots=True)
class ShiftAssignment(Record):
    assignment_id: str = field(metadata={"primary_key": True})
    emp_id: str = field(metadata={"foreign_key": ("Employee", "emp_id")})
    date: date
    template_id: str = field(metadata={"foreign_key": ("ShiftTemplate", "template_id")})
    primary_key: ClassVar[tuple[str, ...]] = ("assignment_id",)


@dataclass(frozen=True, slots=True)
class ShiftAssignmentEvent(Record):
    assignment_event_id: str = field(metadata={"primary_key": True})
    assignment_id: str = field(
        metadata={"foreign_key": ("ShiftAssignment", "assignment_id")}
    )
    previous_emp_id: str | None = field(
        metadata={"foreign_key": ("Employee", "emp_id")}
    )
    new_emp_id: str = field(metadata={"foreign_key": ("Employee", "emp_id")})
    event_type: str
    event_at: datetime
    actor: str
    related_swap_id: str | None = field(
        default=None,
        metadata={"foreign_key": ("SwapRequest", "swap_id")},
    )
    primary_key: ClassVar[tuple[str, ...]] = ("assignment_event_id",)


@dataclass(frozen=True, slots=True)
class LeaveRequest(Record):
    leave_id: str = field(metadata={"primary_key": True})
    emp_id: str = field(metadata={"foreign_key": ("Employee", "emp_id")})
    start_date: date
    end_date: date
    status: LeaveStatus
    primary_key: ClassVar[tuple[str, ...]] = ("leave_id",)


@dataclass(frozen=True, slots=True)
class LeaveRequestEvent(Record):
    leave_event_id: str = field(metadata={"primary_key": True})
    leave_id: str = field(metadata={"foreign_key": ("LeaveRequest", "leave_id")})
    event_type: str
    from_status: str
    to_status: str
    event_at: datetime
    actor: str
    primary_key: ClassVar[tuple[str, ...]] = ("leave_event_id",)


@dataclass(frozen=True, slots=True)
class HolidayCalendar(Record):
    holiday_date: date = field(metadata={"primary_key": True})
    holiday_name: str
    primary_key: ClassVar[tuple[str, ...]] = ("holiday_date",)


@dataclass(frozen=True, slots=True)
class AttendanceEvent(Record):
    attendance_id: str = field(metadata={"primary_key": True})
    assignment_id: str = field(metadata={"foreign_key": ("ShiftAssignment", "assignment_id")})
    emp_id: str = field(metadata={"foreign_key": ("Employee", "emp_id")})
    attendance_status: str
    check_in: datetime | None
    check_out: datetime | None
    primary_key: ClassVar[tuple[str, ...]] = ("attendance_id",)


@dataclass(frozen=True, slots=True)
class CoverageRequirement(Record):
    requirement_id: str = field(metadata={"primary_key": True})
    date: date
    template_id: str = field(metadata={"foreign_key": ("ShiftTemplate", "template_id")})
    skill_id: str = field(metadata={"foreign_key": ("Skill", "skill_id")})
    min_staff_required: int
    primary_key: ClassVar[tuple[str, ...]] = ("requirement_id",)


@dataclass(frozen=True, slots=True)
class SwapRequest(Record):
    swap_id: str = field(metadata={"primary_key": True})
    requesting_emp_id: str = field(metadata={"foreign_key": ("Employee", "emp_id")})
    target_emp_id: str = field(metadata={"foreign_key": ("Employee", "emp_id")})
    assignment_id: str = field(metadata={"foreign_key": ("ShiftAssignment", "assignment_id")})
    status: SwapStatus
    primary_key: ClassVar[tuple[str, ...]] = ("swap_id",)


@dataclass(frozen=True, slots=True)
class SwapRequestEvent(Record):
    swap_event_id: str = field(metadata={"primary_key": True})
    swap_id: str = field(metadata={"foreign_key": ("SwapRequest", "swap_id")})
    event_type: str
    from_status: str
    to_status: str
    event_at: datetime
    actor: str
    primary_key: ClassVar[tuple[str, ...]] = ("swap_event_id",)


@dataclass(frozen=True, slots=True)
class CoverageGap(Record):
    gap_id: str = field(metadata={"primary_key": True})
    requirement_id: str = field(metadata={"foreign_key": ("CoverageRequirement", "requirement_id")})
    assigned_staff: int
    gap: int
    status: str
    primary_key: ClassVar[tuple[str, ...]] = ("gap_id",)


@dataclass(frozen=True, slots=True)
class OvertimeSummary(Record):
    summary_id: str = field(metadata={"primary_key": True})
    emp_id: str = field(metadata={"foreign_key": ("Employee", "emp_id")})
    week_start: date
    scheduled_hours: int
    actual_worked_seconds: int
    weekly_hours_cap: int
    scheduled_overtime_hours: int
    actual_overtime_seconds: int
    primary_key: ClassVar[tuple[str, ...]] = ("summary_id",)


TABLE_MODELS: dict[str, type[Record]] = {
    model.__name__: model
    for model in (
        Employee,
        EmployeeSkill,
        Team,
        Skill,
        ShiftTemplate,
        ShiftAssignment,
        ShiftAssignmentEvent,
        LeaveRequest,
        LeaveRequestEvent,
        HolidayCalendar,
        AttendanceEvent,
        CoverageRequirement,
        SwapRequest,
        SwapRequestEvent,
        CoverageGap,
        OvertimeSummary,
    )
}

FOREIGN_KEYS: dict[str, dict[str, tuple[str, str]]] = {
    table_name: {
        model_field.name: model_field.metadata["foreign_key"]
        for model_field in fields(model)
        if "foreign_key" in model_field.metadata
    }
    for table_name, model in TABLE_MODELS.items()
}


def record_fields(model: type[Record]) -> tuple[str, ...]:
    return tuple(field.name for field in fields(model))
