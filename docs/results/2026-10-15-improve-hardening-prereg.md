# Improvement loop hardening (spike 3, G10): pre-registration

## 1. Status

2026-10-15, branch `np-improvehard` (from `next-phases` a4ab4a1). Written BEFORE any code of this
change; nothing new was run. Predictions come from the a4ab4a1 code (`lattice/improve/*`,
`rebuild.py` `_gates`/`_gate_round`/`_log_gates`, `check.py` `_on_server`) and the committed spike-3
and improve2 data. Results: `2026-10-15-improve-hardening-results.md`, data in
`docs/results/data/2026-10-15-improve-hardening/`. No LLM, API key, network or Docker.

**The gap** (HANDOFF-2026-10-12 section 4): appends are serialised per process only (a
`threading.Lock`, one unchecked `os.write` on an `O_APPEND` fd, `fsync`), and `timings.setup`
(container start, DB setup per gate) is not copied into `gate_run`. **Changed:** `events.py` (the
writer); in `rebuild.py` a helper, `_log_gates`, two lines of `_gate_round` (2.9);
`tests/improve/test_events.py` (additions); `dogfood/improve_hardening_probes.py` (probes, IH
mutants). **Not changed:** `signals.py` (not a signal), every reader (`read_events`, `read_ledger`,
`analyze`, `verify`, `draft`, `importer`), `__main__.py` (the `apply` node's), plan and build
records, mock, UI, `API.md` (2.10).

## 2. Decisions (settled before code)

**2.1 Filesystem.** The guarantee holds for writers of this version on ONE host, local filesystem
with working `flock(2)` (APFS, HFS+, ext4, xfs, btrfs, tmpfs; only APFS measured). Out, documented
not detected: NFS (the client computes `O_APPEND` offsets), SMB, FUSE, synced folders, many hosts.

**2.2 Mechanism: an advisory `flock` on the log file itself, plus the single `O_APPEND` write.**
`append` serialises to bytes outside any lock; takes the in-process `threading.Lock` (kept: it
orders one `EventLog`'s threads); `os.open(path, O_RDWR|O_APPEND|O_CREAT, 0o644)` (non-inheritable,
PEP 446: a gate child never holds the lock); `flock(fd, LOCK_EX|LOCK_NB)` polled every 5 ms up to
`LOCK_TIMEOUT_S = 2.0`; heals the tail (2.5); writes the whole buffer, looping on a short write;
`fsync`; closes (releases). Not `lockf` (POSIX locks drop when ANY fd of the file in the process
closes), not an `O_EXCL` lock file (stale after SIGKILL): `flock` is per open file description
(threads or `EventLog`s of one process exclude each other) and dropped when the holder dies. A
single `O_APPEND` write of a regular file is in practice not interleaved on APFS or ext4 (POSIX
promises it only for pipes up to `PIPE_BUF`: 512 B macOS, 4096 B Linux; real lines reach 655 B);
the lock is what makes tail healing, short-write completion and import de-duplication race-free.

**2.3 Never block a build.** Lock timeout: `append` writes UNLOCKED (still the single `O_APPEND`
write), returns the event, warns `events: waited 2.0 s for the lock on <path>; appended without
it`. `flock` unsupported (`ENOTSUP`, `ENOLCK`, `EOPNOTSUPP`, no `fcntl`): unlocked at once, one
warning per `EventLog` (`events: no file lock on <path> (<reason>); appending without it`).
`extend` (an import, not a build) on timeout writes nothing and warns (`import-plan` already exits
2 on `log.warnings`). Any other `OSError`: as today, a warning, the event dropped, no exception.

**2.4 Line cap.** `MAX_LINE_BYTES = 65536` with the newline: 65537 is dropped (`events: a <kind>
event of <n> bytes is over 65536; not written`, None). Bounds a write and a reader line; ~100x the
largest real line; atomicity does not rely on it.

**2.5 Torn-line policy.** Writer, under the lock: a non-empty file whose last byte (`os.pread(fd, 1,
size-1)`) is not `\n` gets `b"\n" + line`, so the fragment becomes its own mid-file line and the
new event survives (a4ab4a1 glues it onto the fragment: lost). At most one `\n`; a file ending in
`\n` gets none, so on a healthy log the bytes written equal a4ab4a1's. The unlocked fallback runs
the same check (a race adds at worst one blank line; readers skip it). Readers are NOT changed and
never lock; one racing a write can still warn `torn last line`. Visible change: after an append to
a log with a torn tail, that fragment warns `line N: not an event (skipped)`.

**2.6 Ids.** Live ids stay `"e-"` + 16 hex of `uuid4` (`os.urandom`, fork-safe); collision ≈
n²/2^65 (n = 4000: 4e-13). Not made deterministic: a resumed or retried gate repeats every candidate
tuple (plan, node, round, gate). Import ids stay deterministic (`event_id(*parts)`) and become
unique per log under concurrency. At a4ab4a1 `import-plan` reads the known ids, then `extend`
appends: two concurrent imports both see none and both write. New: `extend` holds the lock across
the batch, re-reads the file's ids UNDER it (union with the caller's `known`), writes the rest (one
`os.write` per line, one `fsync`). The fix is inside `extend`; `__main__.py` is untouched. One
import writes the batch; the others print `0 event(s) written, N already there`.

