import csv
import copy
import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from generator import (
    CENTRAL_GAZETTED_HOLIDAYS_2026,
    HOLIDAY_SOURCE_MANIFEST,
    GenerationConfig,
    WorkforceGenerator,
    export_dataset,
    generate_dataset,
)
from models import Employee, TABLE_MODELS, record_fields
from validate import output_hashes, validate, validate_output_directory


def build(seed=20261022):
    return generate_dataset(
        GenerationConfig(
            seed=seed,
            employees=40,
            teams=4,
            days=14,
            leaves=30,
            start=date(2026, 1, 1),
        )
    )


def test_schema_and_relationship_integrity():
    tables, truth = build()
    assert validate(tables) == []
    assert validate(tables, truth) == []
    assert set(tables) == set(TABLE_MODELS)
    assert all(tuple(row) == record_fields(TABLE_MODELS[name]) for name, rows in tables.items() for row in rows)
    assert len({row["emp_id"] for row in tables["Employee"]}) == 40
    assert len(tables["CoverageGap"]) == len(tables["CoverageRequirement"])
    assert len(tables["ShiftAssignmentEvent"]) >= len(tables["ShiftAssignment"])
    assert len(tables["EmployeeSkill"]) >= len(tables["Employee"])
    assert len(tables["LeaveRequestEvent"]) == len(tables["LeaveRequest"])
    assert {row["issue_type"] for row in truth} == {
        "EMPLOYEE_DOUBLE_BOOKED",
        "APPROVED_LEAVE_OVERLAP",
        "REQUIRED_SKILL_ABSENT",
        "OVERTIME_CAP_EXCEEDED",
        "SWAP_APPLIED_WITHOUT_APPROVAL",
    }


def test_ids_statuses_and_leave_volume():
    tables, truth = build()
    assert tables["Employee"][0]["emp_id"] == "EMP-000001"
    assert tables["Team"][0]["team_id"] == "TEAM-001"
    assert tables["Skill"][0]["skill_id"] == "SKL-01"
    assert tables["ShiftTemplate"][0]["template_id"] == "SFT-A"
    assert len(tables["LeaveRequest"]) == 30
    assert all(
        row["status"] in {"Approved", "Rejected", "Pending"}
        for row in tables["LeaveRequest"]
    )
    assert all(set(row) == {"table_name", "entity_id", "issue_type"} for row in truth)


def test_configurable_anomaly_rate_retains_all_required_scenarios():
    tables, truth = generate_dataset(
        GenerationConfig(
            seed=9,
            employees=40,
            teams=4,
            days=14,
            leaves=20,
            anomaly_rate=0.02,
        )
    )
    assert validate(tables, truth) == []
    assert len(truth) >= 5
    assert {row["issue_type"] for row in truth} == {
        "EMPLOYEE_DOUBLE_BOOKED",
        "APPROVED_LEAVE_OVERLAP",
        "REQUIRED_SKILL_ABSENT",
        "OVERTIME_CAP_EXCEEDED",
        "SWAP_APPLIED_WITHOUT_APPROVAL",
    }


def test_coverage_requirements_are_independent_of_assignments():
    generator = WorkforceGenerator(
        GenerationConfig(employees=8, teams=4, days=2, leaves=0)
    )
    generator.make_masters()
    generator.make_coverage_requirements()
    assert generator.tables["ShiftAssignment"] == []
    assert len(generator.tables["CoverageRequirement"]) == 2 * 3 * len(
        generator.tables["Skill"]
    )
    assert {
        (row["date"], row["template_id"], row["skill_id"])
        for row in generator.tables["CoverageRequirement"]
    } == {
        (day.isoformat(), template["template_id"], skill["skill_id"])
        for day in (date(2026, 1, 1), date(2026, 1, 2))
        for template in generator.tables["ShiftTemplate"]
        for skill in generator.tables["Skill"]
    }


