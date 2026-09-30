# Go cost pilot (5b stage 1): dry-run fixtures and live-run cost estimate

## Fixtures

This directory holds the canned model responses for the offline dry run. It uses the
`bench.cli --offline` layout: `<arm>/<task_id>.txt`, with rounds separated by a line
`=====`. The same file serves every model and rep. There is one task per family.

| task | family | arm G (hand-written Go) | arm B (reference Lattice) |
|---|---|---|---|
| c01 | control | correct | correct |
| o01 | ownership | **silent**: no ownership filter | correct |
| v01 | validation | build error (missing `"errors"` import), repaired → correct in 2 rounds | correct |
| e02 | errors | correct | checker error L401 (`col("state")`), repaired → correct in 2 rounds |
| f01 | efficiency | correct | correct |

The arm B answers are the reference programs in `tests/fixtures/api/<id>/lattice.py`.
`tests/bench/test_go_pilot_dryrun.py` runs the dry run and asserts these
classifications. It also checks that the o01 Go answer with the ownership filter added
passes every case.

Command:

    uv run python -m bench.cli run --suite api --arms G,B --lattice-backend go \
      --offline bench/offline/go_pilot --models claude-sonnet-5 --reps 1 \
      --tasks c01,o01,v01,e02,f01 --out <dir>
    uv run python -m bench.analysis.go_pilot <dir>

## Live-run cost estimate (480 runs)

The live run is 40 tasks × arms G and B × claude-sonnet-5 and claude-haiku-4-5 × 3 reps.
To reproduce the figures below, run `uv run python -m bench.analysis.go_pilot_estimate`.

**Range: about $7 (low) to $28 (high); central estimate $21.**

| scenario | Sonnet G | Sonnet B | Haiku G | Haiku B | total |
|---|---:|---:|---:|---:|---:|
| low | $2.95 | $1.96 | $1.47 | $0.98 | **$7.37** |
| central | $8.81 | $6.34 | $3.43 | $2.46 | **$21.05** |
| high | $11.78 | $8.08 | $4.59 | $3.15 | **$27.60** |

Per run in the central scenario: Sonnet G $0.074, Sonnet B $0.053, Haiku G $0.029,
Haiku B $0.021.

### Inputs (actual, not assumed)

- **System prompts.** Each arm's actual system prompt, with hints on: G is 21,303 chars
  (`api_go_system_prompt`) and B is 17,319 chars (`api_lattice_system_prompt`). G's is
  the larger of the two.
- **Task messages.** The actual `api_task_message` for all 40 tasks, about 2,150 chars
  per task for either arm.
- **B answers.** The reference Lattice program of each task, 610 chars on average.
- **G answers.** For the 5 dry-run tasks, the final-round hand-written Go. For the other
  35 tasks, the task's FastAPI reference × 2.51, the mean Go/FastAPI character ratio of
  those 5. This is an extrapolation, because the other 35 tasks have no Go reference.
  The mean is 1,834 chars.

### Assumptions

1. **Tokens from characters.** Tokens are estimated as chars / 3.5, following project
   policy: the CLI has no `count_tokens`. The constants and the repair formula come from
   `bench.edit_slice.run`. Under that formula, repair round k's prompt is the first
   prompt plus k earlier answers and k feedback messages of 600 tokens each.
2. **Tokenizer factor (central and high only).** CLI-billed input tokens ÷ (chars / 3.5)
   was measured on the earlier CLI-transport runs (`bench/results/option1*_*.jsonl`,
   1-round runs only). It is **1.42 on Sonnet 5** (n = 103, range 1.40 to 1.45) and
   **1.09 on Haiku 4.5** (n = 44). Low uses chars / 3.5 unadjusted.
3. **CLI input price (central and high only).** The CLI's `total_cost_usd`, which is the
   pilot's `cli_list_estimate`, bills each prompt as a cache write at **2× the list input
   price**. A least-squares fit of cost on token classes over 210 earlier CLI runs
   recovered exactly the list output price and 2× the list input price on
   cache-creation tokens. No cache reads were ever recorded, because each call is a fresh
   `claude -p` process. List prices come from `bench.metrics.PRICES`: Sonnet 5 costs $2
   per million input tokens and $10 per million output tokens; Haiku 4.5 costs $1 and $5.
   Low prices input at list.
4. **Output size.** Output tokens per round = answer chars / 3.5 × a multiplier:
   - low: 1.25 (`OUTPUT_OVERHEAD`);
   - central: 1.90 on Sonnet and 1.51 on Haiku. This is arm B's measured ratio of output
     tokens to reference chars / 3.5 on the earlier explicit API runs;
   - high: 2.59 on Sonnet and 2.04 on Haiku. This is the same ratio for arm P, which
     writes FastAPI directly.

   Each scenario applies the same multiplier to both arms, so the arms differ only in the
   size of their reference answers.
5. **Repair rate.** This is the share of runs that need a 1st and a 2nd repair round:
   - low: (0.15, 0.05), close to arm B's measured rate on Sonnet;
   - central: (0.30, 0.15), the `edit_slice` default;
   - high: (0.50, 0.25).

   Both arms get the same rate. Arm G's real rate is unknown, and Go compile errors
   could push it above B's.
6. **What is left out.** The estimate leaves out:
   - retries of harness-error runs under `--resume`;
   - CLI retries of transient errors;
   - refusals;
   - any model turn beyond the repair budget of 2.

   Budget roughly 10% above the high figure (about $30) as headroom.

### Offline records

The dry run's records carry `cost_usd = 0.0` and `cost_basis = ""`, because the scripted
offline calls report no transport cost. A live CLI call reports `cli_list_estimate`. The
analysis marks the cost ratio "not computable" for zero-cost arms, and it refuses the
verdict, because 5 tasks × 1 rep is not the pre-registered 40 × 3.
