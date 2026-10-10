# Results: Lattice specifies and checks Lattice Studio's own server (2026-10-10)

Pre-registration: `docs/results/2026-10-10-dogfood-prereg.md`, written before any mutant
ran and not edited since. Everything below is under `dogfood/studio/` unless a path says
otherwise.

## What was built

- `REQUIREMENTS.md`: 27 rules (R1..R27) for Studio's change-set API, the model endpoint,
  errors, ask and the static files, stated from `lattice/studio/server.py` and
  `tests/studio/test_server.py`. R24..R27 (full check, chat sessions, impact, accept
  outside git) are listed as not covered here.
- `spec.py`: a `lattice.spec.Spec` with 16 scenarios, no invariants and 11 `Unchecked`
  notes; `spec.trace.json` links 21 rules to scenarios, R1 is context, R22 and R24..R27
  are unchecked with reasons. The module is self-contained, because Studio loads it in
  isolation.
- `harness.py`: `make_app(db_path) -> ProcessApp` runs `python -m lattice.studio <project>
  --port $PORT --quiet --stub-llm` (through `/bin/sh -c`, because Studio takes its port as
  a flag and the process runtime gives it in `$PORT`). Each scenario gets a fresh copy of
  `project_seed/` (a toy notes spec, R1..R4) made into a git repository, with an untracked
  `SCRATCH.txt` that an accept must not commit. The health path is `GET /api/model`.
  `build_db` writes a one-table SQLite marker file that Studio never reads, because the
  process runtime always hands the app a database. `RUNTIME = process_runtime(framework=
  "studio")`.
- `check.sh`: `uv run python -m lattice.spec check --spec spec:SPEC --app harness:make_app
  --db harness:build_db --runtime harness:RUNTIME --layers DX`. It exits 0 when clean, 1
  on findings and 2 when the check cannot run.
- `studio.toml`: `[app]`/`[check]` connect this spec, so `dogfood/studio` is a Studio
  project itself. `[rebuild]` holds `repo = "../.."` and the two gates. A Studio
  `_run_check` on this directory answers `pass`, 16/16 targets, in 8 s. Studio's loader
  ignores the unknown `[rebuild]` table (`test_unknown_studio_toml_tables_are_ignored`).
- Server changes in `lattice/studio/server.py`. All of them are small and clearly marked:
  - **TEST ONLY `StubGenerator`**, selected by `--stub-llm` or `[llm] backend = "stub"`.
    It is never the default, and nothing in the HTTP API selects it. The edit appends the
    request as one rule. `stub:hold=<s>` sleeps first, which keeps a change drafting, and
    `stub:fail` returns no document. The draft keeps the module and marks the new rules
    as context. Ask always answers `not_specified`.
  - **`GET /api/changes/{id}?wait=S`** (0..60 s; anything else is 422): the request is held
    until the change's background job finishes. Scenarios have no conditional poll, so
    without this every scenario would race the drafting thread. It is documented in
    `API.md` and the module docstring.
  - **The bug fix** below.
  - Unit tests: `tests/studio/test_stub_llm.py` (13 tests).

## Check output on the real server

```
PASS: 0 findings (D 0, X 0); 96 cases run
notes:
  - layer X: the spec has no upper count bound and no machine; nothing to race
```

There are 16 scenarios and 96 step cases. Five runs in a row passed, about 8 s each, with
one server process per scenario.

| layer | ran? | why |
|---|---|---|
| D (scenarios) | yes | the whole spec |
| X (races) | yes, empty | Studio keeps no table, count bound or machine, so the layer has nothing to race. Concurrency stays Unchecked. |
| A (static) | skipped | no static checker exists for this framework (`framework="studio"`), and Studio is plain `http.server` |
| B (programs) | skipped | the spec has no endpoint programs, and Studio's answers are not Lattice programs |
| C (invariants) | skipped | invariants are SQL over the app's database, and Studio has none |
| R (rule layer) | skipped | it probes table contracts and needs a database |