def test_official_gazetted_holidays_are_filtered_to_schedule_window():
    generator = WorkforceGenerator(
        GenerationConfig(employees=8, teams=4, days=90, leaves=0)
    )
    generator.make_masters()
    assert [
        (row["holiday_date"], row["holiday_name"])
        for row in generator.tables["HolidayCalendar"]
    ] == [(holiday.isoformat(), name) for holiday, name in CENTRAL_GAZETTED_HOLIDAYS_2026]

    outside_calendar_year = WorkforceGenerator(
        GenerationConfig(
            employees=8,
            teams=4,
            days=10,
            leaves=0,
            start=date(2026, 4, 1),
        )
    )
    outside_calendar_year.make_masters()
    assert outside_calendar_year.tables["HolidayCalendar"] == []


def test_validator_rejects_missing_coverage_demand_and_assignment_history():
    tables, truth = build()
    tables["CoverageRequirement"].pop()
    assert any(
        "CoverageRequirement: must define exactly one demand" in error
        for error in validate(tables, truth)
    )

    tables, truth = build()
    missing_assignment_id = tables["ShiftAssignment"][0]["assignment_id"]
    tables["ShiftAssignmentEvent"] = [
        event
        for event in tables["ShiftAssignmentEvent"]
        if event["assignment_id"] != missing_assignment_id
    ]
    assert any(
        "missing assignment history" in error
        for error in validate(tables, truth)
    )


def test_model_types_and_foreign_key_metadata_are_enforced():
    assert Employee.__dataclass_fields__["emp_id"].metadata["primary_key"] is True
    assert Employee.__dataclass_fields__["team_id"].metadata["foreign_key"] == (
        "Team",
        "team_id",
    )
    with pytest.raises(TypeError, match="weekly_hours_cap must be int"):
        Employee("EMP-000001", "Example", "TEAM-001", "SKL-01", "40")


def test_validator_reports_invalid_dates_without_aborting():
    tables, _ = build()
    tables["ShiftAssignment"][0]["date"] = "not-a-date"
    errors = validate(tables)
    assert any("ShiftAssignment row 2: invalid ISO date" in error for error in errors)


def test_ground_truth_rows_describe_detectable_violations():
    tables, truth = build()
    assignments = {row["assignment_id"]: row for row in tables["ShiftAssignment"]}
    attendance = {
        row["assignment_id"]: row["attendance_status"]
        for row in tables["AttendanceEvent"]
    }
    employee_skills = {}
    for row in tables["EmployeeSkill"]:
        employee_skills.setdefault(row["emp_id"], set()).add(row["skill_id"])
    leaves = tables["LeaveRequest"]
    requirements = {
        row["requirement_id"]: row for row in tables["CoverageRequirement"]
    }
    swaps = {row["swap_id"]: row for row in tables["SwapRequest"]}
    summaries = tables["OvertimeSummary"]
    definitions = {"A": (6, 14), "B": (14, 22), "C": (22, 30)}

    def interval(row):
        day = date.fromisoformat(row["date"]).toordinal()
        start, end = definitions[row["template_id"][-1]]
        return day * 24 + start, day * 24 + end

    for issue in {row["issue_type"] for row in truth}:
        targets = [row for row in truth if row["issue_type"] == issue]
        for target in targets:
            if issue in {"EMPLOYEE_DOUBLE_BOOKED", "APPROVED_LEAVE_OVERLAP", "OVERTIME_CAP_EXCEEDED"}:
                assignment = assignments[target["entity_id"]]
                if issue == "EMPLOYEE_DOUBLE_BOOKED":
                    start, end = interval(assignment)
                    assert any(
                        other["assignment_id"] != assignment["assignment_id"]
                        and other["emp_id"] == assignment["emp_id"]
                        and interval(other)[0] < end
                        and start < interval(other)[1]
                        for other in assignments.values()
                    )
                elif issue == "APPROVED_LEAVE_OVERLAP":
                    assert any(
                        leave["emp_id"] == assignment["emp_id"]
                        and leave["status"] == "Approved"
                        and leave["start_date"] <= assignment["date"] <= leave["end_date"]
                        for leave in leaves
                    )
                else:
                    assigned_day = date.fromisoformat(assignment["date"])
                    summary = next(
                        row
                        for row in summaries
                        if row["emp_id"] == assignment["emp_id"]
                        and date.fromisoformat(row["week_start"])
                        <= assigned_day
                        <= date.fromisoformat(row["week_start"]) + timedelta(days=6)
                    )
                    assert summary["scheduled_overtime_hours"] > 0
            elif issue == "REQUIRED_SKILL_ABSENT":
                requirement = requirements[target["entity_id"]]
                staffing = sum(
                    1
                    for assignment in assignments.values()
                    if assignment["date"] == requirement["date"]
                    and assignment["template_id"] == requirement["template_id"]
                    and requirement["skill_id"] in employee_skills[assignment["emp_id"]]
                    and attendance[assignment["assignment_id"]] != "Absent"
                )
                assert staffing < requirement["min_staff_required"]
            else:
                swap = swaps[target["entity_id"]]
                assert swap["status"] == "Pending"
                assert assignments[swap["assignment_id"]]["emp_id"] == swap["target_emp_id"]


