"""Deterministic workforce scheduling data generator."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TypeVar

from models import (
    FOREIGN_KEYS,
    TABLE_MODELS,
    AttendanceEvent,
    CoverageRequirement,
    CoverageGap,
    Employee,
    EmployeeSkill,
    HolidayCalendar,
    LeaveRequest,
    LeaveRequestEvent,
    LeaveStatus,
    OvertimeSummary,
    Record,
    ShiftAssignment,
    ShiftAssignmentEvent,
    ShiftTemplate,
    Skill,
    SwapRequest,
    SwapRequestEvent,
    SwapStatus,
    Team,
    record_fields,
)
from validate import validate

DEFAULT_SEED = 20261022
SKILL_NAMES = (
    "Customer Support",
    "Warehouse",
    "Logistics",
    "Sales",
    "IT Support",
    "Finance",
    "Security",
    "Operations",
)
SHIFT_DEFINITIONS = (
    ("A", "Day", time(6), time(14)),
    ("B", "Evening", time(14), time(22)),
    ("C", "Night", time(22), time(6)),
)
LEGACY_TABLE_FILES = ("LeaveBalanceSummary",)


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    seed: int = DEFAULT_SEED
    employees: int = 2000
    teams: int = 100
    days: int = 90
    leaves: int = 5000
    start: date = date(2026, 1, 1)
    anomaly_rate: float = 0.01

    def __post_init__(self) -> None:
        if self.teams < 1 or self.employees < max(2, self.teams):
            raise ValueError("Require employees >= max(2, teams) and teams >= 1")
        if self.days < 1 or self.leaves < 0:
            raise ValueError("Require days >= 1 and leaves >= 0")
        if not 0 <= self.anomaly_rate <= 0.05:
            raise ValueError("anomaly_rate must be between 0 and 0.05")


ModelT = TypeVar("ModelT", bound=Record)


def _record(model: type[ModelT], **values: object) -> dict[str, object]:
    return model(**values).to_record()


class WorkforceGenerator:
    def __init__(self, config: GenerationConfig):
        self.config = config
        self.rng = random.Random(config.seed)
        self.tables: dict[str, list[dict[str, object]]] = {
            name: [] for name in TABLE_MODELS
        }
        self.truth: list[dict[str, str]] = []
        self.employees: dict[str, dict[str, object]] = {}
        self.team_members: dict[str, list[str]] = defaultdict(list)
        self.skill_for_team: dict[str, str] = {}
        self.assignments_by_employee_day: dict[tuple[str, date], list[dict[str, object]]] = defaultdict(list)
        self.assignment_serial = 0
        self.assignment_event_serial = 0
        self.leave_serial = 0
        self.coverage_serial = 0
        self.swap_serial = 0
        self.leave_event_serial = 0
        self.swap_event_serial = 0
        self.gap_serial = 0
        self.employee_week_hours: Counter[tuple[str, date]] = Counter()
        self.assignment_totals: Counter[str] = Counter()
        self.used_days: Counter[tuple[str, date]] = Counter()
        self.leave_days: dict[str, set[date]] = defaultdict(set)
        self.skill_ids_by_employee: dict[str, set[str]] = defaultdict(set)
        total_anomalies = max(
            5,
            round(config.teams * config.days * len(SHIFT_DEFINITIONS) * 2 * config.anomaly_rate),
        )
        self.anomalies_per_type = max(1, total_anomalies // 5)

    def add(self, table_name: str, row: dict[str, object]) -> dict[str, object]:
        self.tables[table_name].append(row)
        return row

    def make_masters(self) -> None:
        for index in range(1, self.config.teams + 1):
            team_id = f"TEAM-{index:03d}"
            skill_id = f"SKL-{(index - 1) % len(SKILL_NAMES) + 1:02d}"
            self.skill_for_team[team_id] = skill_id
            self.add("Team", _record(Team, team_id=team_id, team_name=f"Team {index:03d}"))
        for index, name in enumerate(SKILL_NAMES, start=1):
            self.add("Skill", _record(Skill, skill_id=f"SKL-{index:02d}", skill_name=name))
        for code, name, start_time, end_time in SHIFT_DEFINITIONS:
            self.add(
                "ShiftTemplate",
                _record(
                    ShiftTemplate,
                    template_id=f"SFT-{code}",
                    shift_name=name,
                    start_time=start_time,
                    end_time=end_time,
                    hours=8,
                ),
            )
        for index in range(1, self.config.employees + 1):
            team_id = f"TEAM-{(index - 1) % self.config.teams + 1:03d}"
            emp_id = f"EMP-{index:06d}"
            employee = _record(
                Employee,
                emp_id=emp_id,
                name=f"Employee {index:06d}",
                team_id=team_id,
                primary_skill_id=self.skill_for_team[team_id],
            )
            self.add("Employee", employee)
            self.employees[emp_id] = employee
            self.team_members[team_id].append(emp_id)
            primary_skill_id = str(employee["primary_skill_id"])
            self.skill_ids_by_employee[emp_id].add(primary_skill_id)
            self.add(
                "EmployeeSkill",
                _record(
                    EmployeeSkill,
                    emp_id=emp_id,
                    skill_id=primary_skill_id,
                    proficiency=self.rng.choice(("Basic", "Intermediate", "Advanced")),
                ),
            )
            secondary_skill_id = f"SKL-{self.rng.randint(1, len(SKILL_NAMES)):02d}"
            if (
                secondary_skill_id != primary_skill_id
                and self.rng.random() < 0.25
            ):
                self.skill_ids_by_employee[emp_id].add(secondary_skill_id)
                self.add(
                    "EmployeeSkill",
                    _record(
                        EmployeeSkill,
                        emp_id=emp_id,
                        skill_id=secondary_skill_id,
                        proficiency=self.rng.choice(
                            ("Basic", "Intermediate", "Advanced")
                        ),
                    ),
                )

        for holiday_offset, holiday_name in (
            (0, "New Year Observance"),
            (25, "Synthetic Holiday 1"),
            (59, "Synthetic Holiday 2"),
            (75, "Synthetic Holiday 3"),
        ):
            holiday_date = self.config.start + timedelta(days=holiday_offset)
            if holiday_date < self.config.start + timedelta(days=self.config.days):
                self.add(
                    "HolidayCalendar",
                    _record(
                        HolidayCalendar,
                        holiday_date=holiday_date,
                        holiday_name=holiday_name,
                    ),
                )

    def make_leaves(self) -> None:
        if not self.employees:
            return
        start = self.config.start
        final_day = start + timedelta(days=self.config.days - 1)
        emp_ids = sorted(self.employees)
        for _ in range(self.config.leaves):
            self.leave_serial += 1
            emp_id = self.rng.choice(emp_ids)
            leave_start = start + timedelta(days=self.rng.randrange(self.config.days))
            duration = self.rng.choices((1, 2, 3, 4, 5), (45, 25, 15, 10, 5))[0]
            leave_end = min(final_day, leave_start + timedelta(days=duration - 1))
            status = self.rng.choices(
                (LeaveStatus.APPROVED, LeaveStatus.REJECTED, LeaveStatus.PENDING),
                (0.6, 0.2, 0.2),
            )[0]
            if status is LeaveStatus.APPROVED:
                self.leave_days[emp_id].update(
                    leave_start + timedelta(days=offset)
                    for offset in range((leave_end - leave_start).days + 1)
                )
            self.add(
                "LeaveRequest",
                _record(
                    LeaveRequest,
                    leave_id=f"LVE-{start.year}-{self.leave_serial:06d}",
                    emp_id=emp_id,
                    start_date=leave_start,
                    end_date=leave_end,
                    status=status,
                ),
            )

    def _week_start(self, day: date) -> date:
        return day - timedelta(days=day.weekday())

    def _assignment_times(self, day: date, template_id: str) -> tuple[datetime, datetime]:
        code = template_id[-1]
        _, _, start_time, end_time = next(item for item in SHIFT_DEFINITIONS if item[0] == code)
        start = datetime.combine(day, start_time)
        end_day = day + timedelta(days=1) if end_time <= start_time else day
        return start, datetime.combine(end_day, end_time)

    def _new_assignment(
        self,
        emp_id: str,
        day: date,
        template_id: str,
    ) -> dict[str, object]:
        self.assignment_serial += 1
        code = template_id[-1]
        assignment = self.add(
            "ShiftAssignment",
            _record(
                ShiftAssignment,
                assignment_id=f"SHIFT-{day.isoformat()}-{code}-{self.assignment_serial:06d}",
                emp_id=emp_id,
                date=day,
                template_id=template_id,
            ),
        )
        self._record_assignment_event(
            assignment=assignment,
            previous_emp_id=None,
            new_emp_id=emp_id,
            event_type="ASSIGNED",
            event_at=datetime.combine(day - timedelta(days=7), time(9)),
            actor="SCHEDULER",
        )
        self.assignments_by_employee_day[(emp_id, day)].append(assignment)
        week = self._week_start(day)
        self.employee_week_hours[(emp_id, week)] += 8
        self.assignment_totals[emp_id] += 1
        self.used_days[(emp_id, day)] += 1
        return assignment

    def _record_assignment_event(
        self,
        assignment: dict[str, object],
        previous_emp_id: str | None,
        new_emp_id: str,
        event_type: str,
        event_at: datetime,
        actor: str,
        related_swap_id: str | None = None,
    ) -> None:
        self.assignment_event_serial += 1
        self.add(
            "ShiftAssignmentEvent",
            _record(
                ShiftAssignmentEvent,
                assignment_event_id=f"SAE-{self.assignment_event_serial:07d}",
                assignment_id=str(assignment["assignment_id"]),
                previous_emp_id=previous_emp_id,
                new_emp_id=new_emp_id,
                event_type=event_type,
                event_at=event_at,
                actor=actor,
                related_swap_id=related_swap_id,
            ),
        )
        assignment["emp_id"] = new_emp_id

    def make_schedule(self) -> None:
        for day_offset in range(self.config.days):
            day = self.config.start + timedelta(days=day_offset)
            week = self._week_start(day)
            for team_id, members in self.team_members.items():
                for code, _, _, _ in SHIFT_DEFINITIONS:
                    eligible = [
                        emp_id
                        for emp_id in members
                        if day not in self.leave_days[emp_id]
                        and self.used_days[(emp_id, day)] == 0
                        and self.employee_week_hours[(emp_id, week)]
                        + 8
                        <= int(self.employees[emp_id]["weekly_hours_cap"])
                    ]
                    eligible.sort(
                        key=lambda emp_id: (
                            self.employee_week_hours[(emp_id, week)],
                            self.assignment_totals[emp_id],
                            emp_id,
                        )
                    )
                    for emp_id in eligible[:2]:
                        self._new_assignment(emp_id, day, f"SFT-{code}")

    def make_coverage_requirements(self) -> None:
        skills = sorted(str(row["skill_id"]) for row in self.tables["Skill"])
        for day_offset in range(self.config.days):
            day = self.config.start + timedelta(days=day_offset)
            for code, _, _, _ in SHIFT_DEFINITIONS:
                template_id = f"SFT-{code}"
                for skill_id in skills:
                    self.coverage_serial += 1
                    self.add(
                        "CoverageRequirement",
                        _record(
                            CoverageRequirement,
                            requirement_id=f"COV-{self.coverage_serial:06d}",
                            date=day,
                            template_id=template_id,
                            skill_id=skill_id,
                            min_staff_required=1,
                        ),
                    )

    def _label(self, table_name: str, entity_id: str, issue_type: str) -> None:
        label = {"table_name": table_name, "entity_id": entity_id, "issue_type": issue_type}
        if label not in self.truth:
            self.truth.append(label)

    def _inject_double_booking(self, baseline: list[dict[str, object]]) -> None:
        for index in range(self.anomalies_per_type):
            original = baseline[index % len(baseline)]
            duplicate = self._new_assignment(
                str(original["emp_id"]),
                date.fromisoformat(str(original["date"])),
                str(original["template_id"]),
            )
            self._label("ShiftAssignment", str(duplicate["assignment_id"]), "EMPLOYEE_DOUBLE_BOOKED")

    def _inject_leave_overlaps(self, baseline: list[dict[str, object]]) -> None:
        leave_rows = self.tables["LeaveRequest"]
        offset = 40 * self.anomalies_per_type
        for index in range(self.anomalies_per_type):
            assignment = baseline[(offset + index) % len(baseline)]
            day = date.fromisoformat(str(assignment["date"]))
            emp_id = str(assignment["emp_id"])
            if index < len(leave_rows):
                leave = leave_rows[index]
                leave["emp_id"] = emp_id
                leave["start_date"] = day.isoformat()
                leave["end_date"] = day.isoformat()
                leave["status"] = LeaveStatus.APPROVED.value
            else:
                self.leave_serial += 1
                leave = _record(
                    LeaveRequest,
                    leave_id=f"LVE-{self.config.start.year}-{self.leave_serial:06d}",
                    emp_id=emp_id,
                    start_date=day,
                    end_date=day,
                    status=LeaveStatus.APPROVED,
                )
                self.add("LeaveRequest", leave)
            self.leave_days[emp_id].add(day)
            for overlapping in self.assignments_by_employee_day[(emp_id, day)]:
                self._label(
                    "ShiftAssignment",
                    str(overlapping["assignment_id"]),
                    "APPROVED_LEAVE_OVERLAP",
                )

    def _inject_missing_skill(self, baseline: list[dict[str, object]]) -> None:
        requirement_lookup = {
            (
                str(row["date"]),
                str(row["template_id"]),
                str(row["skill_id"]),
            ): row
            for row in self.tables["CoverageRequirement"]
        }
        counts: Counter[tuple[str, str, str]] = Counter()
        for assignment in self.tables["ShiftAssignment"]:
            emp_id = str(assignment["emp_id"])
            for skill_id in self.skill_ids_by_employee[emp_id]:
                counts[
                    (
                        str(assignment["date"]),
                        str(assignment["template_id"]),
                        skill_id,
                    )
                ] += 1
        labelled_requirements = {
            row["entity_id"]
            for row in self.truth
            if row["table_name"] == "CoverageRequirement"
            and row["issue_type"] == "REQUIRED_SKILL_ABSENT"
        }
        for requirement in self.tables["CoverageRequirement"]:
            key = (
                str(requirement["date"]),
                str(requirement["template_id"]),
                str(requirement["skill_id"]),
            )
            if (
                counts[key] < int(requirement["min_staff_required"])
                and requirement["requirement_id"] not in labelled_requirements
            ):
                self._label(
                    "CoverageRequirement",
                    str(requirement["requirement_id"]),
                    "REQUIRED_SKILL_ABSENT",
                )
        candidates = [
            requirement_lookup[key]
            for key in sorted(requirement_lookup)
            if counts[key] > 0
            and requirement_lookup[key]["requirement_id"] not in labelled_requirements
        ]
        if not candidates:
            raise ValueError("Cannot inject a skill-coverage shortfall without staffed coverage")
        for index in range(self.anomalies_per_type):
            requirement = candidates[index % len(candidates)]
            key = (
                str(requirement["date"]),
                str(requirement["template_id"]),
                str(requirement["skill_id"]),
            )
            requirement["min_staff_required"] = counts[key] + 1
            self._label(
                "CoverageRequirement",
                str(requirement["requirement_id"]),
                "REQUIRED_SKILL_ABSENT",
            )

    def _inject_overtime(self) -> None:
        if not self.tables["ShiftAssignment"]:
            raise ValueError("Cannot inject overtime without baseline assignments")
        candidates = sorted(
            self.employee_week_hours,
            key=lambda key: (-self.employee_week_hours[key], key[0], key[1]),
        )
        for index in range(self.anomalies_per_type):
            selected: tuple[str, date] | None = None
            for candidate in candidates:
                emp_id, week_start = candidate
                day_slots = [
                    week_start + timedelta(days=offset)
                    for offset in range(7)
                    if self.config.start
                    <= week_start + timedelta(days=offset)
                    < self.config.start + timedelta(days=self.config.days)
                ]
                current = self.employee_week_hours[candidate]
                required = max(
                    1,
                    (int(self.employees[emp_id]["weekly_hours_cap"]) - current) // 8 + 1,
                )
                available_slots = [
                    (day, f"SFT-{code}")
                    for day in day_slots
                    if day not in self.leave_days[emp_id]
                    for code, _, _, _ in SHIFT_DEFINITIONS
                    if not self._would_overlap(emp_id, day, f"SFT-{code}")
                ]
                if (
                    current + required * 8
                    > int(self.employees[emp_id]["weekly_hours_cap"])
                    and day_slots
                ):
                    selected = candidate
                    break
            if selected is None:
                raise ValueError("Cannot inject overtime within the configured schedule window")
            emp_id, week_start = selected
            current = self.employee_week_hours[selected]
            cap = int(self.employees[emp_id]["weekly_hours_cap"])
            required = max(1, (cap - current) // 8 + 1)
            day_slots = [
                week_start + timedelta(days=offset)
                for offset in range(7)
                if self.config.start
                <= week_start + timedelta(days=offset)
                < self.config.start + timedelta(days=self.config.days)
            ]
            available_slots = [
                (day, f"SFT-{code}")
                for day in day_slots
                if day not in self.leave_days[emp_id]
                for code, _, _, _ in SHIFT_DEFINITIONS
                if not self._would_overlap(emp_id, day, f"SFT-{code}")
            ]
            created: list[dict[str, object]] = []
            for day, template_id in available_slots[:required]:
                created.append(self._new_assignment(emp_id, day, template_id))
            fallback_days = [
                day for day in day_slots if day not in self.leave_days[emp_id]
            ] or [
                date.fromisoformat(str(row["date"]))
                for row in self.tables["ShiftAssignment"]
                if row["emp_id"] == emp_id
                and self._week_start(date.fromisoformat(str(row["date"]))) == week_start
            ]
            while len(created) < required:
                for day in fallback_days:
                    for code, _, _, _ in SHIFT_DEFINITIONS:
                        if len(created) == required:
                            break
                        template_id = f"SFT-{code}"
                        overlaps = self._would_overlap(emp_id, day, template_id)
                        created_row = self._new_assignment(emp_id, day, template_id)
                        created.append(created_row)
                        if overlaps:
                            self._label(
                                "ShiftAssignment",
                                str(created_row["assignment_id"]),
                                "EMPLOYEE_DOUBLE_BOOKED",
                            )
                        if day in self.leave_days[emp_id]:
                            self._label(
                                "ShiftAssignment",
                                str(created_row["assignment_id"]),
                                "APPROVED_LEAVE_OVERLAP",
                            )
                    if len(created) == required:
                        break
            self._label(
                "ShiftAssignment",
                str(created[-1]["assignment_id"]),
                "OVERTIME_CAP_EXCEEDED",
            )
            candidates = [
                candidate for candidate in candidates if candidate != selected
            ]
            if index + 1 < self.anomalies_per_type and not candidates:
                raise ValueError("Not enough distinct employee-weeks for overtime anomalies")

    def _would_overlap(self, emp_id: str, day: date, template_id: str) -> bool:
        start, end = self._assignment_times(day, template_id)
        first_day = start.date() - timedelta(days=1)
        last_day = end.date()
        for offset in range((last_day - first_day).days + 1):
            assigned_day = first_day + timedelta(days=offset)
            for assignment in self.assignments_by_employee_day.get((emp_id, assigned_day), []):
                existing_start, existing_end = self._assignment_times(
                    assigned_day, str(assignment["template_id"])
                )
                if start < existing_end and existing_start < end:
                    return True
        return False

    def _inject_unapproved_swap(self, baseline: list[dict[str, object]]) -> None:
        offset = 20 * self.anomalies_per_type
        swap_candidates = [
            assignment
            for assignment in baseline
            if self.employee_week_hours[
                (
                    str(assignment["emp_id"]),
                    self._week_start(date.fromisoformat(str(assignment["date"]))),
                )
            ]
            <= int(
                self.employees[str(assignment["emp_id"])]["weekly_hours_cap"]
            )
        ] or baseline
        for index in range(self.anomalies_per_type):
            assignment = swap_candidates[(offset + index) % len(swap_candidates)]
            requester = str(assignment["emp_id"])
            day = date.fromisoformat(str(assignment["date"]))
            team_id = str(self.employees[requester]["team_id"])
            assignment_template = str(assignment["template_id"])
            targets = sorted(
                emp_id
                for emp_id in self.team_members[team_id]
                if emp_id != requester
                and day not in self.leave_days[emp_id]
                and self.employee_week_hours[(emp_id, self._week_start(day))]
                + 8
                <= int(self.employees[emp_id]["weekly_hours_cap"])
                and not self._would_overlap(emp_id, day, assignment_template)
            )
            if not targets:
                targets = [
                emp_id
                for emp_id in sorted(self.employees)
                if emp_id != requester
                and day not in self.leave_days[emp_id]
                and self.employee_week_hours[(emp_id, self._week_start(day))]
                + 8
                    <= int(self.employees[emp_id]["weekly_hours_cap"])
                    and not self._would_overlap(emp_id, day, assignment_template)
                ]
            if not targets:
                targets = [emp_id for emp_id in sorted(self.employees) if emp_id != requester]
            target = targets[0]
            target_has_overlap = self._would_overlap(target, day, assignment_template)
            target_has_leave = day in self.leave_days[target]
            week = self._week_start(day)
            target_exceeds_cap = (
                self.employee_week_hours[(target, week)] + 8
                > int(self.employees[target]["weekly_hours_cap"])
            )
            self.employee_week_hours[(requester, week)] -= 8
            self.employee_week_hours[(target, week)] += 8
            self.assignment_totals[requester] -= 1
            self.assignment_totals[target] += 1
            self.used_days[(requester, day)] -= 1
            self.used_days[(target, day)] += 1
            self.assignments_by_employee_day[(requester, day)].remove(assignment)
            self.assignments_by_employee_day[(target, day)].append(assignment)
            self.swap_serial += 1
            swap = self.add(
                "SwapRequest",
                _record(
                    SwapRequest,
                    swap_id=f"SWP-{self.config.start.year}-{self.swap_serial:06d}",
                    requesting_emp_id=requester,
                    target_emp_id=target,
                    assignment_id=str(assignment["assignment_id"]),
                    status=SwapStatus.PENDING,
                ),
            )
            self._record_assignment_event(
                assignment=assignment,
                previous_emp_id=requester,
                new_emp_id=target,
                event_type="SWAP_APPLIED_WITHOUT_APPROVAL",
                event_at=datetime.combine(day, time(6)),
                actor="SYSTEM",
                related_swap_id=str(swap["swap_id"]),
            )
            self._label("SwapRequest", str(swap["swap_id"]), "SWAP_APPLIED_WITHOUT_APPROVAL")
            if target_has_overlap:
                self._label(
                    "ShiftAssignment",
                    str(assignment["assignment_id"]),
                    "EMPLOYEE_DOUBLE_BOOKED",
                )
            if target_has_leave:
                self._label(
                    "ShiftAssignment",
                    str(assignment["assignment_id"]),
                    "APPROVED_LEAVE_OVERLAP",
                )
            if target_exceeds_cap:
                self._label(
                    "ShiftAssignment",
                    str(assignment["assignment_id"]),
                    "OVERTIME_CAP_EXCEEDED",
                )

    def make_baseline_swap_requests(self) -> None:
        assignments = self.tables["ShiftAssignment"]
        eligible_assignments = [
            assignment
            for assignment in assignments
            if len(
                self.team_members[
                    str(self.employees[str(assignment["emp_id"])]["team_id"])
                ]
            )
            > 1
        ]
        if not eligible_assignments:
            return
        request_count = min(
            len(eligible_assignments),
            max(1, min(30, len(eligible_assignments) // 1000)),
        )
        statuses = (SwapStatus.APPROVED, SwapStatus.PENDING, SwapStatus.DENIED)
        used_assignments: set[str] = set()
        for index in range(request_count):
            assignment = next(
                row
                for offset in range(len(eligible_assignments))
                for row in (
                    eligible_assignments[
                        (index * 97 + offset) % len(eligible_assignments)
                    ],
                )
                if str(row["assignment_id"]) not in used_assignments
            )
            used_assignments.add(str(assignment["assignment_id"]))
            requester = str(assignment["emp_id"])
            team_id = str(self.employees[requester]["team_id"])
            day = date.fromisoformat(str(assignment["date"]))
            template_id = str(assignment["template_id"])
            week = self._week_start(day)
            targets = [
                emp_id
                for emp_id in self.team_members[team_id]
                if emp_id != requester
                and day not in self.leave_days[emp_id]
                and self.employee_week_hours[(emp_id, week)]
                + int(
                    next(
                        row["hours"]
                        for row in self.tables["ShiftTemplate"]
                        if row["template_id"] == template_id
                    )
                )
                <= int(self.employees[emp_id]["weekly_hours_cap"])
                and not self._would_overlap(emp_id, day, template_id)
            ]
            if not targets:
                status = self.rng.choice((SwapStatus.PENDING, SwapStatus.DENIED))
                target = next(emp_id for emp_id in self.team_members[team_id] if emp_id != requester)
            else:
                target = targets[0]
                status = self.rng.choices(statuses, (0.6, 0.2, 0.2))[0]
            self.swap_serial += 1
            swap = self.add(
                "SwapRequest",
                _record(
                    SwapRequest,
                    swap_id=f"SWP-{self.config.start.year}-{self.swap_serial:06d}",
                    requesting_emp_id=requester,
                    target_emp_id=target,
                    assignment_id=str(assignment["assignment_id"]),
                    status=status,
                ),
            )
            if status is SwapStatus.APPROVED and targets:
                self.employee_week_hours[(requester, week)] -= 8
                self.employee_week_hours[(target, week)] += 8
                self.assignment_totals[requester] -= 1
                self.assignment_totals[target] += 1
                self.used_days[(requester, day)] -= 1
                self.used_days[(target, day)] += 1
                self.assignments_by_employee_day[(requester, day)].remove(assignment)
                self.assignments_by_employee_day[(target, day)].append(assignment)
                self._record_assignment_event(
                    assignment=assignment,
                    previous_emp_id=requester,
                    new_emp_id=target,
                    event_type="SWAP_APPLIED",
                    event_at=datetime.combine(day, time(6)),
                    actor="SYSTEM",
                    related_swap_id=str(swap["swap_id"]),
                )

    def make_leave_events(self) -> None:
        for leave in self.tables["LeaveRequest"]:
            self.leave_event_serial += 1
            submitted_date = date.fromisoformat(str(leave["start_date"]))
            event_at = datetime.combine(submitted_date - timedelta(days=2), time(9))
            status = str(leave["status"])
            event_type = {
                LeaveStatus.APPROVED.value: "APPROVED",
                LeaveStatus.REJECTED.value: "REJECTED",
                LeaveStatus.PENDING.value: "PENDING_REVIEW",
            }[status]
            self.add(
                "LeaveRequestEvent",
                _record(
                    LeaveRequestEvent,
                    leave_event_id=f"LVE-EVT-{self.leave_event_serial:06d}",
                    leave_id=str(leave["leave_id"]),
                    event_type=event_type,
                    from_status="Submitted",
                    to_status=status,
                    event_at=event_at,
                    actor="MANAGER" if status != LeaveStatus.PENDING.value else "EMPLOYEE",
                ),
            )

    def make_swap_events(self) -> None:
        assignment_by_id = {
            str(row["assignment_id"]): row for row in self.tables["ShiftAssignment"]
        }
        application_by_swap = {
            str(event["related_swap_id"]): event
            for event in self.tables["ShiftAssignmentEvent"]
            if event["related_swap_id"] is not None
        }
        for swap in self.tables["SwapRequest"]:
            self.swap_event_serial += 1
            assignment = assignment_by_id[str(swap["assignment_id"])]
            shift_date = date.fromisoformat(str(assignment["date"]))
            request_at = datetime.combine(shift_date - timedelta(days=2), time(9))
            status = str(swap["status"])
            self.add(
                "SwapRequestEvent",
                _record(
                    SwapRequestEvent,
                    swap_event_id=f"SWP-EVT-{self.swap_event_serial:06d}",
                    swap_id=str(swap["swap_id"]),
                    event_type="REQUESTED",
                    from_status="NotSubmitted",
                    to_status=SwapStatus.PENDING.value,
                    event_at=request_at,
                    actor=str(swap["requesting_emp_id"]),
                ),
            )
            if status != SwapStatus.PENDING.value:
                self.swap_event_serial += 1
                decision_event = "APPROVED" if status == SwapStatus.APPROVED.value else "DENIED"
                self.add(
                    "SwapRequestEvent",
                    _record(
                        SwapRequestEvent,
                        swap_event_id=f"SWP-EVT-{self.swap_event_serial:06d}",
                        swap_id=str(swap["swap_id"]),
                        event_type=decision_event,
                        from_status=SwapStatus.PENDING.value,
                        to_status=status,
                        event_at=request_at + timedelta(hours=4),
                        actor="MANAGER",
                    ),
                )
            application = application_by_swap.get(str(swap["swap_id"]))
            if application is not None:
                self.swap_event_serial += 1
                self.add(
                    "SwapRequestEvent",
                    _record(
                        SwapRequestEvent,
                        swap_event_id=f"SWP-EVT-{self.swap_event_serial:06d}",
                        swap_id=str(swap["swap_id"]),
                        event_type=str(application["event_type"]).replace(
                            "SWAP_", ""
                        ),
                        from_status=status,
                        to_status=status,
                        event_at=datetime.fromisoformat(str(application["event_at"])),
                        actor=str(application["actor"]),
                    ),
                )

    def inject_anomalies(self) -> None:
        baseline = list(self.tables["ShiftAssignment"])
        if not baseline:
            raise ValueError("Cannot inject required anomalies without baseline assignments")
        self._inject_leave_overlaps(baseline)
        self._inject_overtime()
        self._inject_unapproved_swap(baseline)
        self._inject_missing_skill(baseline)
        self.make_baseline_swap_requests()
        self._inject_double_booking(baseline)

    def make_attendance(self) -> None:
        seen_ids: set[str] = set()
        for assignment in self.tables["ShiftAssignment"]:
            emp_id = str(assignment["emp_id"])
            day = date.fromisoformat(str(assignment["date"]))
            start, end = self._assignment_times(day, str(assignment["template_id"]))
            status = self.rng.choices(
                ("Present", "Late", "Absent"), (0.90, 0.05, 0.05)
            )[0]
            check_in = (
                start
                + timedelta(
                    minutes=self.rng.choices((0, 5, 10, 15), (70, 15, 10, 5))[0]
                )
                if status != "Absent"
                else None
            )
            check_out = (
                end
                + timedelta(
                    minutes=self.rng.choices((0, 5, 10, 15), (70, 15, 10, 5))[0]
                )
                if status != "Absent"
                else None
            )
            attendance_time = check_in or start
            while True:
                attendance_id = f"ATT-{emp_id}-{attendance_time.strftime('%Y%m%dT%H%M%S')}"
                if attendance_id not in seen_ids:
                    break
                attendance_time += timedelta(seconds=1)
            if check_in is not None:
                check_in = attendance_time
            seen_ids.add(attendance_id)
            self.add(
                "AttendanceEvent",
                _record(
                    AttendanceEvent,
                    attendance_id=attendance_id,
                    assignment_id=str(assignment["assignment_id"]),
                    emp_id=emp_id,
                    attendance_status=status,
                    check_in=check_in,
                    check_out=(
                        max(check_out, check_in + timedelta(minutes=1))
                        if check_out is not None and check_in is not None
                        else None
                    ),
                ),
            )

    def make_overtime_summaries(self) -> None:
        attendance_by_assignment = {
            str(row["assignment_id"]): row for row in self.tables["AttendanceEvent"]
        }
        actual_worked_seconds: Counter[tuple[str, date]] = Counter()
        for assignment in self.tables["ShiftAssignment"]:
            attendance = attendance_by_assignment[str(assignment["assignment_id"])]
            if attendance["attendance_status"] == "Absent":
                continue
            check_in = datetime.fromisoformat(str(attendance["check_in"]))
            check_out = datetime.fromisoformat(str(attendance["check_out"]))
            week_start = self._week_start(date.fromisoformat(str(assignment["date"])))
            actual_worked_seconds[
                (str(assignment["emp_id"]), week_start)
            ] += int((check_out - check_in).total_seconds())

        for (emp_id, week_start), hours in sorted(self.employee_week_hours.items()):
            if hours <= 0:
                continue
            cap = int(self.employees[emp_id]["weekly_hours_cap"])
            worked_seconds = actual_worked_seconds[(emp_id, week_start)]
            summary_id = f"OTS-{emp_id}-{week_start.isoformat()}"
            self.add(
                "OvertimeSummary",
                _record(
                    OvertimeSummary,
                    summary_id=summary_id,
                    emp_id=emp_id,
                    week_start=week_start,
                    scheduled_hours=hours,
                    actual_worked_seconds=worked_seconds,
                    weekly_hours_cap=cap,
                    scheduled_overtime_hours=max(0, hours - cap),
                    actual_overtime_seconds=max(0, worked_seconds - cap * 3600),
                ),
            )

    def make_coverage_gaps(self) -> None:
        staffed: Counter[tuple[str, str, str]] = Counter()
        attendance_by_assignment = {
            str(event["assignment_id"]): event
            for event in self.tables["AttendanceEvent"]
        }
        for assignment in self.tables["ShiftAssignment"]:
            emp_id = str(assignment["emp_id"])
            attendance = attendance_by_assignment[str(assignment["assignment_id"])]
            if attendance["attendance_status"] == "Absent":
                continue
            for skill_id in self.skill_ids_by_employee[emp_id]:
                staffed[
                    (
                        str(assignment["date"]),
                        str(assignment["template_id"]),
                        skill_id,
                    )
                ] += 1
        for requirement in self.tables["CoverageRequirement"]:
            key = (
                str(requirement["date"]),
                str(requirement["template_id"]),
                str(requirement["skill_id"]),
            )
            assigned_staff = staffed[key]
            gap = max(0, int(requirement["min_staff_required"]) - assigned_staff)
            self.gap_serial += 1
            self.add(
                "CoverageGap",
                _record(
                    CoverageGap,
                    gap_id=f"GAP-{self.gap_serial:06d}",
                    requirement_id=str(requirement["requirement_id"]),
                    assigned_staff=assigned_staff,
                    gap=gap,
                    status="GAP" if gap else "MET",
                ),
            )

    def reconcile_ground_truth(self) -> None:
        self.truth.clear()
        assignments = self.tables["ShiftAssignment"]
        shifts_by_employee: dict[str, list[tuple[datetime, datetime, dict[str, object]]]] = (
            defaultdict(list)
        )
        for assignment in assignments:
            day = date.fromisoformat(str(assignment["date"]))
            start, end = self._assignment_times(day, str(assignment["template_id"]))
            shifts_by_employee[str(assignment["emp_id"])].append((start, end, assignment))
        double_booked_ids: set[str] = set()
        for employee_shifts in shifts_by_employee.values():
            employee_shifts.sort(key=lambda item: (item[0], item[1]))
            for index, (start, end, assignment) in enumerate(employee_shifts):
                for other_start, _, other in employee_shifts[index + 1 :]:
                    if other_start >= end:
                        break
                    double_booked_ids.add(str(assignment["assignment_id"]))
                    double_booked_ids.add(str(other["assignment_id"]))
        for assignment_id in sorted(double_booked_ids):
            self._label("ShiftAssignment", assignment_id, "EMPLOYEE_DOUBLE_BOOKED")

        approved_leave_days: dict[str, set[date]] = defaultdict(set)
        for leave in self.tables["LeaveRequest"]:
            if leave["status"] != LeaveStatus.APPROVED.value:
                continue
            start = date.fromisoformat(str(leave["start_date"]))
            end = date.fromisoformat(str(leave["end_date"]))
            for offset in range((end - start).days + 1):
                approved_leave_days[str(leave["emp_id"])].add(start + timedelta(days=offset))
        for assignment in assignments:
            if date.fromisoformat(str(assignment["date"])) in approved_leave_days[
                str(assignment["emp_id"])
            ]:
                self._label(
                    "ShiftAssignment",
                    str(assignment["assignment_id"]),
                    "APPROVED_LEAVE_OVERLAP",
                )

        staffed: Counter[tuple[str, str, str]] = Counter()
        hours_by_employee_week: dict[tuple[str, date], list[dict[str, object]]] = defaultdict(list)
        attendance_by_assignment = {
            str(event["assignment_id"]): event
            for event in self.tables["AttendanceEvent"]
        }
        for assignment in assignments:
            emp_id = str(assignment["emp_id"])
            attendance = attendance_by_assignment[str(assignment["assignment_id"])]
            shift_date = date.fromisoformat(str(assignment["date"]))
            hours_by_employee_week[
                (str(assignment["emp_id"]), self._week_start(shift_date))
            ].append(assignment)
            if attendance["attendance_status"] == "Absent":
                continue
            for skill_id in self.skill_ids_by_employee[emp_id]:
                staffed[
                    (
                        str(assignment["date"]),
                        str(assignment["template_id"]),
                        skill_id,
                    )
                ] += 1
        for requirement in self.tables["CoverageRequirement"]:
            key = (
                str(requirement["date"]),
                str(requirement["template_id"]),
                str(requirement["skill_id"]),
            )
            if staffed[key] < int(requirement["min_staff_required"]):
                self._label(
                    "CoverageRequirement",
                    str(requirement["requirement_id"]),
                    "REQUIRED_SKILL_ABSENT",
                )

        templates = {str(row["template_id"]): row for row in self.tables["ShiftTemplate"]}
        for (emp_id, _), week_assignments in hours_by_employee_week.items():
            cap = int(self.employees[emp_id]["weekly_hours_cap"])
            running_hours = 0
            for assignment in sorted(
                week_assignments,
                key=lambda row: (str(row["date"]), str(row["assignment_id"])),
            ):
                running_hours += int(templates[str(assignment["template_id"])]["hours"])
                if running_hours > cap:
                    self._label(
                        "ShiftAssignment",
                        str(assignment["assignment_id"]),
                        "OVERTIME_CAP_EXCEEDED",
                    )

        assignments_by_id = {
            str(row["assignment_id"]): row for row in assignments
        }
        for swap in self.tables["SwapRequest"]:
            assignment = assignments_by_id[str(swap["assignment_id"])]
            if (
                swap["status"] in {SwapStatus.PENDING.value, SwapStatus.DENIED.value}
                and assignment["emp_id"] == swap["target_emp_id"]
            ):
                self._label("SwapRequest", str(swap["swap_id"]), "SWAP_APPLIED_WITHOUT_APPROVAL")

        issue_types = {row["issue_type"] for row in self.truth}
        required = {
            "EMPLOYEE_DOUBLE_BOOKED",
            "APPROVED_LEAVE_OVERLAP",
            "REQUIRED_SKILL_ABSENT",
            "OVERTIME_CAP_EXCEEDED",
            "SWAP_APPLIED_WITHOUT_APPROVAL",
        }
        missing = sorted(required - issue_types)
        if missing:
            raise ValueError(f"Required anomaly scenarios were not detected: {missing}")

    def run(self) -> tuple[dict[str, list[dict[str, object]]], list[dict[str, str]]]:
        self.make_masters()
        self.make_leaves()
        self.make_coverage_requirements()
        self.make_schedule()
        self.inject_anomalies()
        self.make_leave_events()
        self.make_swap_events()
        self.make_attendance()
        self.make_overtime_summaries()
        self.make_coverage_gaps()
        self.reconcile_ground_truth()
        errors = validate(self.tables, self.truth)
        if errors:
            raise ValueError("Generated dataset failed validation: " + "; ".join(errors[:10]))
        return self.tables, self.truth


def generate_dataset(
    config: GenerationConfig | None = None,
) -> tuple[dict[str, list[dict[str, object]]], list[dict[str, str]]]:
    return WorkforceGenerator(config or GenerationConfig()).run()


def dataset_metrics(
    tables: dict[str, list[dict[str, object]]],
    truth: list[dict[str, str]],
) -> dict[str, object]:
    attendance_statuses = Counter(
        str(row["attendance_status"]) for row in tables["AttendanceEvent"]
    )
    leave_statuses = Counter(str(row["status"]) for row in tables["LeaveRequest"])
    swap_statuses = Counter(str(row["status"]) for row in tables["SwapRequest"])
    coverage_gaps = [int(row["gap"]) for row in tables["CoverageGap"]]
    scheduled_overtime_values = [
        int(row["scheduled_overtime_hours"]) for row in tables["OvertimeSummary"]
    ]
    actual_overtime_seconds = [
        int(row["actual_overtime_seconds"]) for row in tables["OvertimeSummary"]
    ]
    template_hours = {
        str(row["template_id"]): int(row["hours"])
        for row in tables["ShiftTemplate"]
    }
    hours_by_employee = Counter()
    for row in tables["ShiftAssignment"]:
        hours_by_employee[str(row["emp_id"])] += template_hours[str(row["template_id"])]
    workload_hours = [
        hours_by_employee[str(employee["emp_id"])]
        for employee in tables["Employee"]
    ]
    return {
        "attendance_statuses": dict(sorted(attendance_statuses.items())),
        "absence_count": attendance_statuses.get("Absent", 0),
        "leave_statuses": dict(sorted(leave_statuses.items())),
        "swap_statuses": dict(sorted(swap_statuses.items())),
        "scheduled_overtime_employee_weeks": sum(
            hours > 0 for hours in scheduled_overtime_values
        ),
        "actual_overtime_employee_weeks": sum(
            seconds > 0 for seconds in actual_overtime_seconds
        ),
        "scheduled_cap_violation_assignments": sum(
            row["issue_type"] == "OVERTIME_CAP_EXCEEDED" for row in truth
        ),
        "total_scheduled_overtime_hours": sum(scheduled_overtime_values),
        "total_actual_overtime_hours": round(
            sum(actual_overtime_seconds) / 3600, 2
        ),
        "coverage_requirements": len(coverage_gaps),
        "coverage_met": sum(gap == 0 for gap in coverage_gaps),
        "coverage_gap_count": sum(gap > 0 for gap in coverage_gaps),
        "coverage_shortfall_total": sum(coverage_gaps),
        "scenario_counts": dict(
            sorted(Counter(row["issue_type"] for row in truth).items())
        ),
        "workload_hours": {
            "minimum": min(workload_hours, default=0),
            "maximum": max(workload_hours, default=0),
            "standard_deviation": round(statistics.pstdev(workload_hours), 2)
            if workload_hours
            else 0,
        },
    }


def _csv_payload(
    rows: list[dict[str, object]],
    fieldnames: list[str] | tuple[str, ...] | None = None,
) -> bytes:
    stream = io.StringIO(newline="")
    fields = list(fieldnames) if fieldnames is not None else (list(rows[0]) if rows else [])
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    if fields:
        writer.writeheader()
        if rows:
            writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def export_dataset(
    tables: dict[str, list[dict[str, object]]],
    truth: list[dict[str, str]],
    output: Path,
    seed: int,
) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    for table_name in LEGACY_TABLE_FILES:
        for suffix in ("csv", "json"):
            stale = output / f"{table_name}.{suffix}"
            if stale.exists():
                stale.unlink()
    for table_name, rows in sorted(tables.items()):
        payload = _csv_payload(rows, record_fields(TABLE_MODELS[table_name]))
        (output / f"{table_name}.csv").write_bytes(payload)
        (output / f"{table_name}.json").write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    ground_truth_dir = output / "ground_truth"
    ground_truth_dir.mkdir(parents=True, exist_ok=True)
    truth_payload = _csv_payload(truth, ("table_name", "entity_id", "issue_type"))
    (ground_truth_dir / "ground_truth.csv").write_bytes(truth_payload)

    for filename in ("workforce_seed.sqlite", "workforce_seed.sql"):
        stale = output / filename
        if stale.exists():
            stale.unlink()
    database = sqlite3.connect(output / "workforce_seed.sqlite")
    database.execute("PRAGMA foreign_keys=OFF")
    for table_name, model in TABLE_MODELS.items():
        columns = list(record_fields(model))
        definitions = [f'"{column}" TEXT' for column in columns]
        definitions.append(
            "PRIMARY KEY ("
            + ", ".join(f'"{column}"' for column in model.primary_key)
            + ")"
        )
        for column, (parent_table, parent_column) in FOREIGN_KEYS.get(table_name, {}).items():
            definitions.append(
                f'FOREIGN KEY ("{column}") REFERENCES "{parent_table}"("{parent_column}")'
            )
        database.execute(f'CREATE TABLE "{table_name}" ({", ".join(definitions)})')
        rows = tables[table_name]
        if rows:
            placeholders = ", ".join("?" for _ in columns)
            database.executemany(
                f'INSERT INTO "{table_name}" VALUES ({placeholders})',
                [
                    [
                        None if row[column] is None else str(row[column])
                        for column in columns
                    ]
                    for row in rows
                ],
            )
    database.commit()
    database.execute("PRAGMA foreign_keys=ON")
    foreign_key_errors = database.execute("PRAGMA foreign_key_check").fetchall()
    integrity = database.execute("PRAGMA integrity_check").fetchone()
    if foreign_key_errors or integrity != ("ok",):
        database.close()
        raise ValueError(
            f"SQLite integrity check failed: foreign_keys={foreign_key_errors[:5]}, integrity={integrity}"
        )
    sql_dump = "\n".join(database.iterdump()) + "\n"
    (output / "workforce_seed.sql").write_text(sql_dump, encoding="utf-8")
    database.close()

    manifest: dict[str, object] = {
        "seed": seed,
        "tables": {},
        "ground_truth_rows": len(truth),
        "metrics": dataset_metrics(tables, truth),
        "source_manifest": {
            "provenance": "Fully synthetic data generated locally",
            "external_sources": [],
            "personal_data": False,
        },
    }
    manifest_tables: dict[str, dict[str, object]] = {}
    csv_paths = [output / f"{name}.csv" for name in sorted(tables)]
    csv_paths.append(ground_truth_dir / "ground_truth.csv")
    for path in sorted(csv_paths):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open(newline="", encoding="utf-8") as file:
            row_count = max(0, sum(1 for _ in file) - 1)
        manifest_tables[str(path.relative_to(output))] = {
            "rows": row_count,
            "sha256": digest,
        }
    manifest["tables"] = manifest_tables
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--employees", type=int, default=2000)
    parser.add_argument("--teams", type=int, default=100)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--leaves", type=int, default=5000)
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--out", default="data/generated")
    parser.add_argument("--anomaly-rate", type=float, default=0.01)
    args = parser.parse_args()
    config = GenerationConfig(
        seed=args.seed,
        employees=args.employees,
        teams=args.teams,
        days=args.days,
        leaves=args.leaves,
        start=date.fromisoformat(args.start),
        anomaly_rate=args.anomaly_rate,
    )
    tables, truth = generate_dataset(config)
    manifest = export_dataset(tables, truth, Path(args.out), args.seed)
    print(
        json.dumps(
            {
                "seed": args.seed,
                "output": args.out,
                "rows": {name: len(rows) for name, rows in sorted(tables.items())},
                "ground_truth": len(truth),
                "metrics": manifest["metrics"],
                "validation_errors": [],
                "valid": True,
                "manifest": manifest,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