## A real bug the self-spec found (fixed)

**A change rejected while it is still drafting comes back `ready`, and it can then be
accepted and committed.** `Studio.reject` sets `status = "rejected"` on a live change, but
the background job only respected `cancelled`. `_set(...)` overwrote the rejection with
`checking`, and `_run_change` then set the status to `ready`. Two things showed it:

- the scenario `a_change_rejected_while_drafting_stays_rejected`, written from R12 before
  any mutant existed, on the server before the fix:
  ```
  FAIL: 1 finding (D 1, X 0); 94 cases run
  [D silent] GET /api/changes/c1 (a_change_rejected_while_drafting_stays_rejected / step 4)
    body: expected {'id': 'c1', 'status': 'rejected'} within {... 'status': 'ready', ...}
  ```
- reproduced by hand: reject answered `200 {"ok": true}`, the change later answered
  `ready`, and `POST .../accept` answered 200 and committed the rejected proposal.

**Fix (a minimal separate change, `lattice/studio/server.py`):** `Studio._set` and the end
of `_run_change_job` now drop the job's result when the change is no longer
drafting/checking (`not in LIVE_STATUSES`). Before the fix they did so only when it was
`cancelled`. The regression test is
`tests/studio/test_stub_llm.py::test_a_change_rejected_while_drafting_stays_rejected`.
After the fix: `PASS ... 96 cases run`.

## Mutation run (blind: pre-registered predictions against outcomes)

The mutants were built by `make_mutants.py` (scratchpad) as patched copies of the frozen
`lattice/` package. `./check.sh` ran each one with `LATTICE_STUDIO_PYTHONPATH=<copy>`.
Control: the unpatched frozen copy gave `PASS: 0 findings; 96 cases run`. A sanity check
confirmed that `lattice.studio.server` imports from the copy when run from a project
directory.

| id | mutant | predicted catch | outcome | exit | as predicted? |
|---|---|---|---|---|---|
| M1 | accept twice allowed | `a_second_accept_is_409` step 4, by detail only | caught there: status 409 either way, detail "the spec changed since this proposal" instead of "change c1 is accepted, not ready" | 1 | yes |
| M2 | cancel allowed after ready | `cancel_after_ready_is_409` step 3 | caught there: 200 `{"ok": true, "status": "cancelled"}` instead of 409 | 1 | yes |
| M3 | follow-up does not supersede its parent | `a_follow_up_supersedes_its_parent` step 5 | caught there: c1 still `ready`, `superseded_by: null` | 1 | yes |
| M4 | stale proposal accept allowed | `a_stale_proposal_is_409` step 6 | caught there: 200 with a commit instead of 409 | 1 | yes |
| M5 | accept makes two commits | `accept_commits_once` step 3, plus every step expecting `85623dd…` | caught in 5 scenarios (`accept_commits_once`, `a_second_accept_is_409`, `a_stale_proposal_is_409`, `an_accepted_change_cannot_be_rejected`, `cancel_after_ready_is_409`): HEAD `b1911df…` instead of `85623dd…` | 1 | yes |
| M6 | reject allowed after accept | `an_accepted_change_cannot_be_rejected` step 4 | caught there: 200 `{"ok": true}` instead of 409 | 1 | yes |

**Blind result: layer D caught 6 of 6. Every mutant failed at the pre-registered scenario
and step, and none survived.** Layer X found nothing on any mutant, as predicted.

### Post-hoc

No scenario was added or changed after the mutation run, so there is nothing post-hoc.
Chronology: the spec, including the scenario that found the real bug, was written and
passing before the pre-registration. The fix came before the pre-registration and is
named in it.

### How strong these catches are

- **M1 is caught by the error text alone.** At the status level the stale guard masks it:
  the first accept changed the files, so a second accept is refused as stale (409) even
  with the status guard relaxed. A spec that checked only statuses would let M1 through.