def test_validator_detects_unlabelled_and_falsely_labelled_anomalies():
    tables, truth = build()
    unlabelled_truth = copy.deepcopy(truth)
    removed_label = next(
        row
        for row in unlabelled_truth
        if row["issue_type"] == "EMPLOYEE_DOUBLE_BOOKED"
    )
    unlabelled_truth.remove(removed_label)
    assert any(
        "ground_truth missing detected anomalies" in error
        for error in validate(tables, unlabelled_truth)
    )

    falsely_labelled_truth = copy.deepcopy(truth)
    met_requirement = next(
        row for row in tables["CoverageGap"] if row["gap"] == 0
    )
    requirement = next(
        row
        for row in tables["CoverageRequirement"]
        if row["requirement_id"] == met_requirement["requirement_id"]
    )
    falsely_labelled_truth.append(
        {
            "table_name": "CoverageRequirement",
            "entity_id": requirement["requirement_id"],
            "issue_type": "REQUIRED_SKILL_ABSENT",
        }
    )
    assert any(
        "ground_truth labels unsupported anomalies" in error
        for error in validate(tables, falsely_labelled_truth)
    )


def test_swap_history_preserves_approval_and_unapproved_application():
    tables, truth = build()
    events_by_swap = {}
    for event in tables["SwapRequestEvent"]:
        events_by_swap.setdefault(event["swap_id"], []).append(event)
    swaps = {row["swap_id"]: row for row in tables["SwapRequest"]}
    for swap_id, swap in swaps.items():
        events = events_by_swap[swap_id]
        assert any(event["event_type"] == "REQUESTED" for event in events)
        if swap["status"] == "Approved":
            assert any(event["event_type"] == "APPROVED" for event in events)
            assert any(event["event_type"] == "APPLIED" for event in events)
    anomaly = next(
        row for row in truth if row["issue_type"] == "SWAP_APPLIED_WITHOUT_APPROVAL"
    )
    assert any(
        event["event_type"] == "APPLIED_WITHOUT_APPROVAL"
        for event in events_by_swap[anomaly["entity_id"]]
    )
    assignment_events = {}
    for event in tables["ShiftAssignmentEvent"]:
        assignment_events.setdefault(event["assignment_id"], []).append(event)
    assignment_by_id = {
        row["assignment_id"]: row for row in tables["ShiftAssignment"]
    }
    for assignment_id, events in assignment_events.items():
        initial = [event for event in events if event["event_type"] == "ASSIGNED"]
        assert len(initial) == 1
        ordered = sorted(
            events, key=lambda event: (event["event_at"], event["assignment_event_id"])
        )
        assert ordered[-1]["new_emp_id"] == assignment_by_id[assignment_id]["emp_id"]
    assert any(
        event["related_swap_id"] == anomaly["entity_id"]
        and event["event_type"] == "SWAP_APPLIED_WITHOUT_APPROVAL"
        for event in tables["ShiftAssignmentEvent"]
    )


