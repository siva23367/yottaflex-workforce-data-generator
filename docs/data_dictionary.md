# Data dictionary

All IDs are strings. Dates are ISO-8601 (`YYYY-MM-DD`), times use 24-hour `HH:MM`,
and timestamps use ISO-8601. CSV columns preserve the order shown in the model
definitions. Every listed primary and foreign key is validated before export.

| Entity | Primary key | Fields and rules |
|---|---|---|
| Employee | `emp_id` | `name` (required string); `team_id` (Team FK); `primary_skill_id` (Skill FK); `weekly_hours_cap` (positive integer, default 40) |
| EmployeeSkill | (`emp_id`, `skill_id`) | Employee and Skill FKs; `proficiency` (`Basic`, `Intermediate`, `Advanced`) |
| Team | `team_id` | `team_name` (required string) |
| Skill | `skill_id` | `skill_name` (required string) |
| ShiftTemplate | `template_id` | `shift_name` (`Day`, `Evening`, `Night`); `start_time`, `end_time` (`HH:MM`); `hours` (positive integer) |
| ShiftAssignment | `assignment_id` | `emp_id` (Employee FK); `date`; `template_id` (ShiftTemplate FK) |
| ShiftAssignmentEvent | `assignment_event_id` | `assignment_id` (ShiftAssignment FK); `previous_emp_id` (nullable Employee FK for initial assignment); `new_emp_id` (Employee FK); `event_type` (`ASSIGNED`, `SWAP_APPLIED`, `SWAP_APPLIED_WITHOUT_APPROVAL`); `event_at`; `actor`; `related_swap_id` (nullable SwapRequest FK) |
| LeaveRequest | `leave_id` | `emp_id` (Employee FK); `start_date`; `end_date` (not before start); `status` (`Approved`, `Rejected`, `Pending`) |
| HolidayCalendar | `holiday_date` | `holiday_name` |
| AttendanceEvent | `attendance_id` | `assignment_id` (ShiftAssignment FK); `emp_id` (Employee FK); `attendance_status` (`Present`, `Late`, `Absent`); `check_in`, `check_out` (optional timestamps, null for absence; check-out follows check-in) |
| CoverageRequirement | `requirement_id` | `date`; `template_id` (ShiftTemplate FK); `skill_id` (Skill FK); `min_staff_required` (positive integer) |
| SwapRequest | `swap_id` | `requesting_emp_id`, `target_emp_id` (Employee FKs); `assignment_id` (ShiftAssignment FK); `status` (`Approved`, `Pending`, `Denied`) |
| LeaveRequestEvent | `leave_event_id` | `leave_id` (LeaveRequest FK); `event_type` (`APPROVED`, `REJECTED`, `PENDING_REVIEW`); `from_status`; `to_status`; `event_at`; `actor` |
| SwapRequestEvent | `swap_event_id` | `swap_id` (SwapRequest FK); `event_type` (`REQUESTED`, `APPROVED`, `DENIED`, `APPLIED`, `APPLIED_WITHOUT_APPROVAL`); `from_status`; `to_status`; `event_at`; `actor` |
| CoverageGap | `gap_id` | `requirement_id` (CoverageRequirement FK); `assigned_staff` (people count); `gap` (people count); `status` (`MET`, `GAP`), derived from assignments and attendance |
| OvertimeSummary | `summary_id` | `emp_id` (Employee FK); `week_start` (Monday); `scheduled_hours`; `actual_worked_seconds` (sum of non-absent punch durations; absent shifts add zero); `weekly_hours_cap`; `scheduled_overtime_hours`; `actual_overtime_seconds` |

## Identifier formats

| Entity | Format | Example |
|---|---|---|
| Employee | `EMP-` + six digits | `EMP-000001` |
| Team | `TEAM-` + at least three digits | `TEAM-001` |
| Skill | `SKL-` + two digits | `SKL-01` |
| ShiftTemplate | `SFT-` + shift code | `SFT-A` |
| ShiftAssignment | `SHIFT-<date>-<shift-code>-<six-digit-sequence>` | `SHIFT-2026-01-01-A-000001` |
| ShiftAssignmentEvent | `SAE-` + seven digits | `SAE-0000001` |
| LeaveRequest | `LVE-<year>-<six-digit-sequence>` | `LVE-2026-000001` |
| AttendanceEvent | `ATT-<employee-id>-<timestamp>` | `ATT-EMP-000001-20260101T060000` |
| CoverageRequirement | `COV-` + six digits | `COV-000001` |
| SwapRequest | `SWP-<year>-<six-digit-sequence>` | `SWP-2026-000001` |

