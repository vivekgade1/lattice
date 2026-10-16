# Improvement loop hardening, round 2 (the kill rig): results

Pre-registration: `2026-10-16-improve-hardening-kill-rig-prereg.md` (with Amendment 1). Worktree
`lattice-ekill`, branch `np-ekill`, 2026-10-16. Runner `dogfood/improve_kill_rig_probes.py` (stdlib
only, 1425 lines). Raw data: `docs/results/data/2026-10-16-improve-kill-rig/` (`<id>.json` per probe
with predicted, observed, pass and every raw row; `summary.json`; `control/` the same for the a4ab4a1
arm; `mutants.json`; the run logs `run-*.txt`). No LLM, no API key, no network, no Docker.
`lattice/`, `tests/`, `dogfood/improve_hardening_probes.py` and `dogfood/improve_mutants.py` are
byte-unchanged (`git diff bb3ff0c -- ...` is empty).

## 1. Verdict

Every tested-arm rule of prereg section 3 held in every run (8 of 8 probes RAN, 8 of 8 pass, 0
INVALID runs, 0 stop-to-kill over 2.0 s). The control (a4ab4a1, no `flock`, no heal) FAILED E-kill2,
E-kill2s, E-kill-import, E-interleave-t and E-interleave-p, so each of those probes discriminates;
no control pass of a required probe, so no non-discrimination to report. 10 of 10 mutants IK1-IK10
were caught by a predicted catcher, with the unmutated copy passing first. Several mutants were
also caught by probes that were not predicted to catch them (section 4); IK3 and IK10 were
predicted to be caught by E-sync only and were also caught by E-kill2.

What this does and does not show (prereg 2.1, 2.4 and risk 1): the torn write is EMULATED (a stop
after a prefix of the line, or a legal short write); a real SIGKILL does not tear a `write` on APFS.
The claim proved is quarantine: each emulated mid-write kill leaves exactly one unparseable line,
byte-equal to the journaled prefix; nothing else lost, duplicated, glued or blank; the next append
present; the lock free at once (0 lock-timeout warnings). The Linux page-boundary argument stays
from source, unmeasured.

## 2. Probes: predicted vs observed

Both arms ran every probe. `pass` is the tested-arm rule applied to each arm. "Fragment" = an
unparseable line; "matched" = equal to a journal prefix (heal newline excluded); "glued" =
unparseable and not a journal prefix. All counts are totals over the probe's runs.

| Probe (runs) | Predicted tested | Observed tested | Predicted control | Observed control |
|---|---|---|---|---|
| E-null (10) | 10/10; 1600 events per run, shim saw one write per line | 10/10 pass; 16000 log writes seen by survivors (= 10 x 1600), 0 unparseable, 0 blank, 0 lost | 10/10 | 10/10 pass (16000 writes seen, 0 unparseable) |
| E-kill2 (50) | 50/50; 5/5 kills confirmed; 4 fragments = the 4 P1 prefixes; stop-to-kill < 50 ms | 50/50; kills confirmed 250/250 (P1 200, P0 17, P2 17, P3 16); unparseable 200, matched 200 of 200, glued 0, blank 0; lost 0, duplicated 0; lock warnings 0; stop-to-kill max 0.03 ms; max survivor append 0.120 s; fresh append max 1.2 ms | 0/50; >= 1 lost event in 50/50 runs, 3-4 per run, >= 150 lost in total; 3-4 unparseable per run, each glued | 0/50; kills confirmed 250/250; unparseable 199, matched 0 of 200, glued 199; lost 199 (4 in 49 runs, 3 in 1 run: two fragments abutted); lost in 50/50 runs; blank 0; lock warnings 0 |
| E-kill2s (10) | 10/10; 4 fragments per run = journal prefixes | 10/10; kills confirmed 40/40; unparseable 40, matched 40 of 40, glued 0; lost 0; duplicated 0; lock warnings 0 | 0/10; ~800 of 800 survivor lines lost or glued per run | 0/10; unparseable 2600 (155-335 per run), matched 0 of 40; lost 10590 (954-1134 per run); blank 0 |
| E-kill-import (20) | 20/20; 27 ids once each; 1 fragment = journal prefix; re-import prints `27-K event(s) written`, rc 0 | 20/20; 27 parseable events in 20/20 (K = 1..25 drawn per run), unparseable 20 (all equal to the journal prefix), re-import line as wanted 20/20 | 0/20; 26 events after the re-import (event K glued to its fragment) | 0/20; 26 parseable events in 20/20 (1 missing each), unparseable 20, none equal to the journal prefix; re-import line as wanted 20/20 (its count is right, the file is not) |
| E-interleave-t (100 trials) | 100/100; B's write not seen in the 0.2 s window; B append >= 0.2 s | 100/100; B's write seen in the window 0/100; B append wall 0.2014-0.2174 s; unparseable 0, blank 0, lost 0, warnings 0 | B's write seen within 5 ms; `A1 B / A2` = 2 unparseable, both events lost, 100/100 | 0/100; B's write seen in 100/100, after 0.04-0.10 ms; unparseable 200, lost 200 (2 per trial); blank 0; warnings 0 |
| E-interleave-p (100 trials) | 100/100; B's done only after A resumes | 100/100; B done inside the window 0/100 (go-to-done 0.2030-0.2245 s); unparseable 0, blank 0, lost 0, warnings 0 | B done in the window; 100/100 corrupt | 0/100; B done inside the window 100/100 (go-to-done 0.2-0.5 ms); unparseable 200, lost 200; warnings 0 |
| E-split-stress (10) | 10/10; 4000 lines, 0 lost/dup/unparseable, 0 lock warnings | 10/10; 4000 lines each; unparseable 0, spliced-but-parseable 0, blank 0, lost 0, duplicated 0, lock warnings 0 (1.59-1.73 s per run) | (diagnostic, not a rule) >= 1 glued line in 10/10 runs, >= 10 per run | 0/10 pass; unparseable 37361 (3689-3780 per run), spliced-but-parseable 485, lost 37846, blank 0 |
| E-sync (1 x 1000 appends) | 1000/1000 `W F`, both arms | 1000/1000 `W F` | 1000/1000 | 1000/1000 `W F` (a4ab4a1 fsyncs too: a mutant catcher, not a discriminator) |

