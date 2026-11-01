# YottaFlex Workforce Data Generator

A deterministic, parameterized workforce scheduling dataset generator. The root-level
`models.py`, `generator.py`, and `validate.py` define the canonical schema, pipeline,
and structural/reproducibility checks. The output is synthetic and contains both a
valid baseline and explicitly labelled negative cases in a separate ground-truth file.

## Requirements and setup

- Python 3.10+
- Runtime generation uses only the Python standard library.
- The optional local dashboard uses Flask (`python -m pip install -r requirements.txt`).
- Tests use pytest (`python -m pip install pytest`).

## Generate data

The default seed is `20261022`. Defaults generate exactly 2,000 employees, 100 teams,
90 consecutive schedule days beginning on 2026-01-01, and 5,000 leave requests:

```bash
python generate.py --out data/full
```

Equivalent explicit scale settings:

```bash
python generate.py \
  --seed 20261022 --employees 2000 --teams 100 \
  --days 90 --leaves 5000 --start 2026-01-01 --out data/full
```

Use smaller volumes for a quick run:

```bash
python generate.py --seed 77 --employees 40 --teams 4 \
  --days 14 --leaves 30 --out data/starter
```

The `--anomaly-rate` option controls the number of labelled examples per scenario
(range `0` to `0.05`). At least one example for each of the five required scenarios is
always generated, including when the rate is zero.

## Output files

Each output directory contains a CSV and JSON file per table, including EmployeeSkill,
shift/leave/swap event history and derived CoverageGap results, `ground_truth/ground_truth.csv`,
`workforce_seed.sqlite`, `workforce_seed.sql`, and `manifest.json`. CSV/JSON records use
the field names and identifiers defined in [`docs/data_dictionary.md`](./docs/data_dictionary.md).
The manifest records the seed, CSV row counts and SHA-256 checksums, derived dashboard
metrics, and a source-provenance record. The source manifest identifies the output as fully
synthetic, with no external sources or personal data. Ground truth contains only
`table_name`, `entity_id`, and `issue_type`; no issue labels are mixed into ordinary entity
rows.

## Generation pipeline

1. A private `random.Random(seed)` instance is created; generation does not use Python's
   global random state.
2. Teams, skills, shift templates, and employees are created before dependent records.
   Employee foreign keys always reference generated teams and primary skills.
3. Leave is generated before scheduling. Approved leave blocks baseline roster allocation.
4. Assignments are scheduled against shift times, weekly hours caps, and approved leave.
   Attendance outcomes and punch timestamps are derived from each assigned shift.
5. Five deliberate negative scenarios are injected and labelled in ground truth. Leave and
   swap decisions are written to append-only event tables; only approved baseline swaps
   are applied without an exception label.
6. Staffing requirements are generated for every date, shift, and skill before comparing
   them with the roster, so unstaffed combinations remain visible as coverage gaps. Each
   combination normally requires one qualified employee; the labelled skill-shortfall
   injection deliberately raises selected requirements above available staffing.
   Overtime summaries distinguish scheduled hours from observed work: scheduled hours
   include every assigned template; actual worked seconds sum attendance punch durations
   and count absent events as zero. Scheduled and actual overtime are each calculated
   against the employee's weekly cap. Coverage gaps are derived from assignments,
   employee skills, and attendance. Structural, lifecycle, key, and arithmetic checks run
   before export.

Negative cases cover an employee double-booking, an approved leave overlap, a required
skill-coverage shortfall, a weekly overtime-cap exceedance, and an applied shift swap
whose request is still Pending or Denied. These are purposeful exceptions, not silent
corruptions. The baseline is generated first, and each introduced exception has a direct
ground-truth row. The overtime-cap anomaly means scheduled hours exceed the cap; it does
not assert that the employee worked those hours. Actual overtime is separately based on
non-absent punch durations.

## Validation and reproducibility

Run the automated suite:

```bash
python -m pytest -q
```

Run structural validation and execute the same generation workflow twice, comparing MD5
checksums for each table's CSV and JSON payloads and the ground-truth CSV:

```bash
python validate.py --seed 20261022 --employees 40 --teams 4 \
  --days 14 --leaves 30
```

The validator independently recomputes all five anomaly types from the records, and checks
that ground truth labels every detected violation exactly once without unsupported labels.
To audit exported CSVs, including a full-scale output directory:

```bash
python validate.py --check-output data/full
```

Generation raises an error if structural validation fails. SQLite exports are additionally
checked with `integrity_check` and `foreign_key_check`.

## Optional local dashboard

```bash
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:8000`. The dashboard invokes the same root-level CLI and stores
downloads under `data/web_generated/`. It binds to localhost and is intended for local
development, not public deployment.

## Synthetic-data limitations

All names, leave decisions, attendance times, and holiday labels are synthetic. Holiday
dates are illustrative placeholders, not an authoritative public calendar. This dataset
is for development and evaluation, not an HR, payroll, or compliance system.
