# AI Use and Prompt-History Disclosure

## 1. How AI Was Used

AI assistance was used extensively throughout this project. It supported the design and implementation of the typed data models, deterministic workforce data generator, anomaly injection, ground-truth labelling, structural and business-rule validation, reproducibility checks, data exports, dashboard, automated tests, and documentation.

AI also helped investigate and resolve issues identified during development, including the distinction between scheduled and punch-derived overtime, shift-assignment history, and assignment-independent coverage demand.

AI was used as a development assistant, not as a substitute for reviewing the results. I ran the generator, inspected the generated data, executed automated tests, investigated failures, reviewed and modified Python code, and checked the dashboard and generated CSV and SQLite outputs.

## 2. Prompt-History Summary

The following is a summary of the main topics covered during development. It is not a verbatim or exhaustive transcript of every prompt.

1. Define the workforce generator: Requested a seeded Python generator with a typed workforce schema, 2,000 employees, 100 teams, 90 schedule days, 5,000 leave requests, five labelled anomaly classes, separate ground truth, and reproducibility validation.
2. Understand execution: Asked how to run the project locally and whether it could be accessed through a website.
3. Follow the challenge requirements: Used the supplied challenge brief to guide the project structure and implementation.
4. Verify anomaly correctness: Requested checks to ensure that the five anomaly classes were genuine, correctly labelled, and independently detected by the validator.
5. Clarify overtime calculations: Worked through the difference between scheduled overtime and actual punch-derived working time, including how absences should be handled.
6. Improve assignment history and coverage: Added append-only shift-assignment events and staffing requirements generated independently of the roster.
7. Simplify the project structure: Reviewed duplicate files, legacy tests, caches, and unnecessary generated data.
8. Prepare the GitHub submission: Prepared the repository, README, validation documentation, and AI-use disclosure for publication.

This summary should not be treated as a replacement for the original conversation if the submission process explicitly requires a complete prompt transcript.

## 3. My Contribution and Review

My contribution included defining the project requirements, providing the challenge context, guiding implementation through iterative feedback, and reviewing the resulting application and data.

I personally:
- Ran the workforce data generator with the required configuration.
- Inspected the generated datasets and checked whether the records were consistent with the intended workforce scenarios.
- Executed the automated tests and investigated issues identified during validation.
- Reviewed and modified Python code during development.
- Checked the dashboard and examined the generated CSV and SQLite outputs.
- Reviewed the validation results and the distinction between scheduled overtime and actual worked overtime.

AI assistance contributed substantially to implementation and debugging. I used the generated implementation as a starting point and performed the above development and verification activities to assess whether the results matched the project requirements.

## 4. Testing and Validation

The project includes automated validation covering:
- Primary-key and foreign-key integrity.
- Required fields, data types, and permitted values.
- Date ordering and lifecycle transitions.
- Leave, attendance, and shift-assignment consistency.
- Anomaly labels and ground-truth records.
- Fixed-seed reproducibility and output checksums.
- SQLite integrity and foreign-key checks.

The recorded validation summary documents the canonical automated tests, CSV validation, reproducibility results, manifest checksums, and SQLite checks.

These results support the correctness of the tested synthetic dataset generator. They do not establish that the system is ready for deployment in a real HR, payroll, or workforce-management environment.

## 5. Limitations

- The prompt history above is a summary, not a complete verbatim transcript.
- AI assistance was extensive, and this is disclosed explicitly.
- The generated workforce data is synthetic and should not be interpreted as real employee information.
- Production deployment would require additional security, privacy, operational, and business-rule reviews.