Intercept guard (prereg 2.3): every survivor reported at least its appends in shim-seen log writes;
every planned stop happened; no run was INVALID in either arm. A stop proves the shim reached the
call, so a refactor to `from os import write` would have produced INVALID, not a pass.

Control discrimination (prereg 2.5, amendment ruling 1): E-kill2 FAILED, E-kill-import FAILED,
E-interleave-t FAILED, E-interleave-p FAILED. E-kill2s FAILED, and that failure is round 1's
short-write defect (a4ab4a1 ignores the count of a short `write`), not a kill defect. E-null and
E-sync pass in the control by design.

A phenomenon the prereg did not name: in the control a glued line can still PARSE. The head of one
line (all fields up to a cut) plus the tail of another is a valid JSON object with a foreign tail.
The runner counts these as `malformed_parseable` (485 in the control's E-split-stress, 0 in the
tested arm); they are not in the unparseable counts.

## 3. Prediction misses

None of the tested-arm or control predictions of section 3 missed. Deviations that are not a failed
rule:

1. Stop-to-kill was 0.03 ms at most (prediction: under 50 ms). The slow-kill ruling (3) never fired.
2. `tests/improve` is 197 passed, not 129 (the prereg quotes round 1's count). It is 197 before the
   first edit and 197 after; the suite grew between round 1 and bb3ff0c. Not a regression.
3. IK3 and IK10 were predicted to be caught by E-sync only; E-kill2 caught both (section 4).
4. The control's E-kill2 "3-4 lost per run" held (4 in 49 runs, 3 in one), the total of 199 clears
   the predicted >= 150.

## 4. Mutants (`--mutants`, quick mode: E-null 2, E-kill2 10, E-kill2s 3, E-kill-import 5,
E-interleave-t/-p 20 trials, E-split-stress 2, E-sync 1; 123.8 s)

The unmutated copy under `<scratch>/mut/base` passed every probe first. "Killed" = any tested-arm
rule fails.