**2.7 Ordering.** Per writer (process, thread): program order. Across writers: lock order, NOT `ts`
order (`ts` is taken before the lock, 1 s resolution). Readers order by `ts` (improve2 2.3), except two
documented, unfixed cases (a fix changes ranker output): a `snapshot` cluster takes the file-first
of equal max `ts`, the ledger's latest entry per signal is the file-last; same-second writes swap.

**2.8 `improvements.jsonl`.** Its only writer is `ledger --signal` in `__main__.py` (`open("a")`, one
buffered `write`, no lock, no fsync): not owned here. `events.py` exports the primitive
`append_lines(path, lines | callable, *, timeout=LOCK_TIMEOUT_S) -> (written, warnings)` (a callable
gets the path under the lock and returns the lines to write; `append`/`extend` use it) for the
`apply` node or the merge to adopt; here the ledger is only MEASURED (E-ledger). Its ids are the
owner's `--id`.

**2.9 The field.** `gate_run` gains optional **`setup`**: the numeric entries of the report's
`timings.setup`, keys verbatim and in report order, values not re-rounded. Units are the check's,
by key: suffix `_s` (`database_server_s` = container start or connect, `seed_build_s.<variant>`,
`db_clone_mean_s`, `database_teardown_s`) = seconds, float, 4 dp as the check rounds; `db_clones`
= count, int stays int. Kept: int/float, not bool, finite. The name mirrors the source key;
`setup_seconds` would mislabel `db_clones`, and keeping only `_s` keys would lose the clone count.
**Absent** (never `{}`/null) when no report was (re)written during that gate, `timings` or
`timings.setup` is missing or not a mapping, it is empty (every SQLite check writes `{}`), or no
entry is kept. Attribution as `layer_seconds`: only the gate during which `findings_json` changed.
Code: `report_setup(data) -> dict | None` beside `report_layer_seconds`; in `_gate_round`, `setups
= [report_setup(g.get("_report")) for g in gates]` before the existing `_report` pop, and
`self._log_gates(run, n, gates, counts, setups)`; `_log_gates(..., setups=())` passes `setup=`
(None is dropped). Never on the gate dict, so build records (`baseline.gates`, `rounds[].gates`),
plan records and per-tip results stay byte-identical. The reused-baseline event in `plan.py` and
`import-plan` (old records lack it) never carry it. Schema stays `v: 1`.

**2.10 Recorded, not ranked.** `analyze.occurrences` reads named fields, so ranker output is
unchanged with no edit. Why not rank it: (a) seed build and clones run inside layers C/D/X, so they
are already in `layer_seconds`, while server start and teardown are outside every layer: a naive
`gate:<role>:setup` cluster double counts; (b) no proposal template (the fix, e.g. a shared
`database_url`, has no `expected`), and `verify` would get a key with no before window; (c) no real
gate run carries it yet: design the cluster on real values. `events.py`'s docstring documents it and
2.1-2.7; for `API.md` at the merge: "`gate_run` may carry `setup` (the report's `timings.setup`,
recorded, not ranked); appends are serialised across processes by an advisory `flock`."

**2.11 Old logs.** Readers untouched: stored logs read as at a4ab4a1; old readers ignore `setup`. An
a4ab4a1 Studio process still running takes no lock (its single write may race a heal: 2.5).

## 3. Probes (blind predictions)

`dogfood/improve_hardening_probes.py`, scratch dirs in the session scratchpad (APFS). **Control
arm:** the same probes with `PYTHONPATH` on an a4ab4a1 copy (`git archive a4ab4a1 lattice`);
reported, never a pass rule. Deterministic probes: 10 runs, byte-identical. Stochastic: 10 runs,
the rule holds in each.

