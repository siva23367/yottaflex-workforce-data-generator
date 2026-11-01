# Validation summary

The final challenge-scale run used seed `20261022` and the required volumes: 2,000
employees, 100 teams, 90 days, and exactly 5,000 leave requests. Generation and structural
validation completed successfully.

| Output | Rows |
|---|---:|
| Employee | 2,000 |
| Team | 100 |
| Skill | 8 |
| EmployeeSkill | 2,473 |
| ShiftTemplate | 3 |
| ShiftAssignment | 54,432 |
| ShiftAssignmentEvent | 54,559 |
| AttendanceEvent | 54,432 |
| LeaveRequest / LeaveRequestEvent | 5,000 each |
| HolidayCalendar | 4 |
| CoverageRequirement / CoverageGap | 2,160 each |
| SwapRequest / SwapRequestEvent | 138 / 290 |
| OvertimeSummary | 27,118 |
| Ground truth | 622 |

Ground-truth issue counts are 216 double-booked assignment rows (108 overlapping pairs),
108 approved-leave overlaps, 83 required-skill shortfalls, 108 overtime-exceeding assignment
rows, and 107 unapproved swap applications. Coverage reporting found 83 gaps with a total
shortfall of 213 staff slots. Attendance included 2,803 absences. Overtime is explicitly
split into two measures: scheduled work totaled 864 overtime hours across 108 employee-weeks;
punch-derived work totaled 611.08 actual overtime hours across 88 employee-weeks. Absent
assignments contribute scheduled hours but zero actual worked seconds.

- All table primary keys, foreign keys, schema constraints, lifecycle rules, and derived
  coverage/overtime values validated independently from the ground-truth labels.
- Staffing demand has 2,160 date × shift × skill rows, generated before the roster and
  checked as a complete Cartesian product. Shift-assignment history records an initial
  state for every assignment and each of the 127 later swaps/reassignments.
- Audited the exported CSV files and confirmed SQLite `integrity_check` returned `ok` with
  no foreign-key violations.
- All 17 files listed in the manifest matched their SHA-256 checksums.
- Canonical test suite (legacy duplicate generator tests removed): 12 tests passed.
- Full-scale reproducibility: two independent workflow executions with the same seed
  produced 33 matching MD5 hashes (16 table CSVs, 16 table JSON files, and ground truth).

The two records involved in each double-booking are both mapped in ground truth. The
ground-truth layer is derived from the completed schedule and includes every affected
entity identifier rather than only the record that initiated an injection. The validator
reconstructs the expected anomaly labels from schedules, approved leave, qualified
attendance, overtime, and swap state; missing violations and labels unsupported by a real
violation both fail validation. The overtime ground-truth issue specifically indicates a
scheduled-cap breach; it is not asserted as actual overtime unless punch-derived work also
exceeds the cap. This run supersedes prior overtime metrics and replaces the stale
`data/full` output with the current schema.
