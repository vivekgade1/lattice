# Improvement loop hardening, round 2 (the kill rig): pre-registration

## 1. Status

2026-10-16, worktree `lattice-ekill`, branch `np-ekill` (from `next-phases` bb3ff0c). Written BEFORE
any code of this round; nothing new was run. Every number below that is not quoted from round 1 is
a PREDICTION. Round 1: `2026-10-15-improve-hardening-{prereg,results}.md`, data in
`docs/results/data/2026-10-15-improve-hardening/`. Results of this round:
`2026-10-16-improve-hardening-kill-rig-results.md`, data in
`docs/results/data/2026-10-16-improve-kill-rig/`. No LLM, API key, network or Docker.

**Why a round 2.** E-kill FAILED one clause ("every SIGKILLed writer was cut short, m < 500"):
appends cost ~0.1 ms on APFS, 8 writers finish 4000 lines in 0.3-0.5 s, and a kill at U(0.05, 0.5)
s often lands after the writer is done (26/40 killed writers really cut short, control 12/40). Every
other clause held in both arms, the control included (0 unparseable lines). E-stress passed in the
a4ab4a1 control arm too. So neither rig discriminated: round 1 does not show that the lock or the
torn-tail heal does anything under a kill. **Changed:** one new runner,
`dogfood/improve_kill_rig_probes.py` (stdlib only). **Not changed:** `lattice/` (no hook: 2.3),
`tests/`, `dogfood/improve_hardening_probes.py`, `dogfood/improve_mutants.py`.

## 2. Decisions (settled before code)

**2.1 Why a random SIGKILL cannot tear a line on APFS.** A signal is acted on at a syscall
boundary; a `write(2)` of a regular file of <= 9 KB is completed by the kernel or not started. The
writer issues ONE `write` per line (round 1's `_write_all` loops only on a short write, which a local
regular file does not return), so a kill lands before or after a whole line: no fragment exists for
the heal to fix. Round 1's control (0 unparseable in 10/10) is consistent with that. On Linux,
`generic_perform_write` stops at a page boundary when a fatal signal is pending, so a SIGKILL CAN
tear a write over 4 KiB there (from the kernel source; not measured, no Linux host here). A torn
line also follows a short write (disk full, NFS) or power loss. The round-2 rig therefore makes the
torn write happen deterministically (2.2) and measures what the code does AFTER it.

**2.2 Deterministic kill points by a syscall shim in the writer child.** Before importing
`lattice`, a worker replaces `os.write` and `os.fsync` (module attributes) with a shim that acts
only on the log file's fd (matched by `fstat` `(st_dev, st_ino)` against the log the parent created
before spawning; ack and journal files use the saved originals). A plan per writer: the append
number `K` (= the K-th log `write` call; one per line in both trees) and a point:
- **P0** entry of write K, nothing written. **P2** entry of fsync K (line written, not synced).
  **P3** after fsync K returns (still before `close`, i.e. under the lock in the tested tree).
- **P1 split** (emulated torn write): the real `write` of the first `k` bytes, then stop. `k ~
  U{1..len-1}` over the line (the heal `\n` prefix, when present, is extra), seeded, so the fragment
  never ends in `\n` and is never empty. Without a stop the shim writes the rest and returns the full
  count: transparent to the caller.