**E-stress.** 8 processes (spawn, `Barrier` after import) x 2 threads sharing one `EventLog` x 250
`finding_class` appends = 4000 lines, random ids, fields `w`, `t`, `seq`; `pad` cycles line sizes
~200/600/4200/9000 B (past both `PIPE_BUF`s). Pass: 4000 lines; 0 unparseable; 0 blank; the
`(w,t,seq)` set equals the attempted one (0 lost, 0 duplicated); 0 duplicate ids; `seq` increasing
in file order for 16/16 `(w,t)`; 0 lock-timeout warnings. **Predicted: 10/10.** Wall reported (2-40
s). Control predicted also 0 torn, 0 lost (~70% confident): E-stress may not discriminate.

**E-kill.** 8 processes x 1 thread x 500; each writes `seq\n` to its own ack file (raw `os.write`)
after `append` returns. SIGKILL writers 0-3 at U(0.05, 0.5) s (seed = run index, recorded); then a
fresh process appends one event. Pass: 0 unparseable lines INCLUDING the tail (a kill cannot split
a regular-file `write` this size: predicted 0, not "at most one"); per writer the present `seq`s are
exactly `0..m-1` with `acked <= m <= acked+1`; the 4 killed have `m < 500`; the post-kill append
returns in < 0.5 s and is present. **Holder kill:** a process holding the `flock` is SIGKILLed; the
next append returns in < 0.2 s (predicted ~0.01 s). **Predicted: 10/10.**

**E-lock.** (a) A holder takes the lock, sleeps 0.5 s, writes its line, releases; an append started
0.1 s later returns after 0.35-0.8 s, its line AFTER the holder's. (b) Holder keeps it 10 s: the
append returns in 2.0-2.5 s with the 2.3 warning, its line present and BEFORE the holder's.
**Predicted: 10/10.** Control: returns < 0.05 s, line first, no warning.

**E-import.** 4 processes call `lattice.improve.__main__.main(["import-plan", <scratch copy of
streak-plans-2026-10-11/p2.json>, "--project", P, "--state", S])` after a barrier on an empty state
dir. Pass: `events.jsonl` sha256 = one sequential import made by the a4ab4a1 tree; stdout = 1 x `N
event(s) written, 0 already there` + 3 x `0 event(s) written, N already there`. Also for p1, p3,
p2-final. **Predicted: 10/10 for all four.** Control: duplicates in 10/10 runs.

**E-heal.** 3 valid lines + the first 40 bytes of a 4th, no `\n`; append one event. Pass: file = old
bytes + `\n` + the line; `read_events` 4 events, 1 warning `line 4: not an event (skipped)`; a 2nd
append adds exactly its line (no blank). Control: 3 events (the new one lost).

**E-short** (test): `os.write` patched to write <= 7 bytes per call gives one complete line; an
`OSError` on the 3rd call gives a warning and None, and the next append's heal keeps its event.
**E-cap**: lines of exactly 65536 and 65537 bytes: the first written, the second dropped (2.4).

**E-field.** One real Rebuilder build round on the Studio seed (the rig of
`tests/studio/test_events.py`, imported, not edited); gate scripts write a check-shaped report to
`findings_json`. F1 `timings.setup = {"database_server_s": 3.2101, "seed_build_s.default": 0.8123,
"db_clones": 12, "db_clone_mean_s": 0.0412, "database_teardown_s": 1.5034}` -> that gate's
`gate_run` events (round 0 `build_baseline` too) have `setup` equal to it, same key order,
`db_clones` an int. F2 `setup: {}` -> no key. F3 no `timings` -> no `setup` (no `layer_seconds`, as
today). F4 report not rewritten during the gate -> none. F5 `{"database_server_s": 2.5, "flag": true,
"note": "x", "big": 1e309}` -> `{"database_server_s": 2.5}`. F6 two gate commands, only the second
rewrites the report -> only its event. F7 the saved build record has no `"setup"` and equals the
a4ab4a1 record of the same build (ids, times, paths masked). F8 apart from `setup`, `id`, `ts`,
each event equals its a4ab4a1 counterpart. **Predicted: 8/8, 10 runs identical (masked).**

**E-compat.** Both trees, same scratch paths. Inputs: `i2-run{1,2,3}-events.jsonl`,
`i3-events.jsonl`, `ir-replay-events.jsonl` with `ir-replay-improvements.jsonl` as ledger.
Commands: `report` (text, `--json`, `--top 10`), `ledger` (text, `--json`), `verify` and `draft`
(text, `--json`) for every proposal id of `report --json` and every ledger id, `import-plan` of p1,
p2, p2-final, p3 into empty dirs (stdout and file). **Forward:** `i3-events.jsonl` with F1's
`setup` added to every `gate_run` gives the unmodified log's outputs on both trees. Pass: 0
differing bytes over all comparisons (count reported). **Predicted: 0.** improve2:
`improve2_probes.py --runs 10` on both trees: 15/15 each, per-probe sha256 lists equal.

