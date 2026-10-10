# Pre-registration: dogfood check of Lattice Studio's server, mutation run (2026-10-10)

Written before any mutant was built or run. Not edited afterwards; the outcome is in
`docs/results/2026-10-10-dogfood-results.md`.

## What is under test

- **The spec:** `dogfood/studio/spec.py` (sha256 prefix `4f65af7fdc6fb25f`), 16 scenarios,
  run by `dogfood/studio/check.sh` (layers D and X) through `dogfood/studio/harness.py`
  (sha256 prefix `2663618baf9bfead`): the real server, `python -m lattice.studio <fresh git
  copy of project_seed/> --stub-llm`, one process per scenario.
- **The server:** `lattice/studio/server.py` as of this note (sha256 prefix
  `be7fe2137265fef7`): the stub LLM, `?wait=` and the fix for a change rejected while
  drafting are in. On it, `./check.sh` answers `PASS: 0 findings (D 0, X 0); 96 cases run`
  (three runs in a row).
- **How a mutant runs:** the whole `lattice/` package is copied to a scratch directory, one
  patch is applied to the copy's `lattice/studio/server.py`, and `./check.sh` runs with
  `LATTICE_STUDIO_PYTHONPATH=<copy>` (the harness gives it to the server process as
  `PYTHONPATH`; the server runs in the project directory, so no working-tree `lattice` is
  on its path; the checker keeps the working tree). The working tree is never patched.

## The six mutants (exact patches, all in `lattice/studio/server.py`)

| id | behaviour | patch |
|---|---|---|
| M1 | a change can be accepted twice | `Studio.accept`: `if c["status"] != "ready":` becomes `if c["status"] not in ("ready", "accepted"):` |
| M2 | cancel is allowed after ready | `Studio.cancel`: `if c["status"] not in LIVE_STATUSES:` becomes `if c["status"] not in (*LIVE_STATUSES, "ready"):` |
| M3 | a follow-up does not supersede its parent | `Studio._run_change_job`: the two lines `parent["status"] = "rejected"` and `parent["superseded_by"] = cid` are replaced by `pass` |
| M4 | a stale proposal can be accepted | `Studio.accept`: the guard `if files_hash(self.root) != priv["project_hash"]: raise ApiError(409, "the spec changed since this proposal")` is deleted |
| M5 | accept makes two commits | `Studio.accept`: the one `git add -- <3 files>` + `git commit -q -m <message> -- <3 files>` becomes two commits with the same message: first `REQUIREMENTS.md` alone (add + commit), then `spec.py` and `spec.trace.json` (add + commit); the answered `commit` is HEAD (the second) |
| M6 | reject is allowed after accept | `Studio.reject`: the guard `if c["status"] == "accepted": raise ApiError(409, "change ... is already accepted")` is deleted |

## Predictions (which scenario fails first, and why)

| id | predicted catching scenario / step | why |
|---|---|---|
| M1 | `a_second_accept_is_409` step 4 | The status stays 409 -- the first accept changed the files, so the stale guard refuses the second -- but the detail becomes "the spec changed since this proposal" instead of "change c1 is accepted, not ready". Caught by the body only; at the status level M1 is masked by the stale guard. |
| M2 | `cancel_after_ready_is_409` step 3 | DELETE on a ready change answers 200 instead of 409. |
| M3 | `a_follow_up_supersedes_its_parent` step 5 | c1 stays `ready` with `superseded_by: null` after c2 is ready. |
| M4 | `a_stale_proposal_is_409` step 6 | accepting c2 after c1 answers 200 (and commits) instead of 409. |
| M5 | `accept_commits_once` step 3 (and every other step expecting the commit id `85623dd...`) | HEAD is a second commit whose parent is the first, so the answered id differs from the one-commit id the spec states. |
| M6 | `an_accepted_change_cannot_be_rejected` step 4 | reject after accept answers 200 instead of 409. |

**Prediction: layer D catches all 6** (`./check.sh` exits 1 on each). Layer X has nothing
to race (no table, no bound, no machine) and is predicted to find nothing on any mutant.
Any survivor is reported as a survivor; scenarios added after seeing a result are reported
separately as post-hoc and do not count toward the blind result.