- **S short** (legal POSIX): the real write of `k` bytes, RETURN `k`; stop at the next log syscall
  (the tested loop's continuation `write`; a4ab4a1 ignores the count, so its `fsync`).
At P1 and S the shim first journals the whole intended buffer (`jrn-<w>`, raw). **Stop protocol:**
the shim prints `stop <w> <K> <point> <k>` (flushed) and `os.kill(os.getpid(), SIGSTOP)`; the
parent confirms `WIFSTOPPED` by `os.waitpid(pid, WUNTRACED)`, then SIGKILLs. The kill point is fixed
by program position, never by timing; every point is inside `append_lines`'s lock, so every kill is
also a holder kill. Ack: `seq\n` to `ack-<w>` after `append` returns (round 1's protocol).

**2.3 No hook in `events.py`.** Both trees call `os.write`/`os.fsync` through the module at call
time (tested: `_write_all`, `append_lines`; a4ab4a1: `append` lines 155-156), so the child shim
reaches them. Guard: each worker reports the shim's count of intercepted log writes; a count below
the number of appends attempted makes the probe INVALID (reported, never a pass), so a refactor to
`from os import write` cannot pass silently.

**2.4 The claim, corrected (contradicts the round-2 premise).** No append-only writer can make "0
torn lines" true after a torn write: the fragment is already on disk when the kill lands. The claim
proved is QUARANTINE: (a) each P1/S kill leaves exactly one unparseable line, byte-equal to the
journaled prefix (heal `\n` excluded); (b) no other unparseable line, 0 blank lines; (c) no
complete event lost, duplicated, reordered within its writer, or glued to a fragment; (d) the next
append (another writer or a fresh process) is present and the tail ends in `\n`; (e) the lock is
free at once after the kill (0 lock-timeout warnings). "Torn" below means a GLUED line (bytes of a
fragment and of a complete event in one line), not an isolated fragment.

**2.5 The control (a4ab4a1, no `flock`, no heal) must fail** E-kill2, E-kill2s, E-kill-import,
E-interleave-t/-p. If it passes one, that probe does not discriminate: reported as a finding, not
re-tuned. The control is built as round 1 (`make_control`, a shared clone at a4ab4a1 in scratch).

**2.6 E-stress replaced by a deterministic interleaving.** Two appenders are forced into the
critical section: A stops after half its line, B appends while A is stopped, A resumes. Tested: B
waits on the `flock`. a4ab4a1: B's line lands inside A's. Threads (two `EventLog` objects of one
process: `flock` is per open file description) and processes (SIGSTOP/SIGCONT).

**2.7 Mutants run by this runner** (not `improve_mutants.py`: its catchers are pytest, these are
probes, and it copies into `$TMPDIR`). Copy `lattice/` to `<scratch>/mut/<id>/`, apply one or more
`(old, new)` hunks (each must match once), run the quick mode with `PYTHONPATH` on the copy; killed =
any tested-arm rule fails. An unmutated copy runs first and must pass.

## 3. Probes (blind predictions)

Scratch: `<S>/ekill` (APFS). Seeds = run index, recorded. Pads cycle round 1's `PADS` (lines ~200,
600, 4200, 9000 B). Both arms run every probe; the pass rule is the tested arm's.

**E-null (shim transparency).** 10 runs, 8 processes x 200 appends, shim armed, no plan. Pass: 1600
events, 0 unparseable, 0 blank, intercepted writes = 1600 (one per line). **Predicted: 10/10, both
arms.**

**E-kill2 (the kill rig).** 50 runs; 8 processes x N = 200 appends after a go-file. Writers 0-3:
P1 at `K ~ U{20..180}`; writer 4: P0, P2, P3 by `run % 3`; writers 5-7 never stopped; then a fresh
process appends one event. Kills per arm: 250 (200 mid-write P1, 50 P0/P2/P3), each confirmed
stopped then rc -9. Pass, per run: (1) armed; 5/5 kills confirmed; (2) per killed writer seqs are
exactly `0..m-1`, `acked == K`, `m == K` (P0, P1) or `K+1` (P2, P3: the page cache survives a kill),
so `m < 200` for 100% by construction; (3) survivors 200 each, `seq` in file order; (4) exactly 4
unparseable lines, each equal to one P1 journal prefix; 0 blank; 0 duplicate ids or `(w, seq)`; (5)
tail ends in `\n`; the fresh append present, < 0.5 s; (6) 0 lock warnings. Recorded: stop-to-kill
latency, max survivor append wall while a holder was stopped. **Predicted tested: 50/50**; latency <
50 ms. **Control: 0/50.** With no heal the next line written after a fragment is glued to it; with no
lock survivors keep writing during the stop: predicted >= 1 lost event in 50/50 runs, 3-4 per run
(fewer than 4 only when two fragments abut, a fragment glued to a fragment loses no event), >= 150
lost in total; unparseable lines 3-4 per run, each glued.

**E-kill2s (real short writes).** 10 runs as E-kill2 but writers 0-3 use S at `K`, and writers 4-7
get S without a stop on every line (`k` = half the line; the tested loop completes it). Pass: as
E-kill2 (40 kills, 4 fragments = journal prefixes, survivors complete). **Predicted tested 10/10.
Control 0/10:** a4ab4a1 ignores the count, so every line of writers 4-7 is truncated and the next
line glues onto it (predicted ~800 of 800 survivor lines lost or glued per run; this is round 1's
E-short defect, not a kill defect, and is reported as such).

**E-kill-import.** 20 runs; an `import-plan` of a scratch copy of `p2-final.json` (27 events) into an
empty state dir, P1 at write `K ~ U{1..25}`, killed; then one fresh `import-plan`. Pass: parseable
events = the 27 ids of a clean sequential import, each once; one unparseable line = the journal
prefix; the re-import prints `<27-K> event(s) written` and exits 0. **Predicted tested 20/20.
Control 0/20:** event K is glued to its own fragment (26 events after the re-import; a third import
would restore it: not run, noted).

**E-interleave-t (threads).** 100 trials per arm (4 pads x 25, `k` seeded): one process, two
`EventLog` objects on one path; thread A's shim writes `k` bytes then waits on an `Event`; thread
B then appends; the harness waits until the shim sees B's log write or 0.2 s pass, then sets the
`Event`; both join. Pass (tested): B's write not seen in the window, file = A's line then B's, 0
unparseable, 0 blank, B's `append` >= 0.2 s, 0 warnings. **Predicted 100/100.** **Control:** B's
write seen within the window (< 5 ms), file = `A1 B \n A2 \n`: 2 unparseable, both events lost,
100/100.

**E-interleave-p (processes).** 100 trials per arm: A (P1-stop at its first append) SIGSTOPs itself;
parent confirms, sends B a go line on stdin, waits for B's `done` or 0.2 s, then SIGCONT A. Pass and
predictions as E-interleave-t (tested: B's `done` after A resumes, 100/100; control 100/100 corrupt).

**E-split-stress (round 1's E-stress, made discriminating).** 10 runs, round 1's shape (8 processes
x 2 threads x 250 = 4000), every log write split into two real writes 0.2 ms apart. Pass: 4000
lines, 0 unparseable, 0 blank, 0 lost or duplicated, order kept, 0 lock warnings. **Predicted tested
10/10.** Control (reported, not a rule; stochastic): >= 1 glued line in 10/10 runs, >= 10 per run
(~85% confident).

**E-sync (the fsync call, which no kill observes).** 1 process x 1000 appends, the shim tracing log
syscalls. Pass: between two `append` returns the trace is exactly `W F` (one write, then one fsync).
**Predicted 1000/1000, both arms** (a4ab4a1 fsyncs too: a mutant catcher, not a discriminator).

**E-regress.** `tests/improve` 129 passed (round 1's count); `git diff bb3ff0c -- lattice tests
dogfood/improve_hardening_probes.py dogfood/improve_mutants.py` empty; ruff check and format clean
on the new runner; `improve_hardening_probes.py --runs 2 --only E-heal E-lock-a` passes unchanged.

## 4. Mutants

`improve_kill_rig_probes.py --mutants` (2.7). Quick mode: E-null 2, E-kill2 10 runs, E-kill2s 3,
E-kill-import 5, E-interleave-t/-p 20 trials each, E-split-stress 2, E-sync 1. Ids IK (unused).

| Mutant | Edit in `events.py` | Predicted catcher |
|---|---|---|
| IK1 | `flock` call removed | E-interleave-t/-p (`A1\nB\nA2\n`: A lost), E-split-stress |
| IK2 | no heal (`if torn:` -> `if False:`) | E-kill2, E-kill2s, E-kill-import (glued, lost) |
| IK3 | `os.fsync(fd)` removed | E-sync only (predicted to SURVIVE every kill probe) |
| IK4 | heal reads byte `size` not `size-1` (always a prefix) | E-null, E-kill2 (blank lines) |
| IK5 | `LOCK_UN` right after the lock is taken | E-interleave-t/-p |
| IK6 | `size`/`pread` (torn?) decided before the lock loop | E-interleave-t/-p (a blank line) |
| IK7 | `LOCK_SH` instead of `LOCK_EX` | E-interleave-t/-p |
| IK8 | `_write_all` ignores the count (IH4's edit) | E-kill2s (survivors' lines truncated) |
| IK9 | `LOCK_TIMEOUT_S = 0.0` | E-interleave (warning, A lost), E-kill2 (warnings) |
| IK10 | `fsync` moved before the write loop | E-sync (trace `F W`) |

Rule: 10/10 caught by a predicted catcher; a survivor is reported, never re-anchored. Not predicted
either way: IK1 under E-kill2 (unlocked heals still isolate fragments; a racing pair of heals may or
may not add a blank line).

## 5. Risks, and what this cannot show

1. **P1 and S are emulated torn writes.** On APFS a real SIGKILL does not tear one `write` (2.1);
   the rig proves recovery GIVEN a torn write (Linux page-boundary kill, short write, NFS, crash),
   not that the kernel never tears. Linux is argued from source, not measured.
2. **The shim depends on `os.write`/`os.fsync` attribute calls** (2.3); the armed count makes a
   blind spot an INVALID run, not a pass. The shim is test code: a bug in it is a rig defect, which
   E-null and the per-kill journal check bound but cannot exclude.
3. **A fragment is permanent.** Readers warn `not an event (skipped)` for it forever; "0 torn
   lines" is not achievable by an append-only writer (2.4). Compaction is out of scope.
4. **fsync and power loss.** A SIGKILL keeps the page cache, so durability is unobservable here
   (IK3 survives the kill probes); E-sync checks the call, not the disk. macOS `fsync` does not flush
   the drive cache (round 1, risk 3).
5. **Every kill is inside the lock** (2.2); kills between appends were round 1's. Two writers in
   E-interleave, 8 in E-kill2: not a Studio server.
6. **Parent latency.** If a stop-to-kill takes over 2.0 s (a loaded machine), survivors degrade and
   the run fails clause 6: a finding, never retried.
7. **The control's E-kill2s failure** is round 1's short-write defect, not a kill defect (3).

## 6. Blind vs post hoc; verification

Every prediction is blind. A failed one is a finding, never silently fixed; a fix it motivates is a
later, labelled post-hoc commit; blind numbers stay as first measured. **Pass:** every tested-arm
rule of section 3 in every run; the control FAILS E-kill2, E-kill2s, E-kill-import, E-interleave-t
and -p (2.5; a control pass is reported as non-discrimination); 10/10 IK caught with the unmutated
copy passing; E-regress. Control counts (lost, glued) are diagnostic. Each probe writes `<id>.json`
(predicted, observed, pass, raw rows, seeds) and `summary.json` into `--out`; trials use a scratch
`--out` and only the final run writes the data dir.

## 7. Commands

```
cd /Users/vivekgade/Documents/lattice-ekill
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-ekill PYTHONDONTWRITEBYTECODE=1
S=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad
uv run python dogfood/improve_kill_rig_probes.py --arm tested --scratch $S/ekill \
       --out docs/results/data/2026-10-16-improve-kill-rig
uv run python dogfood/improve_kill_rig_probes.py --arm control --scratch $S/ekill \
       --out docs/results/data/2026-10-16-improve-kill-rig/control
uv run python dogfood/improve_kill_rig_probes.py --mutants --scratch $S/ekill \
       --out docs/results/data/2026-10-16-improve-kill-rig       # unmutated copy, then IK1-IK10
uv run pytest tests/improve -q
uv run python dogfood/improve_hardening_probes.py --runs 2 --only E-heal E-lock-a \
       --scratch $S/ekill/ih --out $S/ekill/ih-out
uv run ruff check dogfood/improve_kill_rig_probes.py
uv run ruff format --check dogfood/improve_kill_rig_probes.py
git diff --stat bb3ff0c -- lattice tests dogfood/improve_hardening_probes.py dogfood/improve_mutants.py
```

## Amendment 1 (orchestrator rulings, 2026-10-16, before any code)

**Approved as designed:** no hook in `lattice/improve/events.py` (the shim lives in the writer child only and
intercepts `os.write`/`os.fsync` by device+inode of the log); fixed kill points by program position (SIGSTOP,
`waitpid(WUNTRACED)`, SIGKILL); "torn" in this document means a glued line, and the integrity claim is
quarantine (exactly one unparseable line per mid-write kill, byte-equal to the journaled prefix, nothing else
lost, duplicated, reordered or glued, next append present, lock free at once), not "no fragment on disk".
The premise correction stands: a random SIGKILL cannot tear a line on APFS; the rig emulates the torn write
and says so; the Linux claim is from kernel source and stays unmeasured.
**Rulings.** (1) The control (a4ab4a1 tree via `git archive`) must fail E-kill2, E-kill-import and both
E-interleave variants; a control that passes one is reported as non-discrimination, never retuned. E-kill2s's
control failure is round 1's short-write defect and is labelled as such. (2) INVALID (intercept count zero) is
never a pass. (3) A run in which the parent takes over 2.0 s from stop to kill is a finding, not retried.
(4) IK1 under E-kill2 stays honestly unpredicted; report the observed result. (5) `lattice/` is expected
unchanged; any edit needs a stated, minimal, default-off justification in the results before it is kept.
(6) Existing `tests/improve`, `dogfood/improve_hardening_probes.py` and `dogfood/improve_mutants.py` are
byte-unchanged. (7) No writes outside the scratch directory; no API key.
