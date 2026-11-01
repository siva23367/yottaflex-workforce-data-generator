"""Structural, relational, and reproducibility checks for generated data."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from models import FOREIGN_KEYS, TABLE_MODELS, LeaveStatus, SwapStatus, record_fields

if TYPE_CHECKING:
    from generator import GenerationConfig


def validate(
    tables: dict[str, list[dict[str, object]]],
    truth: list[dict[str, str]] | None = None,
) -> list[str]:
    errors: list[str] = []
    expected_tables = set(TABLE_MODELS)
    if set(tables) != expected_tables:
        missing = sorted(expected_tables - set(tables))
        extra = sorted(set(tables) - expected_tables)
        errors.append(f"Table set mismatch; missing={missing}, extra={extra}")
        return errors

    ids_by_table: dict[str, set[tuple[object, ...]]] = {}
    id_patterns = {
        "Employee": ("emp_id", r"EMP-\d{6}"),
        "LeaveRequestEvent": ("leave_event_id", r"LVE-EVT-\d{6}"),
        "Team": ("team_id", r"TEAM-\d{3,}"),
        "Skill": ("skill_id", r"SKL-\d{2}"),
        "CoverageGap": ("gap_id", r"GAP-\d{6}"),
        "ShiftTemplate": ("template_id", r"SFT-[A-C]"),
        "ShiftAssignment": (
            "assignment_id",
            r"SHIFT-\d{4}-\d{2}-\d{2}-[A-C]-\d{6}",
        ),
        "ShiftAssignmentEvent": ("assignment_event_id", r"SAE-\d{7}"),
        "LeaveRequest": ("leave_id", r"LVE-\d{4}-\d{6}"),
        "AttendanceEvent": (
            "attendance_id",
            r"ATT-EMP-\d{6}-\d{8}T\d{6}",
        ),
        "CoverageRequirement": ("requirement_id", r"COV-\d{6}"),
        "SwapRequest": ("swap_id", r"SWP-\d{4}-\d{6}"),
        "SwapRequestEvent": ("swap_event_id", r"SWP-EVT-\d{6}"),
        "OvertimeSummary": (
            "summary_id",
            r"OTS-EMP-\d{6}-\d{4}-\d{2}-\d{2}",
        ),
    }
    has_field_errors = False
    for table_name, model in TABLE_MODELS.items():
        rows = tables[table_name]
        expected_fields = record_fields(model)
        seen: set[tuple[object, ...]] = set()
        for row_number, row in enumerate(rows, start=2):
            if tuple(row) != expected_fields:
                errors.append(f"{table_name} row {row_number}: fields do not match model")
                has_field_errors = True
                continue
            id_rule = id_patterns.get(table_name)
            if id_rule and not re.fullmatch(id_rule[1], str(row[id_rule[0]])):
                errors.append(f"{table_name} row {row_number}: invalid identifier format")
            key = tuple(row[field] for field in model.primary_key)
            if key in seen:
                errors.append(f"{table_name} row {row_number}: duplicate primary key {key}")
            seen.add(key)
        ids_by_table[table_name] = seen
    if has_field_errors:
        return errors

    parent_keys = {
        table_name: {key[0] for key in keys}
        for table_name, keys in ids_by_table.items()
    }
    for table_name, relationships in FOREIGN_KEYS.items():
        for row_number, row in enumerate(tables[table_name], start=2):
            for field, (parent_table, parent_field) in relationships.items():
                if row.get(field) is not None and row.get(field) not in parent_keys[parent_table]:
                    errors.append(
                        f"{table_name} row {row_number}: invalid {field} foreign key"
                    )

    allowed_leave = {status.value for status in LeaveStatus}
    for row_number, row in enumerate(tables["Employee"], start=2):
        try:
            if int(row["weekly_hours_cap"]) <= 0:
                errors.append(f"Employee row {row_number}: weekly_hours_cap must be positive")
        except (TypeError, ValueError):
            errors.append(f"Employee row {row_number}: weekly_hours_cap must be an integer")
    template_hours: dict[str, int] = {}
    for row_number, row in enumerate(tables["ShiftTemplate"], start=2):
        try:
            start_time = time.fromisoformat(str(row["start_time"]))
            end_time = time.fromisoformat(str(row["end_time"]))
            hours = int(row["hours"])
        except (TypeError, ValueError):
            errors.append(f"ShiftTemplate row {row_number}: invalid time or hours")
            continue
        if (
            row["shift_name"] not in {"Day", "Evening", "Night"}
            or hours <= 0
            or start_time.strftime("%H:%M") != row["start_time"]
            or end_time.strftime("%H:%M") != row["end_time"]
        ):
            errors.append(f"ShiftTemplate row {row_number}: invalid shift definition")
        template_hours[str(row["template_id"])] = hours
    for row_number, row in enumerate(tables["LeaveRequest"], start=2):
        if row["status"] not in allowed_leave:
            errors.append(f"LeaveRequest row {row_number}: invalid status")
        try:
            if date.fromisoformat(str(row["end_date"])) < date.fromisoformat(
                str(row["start_date"])
            ):
                errors.append(f"LeaveRequest row {row_number}: end_date precedes start_date")
        except ValueError:
            errors.append(f"LeaveRequest row {row_number}: invalid ISO date")

    allowed_swap = {status.value for status in SwapStatus}
    for row_number, row in enumerate(tables["SwapRequest"], start=2):
        if row["status"] not in allowed_swap:
            errors.append(f"SwapRequest row {row_number}: invalid status")
    for row_number, row in enumerate(tables["LeaveRequestEvent"], start=2):
        if row["to_status"] not in allowed_leave:
            errors.append(f"LeaveRequestEvent row {row_number}: invalid to_status")
        try:
            datetime.fromisoformat(str(row["event_at"]))
        except ValueError:
            errors.append(f"LeaveRequestEvent row {row_number}: invalid event_at")
    leave_events: dict[str, list[dict[str, object]]] = {}
    for event in tables["LeaveRequestEvent"]:
        leave_events.setdefault(str(event["leave_id"]), []).append(event)
    leave_requests = {str(row["leave_id"]): row for row in tables["LeaveRequest"]}
    if set(leave_events) != set(leave_requests):
        errors.append("LeaveRequestEvent: every request must have a lifecycle event")
    for leave_id, leave in leave_requests.items():
        events = leave_events.get(leave_id, [])
        if len(events) != 1 or events[0]["to_status"] != leave["status"]:
            errors.append(f"LeaveRequest {leave_id}: lifecycle does not match final status")
    for row_number, row in enumerate(tables["SwapRequestEvent"], start=2):
        if row["to_status"] and row["to_status"] not in allowed_swap:
            errors.append(f"SwapRequestEvent row {row_number}: invalid to_status")
        try:
            datetime.fromisoformat(str(row["event_at"]))
        except ValueError:
            errors.append(f"SwapRequestEvent row {row_number}: invalid event_at")
    proficiency_values = {"Basic", "Intermediate", "Advanced"}
    for row_number, row in enumerate(tables["EmployeeSkill"], start=2):
        if row["proficiency"] not in proficiency_values:
            errors.append(f"EmployeeSkill row {row_number}: invalid proficiency")

    employees = {row["emp_id"]: row for row in tables["Employee"]}
    templates = {row["template_id"]: row for row in tables["ShiftTemplate"]}
    assignments = {row["assignment_id"]: row for row in tables["ShiftAssignment"]}
    assignment_dates: dict[str, date] = {}
    for row_number, row in enumerate(tables["ShiftAssignment"], start=2):
        try:
            assignment_dates[str(row["assignment_id"])] = date.fromisoformat(
                str(row["date"])
            )
        except ValueError:
            errors.append(f"ShiftAssignment row {row_number}: invalid ISO date")
    attendance_by_assignment: Counter[str] = Counter()
    attendance_by_assignment_id: dict[str, dict[str, object]] = {}
    for row_number, row in enumerate(tables["AttendanceEvent"], start=2):
        assignment_id = str(row["assignment_id"])
        attendance_by_assignment[assignment_id] += 1
        attendance_by_assignment_id[assignment_id] = row
        assignment = assignments.get(str(row["assignment_id"]))
        if assignment and row["emp_id"] != assignment["emp_id"]:
            errors.append(
                f"AttendanceEvent row {row_number}: employee differs from assignment"
            )
        if row["attendance_status"] not in {"Present", "Late", "Absent"}:
            errors.append(f"AttendanceEvent row {row_number}: invalid attendance_status")
        try:
            if row["attendance_status"] == "Absent":
                if row["check_in"] is not None or row["check_out"] is not None:
                    errors.append(
                        f"AttendanceEvent row {row_number}: absent employee has punch timestamps"
                    )
            elif row["check_in"] is None or row["check_out"] is None:
                errors.append(
                    f"AttendanceEvent row {row_number}: present employee is missing punch timestamps"
                )
            else:
                check_in = datetime.fromisoformat(str(row["check_in"]))
                check_out = datetime.fromisoformat(str(row["check_out"]))
                if check_out <= check_in:
                    errors.append(f"AttendanceEvent row {row_number}: invalid timestamp order")
                if assignment:
                    shift_date = date.fromisoformat(str(assignment["date"]))
                    shift_start = datetime.combine(
                        shift_date,
                        time.fromisoformat(str(templates[str(assignment["template_id"])]["start_time"])),
                    )
                    if check_in < shift_start:
                        errors.append(
                            f"AttendanceEvent row {row_number}: check_in precedes shift start"
                        )
                else:
                    shift_start = datetime.fromisoformat(str(row["check_in"]))
                id_timestamp = (
                    shift_start.strftime("%Y%m%dT%H%M")
                    if row["attendance_status"] == "Absent"
                    else check_in.strftime("%Y%m%dT%H%M%S")
                )
                if not str(row["attendance_id"]).startswith(
                    f"ATT-{row['emp_id']}-{id_timestamp}"
                ):
                    errors.append(f"AttendanceEvent row {row_number}: ID does not match shift")
        except ValueError:
            errors.append(f"AttendanceEvent row {row_number}: invalid ISO timestamp")

    for row_number, row in enumerate(tables["ShiftAssignment"], start=2):
        assignment_id = str(row["assignment_id"])
        if (
            assignment_id not in assignment_dates
            or row["emp_id"] not in employees
            or row["template_id"] not in templates
        ):
            continue
        if not str(row["assignment_id"]).startswith(
            f"SHIFT-{row['date']}-{str(row['template_id'])[-1]}-"
        ):
            errors.append(f"ShiftAssignment row {row_number}: ID does not match date/template")
        if attendance_by_assignment[assignment_id] != 1:
            errors.append(
                f"ShiftAssignment row {row_number}: expected exactly one attendance event"
            )

    assignment_events: dict[str, list[dict[str, object]]] = {}
    swaps = {str(row["swap_id"]): row for row in tables["SwapRequest"]}
    for row_number, event in enumerate(tables["ShiftAssignmentEvent"], start=2):
        assignment_id = str(event["assignment_id"])
        assignment_events.setdefault(assignment_id, []).append(event)
        try:
            datetime.fromisoformat(str(event["event_at"]))
        except ValueError:
            errors.append(f"ShiftAssignmentEvent row {row_number}: invalid event_at")
        if event["event_type"] not in {
            "ASSIGNED",
            "SWAP_APPLIED",
            "SWAP_APPLIED_WITHOUT_APPROVAL",
        }:
            errors.append(f"ShiftAssignmentEvent row {row_number}: invalid event_type")
        if event["event_type"] == "ASSIGNED":
            if event["previous_emp_id"] is not None or event["related_swap_id"] is not None:
                errors.append(
                    f"ShiftAssignmentEvent row {row_number}: initial event has prior state"
                )
            continue
        swap_id = event["related_swap_id"]
        swap = swaps.get(str(swap_id))
        if swap is None:
            errors.append(f"ShiftAssignmentEvent row {row_number}: missing related swap")
            continue
        expected_status = (
            SwapStatus.APPROVED.value
            if event["event_type"] == "SWAP_APPLIED"
            else None
        )
        if expected_status is not None and swap["status"] != expected_status:
            errors.append(f"ShiftAssignmentEvent row {row_number}: swap is not approved")
        if event["event_type"] == "SWAP_APPLIED_WITHOUT_APPROVAL" and swap[
            "status"
        ] == SwapStatus.APPROVED.value:
            errors.append(f"ShiftAssignmentEvent row {row_number}: approved swap mislabelled")
        if (
            swap["assignment_id"] != assignment_id
            or swap["requesting_emp_id"] != event["previous_emp_id"]
            or swap["target_emp_id"] != event["new_emp_id"]
        ):
            errors.append(f"ShiftAssignmentEvent row {row_number}: swap state does not match")
    for assignment_id, assignment in assignments.items():
        events = assignment_events.get(str(assignment_id), [])
        if not events:
            errors.append(f"ShiftAssignment {assignment_id}: missing assignment history")
            continue
        ordered_events = sorted(
            events, key=lambda event: (str(event["event_at"]), str(event["assignment_event_id"]))
        )
        if sum(event["event_type"] == "ASSIGNED" for event in ordered_events) != 1:
            errors.append(f"ShiftAssignment {assignment_id}: expected one initial event")
            continue
        current_emp_id: object = None
        for event in ordered_events:
            if event["event_type"] == "ASSIGNED":
                if current_emp_id is not None:
                    errors.append(
                        f"ShiftAssignment {assignment_id}: initial event is not first"
                    )
                current_emp_id = event["new_emp_id"]
                continue
            if event["previous_emp_id"] != current_emp_id:
                errors.append(f"ShiftAssignment {assignment_id}: broken employee history")
            current_emp_id = event["new_emp_id"]
        if current_emp_id != assignment["emp_id"]:
            errors.append(f"ShiftAssignment {assignment_id}: history does not match current assignee")

    for row_number, row in enumerate(tables["CoverageRequirement"], start=2):
        try:
            date.fromisoformat(str(row["date"]))
            minimum = int(row["min_staff_required"])
        except (TypeError, ValueError):
            errors.append(f"CoverageRequirement row {row_number}: invalid date or minimum")
            continue
        if minimum < 1:
            errors.append(
                f"CoverageRequirement row {row_number}: min_staff_required must be positive"
            )
    if assignment_dates and templates and tables["Skill"]:
        first_date = min(assignment_dates.values())
        last_date = max(assignment_dates.values())
        expected_coverage_keys = {
            (day.isoformat(), str(template_id), str(skill["skill_id"]))
            for offset in range((last_date - first_date).days + 1)
            for day in (first_date + timedelta(days=offset),)
            for template_id in templates
            for skill in tables["Skill"]
        }
        actual_coverage_keys = {
            (str(row["date"]), str(row["template_id"]), str(row["skill_id"]))
            for row in tables["CoverageRequirement"]
        }
        if (
            actual_coverage_keys != expected_coverage_keys
            or len(actual_coverage_keys) != len(tables["CoverageRequirement"])
        ):
            errors.append(
                "CoverageRequirement: must define exactly one demand row for every date/template/skill combination"
            )
    coverage_gaps = {
        str(row["requirement_id"]): row for row in tables["CoverageGap"]
    }
    requirements_by_id = {
        str(row["requirement_id"]): row for row in tables["CoverageRequirement"]
    }
    if set(coverage_gaps) != {
        str(requirement_id) for requirement_id in requirements_by_id
    }:
        errors.append("CoverageGap: each coverage requirement must have one result")
    employee_skills: dict[str, set[str]] = {}
    for row in tables["EmployeeSkill"]:
        employee_skills.setdefault(str(row["emp_id"]), set()).add(str(row["skill_id"]))
    staffed_coverage: Counter[tuple[str, str, str]] = Counter()
    for assignment in tables["ShiftAssignment"]:
        attendance = attendance_by_assignment_id.get(str(assignment["assignment_id"]))
        if attendance is None or attendance["attendance_status"] == "Absent":
            continue
        for skill_id in employee_skills.get(str(assignment["emp_id"]), set()):
            staffed_coverage[
                (
                    str(assignment["date"]),
                    str(assignment["template_id"]),
                    skill_id,
                )
            ] += 1
    for row_number, row in enumerate(tables["CoverageGap"], start=2):
        requirement = requirements_by_id.get(str(row["requirement_id"]))
        if requirement is None:
            continue
        expected_gap = max(
            0, int(requirement["min_staff_required"]) - int(row["assigned_staff"])
        )
        if int(row["gap"]) != expected_gap:
            errors.append(f"CoverageGap row {row_number}: gap does not reconcile")
        expected_status = "GAP" if expected_gap else "MET"
        if row["status"] != expected_status:
            errors.append(f"CoverageGap row {row_number}: status does not reconcile")
        requirement_day = str(requirement["date"])
        requirement_template = str(requirement["template_id"])
        requirement_skill = str(requirement["skill_id"])
        actual_staff = staffed_coverage[
            (requirement_day, requirement_template, requirement_skill)
        ]
        if int(row["assigned_staff"]) != actual_staff:
            errors.append(f"CoverageGap row {row_number}: assigned staff does not reconcile")

    swaps = {str(row["swap_id"]): row for row in tables["SwapRequest"]}
    swap_events: dict[str, list[dict[str, object]]] = {}
    for event in tables["SwapRequestEvent"]:
        swap_events.setdefault(str(event["swap_id"]), []).append(event)
    if set(swap_events) != set(swaps):
        errors.append("SwapRequestEvent: every request must have a lifecycle event")
    for swap_id, swap in swaps.items():
        events = swap_events.get(swap_id, [])
        assignment_changes = [
            event
            for event in assignment_events.get(str(swap["assignment_id"]), [])
            if event["related_swap_id"] == swap_id
        ]
        if not any(event["event_type"] == "REQUESTED" for event in events):
            errors.append(f"SwapRequest {swap_id}: missing REQUESTED event")
        if swap["status"] != SwapStatus.PENDING.value and not any(
            event["to_status"] == swap["status"] for event in events
        ):
            errors.append(f"SwapRequest {swap_id}: decision missing from event history")
        if swap["status"] == SwapStatus.APPROVED.value and not any(
            event["event_type"] == "APPLIED" for event in events
        ):
            errors.append(f"SwapRequest {swap_id}: approved swap lacks applied event")
        if any(event["event_type"] == "APPLIED_WITHOUT_APPROVAL" for event in events):
            if swap["status"] == SwapStatus.APPROVED.value:
                errors.append(f"SwapRequest {swap_id}: approved request marked unapproved")
        applications = [
            event
            for event in events
            if event["event_type"] in {"APPLIED", "APPLIED_WITHOUT_APPROVAL"}
        ]
        if len(assignment_changes) != len(applications) or len(applications) > 1:
            errors.append(f"SwapRequest {swap_id}: assignment history/application mismatch")
        elif assignment_changes:
            expected_application = (
                "APPLIED"
                if assignment_changes[0]["event_type"] == "SWAP_APPLIED"
                else "APPLIED_WITHOUT_APPROVAL"
            )
            if applications[0]["event_type"] != expected_application:
                errors.append(f"SwapRequest {swap_id}: application type mismatch")

    overtime: Counter[tuple[str, date]] = Counter()
    actual_worked_seconds: Counter[tuple[str, date]] = Counter()
    for row in tables["ShiftAssignment"]:
        assignment_id = str(row["assignment_id"])
        template_id = str(row["template_id"])
        emp_id = str(row["emp_id"])
        if (
            assignment_id not in assignment_dates
            or template_id not in template_hours
            or emp_id not in employees
        ):
            continue
        shift_date = assignment_dates[assignment_id]
        overtime[(emp_id, shift_date - timedelta(days=shift_date.weekday()))] += (
            template_hours[template_id]
        )
        attendance = attendance_by_assignment_id.get(assignment_id)
        if attendance is None or attendance["attendance_status"] == "Absent":
            continue
        try:
            check_in = datetime.fromisoformat(str(attendance["check_in"]))
            check_out = datetime.fromisoformat(str(attendance["check_out"]))
        except (TypeError, ValueError):
            continue
        actual_worked_seconds[
            (emp_id, shift_date - timedelta(days=shift_date.weekday()))
        ] += int((check_out - check_in).total_seconds())
    summaries = {}
    for row_number, row in enumerate(tables["OvertimeSummary"], start=2):
        try:
            key = (str(row["emp_id"]), date.fromisoformat(str(row["week_start"])))
        except ValueError:
            errors.append(f"OvertimeSummary row {row_number}: invalid week_start")
            continue
        summaries[key] = row
    if set(overtime) != set(summaries):
        errors.append("OvertimeSummary: rows do not match scheduled employee weeks")
    for key, hours in overtime.items():
        summary = summaries.get(key)
        if summary is None or key[0] not in employees:
            continue
        try:
            cap = int(employees[key[0]]["weekly_hours_cap"])
            scheduled = int(summary["scheduled_hours"])
            worked_seconds = int(summary["actual_worked_seconds"])
            summary_cap = int(summary["weekly_hours_cap"])
            scheduled_overtime = int(summary["scheduled_overtime_hours"])
            actual_overtime = int(summary["actual_overtime_seconds"])
        except (TypeError, ValueError):
            errors.append("OvertimeSummary: derived fields must be integers")
            continue
        expected_worked_seconds = actual_worked_seconds[key]
        if (
            scheduled != hours
            or worked_seconds != expected_worked_seconds
            or summary_cap != cap
            or scheduled_overtime != max(0, hours - cap)
            or actual_overtime != max(0, expected_worked_seconds - cap * 3600)
        ):
            errors.append("OvertimeSummary: derived values do not reconcile")

    expected_issue_labels: set[tuple[str, str, str]] = set()
    shifts_by_employee: dict[
        str, list[tuple[datetime, datetime, str]]
    ] = {}
    for assignment_id, assignment in assignments.items():
        shift_date = assignment_dates.get(assignment_id)
        template = templates.get(str(assignment["template_id"]))
        if shift_date is None or template is None:
            continue
        try:
            start_time = time.fromisoformat(str(template["start_time"]))
            end_time = time.fromisoformat(str(template["end_time"]))
        except ValueError:
            continue
        start = datetime.combine(shift_date, start_time)
        end_date = shift_date + timedelta(days=1) if end_time <= start_time else shift_date
        end = datetime.combine(end_date, end_time)
        shifts_by_employee.setdefault(str(assignment["emp_id"]), []).append(
            (start, end, assignment_id)
        )
    for employee_shifts in shifts_by_employee.values():
        employee_shifts.sort()
        for index, (start, end, assignment_id) in enumerate(employee_shifts):
            for other_start, _, other_assignment_id in employee_shifts[index + 1 :]:
                if other_start >= end:
                    break
                expected_issue_labels.add(
                    ("ShiftAssignment", assignment_id, "EMPLOYEE_DOUBLE_BOOKED")
                )
                expected_issue_labels.add(
                    ("ShiftAssignment", other_assignment_id, "EMPLOYEE_DOUBLE_BOOKED")
                )

    approved_leave_dates: dict[str, set[date]] = {}
    for leave in tables["LeaveRequest"]:
        if leave["status"] != LeaveStatus.APPROVED.value:
            continue
        try:
            leave_start = date.fromisoformat(str(leave["start_date"]))
            leave_end = date.fromisoformat(str(leave["end_date"]))
        except ValueError:
            continue
        if leave_end < leave_start:
            continue
        approved_leave_dates.setdefault(str(leave["emp_id"]), set()).update(
            leave_start + timedelta(days=offset)
            for offset in range((leave_end - leave_start).days + 1)
        )
    for assignment_id, assignment in assignments.items():
        shift_date = assignment_dates.get(assignment_id)
        if shift_date in approved_leave_dates.get(str(assignment["emp_id"]), set()):
            expected_issue_labels.add(
                ("ShiftAssignment", assignment_id, "APPROVED_LEAVE_OVERLAP")
            )

    for requirement_id, requirement in requirements_by_id.items():
        try:
            minimum = int(requirement["min_staff_required"])
            requirement_date = date.fromisoformat(str(requirement["date"]))
        except (TypeError, ValueError):
            continue
        actual_staff = staffed_coverage[
            (
                requirement_date.isoformat(),
                str(requirement["template_id"]),
                str(requirement["skill_id"]),
            )
        ]
        if actual_staff < minimum:
            expected_issue_labels.add(
                ("CoverageRequirement", requirement_id, "REQUIRED_SKILL_ABSENT")
            )

    assignments_by_employee_week: dict[
        tuple[str, date], list[dict[str, object]]
    ] = {}
    for assignment in tables["ShiftAssignment"]:
        assignment_id = str(assignment["assignment_id"])
        shift_date = assignment_dates.get(assignment_id)
        template_id = str(assignment["template_id"])
        if shift_date is None or template_id not in template_hours:
            continue
        week_start = shift_date - timedelta(days=shift_date.weekday())
        assignments_by_employee_week.setdefault(
            (str(assignment["emp_id"]), week_start), []
        ).append(assignment)
    for (emp_id, _), week_assignments in assignments_by_employee_week.items():
        employee = employees.get(emp_id)
        if employee is None:
            continue
        try:
            cap = int(employee["weekly_hours_cap"])
        except (TypeError, ValueError):
            continue
        running_hours = 0
        for assignment in sorted(
            week_assignments,
            key=lambda row: (
                str(row["date"]),
                str(row["assignment_id"]),
            ),
        ):
            running_hours += template_hours[str(assignment["template_id"])]
            if running_hours > cap:
                expected_issue_labels.add(
                    (
                        "ShiftAssignment",
                        str(assignment["assignment_id"]),
                        "OVERTIME_CAP_EXCEEDED",
                    )
                )

    for swap in tables["SwapRequest"]:
        assignment = assignments.get(str(swap["assignment_id"]))
        if (
            assignment is not None
            and swap["status"]
            in {SwapStatus.PENDING.value, SwapStatus.DENIED.value}
            and assignment["emp_id"] == swap["target_emp_id"]
        ):
            expected_issue_labels.add(
                ("SwapRequest", str(swap["swap_id"]), "SWAP_APPLIED_WITHOUT_APPROVAL")
            )

    if truth is not None:
        expected_truth_fields = ("table_name", "entity_id", "issue_type")
        allowed_issues = {
            "EMPLOYEE_DOUBLE_BOOKED",
            "APPROVED_LEAVE_OVERLAP",
            "REQUIRED_SKILL_ABSENT",
            "OVERTIME_CAP_EXCEEDED",
            "SWAP_APPLIED_WITHOUT_APPROVAL",
        }
        entity_ids = {
            table_name: {
                str(record[TABLE_MODELS[table_name].primary_key[0]])
                for record in tables[table_name]
            }
            for table_name in TABLE_MODELS
        }
        seen_truth: set[tuple[str, str, str]] = set()
        for row_number, row in enumerate(truth, start=2):
            if tuple(row) != expected_truth_fields:
                errors.append(f"ground_truth row {row_number}: invalid fields")
                continue
            table_name = row["table_name"]
            if table_name not in TABLE_MODELS:
                errors.append(f"ground_truth row {row_number}: unknown table")
                continue
            if row["entity_id"] not in entity_ids[table_name]:
                errors.append(f"ground_truth row {row_number}: entity_id does not exist")
            if row["issue_type"] not in allowed_issues:
                errors.append(f"ground_truth row {row_number}: unknown issue_type")
            label = (table_name, row["entity_id"], row["issue_type"])
            if label in seen_truth:
                errors.append(f"ground_truth row {row_number}: duplicate label")
            seen_truth.add(label)
        expected_issues = {
            "EMPLOYEE_DOUBLE_BOOKED",
            "APPROVED_LEAVE_OVERLAP",
            "REQUIRED_SKILL_ABSENT",
            "OVERTIME_CAP_EXCEEDED",
            "SWAP_APPLIED_WITHOUT_APPROVAL",
        }
        found_issues = {row["issue_type"] for row in truth}
        if not expected_issues.issubset(found_issues):
            errors.append(
                f"ground_truth missing required issue labels: {sorted(expected_issues - found_issues)}"
            )
        if seen_truth != expected_issue_labels:
            missing_labels = sorted(expected_issue_labels - seen_truth)
            unsupported_labels = sorted(seen_truth - expected_issue_labels)
            if missing_labels:
                errors.append(
                    f"ground_truth missing detected anomalies: {missing_labels[:10]}"
                )
            if unsupported_labels:
                errors.append(
                    f"ground_truth labels unsupported anomalies: {unsupported_labels[:10]}"
                )
    return errors


def validate_output_directory(output: Path) -> list[str]:
    """Validate exported CSV tables and ground truth from an output directory."""
    tables: dict[str, list[dict[str, object]]] = {}
    errors: list[str] = []
    nullable_csv_fields = {
        "AttendanceEvent": {"check_in", "check_out"},
        "ShiftAssignmentEvent": {"previous_emp_id", "related_swap_id"},
    }
    for table_name in TABLE_MODELS:
        path = output / f"{table_name}.csv"
        if not path.is_file():
            errors.append(f"Missing required CSV file: {path.name}")
            continue
        try:
            with path.open(newline="", encoding="utf-8") as file:
                tables[table_name] = [
                    {
                        field: (
                            None
                            if field in nullable_csv_fields.get(table_name, set())
                            and value == ""
                            else value
                        )
                        for field, value in row.items()
                    }
                    for row in csv.DictReader(file)
                ]
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            errors.append(f"Could not read {path.name}: {exc}")
    truth_path = output / "ground_truth" / "ground_truth.csv"
    truth: list[dict[str, str]] = []
    if not truth_path.is_file():
        errors.append("Missing required CSV file: ground_truth/ground_truth.csv")
    else:
        try:
            with truth_path.open(newline="", encoding="utf-8") as file:
                truth = list(csv.DictReader(file))
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            errors.append(f"Could not read ground_truth/ground_truth.csv: {exc}")
    if errors:
        return errors
    return validate(tables, truth)


def output_hashes(
    tables: dict[str, list[dict[str, object]]],
    truth: list[dict[str, str]],
) -> dict[str, str]:
    """Return MD5 checksums of the exact deterministic CSV payloads."""
    hashes: dict[str, str] = {}
    for table_name, rows in sorted(tables.items()):
        stream = io.StringIO(newline="")
        fields = record_fields(TABLE_MODELS[table_name])
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        hashes[f"{table_name}.csv"] = hashlib.md5(
            stream.getvalue().encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        json_payload = json.dumps(rows, indent=2, sort_keys=True) + "\n"
        hashes[f"{table_name}.json"] = hashlib.md5(
            json_payload.encode("utf-8"), usedforsecurity=False
        ).hexdigest()
    stream = io.StringIO(newline="")
    truth_fields = ("table_name", "entity_id", "issue_type")
    writer = csv.DictWriter(stream, fieldnames=truth_fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(truth)
    hashes["ground_truth/ground_truth.csv"] = hashlib.md5(
        stream.getvalue().encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    return hashes


def verify_reproducibility(
    seed: int,
    config: GenerationConfig | None = None,
) -> dict[str, str]:
    from generator import GenerationConfig, generate_dataset

    settings = config or GenerationConfig(seed=seed)
    first_tables, first_truth = generate_dataset(settings)
    second_tables, second_truth = generate_dataset(settings)
    first = output_hashes(first_tables, first_truth)
    second = output_hashes(second_tables, second_truth)
    if first != second:
        changed = sorted(
            name for name in first.keys() | second.keys() if first.get(name) != second.get(name)
        )
        raise AssertionError(f"Determinism check failed for: {changed}")
    return first


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--employees", type=int, default=2000)
    parser.add_argument("--teams", type=int, default=100)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--leaves", type=int, default=5000)
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--anomaly-rate", type=float, default=0.01)
    parser.add_argument(
        "--check-output",
        type=Path,
        help="validate CSV files in an existing generated output directory",
    )
    args = parser.parse_args()

    if args.check_output is not None:
        errors = validate_output_directory(args.check_output)
        if errors:
            raise SystemExit("\n".join(errors))
        print(f"CSV validation passed for {args.check_output}.")
        return

    from generator import GenerationConfig, generate_dataset

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
    errors = validate(tables, truth)
    if errors:
        raise SystemExit("\n".join(errors))
    hashes = verify_reproducibility(args.seed, config)
    print(
        f"Validation passed; {len(hashes)} CSV/JSON MD5 checksums match across two runs."
    )


if __name__ == "__main__":
    main()