The default shift templates are Day (06:00–14:00), Evening (14:00–22:00), and Night
(22:00–06:00 next day), each with eight scheduled hours. Night assignment dates identify
the calendar date on which the shift starts.

## Ground truth

`ground_truth/ground_truth.csv` has exactly these columns:

| Column | Meaning |
|---|---|
| `table_name` | Name of the entity table containing the deliberate negative case |
| `entity_id` | Primary-key value of the target record |
| `issue_type` | Stable label for the injected scenario |

The five labels are `EMPLOYEE_DOUBLE_BOOKED`, `APPROVED_LEAVE_OVERLAP`,
`REQUIRED_SKILL_ABSENT`, `OVERTIME_CAP_EXCEEDED`, and
`SWAP_APPLIED_WITHOUT_APPROVAL`. The required-skill scenario raises a coverage shortfall
for an existing date/template/skill requirement. This table has no `team_id` column, so
coverage is represented by date, shift template, and skill across the workforce.

## Relationships and derived values

- Employee to Team and Skill to Employee: 1:N; Team to Employee: 1:N.
- Employee to Skill: N:M through EmployeeSkill. Each bridge row requires both parent
  records; its composite key prevents duplicate employee-skill pairs.
- `EmployeeSkill` is the many-to-many bridge between Employee and Skill; employees have
  their primary skill and may have an additional skill with a proficiency level.
- Assignments reference employees and templates; attendance references both the assignment
  and employee.
- Assignment history is append-only: each assignment has an initial `ASSIGNED` event, and
  every employee change has a subsequent event recording prior/new employee, actor,
  timestamp, and related swap request when applicable. The current assignee on
  `ShiftAssignment` must equal the final history event's new employee.
- Leave and swap requests reference existing employees and assignments.
- Coverage requirements reference existing templates and skills. Demand is generated
  independently of the roster: every schedule date × shift template × skill combination
  normally requires one qualified staff member. The explicit `REQUIRED_SKILL_ABSENT`
  injection may raise a requirement above available staffing; that shortfall is labelled
  in ground truth. A zero-staff combination is a real coverage gap.
- ShiftTemplate to ShiftAssignment and CoverageRequirement: 1:N. ShiftAssignment to
  ShiftAssignmentEvent: 1:N append-only history, and to AttendanceEvent: 1:1 in this
  generated snapshot. LeaveRequest to LeaveRequestEvent and SwapRequest to
  SwapRequestEvent: 1:N append-only histories. CoverageRequirement to CoverageGap: 1:1
  derived result.
- `HolidayCalendar.holiday_date` is a unique calendar key. Attendance punch timestamps are
  null only when `attendance_status` is `Absent`; every generated assignment has one
  attendance record.
- Baseline assignments avoid approved leave, avoid overlapping work, and stay at or below
  each employee's weekly cap. Explicitly injected exceptions are identified in ground truth.
- Check-in and check-out timestamps are based on shift template start/end values.
- Attendance outcomes are generated reproducibly from the private seeded RNG. An absent
  employee has no punch timestamps; attendance-linked coverage counts only non-absent
  qualified employees.
- Leave and swap lifecycle changes are represented by append-only event rows. Approved
  swaps include approval and application events; pending/denied requests are not normally
  applied. The deliberate exception records an explicit application event while the request
  remains Pending.
- One `CoverageGap` result is persisted per `CoverageRequirement`; its assigned staff count
  and shortfall are calculated from employee skills and attendance.
- Event records are append-only snapshots of the generated lifecycle. The request rows
  contain final state; events preserve submission, decision, and application steps.
- Overtime summaries are grouped by employee and Monday-based week. `scheduled_hours`
  sums template hours for every assignment, regardless of attendance;
  `scheduled_overtime_hours = max(0, scheduled_hours - weekly_hours_cap)`.
- `actual_worked_seconds` sums `check_out - check_in` for non-absent attendance rows;
  absent events contribute zero and have no punch timestamps.
  `actual_overtime_seconds = max(0, actual_worked_seconds - weekly_hours_cap * 3600)`.
- `OVERTIME_CAP_EXCEEDED` ground-truth labels describe a scheduled-cap violation: the
  employee's cumulative scheduled hours cross their cap at that assignment. This is
  distinct from actual worked overtime; an absence cannot itself create worked overtime.
