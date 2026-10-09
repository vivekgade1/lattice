# Pre-registration: incremental specs and spec questions (2026-10-09)

The inputs are fixed before either tool exists (`job-scheduler/metrics/incremental/`): `REQUIREMENTS.v1.md`, `REQUIREMENTS.v2.md` and `questions.json`.

## (1) `lattice spec draft --since`

**Inputs:**
- the base: the drafted spec from the authoring experiment (`spec_gen.py` plus its trace, built from v1);
- the change: `REQUIREMENTS.v2.md`, which has four planted edits.

**The four planted edits, with what should happen to each:**
- **Changed:** "at most **two** queued or running runs per config" replaces "at most one". Expected: the old one-live-run checks are replaced, not kept alongside.
- **Changed, and a contradiction:** cancelling becomes "app admin or super admin", but the unchanged Roles rule still says executors "run and cancel jobs". Expected: the clash is reported, as a question or a conflicting-expectations finding.
- **Added:** `POST …/runs/{id}/retry` for failed runs.
- **Removed:** the "`PATCH` answers 405 / a version never changes" rule. Expected: its checks are retired.

**Pass when:**
- The rule diff classifies the edits exactly: 2 changed, 1 added, 1 removed, everything else unchanged.
- The combined spec validates.
- The cancel contradiction is reported.
- No checks of the removed rule remain.
- Run against the current spec-first Go build (which implements v1), the failures come **only** from the changed and added rules, and each is labelled "not built yet".

## (2) `lattice spec ask`

**Inputs:**
- the owner's spec v2 (job-scheduler `753a8d2`);
- the spec-first Go build, for `--verify`;
- 20 questions with answers written in advance from `REQUIREMENTS.v1.md`: 15 the spec covers and 5 it doesn't (Q16–Q20).

**Pass when:**
- at least 14 of the 15 specified questions are answered correctly;
- at least 4 of the 5 unspecified ones are answered "not specified";
- no made-up citation is accepted;
- with `--verify`, every answer verified on the app agrees with the truth.

**Report also:** answers that were wrong with confidence, the verified count, tokens and time.