| Mutant | Edit in `events.py` | Predicted catcher | Probes that failed (observed) |
|---|---|---|---|
| IK1 | `flock` call removed | E-interleave-t/-p, E-split-stress | E-kill2, E-kill2s, E-interleave-t, E-interleave-p, E-split-stress |
| IK2 | no heal | E-kill2, E-kill2s, E-kill-import | E-kill2, E-kill2s, E-kill-import |
| IK3 | `os.fsync` removed | E-sync only (survive every kill probe) | E-kill2 (6 of 10 runs INVALID: the P2/P3 stop waits on an fsync that never comes), E-sync (trace `W` 1000/1000) |
| IK4 | heal reads byte `size` (always a prefix) | E-null, E-kill2 | E-null, E-kill2, E-kill2s, E-interleave-t, E-interleave-p, E-split-stress |
| IK5 | `LOCK_UN` right after the lock | E-interleave-t/-p | E-kill2, E-kill2s, E-interleave-t, E-interleave-p, E-split-stress |
| IK6 | `size`/`pread` before the lock loop | E-interleave-t/-p | E-kill2, E-kill2s, E-interleave-t, E-interleave-p, E-split-stress |
| IK7 | `LOCK_SH` instead of `LOCK_EX` | E-interleave-t/-p | E-kill2, E-kill2s, E-interleave-t, E-interleave-p, E-split-stress |
| IK8 | `_write_all` ignores the count | E-kill2s | E-kill2s |
| IK9 | `LOCK_TIMEOUT_S = 0.0` | E-interleave-t/-p, E-kill2 | E-null, E-kill2, E-kill2s, E-interleave-t, E-interleave-p, E-split-stress |
| IK10 | fsync before the write loop | E-sync only | E-kill2 (clause 2: the P2/P3 runs stop with m == K, not K+1; 6 events lost in 6 of 10 runs), E-sync (trace `FW` 1000/1000) |

`mutants: 10/10 killed`; every mutant was caught by at least one predicted catcher. IK1 under E-kill2
(honestly unpredicted, ruling 4): killed, 0/10 runs pass (blank 19, 24 glued, 24 lost; unlocked heals
do race). IK3 and IK10 are the only ones the kill points do not behaviourally observe: E-sync is the
BEHAVIOURAL catcher of both (a kill never observes the fsync), but the P2/P3 stops of E-kill2 are
placed by program position relative to the fsync, so removing or moving the fsync also moves or
removes the stop. For IK3 that surfaces as INVALID (6 runs), which the rules count as a failure,
never a pass; for IK10 as a failed clause 2. This is a property of how the rig places P2/P3, and it
means E-kill2 is not independent of the fsync call.

## 5. Post-hoc script corrections (old / new)

All are fixes to the runner; none changed a threshold, a control, or a recorded number. The first two
were found in `--quick` smoke trials (scratch `--out`) before any recorded run; the third and fourth
after the tested and control record runs.

1. Old: `Shim._stop` printed the stop line with `print(...)` to `sys.stdout`. New: `print(...,
   file=sys.__stdout__)`. An import-plan worker redirects `sys.stdout` while the CLI runs, so the stop
   line went into a StringIO, the parent never saw it and the stopped worker hung the trial (killed
   by hand).
2. Old: E-split-stress and E-kill2 indexed `e["w"]`, `e["seq"]` of every parsed event. New: an event
   lacking them is counted as `malformed_parseable` and excluded from the (w, t, seq) sets (the old
   code raised KeyError in the control arm and reported the probe as ERROR).