def test_overtime_separates_scheduled_hours_from_punched_work():
    tables, _ = build()
    attendance = {
        row["assignment_id"]: row for row in tables["AttendanceEvent"]
    }
    assignments = {row["assignment_id"]: row for row in tables["ShiftAssignment"]}
    employees = {row["emp_id"]: row for row in tables["Employee"]}
    templates = {row["template_id"]: row for row in tables["ShiftTemplate"]}
    for summary in tables["OvertimeSummary"]:
        week_start = date.fromisoformat(summary["week_start"])
        matching = [
            assignment
            for assignment in assignments.values()
            if assignment["emp_id"] == summary["emp_id"]
            and week_start
            <= date.fromisoformat(assignment["date"])
            <= week_start + timedelta(days=6)
        ]
        scheduled_hours = sum(
            templates[row["template_id"]]["hours"] for row in matching
        )
        actual_worked_seconds = 0
        for assignment in matching:
            event = attendance[assignment["assignment_id"]]
            if event["attendance_status"] == "Absent":
                assert event["check_in"] is None and event["check_out"] is None
                continue
            actual_worked_seconds += int(
                (
                    datetime.fromisoformat(event["check_out"])
                    - datetime.fromisoformat(event["check_in"])
                ).total_seconds()
            )
        cap = employees[summary["emp_id"]]["weekly_hours_cap"]
        assert summary["scheduled_hours"] == scheduled_hours
        assert summary["actual_worked_seconds"] == actual_worked_seconds
        assert summary["scheduled_overtime_hours"] == max(0, scheduled_hours - cap)
        assert summary["actual_overtime_seconds"] == max(
            0, actual_worked_seconds - cap * 3600
        )
    assert validate(tables) == []
    corrupted = copy.deepcopy(tables)
    corrupted["OvertimeSummary"][0]["actual_worked_seconds"] += 1
    assert any(
        "OvertimeSummary: derived values do not reconcile" in error
        for error in validate(corrupted)
    )


def test_reproducibility_md5_and_exports():
    first_tables, first_truth = build(seed=123)
    second_tables, second_truth = build(seed=123)
    assert output_hashes(first_tables, first_truth) == output_hashes(
        second_tables, second_truth
    )
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory)
        manifest = export_dataset(first_tables, first_truth, output, 123)
        assert manifest["ground_truth_rows"] == len(first_truth)
        assert manifest["source_manifest"] == {
            "provenance": (
                "Synthetic workforce records generated locally; holiday dates "
                "transcribed from the official National Portal of India calendar."
            ),
            "external_sources": [HOLIDAY_SOURCE_MANIFEST],
            "personal_data": False,
        }
        assert (output / "ground_truth" / "ground_truth.csv").is_file()
        truth_json_path = output / "ground_truth" / "ground_truth.json"
        assert truth_json_path.is_file()
        assert json.loads(truth_json_path.read_text()) == first_truth
        assert "ground_truth/ground_truth.json" in manifest["tables"]
        assert len(manifest["tables"]) == 18
        with (output / "CoverageGap.csv").open(newline="", encoding="utf-8") as file:
            assert next(csv.DictReader(file))["status"] in {"MET", "GAP"}
        with (output / "Employee.csv").open(newline="", encoding="utf-8") as file:
            assert next(csv.DictReader(file))["emp_id"] == "EMP-000001"
        assert (output / "ShiftAssignmentEvent.csv").is_file()
        assert (output / "ShiftAssignmentEvent.json").is_file()
        assert "ShiftAssignmentEvent" in (output / "workforce_seed.sql").read_text(
            encoding="utf-8"
        )
        assert validate_output_directory(output) == []
        truth_path = output / "ground_truth" / "ground_truth.csv"
        with truth_path.open(newline="", encoding="utf-8") as file:
            ground_truth_rows = list(csv.DictReader(file))
        with truth_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(
                file, fieldnames=("table_name", "entity_id", "issue_type")
            )
            writer.writeheader()
            writer.writerows(ground_truth_rows[1:])
        truth_json_path.write_text(
            json.dumps(ground_truth_rows[1:], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        assert any(
            "ground_truth missing detected anomalies" in error
            for error in validate_output_directory(output)
        )


def test_output_validator_rejects_ground_truth_json_mismatch():
    tables, truth = build()
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory)
        export_dataset(tables, truth, output, 123)
        json_path = output / "ground_truth" / "ground_truth.json"
        json_path.write_text("[]\n", encoding="utf-8")
        assert any(
            "ground_truth CSV/JSON mismatch" in error
            for error in validate_output_directory(output)
        )
