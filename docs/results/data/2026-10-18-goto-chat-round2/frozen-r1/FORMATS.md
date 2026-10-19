# Spike F question set: formats for the build node

This file covers three things only: the answer-file formats the scorer reads, how the six
candidate changes are recorded as Studio change records, and the scorer's command line and exit
codes. It contains no question text and no expected answers. The runner reads `qset.json`; the
build node does not need to open it to write the runner against these formats.

## 1. What the runner reads from qset.json

- `questions[]`: each has `id`, `set`, `corpus` (`S` = Streak a0679e9 with the workflows capture,
  `S4` = Streak 1d00ede, static inventory) and `text`. The runner sends `text` verbatim and keys
  every answer by `id`.
- Sets by surface:
  - map surface: sets `M_rules`, `M_workflows`, `M_machines`, `M_access`, `N_not_built`
  - router surface (`POST /api/ask`): sets `I_impact`, `H_hypothetical`, `C_control`
  - agent surface: every question
- `changes`: keys `c1` .. `c6`. Each entry has `candidate` (a directory relative to the
  questions directory, holding `REQUIREMENTS.md`, `spec.py`, `spec.trace.json`), `files_sha256`
  (sha256 of each of the three files) and `input.candidate` (the candidate hashes a D report
  must carry).

## 2. Recording the six candidates as ready change records

1. Start from a fresh scratch copy of Streak a0679e9 (`git archive`, never the Streak checkout).
   Give its `studio.toml` an `[implementation]` table whose `inventory` points to an inventory
   built with `lattice import inventory <copy> --out <dir outside the copy> --workflows
   docs/results/data/2026-10-15-workflow-layer/workflows.json` (sha256 af1383db...), plus an
   `[impact]` table. Leave the copy's own `REQUIREMENTS.md`, `spec.py` and `spec.trace.json`
   unmodified: they are the base.
2. Use a **fresh, empty Studio state directory**, with no change records from earlier, so that
   Studio gives out the ids `c1`, `c2`, ... in order.
3. Record the candidates **in order c1, c2, c3, c4, c5, c6**, one record each. For `cN`, use the
   directory named in `changes.cN.candidate`. Draft each record through the scripted stub
   generator (D's PS method, as in `tests/studio`). The stub returns that directory's three
   files, and the record must reach status `ready`.
4. **Byte-equality:** the record's new `REQUIREMENTS.md`, `spec.py` and `spec.trace.json`
   contents must equal the candidate files byte for byte. Check each against
   `changes.cN.files_sha256` and stop on any difference. A record with other bytes is not that
   change: do not renumber, start again from a fresh state directory.
5. Verify with D's CLI in `change_impact`'s default mode, for each `cN`:

   ```sh
   python -m lattice.spec impact --project <copy> --change cN --state-dir <state dir> \
       --tiers 23 --baseline none --json --out <REPORTS>/cN.json
   ```

   `<REPORTS>` must be outside the project and the app root. Give that directory to the scorer as
   `--reports <REPORTS>`. The scorer checks that each report's `input.candidate` equals
   `changes.cN.input.candidate`.
6. Run the agent exercise on that same copy with the same state directory, so `cN` in a question
   names the same record.

## 3. Answer files

Answers are JSON files in UTF-8, keyed by question `id`. A question with no entry is scored as
not answered (map and router) or not run (agent). It never counts as a pass.

### map (`--surface map`)

```json
{
  "maps":    {"S": <GET /api/appmap body>, "S4": <GET /api/appmap body>},
  "answers": {"<id>": <GET /api/appmap[?section=...] body>}
}
```

Either key may be used, or both. An entry under `answers` wins for its question. Otherwise the
whole map for the question's `corpus` is used. The body is the `lattice.appmap/1` JSON unchanged:
the whole map, or the section response `{schema, state, inputs, <section>}`.

### router (`--surface router`)

```json
{
  "answers": {
    "<id>": {
      "response": <POST /api/ask response body, verbatim>,
      "generator_calls": <int: counting-stub generator calls made while answering, optional>,
      "base": <base server's response to the same question with the same stub, optional>
    }
  }
}
```

`response.usage.tokens`, when present, also counts as a model call if it is non-zero. Give
`base` for every question of set `C_control`, so byte-identity with the base path is checked.

### agent (`--surface agent`)

```json
{
  "answers": {
    "<id>": {"text": "<the session's final answer text>",
             "status": "ok" | "not_run" | "usage_limit" | "error"}
  }
}
```

Only `status: "ok"` is scored. Any other status, or a missing entry, is reported as not run. The
summary then shows `partial: k of 44`, and the gate is never passed.

## 4. Scorer command line

```sh
python goto_score.py --qset qset.json --surface map    --answers maps.json   [--json]
python goto_score.py --qset qset.json --surface router --answers router.json [--reports DIR] [--json]
python goto_score.py --qset qset.json --surface agent  --answers agent.json  [--reports DIR] [--json]
python goto_score.py --qset qset.json --self-test goto_score_selftest.json
```

The scorer is deterministic and uses the standard library only. It starts no process, makes no
network call and calls no model. `--reports DIR` reads `DIR/cN.json` (section 2, step 5). For
each change it reports one of these states:
- `frozen`: no file was given.
- `verified`: the candidate hashes match and the tier 1, 2 and 3 item ids and the `tiers` map
  equal the frozen ones.
- `candidate_mismatch`: the candidate hashes differ. That change's questions are left unscored.
- `report_drift`: the hashes match but the ids differ. This is a scorer error.

`--json` prints `{summary, questions[]}`. `summary` holds:
- `n`, `correct` and `not_run`;
- `unsupported_failure_claims`, the literal count, which is the gate;
- `unsupported_failure_claims_lenient`, which is report-only (Amendment 2: negations and named
  recorded replays exempted);
- `by_set` (per set: `n`, `correct`, `claims`, `claims_lenient`, `not_run`, `unscored`);
- `changes` and `errors`;
- `gate {rule, pass}`;
- on the router surface, `model_calls_on_routed` and `model_calls_unknown`;
- on the agent surface, `i_verbatim_id_compliance` and `gate.partial`.

Each `questions[]` entry holds `id`, `set`, `correct`, `checks` (each named check true or false),
`got`, `claims` and `claims_lenient`.

### Exit codes

| code | meaning |
|---|---|
| 0 | scored (whether a gate passed is in the output, not in the exit code) |
| 1 | self-test: at least one case did not score as specified |
| 2 | bad input (unreadable or invalid qset or answers file, missing `--surface`/`--answers`) |
| 3 | report drift: a live D report with the frozen candidate hashes has different item ids |