- **M5 is caught through a reproducible commit id.** The harness pins git's identity and
  dates and turns off user and system config, so one accept of "Notes can be starred." on
  the seed always produces `85623dd…`. That id pins the tree, the single parent (the seed
  commit `6e48fed…`) and the message, and `SCRATCH.txt` is not in it. The id was recorded
  from the real server and checked by hand with `git log --name-status` (one commit,
  parent = seed, `M REQUIREMENTS.md`, `M spec.trace.json`; spec.py was rewritten
  unchanged, so it does not show). It is a snapshot oracle, not one computed
  independently. Any change to `project_seed/` or to the stub's output changes the id,
  and the spec's constants have to be re-recorded. `a_follow_up_supersedes_its_parent`
  checks the follow-up's accept by markdown and message only, so it does not catch M5.

## Open finding (reported, not fixed)

**Two follow-ups on the same parent can both be drafted.** The parent stays `ready` until
its first follow-up is ready, so a second `POST /api/changes {"parent": "c1"}` sent while
the first is still drafting answers 200. Reproduced: c2 (held) and c3 on c1 both reached
`ready`; c1 became `rejected` with `superseded_by: "c3"`, the one that finished first; c2
stayed `ready` on a rejected parent, and accepting c3 wrote c1+c3. No data is corrupted,
because accepting c2 afterwards is refused as stale. Still, "the follow-up supersedes its
parent" (API.md v1.1 item 3) is decided by which job finishes first. R16 states the rule
only for the window after the first follow-up is ready, and an `Unchecked` note names the
gap. A possible fix is to refuse a parent that already has a live follow-up (409). That is
a product decision (parallel alternatives may be wanted), so it is left to the owner.

## What the spec cannot express (the DSL, on a server with no database)

- **Repeated polling and timing.** A scenario has no conditional or retry step. The
  server needed `?wait=` to be checkable, and the `checking` status in between is never
  observed (R4 note).
- **Values computed at run time.** Steps cannot capture a value from one answer and use it
  in the next (an id, a commit sha, `git_head == commit[:7]`). Determinism had to be built
  into the harness (fixed git dates, a fixed project directory name, sequential ids)
  before answers could be stated as literals.
- **Effects outside HTTP answers.** `expect_db` and invariants read a SQL database only.
  Git history, files on disk and background threads cannot be addressed, so "exactly one
  commit, of those files only" is checked through the snapshot id (R7 note).
- **Lint is built around a database.** `lattice.spec.lint` on this spec reports 26 L004
  ("expects 409 but does not check that nothing was written": it only recognises
  `expect_db`, while here a later `GET /api/model` step checks it) and 4 L008 ("mentions
  'background' but is linked to no Await": an `Await` needs a service and a database).
  For an app with no database these are false positives. `check.sh` does not run lint.
- **LLM content.** What a real model edits, drafts or answers is not checked (R21/R22,
  LLM note). The stub makes every change deterministic, so the spec checks the server's
  lifecycle and not the model.
- **Concurrency.** Layer X races only table writes against count bounds and machines.
  Races between an accept and a drafting job, or between two follow-ups, cannot be stated
  (Concurrency note). The open finding above came from reading the code, not from a
  check.
- **Not covered by design:** `POST /api/check`, the chat sessions, change impact against a
  configured app, accept outside git or on a git failure (R24..R27). They need an app, the
  `claude` binary or a broken repository, and `tests/studio/` keeps them.

## Files

- `dogfood/studio/{REQUIREMENTS.md,spec.py,spec.trace.json,harness.py,check.sh,studio.toml}`
- `dogfood/studio/project_seed/{REQUIREMENTS.md,spec.py,spec.trace.json,studio.toml}`
- `lattice/studio/server.py` (stub LLM, `?wait=`, the reject fix), `lattice/studio/API.md`
  (`?wait=`)
- `tests/studio/test_stub_llm.py`
- `docs/results/2026-10-10-dogfood-prereg.md`, this file
