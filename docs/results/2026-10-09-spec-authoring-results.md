# Results: LLM-drafted specs from markdown (2026-10-09)

Pre-registration: `2026-10-09-spec-authoring-prereg.md` (`9638cf7`). Tools: lattice `b42485c` (draft, lint, probe, review), fixed at `e0073ea`. Artifacts: `job-scheduler/metrics/authoring/`.

## The run

- **Input:** `REQUIREMENTS.md` (65 rules) plus a 277-word context file (seed, service names, fixtures). The drafter, Opus through `claude -p` with no tools, never saw the owner's spec.
- **Attempt 1 failed because of a tool bug**, not the model: 5 rounds, 53 min, about $9.44 at list price. The CLI's final `result` holds only the last text block of a long answer, so every round parsed a tail fragment. Fixed in `e0073ea` (`ClaudeCliClient(full_text=True)` joins every assistant text block). The attempt is kept in `attempt1/`.
- **Attempt 2 (blind):** valid in 4 rounds, 17.8 min, 229k input / 147k output tokens, about $4.28 at list price.
  - The repair rounds were all about how to declare JWT auth in the `ApiProfile`, so the reference needs a clearer auth section.
  - The result: 903 lines, 50 scenarios (270 cases when checked, against spec v2's 154), 65/65 rules traced (60 checked, 3 unchecked, 2 context) and **12 open questions with defaults**.
- **Lint:** 2 findings, both false positives (L006 on a route that exists only to answer 405; lint should exempt 405-only templates). Vacuity against the all-200 and all-500 stubs found no vacuous scenario.
- **The review page:**
  - must-read sections (summary, 12 decisions, gaps): about **1,080 words**;
  - rule-by-rule table: about 1,460 words;
  - reference sections (scenario detail, back-translation): about 10k words, read as needed.
  - Compare: spec v2 was about 40k characters of hand-written Python.

## Primary measure: verdicts on the 11 builds against spec v2

| | blind (defaults only) | after one answer (Q7, a 2.8-min revise, $0.78) |
|---|---|---|
| agreement with spec v2 | **5/11** | **8/11** |
| v2-pass builds the draft fails | all 6 | 3: Python Sonnet master, Fable, Haiku |
| v2-fail builds the draft passes | 0 | 0 |

**The pre-registered success criterion (11/11) was not met. The expectation (≥ 9/11) was not met either, even after one answer.**

**Every remaining disagreement is a defect the hand spec missed.** Checked against the prose, by hand:
- **Passwords are 8 to 72 bytes** (R20). A password of four `é`s is 8 bytes and must be accepted. The Python Sonnet master, Fable and Haiku builds measure the minimum in characters and answer 422. Spec v2 only tests the upper boundary in bytes.
- **Haiku:**
  - outsiders listing an application's members get 403 instead of 404 (R5);
  - a 6-field cron expression is accepted (R36 says 5 fields);
  - `GET /applications/{app}/jobs/{id}` returns only `id`, `code_path` and `command`, with the other fields null.
- **The blind run's six extra failures** were one open question's default: Q7, the allowlist matched exactly versus on the basename. Answering it the way the owner's builds behave removed them.

On the 5 builds v2 fails, the draft fails them too, for the same classes of problem:
- concurrency 500s;
- unbounded inputs;
- the missing worker and scheduler services.

It also found more violations of the prose: `job_name` accepting `.`, `name` longer than 100 characters, the 404 detail text, and `now` with a `Z` offset rejected.

## Gap probe (Sonnet patches the spec-first Go build to break each of 20 signal rules)

| | generated spec (after Q7) | spec v2 (the owner's) |
|---|---|---|
| probe run on its own spec | 18 caught, **2 gaps** (R7 bcrypt, R65 the scheduler ticks every 2 min) | 15 caught, **5 gaps** (R7, R21, R47, R62, R65) |
| paired: generated-run patches replayed on the other spec | 2 gaps | 3 gaps (+R62); 3 broken |
| paired: v2-run patches replayed on the other spec | 3 gaps (R7, R47, R65) | 5 gaps; 1 broken |

**On paired patches, the generated spec never missed a violation that v2 caught.**

The gaps both specs share are the hard ones:
- **bcrypt (R7)** can't be seen over HTTP. It needs a database-level format check.
- **"once a minute" (R65)** can't be told apart from "every 2 minutes" within a 75 s wait.

## Reading

- **The drafted spec is stricter than the hand spec, not looser.** It caught real defects in three builds the owner's spec passed, and it closed more probe gaps.
- **Its blind disagreements came from open questions, as predicted.** A one-line answer and a 3-minute revise fixed the only one that mattered here.
- **The owner's job becomes reading about 1,100 words and answering 12 questions,** instead of writing about 40k characters. The owner's actual review time is pending; only the owner can measure it.
- **Cost:** about $5 at list price (the CLI, not the API) and about 21 min of model time for draft plus revise, with no human writing.
- **Caveats:**
  - one app, one drafting run, and the probe sampled once per rule per spec;
  - "a disagreement is a real defect" was judged by me against the prose;
  - spec v2 was written with this app's history in view, so the comparison doesn't favour the draft.

## Follow-ups found

1. Fix the draft reference's auth section: three of four repair rounds were spent there.
2. Exempt routes that only answer 405 from lint's L006.
3. `given` rows depend on the exact schema: Haiku's null-field bug only showed through them. That is a strength, but the review page should say when a scenario seeds rows directly.
4. Add a database-format check for hashed-secret rules (R7-type gaps).
