# AI use and prompt-history disclosure

> **Candidate review required:** This is a draft based on the project conversation.
> Edit it in your own words before submitting. The timeline below summarizes the
> available conversation; it is not a verbatim or exhaustive transcript. If the
> submission portal requires raw prompts, attach the original conversation export.

## How AI was used

AI assistance was extensive. It helped design and implement the typed data models,
deterministic workforce generator, anomaly injection and ground-truth layer, structural
and business-rule validator, reproducibility checks, exports, dashboard, tests, and
documentation. It also helped investigate and fix issues found while validating the
implementation, including the distinction between scheduled and punch-derived overtime,
shift-assignment history, and assignment-independent coverage demand.

The AI-generated implementation and test suite were run during development. The
`validation_summary.md` file records the measured test, data-export, SQLite, checksum, and
reproducibility results for the tested project version. Automated checks should not be
presented as personal manual verification unless the candidate independently performed
those checks.

## Prompt-history summary

This is a summary of the available development conversation, not a verbatim prompt log:

1. **Define the workforce generator.** Requested a production-quality, seeded Python
   generator with a typed workforce schema, 2,000 employees, 100 teams, 90 schedule days,
   5,000 leave requests, five labelled anomaly classes, separate ground truth, and
   reproducibility validation.
2. **Explain execution and web access.** Asked how to run the project and whether it could
   be run through a website.
3. **Align with the challenge brief.** Asked to build the project according to the supplied
   challenge PDF.
4. **Audit anomaly correctness.** Requested verification that the five anomaly classes
   were genuine, correctly labelled, and independently detected by the validator.
5. **Clarify overtime semantics.** Asked to distinguish scheduled overtime from actual
   punch-derived work and ensure absences do not count as worked time.
6. **Add assignment history and independent coverage demand.** Requested append-only
   assignment-change events and staffing requirements generated independently of the
   roster.
7. **Simplify the submission structure.** Requested removal of the duplicate generator,
   legacy tests, caches, and unnecessary duplicate generated data.
8. **Prepare the GitHub submission.** Supplied the repository URL and requested that the
   project be pushed there.

## Candidate contribution and review

The candidate supplied the project requirements, challenge context, repository URL, and
iterative feedback during development. AI assistance was used for implementation,
debugging, validation, and documentation. The candidate should add a factual account of the
code they personally reviewed, modified, and tested before submission.

## What I reviewed and how I know it works

The recorded validation is reproducible from the project instructions. It includes the
canonical automated tests, CSV validation, fixed-seed output hashes, manifest checksums,
and SQLite integrity and foreign-key checks. These establish the tested behavior of this
synthetic dataset generator; they do not establish readiness for real HR, payroll, or
workforce deployment.

Before submitting, the candidate should personally review the final code and generated
outputs, rerun the documented checks if possible, and update this disclosure with what was
actually reviewed, changed, and validated.
