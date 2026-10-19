# Typed rule conditions (Spike H): the Streak arm (PS0-PSx), run on the owner's machine

2026-10-18. Worktree `/Users/vivekgade/Documents/lattice-ha`, branch `np-h-arm` from `next-phases` 978b468. Registered in
`2026-10-17-typed-conditions-prereg.md` section 3.3, carried unchanged by rounds 2 and 3 (`...-round2-prereg.md` 3.4,
`...-round3-prereg.md` 5.4). Data: `data/2026-10-18-typed-conditions-streak-arm/`. Nothing was committed, staged or merged. No
model was called; no API key. Streak was archived read-only (`git archive build/streak` into scratch); nothing was written into
`~/Documents/streak` or the main lattice checkout. Labels: **(m)** measured here, **(p)** the registered prediction.

## 1. Summary

**The Streak arm could not be run as registered: every PS row that scores forms is NOT RUN because its input and its scorer path do
not exist.** The prereg registered the Streak rule-shape files (`streak/`: forms, hand-written links, mutants, truth, pairs) as
"written when a Streak checkout is available" by a separate agent; no such files were ever written, and none of the archived sets
(`hshapes`, `hshapes2`, `hshapes3`) contains one. None of the frozen runners (`forms_probes.py`, `forms_probes2.py`,
`forms_probes3.py`, `forms_reader3.py`) has a `--stage streak` or any Streak code (the prereg's own command line for it is
commented "NOT RUN here"). Per the instruction for this node, those rows are stopped and reported, not worked around: no frozen
file was edited, no form was written by this node, no substitute number is given. The only things that could be measured with the
frozen library and no authored input were the environment checks, the scope count and the **no-forms baseline** of the PSx run,
below. Passing or failing nothing here says anything about gate H (it stays not met, round 3: 12 of 16 gated rows).

| row | registered probe | prediction (p) | observed | status |
|---|---|---|---|---|
| PS0 | `forms coverage` on S's rules | formed 22-45 (15-30%); `not_expressible` 40-70 | scope **146** rules (148 minus 2 context) (m). With no forms file: english 146, formed 0, not_expressible 0 (m, trivially) | **NOT RUN**: no Streak forms file and no not-expressible classification exists (both are authored inputs) |
| PS1 | PG1 on S cells | G == H 85-100% | none | **NOT RUN**: no Streak forms, truth, mutants; no Streak stage in any runner |
| PS1t | PG1t on S | 90-100% | none | **NOT RUN**: as PS1 |
| PS2 | PT2f on Streak pairs; the 50-active-habit limit stated twice (50 and 60) determined | as PT2f | none | **NOT RUN**: no Streak pairs file, no Streak forms |
| PS3 | PG3 on S: CD with G 10x, byte-identical except `Await` timing | 10/10 | none | **NOT RUN**: there is no G (no approved Streak forms). Only the no-forms CD was run (below) |
| PSx | wall time: CD on S with G | 30-60 s per run; gate <= 180 s | **no G run.** No-forms CD on S: 28.55, 27.81, 30.53 s (m) | **NOT RUN** with G. The no-G baseline is reported only as a baseline |

Gate (prereg: "The Streak arm's rows are NOT RUN, which is not a pass"): **Streak arm NOT RUN; nothing is claimed.**

## 2. What was verified before any run

* Frozen files (`data/.../verification.txt`): `freeze3.sha256` OK for `lattice/spec/forms.py` (`161c1bb9...`), `forms_tier2.py`
  (`288beb57...`), `impact_report.py` (`aa29aede...`), `dogfood/forms_probes3.py` (`c35cb27e...`), `forms_reader3.py`
  (`bcd638fd...`), `forms_mutants3.py` (`eafaedf0...`); `runner3.sha256` OK (3 files). The round-1 and round-2 runner files are
  at their freeze hashes (`7449106...`, `9e3ec0ad...`).
* Archived sets (the development data now): `sha256sum -c MANIFEST.sha256` OK for `hshapes` (9 files), `hshapes2` (11), `hshapes3`
  (11); `hshapes3` MANIFEST sha `1d0b8256...` = the round-3 record.
* Streak: `~/Documents/streak` branch `build/streak` = `a0679e9044486c98611febcd050a1fd23b6cd756` (the prereg's a0679e9). The
  checkout is on `main` with an uncommitted `studio.toml` edit (not mine, untouched); the arm used `git archive build/streak`.
* Docker 29.4.0 answers; image `mysql:8.4` present; `go` 1.26.2; driver venv has pymysql, bcrypt.

## 3. What was run (not PS rows)

**Scope (m).** `python -m lattice.spec forms coverage --trace spec.trace.json --json` on the archived Streak at a0679e9:
`{"english": 146}`, scope 146. The trace has 148 rules and 2 context rules, so the registered "S's 148 rules" is 146 in scope.
`forms show`: "no forms file".

**No-forms CD baseline (m), the denominator PSx needs.** `check.sh`'s own arguments with `--layers CD` and `--json` (layer A, B, X
off; the scoped dev layers the prereg names), `--database mysql` on a throwaway `mysql:8.4` container started by Lattice's dbserver
with `LATTICE_CONTAINER_OWNER=w-h` (label `lattice.owner=w-h`; ports are random free ports on 127.0.0.1), the lattice-ha library,
the archived Streak with a freshly built `bin/streak-api`. Three runs:

| run | wall (script) | `timings.total_s` | verdict | findings | cases | unchecked |
|---|---|---|---|---|---|---|
| 1 | 28.9 s | 28.55 | pass | 0 | 480 | 13 |
| 2 | 28 s | 27.81 | pass | 0 | 480 | 13 |
| 3 | 31 s | 30.53 | pass | 0 | 480 | 13 |

Everything except `timings` is identical across the three reports and the `notes` line (container name and seed time differ, as
expected). This agrees with Spike D's measured 29.6 s for the same app. It says the registered PSx prediction (30-60 s with G)
starts from a base of about 29 s without G, with `layers C` 0.41 s and `D` 22.7 s of run 1; whether G adds 1 s or 30 s was not
measured. Data: `baseline-cd-run{1,2,3}.json.gz`.

## 4. Why each row stopped (the missing pieces, exactly)

1. **No `streak/` shape files exist.** Needed for PS0 (the formed and not-expressible classification), PS1/PS1t (forms,
   hand-written links, `truth.json` cells over Streak apps, `h_verdicts.json`), PS2 (`pairs.json` incl. the 50 and 60 active-habit
   pair). The prereg: written by a separate agent after a checkout exists, hashed before use. Not done in rounds 1-3 (cloud
   container, no checkout) and not prepared since. This node did not write them: forms are owner-approved artefacts (`approved_sha`,
   `anchor`; `forms approve` refuses without a TTY), the registered author is a separate agent, and writing them here would make
   this node author and scorer.
2. **No Streak mutants.** The registered mutated apps are Spike D's change-set reference diffs in reverse where they touch a formed
   rule plus >= 10 agent-written Streak (Go) mutants. Neither list exists; D's 21-change set is itself a not-yet-run arm of the
   handoff.
3. **No runner path.** The frozen runners take `--stage dev|round1|h2|frozen` over a minishop shapes directory (in-process
   FastAPI on sqlite). There is no Streak stage, no process-runtime / MySQL cell runner, and no Go-mutation applier. The prereg's
   command line (`--stage streak --streak-ref a0679e9 --shapes .../hshapes/streak`) was a placeholder. A new runner would be a new
   file; the frozen ones may not be edited.
4. **PS3/PSx need a G**: `forms generate`/`check_with_forms` only run with an authoritative, approved forms file.

## 5. Predicted vs observed, misses

No registered prediction could be scored; there is nothing to call a hit or a miss. The one thing the run can say about a
prediction: PS0's scope is 146 rules, not 148, so its percentage bands (15-30% = 22-45 forms) were computed on a denominator two
too high (22-45 of 146 is 15-31%: immaterial). PSx's 30-60 s starts from a measured no-G base of 27.8-30.5 s.

## 6. Containers

All runs used `LATTICE_CONTAINER_OWNER=w-h`. Each check removes its throwaway container (3 started: `lattice-mysql-fde1ef5e`,
`...-ebc5189a`, `...-e7d2fd14`). After the runs `docker ps -a` shows 0 containers named `lattice-*` or labelled
`lattice.owner=w-h` (m). No other container was touched.

## 7. To run the arm for real (what it takes, in order)

1. Commission a separate author (a new agent, by the `claude` CLI, no API key; the prereg 3.1 brief: D1, D6, read access to the
   Streak checkout and `spec.py`/`spec.trace.json`) to write `streak/`: forms for 22-45 of the 146 rules (the 50-active-habit
   limit, list `limit` 1-200, ownership of habits/devices/check-ins, the habit and nudge state machines), a not-expressible
   classification for the rest, hand-written links (Streak's own `spec.py` already supplies H), >= 10 Go mutants as
   `(file, old, new)` edits with truth cells, and pairs (50 vs 60 limit). Hash it before any use.
2. Write a new runner file (frozen runner files stay as they are) that builds `bin/streak-api` per mutant, runs CD with and without
   G on MySQL, and scores PS0-PSx exactly as 3.3; freeze its hash first.
3. Then PS3 (CD with G ten times, byte-identical but `Await` timing) and PSx (<= 180 s).
This also gives the first execution of generated invariants on MySQL (so far compiled to MySQL text only, PG4).
