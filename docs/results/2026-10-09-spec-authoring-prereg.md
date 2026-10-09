# Pre-registration: LLM-drafted specs from markdown (2026-10-09)

**Question.** Can an LLM turn the owner's markdown requirements into a Lattice spec, checked by Lattice and reviewed by the owner instead of written by hand, and judge real builds the same way the owner's hand spec does?

**Pipeline under test** (lattice, built by this plan): `lattice spec draft`, then `lattice spec lint` (untraced rules, coverage, vacuity against stub apps), then `lattice spec probe` (a second model tries to break each rule while passing the spec), then `lattice spec review`. The rule ids and the trace come from `lattice/spec/rules.py` (`extract_rules`, `Trace`).

**Input.** `job-scheduler-go-baseline/REQUIREMENTS.md`, 65 rules, the prose the baseline builds were made from, plus a short context file (the seed users, applications and passwords, the service names the harnesses provide, and the fixture scripts).
- The drafting model never sees the owner's spec (`job-scheduler/spec/`). It runs through `claude -p` with no tools.
- Open questions are answered with the draft's own defaults. **No human help.**

**Primary measure.** Verdict agreement with the owner's spec v2 (job-scheduler `753a8d2`, lattice `3bc3539`) on the 11 existing builds:
- Go: spec-first, matched baseline, and the four bare-brief builds;
- Python: Sonnet master, prose baseline, Haiku, Opus, Fable.
- Spec v2's verdicts: pass on go, go-baseline and all 4 Python spec-first builds; fail on go-min-base-opus, go-min-base-sonnet, go-min-spec-opus, go-min-spec-sonnet and the Python baseline.
- Success means **11/11 same pass/fail**.

**Secondary measures:**
- For each failing build, the overlap of findings by the rule they break, compared with spec v2.
- Drafting cost: tokens, calls and time.
- Review cost: words on the review page, open questions and lint findings, compared with spec v2's hand-written size (spec.py + tickets.py, about 40k characters).

The owner's actual review time can only be measured by the owner, so it is reported as pending.

**Expectation, stated in advance.** At least 9/11 verdicts agree. Disagreements come from rules the prose leaves open (exact limits, error details), not from missed rules, if lint and probe work.