3. Mutant definitions (after the first `--mutants` run, aborted at IK7; its log is kept as
   `run-mutants-aborted.txt`, no `mutants.json` was written). Old: IK4's new text `os.pread(fd, 1,
   size)  # MUTANT IK4` and IK7's `fcntl.LOCK_SH | fcntl.LOCK_NB  # MUTANT IK7` had a trailing
   comment in mid-expression. New: no comment. The comment swallowed the rest of the line: IK4 became
   `torn = size > 0 and b""` (no heal at all, IK2's semantics) and IK7 did not compile. The aborted
   run's IK4 verdict (killed by E-kill2, E-kill2s, E-kill-import) is void; the final run is the one
   above. `make_mutant` also moved inside the per-mutant try (an uncompilable mutant is an error row,
   not a crash).
4. Old: the control arm wrote to `<--out>/control`. New: every arm writes to `--out` as given (the
   prereg's control command already passes `--out .../control`; the old code made `control/control/`).
   The record run's control files were moved up one level by hand, contents untouched.

The tested and control record runs were made by the script before correction 3 and 4 (probe code
identical); their `summary.json` lists corrections 1 and 2 only.

## 6. Decisions the prereg left open

- K is the 0-based index of the log write: K complete lines precede the stop, so `acked == K` and,
  for a stop in write K, `m == K` (P0, P1, S) or `K+1` (P2, P3). A continuation write of a short
  write belongs to its line.
- k (the cut for P1 and S) is drawn over the line without its newline, `1..len(body)-1`. The
  prereg's literal `1..len(line)-1` would let the largest k write a complete JSON object without its
  newline, which parses and is not a fragment.
- The parent creates the log empty before spawning (the shim matches it by device and inode).
- A planned stop that never happens, a kill not preceded by `WIFSTOPPED`, a worker that exits
  non-zero, or a shim count below the appends is INVALID (reported, never a pass).
- "Max survivor append wall while a holder was stopped" is the largest survivor append wall of the
  run (every holder stop is inside it), not split per stop.
- E-interleave-t: the 0.2 s window starts after B has entered `append` (B signals first), so a held
  lock gives B an append wall >= 0.2 s; E-interleave-p measures go-to-done in the parent. The 100
  thread trials run as 4 worker processes (one per pad, 25 trials, seeds `1000*i + trial`).
- E-kill-import K is the 0-based write index too (K events complete; the re-import writes 27-K).
- Stop-lines: workers print `stop <w> <K> <point> <k>`; the parent's reader thread does
  `waitpid(WUNTRACED)`, SIGKILL, `waitpid`, and records the interval from receiving the line.
- The control is `git archive a4ab4a1 lattice` extracted in the scratch directory (amendment), not
  round 1's shared clone.
- Seeds: E-kill2 writer `w` of run `r` draws P1's k from `Random(100 r + w)`; K from `Random(r)`.

## 7. E-regress and commands

| Check | Result |
|---|---|
| `uv run pytest tests/improve -q -p no:cacheprovider`, before the first edit | 197 passed (31.3 s); prereg quotes 129 (round 1) |
| the same, after | 197 passed (37.3 s) |
| `git diff --stat bb3ff0c -- lattice tests dogfood/improve_hardening_probes.py dogfood/improve_mutants.py` | empty |
| `ruff check` / `ruff format --check` on `dogfood/improve_kill_rig_probes.py` | All checks passed / already formatted |
| `improve_hardening_probes.py --runs 2 --only E-heal E-lock-a` (prefix match also ran E-lock-b) | 3/3 pass, 11.8 s |

Commands (`S` = the session scratchpad, `UV_NO_SYNC=1`, `PYTHONPATH` the worktree, `TMPDIR`
`$S/ekill/tmp`), wall time from `time`:

```
uv run python dogfood/improve_kill_rig_probes.py --arm tested  --scratch $S/ekill/run \
    --out docs/results/data/2026-10-16-improve-kill-rig            # 1:09.7 (summary 69.5 s)
uv run python dogfood/improve_kill_rig_probes.py --arm control --scratch $S/ekill/run \
    --out docs/results/data/2026-10-16-improve-kill-rig/control    # 22.7 s (summary 22.6 s)
uv run python dogfood/improve_kill_rig_probes.py --mutants --scratch $S/ekill/run \
    --out docs/results/data/2026-10-16-improve-kill-rig            # 2:03.8 (first, aborted run: 1:22)
```

Per-probe wall (tested / control, seconds): E-null 2.6 / 1.9, E-kill2 12.4 / 8.3, E-kill2s 2.5 / 1.6,
E-kill-import 2.4 / 2.3, E-interleave-t 5.4 / 0.1, E-interleave-p 26.9 / 5.3, E-split-stress 17.2 /
3.1, E-sync 0.1 / 0.1.

## 8. Limits (unchanged from the prereg, risks 1-7)

The torn write is emulated; the shim depends on the `os.write` / `os.fsync` attribute calls (guarded
by the stop and the count); a fragment is permanent (readers warn `not an event (skipped)` for it
forever); fsync and power loss are not observable by a kill (E-sync checks the call, not the disk,
and E-kill2's P2/P3 depend on the fsync's position, section 4); every kill is inside the lock and at
most 8 writers; 0 stop-to-kill delays over 2.0 s occurred, so the parent-latency risk did not
materialise on this host (an idle APFS laptop).

## Orchestrator verification (independent re-run)
`--arm tested`: 8/8 probes pass (E-kill2 50/50 with 250/250 kills confirmed and 200 of 200 fragments equal to the
journal prefix, 0 glued, 0 lost; E-interleave-t and -p 100/100; E-split-stress 10/10; E-sync WF 1000). `--arm
control` (the pre-hardening code, `git archive a4ab4a1`): E-kill2 0/50 (199 glued fragments), E-kill-import
0/20, E-interleave-t and -p 0/100, E-split-stress 0/10: the rig discriminates. `tests/improve` 197 passed; ruff
check and format clean; `lattice/`, `tests/` and the round-1 probe files are byte-unchanged (`git status` shows
only the three new paths).
**What this proves and what it does not.** With a deterministic emulated torn write, the hardened code leaves
exactly one quarantined fragment per mid-write kill and loses, duplicates, reorders or glues nothing, and the
next append goes through; the pre-hardening code glues the next event onto the fragment (199 of 200) and loses
events. A real SIGKILL does not tear a one-`write` append on APFS (so a random-kill rig cannot discriminate
here), the Linux behaviour is from kernel source and unmeasured, and power loss is untested.