**E-ledger** (unchanged writer, measured, not a pass rule): 8 processes x 200 `ledger --signal
s:w<k> --status open --id w<k>-<i>` via `main`: predicted 1600 entries, 0 `not JSON`, 0 lost or
duplicated, 10/10 (one ~130 B buffered `write` per `O_APPEND` open). **E-cost**: 1000 appends, one
writer; pass: median <= control + 1.0 ms (predicted < 0.1 ms: fsync dominates).

**E-regress.** `tests/improve` (a4ab4a1 count taken before the first edit) + new tests,
`tests/studio/test_events.py` (11), `tests/test_cli.py` pass; `improve_mutants.py` 21/21; `check.sh`
PASS; `git diff a4ab4a1 -- lattice/` only in `events.py`, `rebuild.py` (helper, `_log_gates`, 2 lines).

## 4. Mutants

`improve_hardening_probes.py --mutants` reuses `improve_mutants.py`'s copy-patch-run code by import
(not edited; ids IH, clear of the `apply` node's IM22+). Suite `tests/improve`, one test per row.

| Mutant | Edit | Predicted catcher |
|---|---|---|
| IH1 | `flock` removed (append and extend) | E-lock (a), E-import |
| IH2 | `extend` does not re-read ids under the lock | E-import |
| IH3 | no tail heal | E-heal |
| IH4 | `os.write` return ignored | E-short |
| IH5 | unbounded lock wait (blocking `LOCK_EX`) | E-lock (b), test timeout 0.2 s |
| IH6 | event dropped on lock timeout | E-lock (b) |
| IH7 | cap `>=` instead of `>` | E-cap |
| IH8 | `setup` copied from `timings.layers` | F1 |
| IH9 | empty `timings.setup` logged as `{}` | F2 |
| IH10 | bool and non-finite values kept | F5 |
| IH11 | `setup` on every gate of the round | F6 |
| IH12 | the ranker adds a `gate:<role>:setup` cluster | E-compat forward |
| IH13 | `setup` stored on the gate dict | F7 |
| IH14 | heal prefix written after a final `\n` too | E-heal 2nd append, E-import bytes |

Rule: 14/14 caught by a predicted catcher; a survivor is reported, never re-anchored.

## 5. Risks, and what this cannot show

1. **E-stress may not discriminate** (APFS serialises single appends anyway): the lock then rests
   on the deterministic E-lock, E-import, E-heal, E-short.
2. **APFS only**: Linux claimed from kernel semantics, not measured; NFS, SMB, FUSE, synced folders
   are outside the guarantee.
3. **Power loss, kernel panic, disk full: not tested.** macOS `fsync` does not flush the drive
   cache (`F_FULLFSYNC` costs ms per event; not adopted): acknowledged events can be lost and a torn
   tail left; 2.5 only saves the NEXT event.
4. **A stopped holder** (SIGSTOP, debugger) costs every append 2.0 s until it resumes (~100 events:
   minutes). No backoff, by choice; documented.
5. **Mixed versions** (2.11), **readers racing writers** (a transient `torn last line` during a
   build) and **order** (2.7) remain. The stress probes drive `EventLog`, not two Studio servers.
6. **`setup` keys belong to `check.py`**: a rename changes the field silently (nothing reads it);
   no real Docker gate produced it here (the F rows are scripted reports). Two lines of
   `_gate_round` lie outside `_log_gates` (2.9); a field on the gate dict would change records.

## 6. Blind vs post hoc; verification

Every prediction is blind. A failed one is a finding, never silently fixed; a fix it motivates is
a later, labelled post-hoc commit; blind numbers stay as first measured; control numbers are
diagnostic. Pass: every rule of section 3, 14/14 IH and 21/21 IM caught, `check.sh` PASS with
a4ab4a1's case count, the diff as in E-regress. `improve2_probes.py` needs `--out` in scratch.

```
cd /Users/vivekgade/Documents/lattice-improvehard
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-improvehard
S=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad
uv run pytest tests/improve tests/studio/test_events.py -q && uv run pytest tests/test_cli.py -q
uv run python dogfood/improve_hardening_probes.py --runs 10 --scratch $S/ih \
       --out docs/results/data/2026-10-15-improve-hardening
uv run python dogfood/improve_hardening_probes.py --mutants            # control, IH1-IH14
uv run python dogfood/improve2_probes.py --runs 10 --scratch $S/i2 --out $S/i2/out
uv run python dogfood/improve_mutants.py                               # control, IM1-IM21
dogfood/studio/check.sh
uv run ruff check lattice/improve/events.py lattice/studio/rebuild.py tests/improve \
       dogfood/improve_hardening_probes.py
git diff --stat a4ab4a1 -- lattice/
```
