# Spike 3, second half (verify and draft): pre-registration

## 1. Status

Date: 2026-10-13. Branch `np-improve2` (from `next-phases` 7358be2). Written BEFORE any code of this
change exists; nothing of the new behaviour was run. Predictions come from the code at 7358be2
(`lattice/improve/`) and spike 3's data (`docs/results/data/2026-10-12-spike3/`). Only OLD behaviour
was run: `report` at 7358be2 reproduces `i3-report-before.txt` (first 9 lines of `i3-events.jsonl`)
and `ir-replay-report.txt` byte for byte (D5's baseline). Results: `2026-10-13-improve2-results.md`,
data in `docs/results/data/2026-10-13-improve2/`. No LLM, no API key, 0 tokens.

**Scope (the orchestrator's).** `lattice/improve/verify.py`, `draft.py` (wired into `__main__.py`),
`tests/improve/test_verify.py`, `test_draft.py` (one test per probe row), `dogfood/improve2_probes.py`,
mutants IM7-IM21. **Not done:** Studio API, web UI, mock, dogfood rule, rebuild-loop integration,
applying a draft, auto-closing the ledger. Log schema stays `v: 1`; log and ledger are only read.

## 2. Decisions (settled before code)

**2.1 Syntax, exit codes.** `lattice improve verify ID [--at TS] [--project DIR] [--state DIR] [--json]`;
`lattice improve draft ID [--project DIR] [--state DIR] [--out FILE] [--json]`. The state dir is found
as `report` finds it (`state_dir(..., create=False)`: never created).
- verify: **0** `improved`; **1** any other verdict (never a pass); **2** cannot read; **3** refused.
  draft: **0** produced; **2** cannot read inputs or write `--out`; **3** refused.
- A refusal prints `lattice improve: refused: <detail>` on stderr; with `--json` also `{"format": 1,
  "command", "id", "refused": true, "reason", "detail"}` on stdout. Reasons: `unknown_id`, `not_closed`,
  `no_closing_point`, `bad_at` (verify); `unknown_id`, `closed`, `needs_owner`, `out_in_project` (draft).
- **`--at TS`** (orchestrator amendment, 2026-10-13): the time the change took effect, in the exact
  format `%Y-%m-%dT%H:%M:%SZ`. It overrides ONLY the closing point of a CLOSED entry (an `open`
  entry is still `not_closed`; a malformed TS is refused `bad_at`, exit 3). Reason: the ledger `ts`
  is when the owner ran `ledger`, which is often after the measuring runs (V1d), and the report
  would then say `not_enough_data` although the data exist. The report carries
  `closing_point: {"ts", "source": "ledger" | "--at"}` and, when overridden, the warning
  `closing point overridden by --at (ledger ts <ts>)`. Nothing else changes (before/after rule,
  minimums, verdicts). The ledger is never edited.

**2.2 The ledger entry (verify).** `ID` matches the ledger `id` field first (`I-01`, or the `imp-` id
the draft suggests); else an `imp-` id resolves to the log's key with that id, the entry is
`analyze._closed_for(key, ledger)` and only that key is reported; else `unknown_id`. Several entries
for one id (file order): the **last** decides the status (`open` -> `not_closed`); the **closing
point** is the `ts` of the FIRST entry of the final run of consecutive `closed` entries (closed twice
-> the earlier; warning `closed N times in a row`). Closed, reopened, closed again: the before window
starts after the earlier close. Mixed signals for one id: the closing entry's, with a warning. A
closing entry without a canonical `ts` -> `no_closing_point`. `commit` is echoed, never used (free
text in real ledgers: `v1.11`, `scratch-clone studio.toml (not merged)`; all six I-3 gate runs carry
the same `sha` a0679e9); no commit -> verified, warning `no commit recorded`.

**2.3 Attribution.** `ts` is parsed with exactly `%Y-%m-%dT%H:%M:%SZ` (the format of
`EventLog.append`, the importer and `ledger`). Before = `ts` < closing point (and > a previous closing
point); after = `ts` > closing point; `ts` == closing point is **excluded from both** and counted
(`excluded.at_close`); a non-canonical or missing `ts` is excluded and counted (`excluded.undated`).
File order is irrelevant. After frequency == `report --json` `closed[].recurred` (canonical logs).

**2.4 Families and units.** Occurrences come from `analyze.occurrences(events)` unchanged. The ledger
`signal` is an fnmatch pattern; each matched key gets a `per_key` row. Family, by the producing kind:
- **cost** (`gate_run`): occurrences continue by nature; measure = mean cost per occurrence; several
  matched keys are summed per source event (one occurrence per event). Unit always `s`.
- **level** (`finding_class` with `snapshot: true`): latest count before vs latest count after.
- **occurrence** (`node_ended` causes, `human`, `change:rejected`, other `finding_class`): the change
  should stop them. Exposure per window (no such field exists; this stands in): `cause:*` ->
  `node_ended` of any status; `human:*` -> `plan_started`; `change:rejected`, `lint:*` -> `change`;
  other finding classes -> non-reused `gate_run`. Two families under one pattern: `not_enough_data`.
- Unit over both windows together, as the ranker: `s` if any occurrence recorded seconds, else `usd`
  if any recorded dollars, else none; **never converted**; a window with no value in that unit has
  `cost_per: null`. In the occurrence family cost is information, not the verdict.

**2.5 Minimums and verdicts.** Vocabulary: `improved`, `not_improved`, `recurred`, `not_enough_data`;
`direction` `down` (rel <= -5%), `up` (rel >= +5%), else `flat`.
- cost: n_before >= 3 and n_after >= 3, else `not_enough_data`. `improved` iff rel <= -0.05 AND
  max(after) < min(before) (full separation; for 3 vs 3 the one-sided exact permutation p = 1/20).
  Else `not_improved`, reason `cost rose` (up), `no material change` (flat) or `overlap` (down).
- occurrence: f_before = 0 -> `not_enough_data` (`nothing to verify`); f_after > 0 -> `recurred`
  (whatever the exposure); f_after = 0 and exposure_after >= 3 -> `improved`; else `not_enough_data`.
- level: no snapshot in a window -> `not_enough_data`; latest_after < latest_before -> `improved`;
  else `not_improved` (one snapshot suffices: a level). No occurrence at all: `not_enough_data`.

**2.6 Prediction and error.** The ledger stores no prediction and the log has no proposal event, so
it is reconstructed: `_proposal(c)["expected"]` for the matched key's cluster built from the **before
window alone** (no ledger, no top-N), i.e. what `report` showed at the close. `gate:level_*:X` with
logged repeats r > 2 (`change_set`): predicted delta = `-expected.saving_s_per_gate` s per occurrence
(X scales with repeats); r <= 2 or a tie (`experiment`): none; `gate:node:X`, r > 1: same, to 1.
**Every other template: `no prediction recorded`, nothing compared.** `score = f x c` is the cost of
the problem, never a predicted saving or used as one. A pattern of > 1 key, or other units: none.
Error (as I-3) = |measured_delta - predicted_delta| / |predicted_delta|, both signed, measured_delta =
mean_after - mean_before, predicted as shown (rounded to 0.1 s). `error: null` if a side is missing.

**2.7 Verify output.** JSON (`indent=1`, this key order): `format: 1`, `command: "verify"`, `id`,
`entry {id, signal, status, commit, ts}`, `closing_point`, `keys`, `family`, `unit`, `verdict`,
`reason`, `direction`, `before`/`after` `{n, frequency, cost_per, min, max, first_ts, last_ts,
exposure, evidence (<= 25 event ids)}`, `change {delta, relative, separated}`, `predicted {delta,
unit, source, basis} | null`, `prediction_note`, `error`, `excluded {at_close, undated}`, `context
{other_closes, per_window}` (other ledger closes between the first before and last after event; for
gate runs the distinct `x_repeats`, `selected`, `mode`, `sha[:7]` per window), `per_key`,
`blind_spots`, `warnings`. Costs to 2 decimals, `relative` and `error` to 3. No absolute path (the
log is `events.jsonl`). Text: `Lattice improve verify: <id> (<signal>), closed <ts>, commit
<commit|->`, then labelled lines `Verdict`, `Family`, `Before (ts < close)`, `After (ts > close)`,
`Change` (with `every after below every before: yes|no`), `Predicted` (or `no prediction recorded`),
`Error: |measured - predicted| / |predicted| = <pct>` (or `-`), `Excluded`, `Context`, `Evidence`,
`Blind spots` (fixed: section 5's risks 1-6, worded for the reader), warnings.

**2.8 Draft.** `ID` is a proposal id, resolved against ALL clusters of the full log with the ledger
(`clusters(events, ledger)`, `_proposal`, no top-N). Refusal order: `--out` inside the project or
state dir -> `out_in_project`; no such id -> `unknown_id`; cluster closed -> `closed` (`closed by <id>
at <ts>`); `needs_owner` -> refused with the proposal's `why` (it names the protected paths). Nothing
is written on a refusal. Output: stdout, or only `--out` (stdout then `draft <id>: <N> bytes
written`); never a project file, the log, the ledger or a commit. `studio.toml` is read (gates,
current values; absent -> `current: not set`). A draft opens with `# Draft: <title>` (prereg: `#
<title>: pre-registration (stub)`), `Proposal <id> (<kind>), signal <key>; status open. As of <ts>.`
(the log's latest `ts`: the only time in a draft) and `NOT APPLIED: this draft changes nothing; the
owner decides.` Required sections, in order:
- **change request** (`change_set`, no literal): `## What to change`, `## Why`, `## Evidence` (the
  `report` signal line plus ` [<class>]`, then `<event id> <ts> <cost>` per evidence event), `##
  Acceptance gate` (`[rebuild] gates`, else `the owner names the gate`), `## Measure afterwards`, `##
  Not decided here` (cost to fix, risk, human time). `cause:timeout|token_budget|usage_limit` name a
  knob, no value: `value: the owner decides`.
- **config** (`change_set` with a literal in this fixed table, all `[rebuild]`: `gate:level_*:X` ->
  `level_gate_args = ["--x-repeats", "2"]`; `gate:node:X` -> `scoped_gate_args += ["--x-repeats",
  "1"]`; `gate:node|level_*:D` -> `scoped_gate_args|level_gate_args += ["--await-cap", "5"]`;
  `human:spec_sync` -> `sync_spec = "auto"`): as change request, `## What to change` holding a fenced
  `toml` block with the literal result (`+=` appends to the current list; `=` as titled, noting a
  replaced current value), a `current:` line, and `## Expected` (`report` wording) if `expected` exists.
- **module** (`module_draft`): `## Module skeleton` (fenced `lattice-module.toml`: `name` = key tail,
  `version = "0.0.0"`, `kind = "custom"`, `description` = title, `provides = []`, `requires = []`), `##
  What it must provide`, `## Evidence`, `## Acceptance gate`, `## Measure afterwards`, `## Not decided
  here`.
- **prereg stub** (`experiment`): `## 1. Status` (`written BEFORE any probe`), `## 2. Question`, `## 3.
  Signal and evidence`, `## 4. Probe`, `## 5. Pass rule and blind prediction` (empty `PREDICT:` lines),
  `## 6. Measure afterwards`, `## 7. Blind spots`.
- `## Measure afterwards` always holds `lattice improve ledger --signal <key> --status closed --id
  <proposal id> --commit <SHA>` with `run it when the change lands, before the runs that measure it`,
  `lattice improve verify <proposal id>`, and the minimum (3 occurrences / 3 exposures / 1 snapshot).
- JSON: `{format: 1, command: "draft", proposal, as_of, template, sections: [{heading, body}], toml,
  ledger_command, verify_command, text}`. Same log, ledger and `studio.toml` -> the same bytes.

## 3. Probes (blind)

Every probe is deterministic and runs **10 times**: pass = identical output 10 of 10 plus its rule.
Inputs are scratch copies; `--project` is an empty dir (the "fixture" adds `studio.toml` = `[rebuild]
gates = ["./check.sh"]`). I-3 log = `i3-events.jsonl` (18 events; the first 9 are the before phase).

**V1 real I-3 data.** The real closing entry was never committed, only its effect
(`i3-report-after-closed.txt`: I-01, commit `scratch-clone studio.toml (not merged)`, no `recurred`,
so its `ts` was >= 23:11:50, the last after-run). V1a writes `{"id": "I-01", "signal":
"gate:level_regression:X", "status": "closed", "commit": "scratch-clone studio.toml (not merged)",
"ts": "2026-10-12T23:08:00Z"}`; any ts in [23:07:12, 23:09:05] gives the same split.

| Case | Ledger | Verdict, exit | n b/a | cost before -> after | delta, rel | predicted | error |
|---|---|---|---|---|---|---|---|
| V1a | as above | improved, 0 | 3/3 | 119.51 -> 58.50 s (min/max 119.31/119.75, 58.44/58.56) | -61.01 s, -0.510, separated | -71.7 s | 0.149 (14.9%) |
| V1b | ts 23:07:11 (= 3rd before) | not_enough_data, 1 | 2/3, at_close 1 | - | - | -71.7 s | null |
| V1c | ts 23:09:06 (= 1st after) | not_enough_data, 1 | 3/2, at_close 1 | - | - | -71.7 s | null |
| V1d | ts 23:12:00 (as really closed) | not_enough_data, 1 | 6/0 | - | - | null (repeats tie 5/2) | null |
| V1e | `gate:level_regression:D`, 23:08:00 (control) | not_improved, `no material change`, 1 | 3/3 | 22.52 -> 22.31 s | -0.21 s, -0.009, flat, separated | null | null |
| V1f | `gate:level_regression:*`, 23:08:00 | improved, 0 | 3/3, 4 keys summed | 142.46 -> 81.19 s | -61.27 s, -0.430 | null (4 keys) | null |
| V1g | V1d's ledger (ts 23:12:00) with `--at 2026-10-12T23:08:00Z` | improved, 0 | 3/3 | 119.51 -> 58.50 s | -61.01 s, -0.510, separated | -71.7 s | 0.149 (14.9%) |
| V1h | `--at 2026-10-12 23:08` (malformed); `--at` on an `open` entry | 3 `bad_at`; 3 `not_closed` | - | - | - | - | - |

V1a context: x_repeats {5} -> {2}, selected {152}, sha a0679e9. **Reproduce 14.9%? Yes:** a close
between the phases gives I-3's 3 + 3 split, means 119.5142 / 58.5038 and the 71.7 s rebuilt from the 9
before-events: |-61.0104 + 71.7| / 71.7 = 0.1491. Closed as really written (V1d): it cannot, and that is
what `--at` is for: V1g is the real ledger line with the landing time supplied, and must equal V1a
exactly, with `closing_point.source` `--at` and the override warning (the output differs from V1a in
those two fields only).

**V2 synthetic, known truth.** Close C = 2026-10-01T12:00:00Z; before 10:00-11:59, after 12:01-13:59;
gate runs `level`/`regression`, `x_repeats` 5 before, 2 after.

| Case | Content | Verdict, exit | Key numbers |
|---|---|---|---|
| V2a improved | X 100, 101, 99 -> 40, 41, 39 | improved, 0 | 100.00 -> 40.00, delta -60.00, rel -0.600; predicted -60.0; error 0.000 |
| V2b no change | X 100, 101, 99 -> 100, 99, 101 | not_improved, 1 | delta 0.00, flat; error 1.000 |
| V2c worse | X 100, 101, 99 -> 110, 111, 109 | not_improved (`cost rose`), 1 | delta +10.00, rel +0.100, up; error 1.167 |
| V2d recurred | `cause:timeout`: 3 give-ups (wall_s 600) + 2 passed; after 3 passed + 1 timeout | recurred, 1 | f 3 -> 1, exposure 5 -> 4 |
| V2d' | as V2d, after 3 passed / 2 passed, no timeout | improved, 0 / not_enough_data, 1 | exposure_after 3 / 2 |
| V2e none after | X 3 before, 0 after | not_enough_data, 1 | n_after 0; predicted -60.0; error null |
| V2f one after | X 3 before, 1 after (40) | not_enough_data, 1 | n_after 1 |
| V2g interleaved | V2a with D 20 -> 30 on each gate, plus 4 `lint:L012`; ledger closes X | as V2a | `keys` = [X]; D and lint absent |
| V2h units | (ii) `cause:token_budget` usd 0.50 only, after 3 passed; (iii) `human:resume` x3, no cost, after 3 `plan_started`; (iv) `cause:timeout` seconds before, 1 after with usd only | (ii) improved, 0; (iii) improved, 0; (iv) recurred, 1 | (ii) unit usd, 0.50 -> null; (iii) unit null; (iv) unit s, after cost_per null; nothing converted |
| V2i tie | V2a plus X 1000 at exactly C | as V2a | at_close 1, means unchanged |
| V2j undated | V2a plus X 1000 with ts `2026-10-01 13:00` | as V2a | undated 1 |

Rule: 11 of 11 rows exact; on canonical rows `after.frequency` == `report --json` `closed[].recurred`.

**V3 ledger edge cases** (I-3 log, V1a's entry varied). V3a only an `open` entry -> 3 `not_closed`.
V3b closed then reopened -> 3 `not_closed`. V3c `I-99`, `imp-00000000` -> 3 `unknown_id`. V3d `"commit":
null` -> 0, V1a's numbers, warning `no commit recorded`. V3e closed twice (23:08:00, 23:12:00): closing
point 23:08:00, V1a's numbers, warning `closed 2 times in a row`. V3f closed without `ts` -> 3
`no_closing_point`. V3g `"id": null`, `verify imp-3e1cd7f9` -> V1a's numbers. Rule: 7 of 7.

**D1 one draft per kind** (ids = `imp-` + sha1(key)[:8], from the committed rule).

| Case | Log | Proposal | Template, exit | Predicted key lines |
|---|---|---|---|---|
| D1a | I-4 `i4_lint_noise` | imp-f377ca42 `lint:L012` | change_request, 0 | `# Draft: Lint L012: tune it (it fired 6 time(s) at a cost)`; targets `lattice/spec/lint.py`, `lattice/spec/preconditions.py`; `signal lint:L012: 6 x 180.0 s = 1080.0 s [A]`; 6 lines `e-2026-10-01T00:0N:00Z ... 180.0 s`; `As of 2026-10-01T01:00:00Z`; `the owner names the gate`; minimum 3 exposures (change events); `toml` null |
| D1b | I-3 first 9 + fixture | imp-3e1cd7f9 `gate:level_regression:X` | config, 0 | `[rebuild]` / `level_gate_args = ["--x-repeats", "2"]`; `current: not set`; `expected: X 119.5 s -> saving 71.7 s a gate (60%; x_repeats 5 -> 2)`; `signal gate:level_regression:X: 3 x 119.5 s = 358.5 s [A]`; evidence e-e8d9c91c15964512, e-327c71cd641f49a0, e-f6061c7895644d98 at 119.8, 119.3, 119.5 s; gate `./check.sh`; `As of 2026-10-12T23:07:11Z`; ledger and verify commands with imp-3e1cd7f9 |
| D1c | I-3 first 9 | imp-7fad29b4 `unchecked:stub_observability` | module, 0 | `name = "stub_observability"`, `kind = "custom"`, `description = "Service stubs that record what they were sent"`; `signal unchecked:stub_observability: f = 3 (no cost recorded) [C]`; evidence e-ddf0ec95bc824d83; minimum 1 snapshot |
| D1d | I-3 first 9 | imp-a8f0a344 `unchecked:free_text` | prereg, 0 | `# Unchecked rules (free text): pre-registration (stub)`; sections 1-7; `PREDICT:` empty; `f = 5` |
| D1e | I-3 all 18, no ledger | imp-3e1cd7f9 | prereg, 0 | kind `experiment` (repeats tie): no `toml`, no expected; `6 x 89.0 s = 534.0 s [A]` |
| D1f | I-3 first 9 + fixture | imp-256fef6a `gate:level_regression:D` | config, 0 | `level_gate_args = ["--await-cap", "5"]`; `3 x 22.5 s = 67.5 s [A]` |

Rule: every required heading of 2.8 present in order and every key line present, 6 of 6.

**D2 refusals.** imp-56861ff8 (`gate:level_regression:C`; I-4's `i4_checker`, and the I-3 log) and
imp-adeea5ef (`unchecked:request_header`, target `lattice/spec/model.py`) -> 3 `needs_owner`, detail
names `lattice/spec/check.py` (resp. `model.py`) and `never drafted automatically`; imp-3e1cd7f9 on the
18-event log with V1a's entry -> 3 `closed by I-01`; imp-00000000 -> 3 `unknown_id`; `--out
<project>/draft.md` -> 3 `out_in_project`. Rule: 5 of 5; no `--out` file exists after any refusal.

**D3 safety.** sha256 of every file under `--project` and `--state` before and after every V and D
call (refusals included): 0 changes; the parent of `--out` gains exactly one file. Imports of
`verify.py`, `draft.py`: stdlib only (`argparse fnmatch hashlib json re datetime pathlib tomllib
collections dataclasses typing sys`) plus `lattice.improve.*`; 0 import lines matching
`subprocess|socket|urllib|http|requests|httpx|anthropic|lattice.llm|claude`; 0 hits for `71.7`,
`119.5`, `61.01`, `3e1cd7f9` in `lattice/improve/` (0 today).

**D4 determinism.** V1a, V2a, D1b, D2-checker (text, `--json`, `--out` bytes): 0 differing bytes over
10 runs (1.1 s between runs 1 and 2), from two working dirs, and on a byte copy of the state dir.

**D5 CLI.** `verify|draft --help` exit 0, listing every option of 2.1. `report` (text, `--json`) on the
9- and 18-event I-3 states and the I-R state, `ledger` (text, `--json`) on the I-R ledger, `import-plan
p2-final.json --json` twice (fresh state, same path per tree): byte-identical between 7358be2 (`git
archive`) and the branch; `report` = committed `i3-report-before.txt`, `ir-replay-report.txt`; the old
`--help` texts unchanged.

## 4. Mutation plan

| Mutant | Edit | Predicted catcher |
|---|---|---|
| IM7 | an event at the close second counted as after (`>=`) | V1c, V2i |
| IM8 | an event at the close second counted as before (`<=`) | V1b, V2i |
| IM9 | `not_enough_data` reported as `improved` (exit 0) | V1d, V2e, V2f |
| IM10 | minimum 2 instead of 3 | V1b, V1c, V2d' (exposure 2) |
| IM11 | dollars converted into seconds when a window lacks seconds | V2h (ii), (iv) |
| IM12 | error sign-dependent: `abs(abs(m) - abs(p)) / abs(p)` | V2c (0.833 instead of 1.167) |
| IM13 | recurrence ignored (improved when f_after > 0) | V2d |
| IM14 | pattern filter dropped (all keys pooled) | V2g |
| IM15 | prediction from the whole log, not the before window | V1a (null instead of -71.7) |
| IM16 | `score` used as the prediction when `expected` is absent | V1e, V2d |
| IM17 | `needs_owner` drafted | D2 (checker, model.py) |
| IM18 | draft written to `<project>/improve-draft.md`; verify appends a `verified` ledger line | D3 hashes |
| IM19 | closed twice: the last close used | V3e |
| IM20 | wall-clock time in the draft header | D4 (1.1 s gap) |
| IM21 | `--at` ignored (the ledger ts used) | V1g (not_enough_data instead of improved) |

Rule: 15 of 15 caught by a predicted catcher (IM1-IM6 still caught); a survivor is reported as such.

## 5. Named risks

1. **Confounders**: all else that changed between the windows is credited to this change.
2. **Small n**: 3 vs 3 is the floor (full separation: p = 0.05 one-sided, weak). One project.
3. **The prediction is a heuristic**: only the X template predicts (X proportional to repeats; I-3
   showed it overshoots, about 17.8 s fixed + 20.3 s per repeat). f x c is a cost, not a saving.
4. **Goodhart**: hiding the signal (renamed key, gate not run) reads as improved; exposure catches
   "not exercised", not "not logged".
5. **Unseen**: human time, coverage and risk (fewer X repeats saved 61 s and look for fewer races).
6. **Timing**: the close is when the owner ran `ledger` (V1d) unless the owner supplies `--at`, which
   moves attribution to the owner's word (the report says so); a gate run is stamped at its end.
7. **The builder reads the predictions** (D3's literal grep, the runner audited against this
   document). Rebuilt predictions drift with the ranker's code (none is recorded).

## 6. Blind vs post hoc; verification

Every prediction above is blind: a failed one is a finding, never silently fixed; a fix it motivates
is a later, labelled post-hoc commit and the blind numbers stay as first measured.

```
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-improve2
uv run pytest tests/improve -q
uv run python dogfood/improve2_probes.py     # V1-V3, D1-D5 -> docs/results/data/2026-10-13-improve2/
uv run python dogfood/improve_mutants.py     # control, IM1-IM20
dogfood/studio/check.sh                      # unchanged: no Studio, API or dogfood rule change
```

Pass: every rule of section 3; all mutants caught; `check.sh` PASS with 7358be2's case count; `git
diff 7358be2 -- lattice/` only in `lattice/improve/{verify,draft,__main__}.py`, `lattice/cli.py` help.
