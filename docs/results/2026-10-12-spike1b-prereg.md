# Spike 1b pre-registration: security attack module

Date: 2026-10-12. Branch `np-spike1b` (worktree `lattice-np1b`, from `next-phases` fdb5cc6).
Written BEFORE any probe is run. The criteria below are blind: a failed prediction is a
finding, reported as such, never silently fixed.

Plan read: `docs/design/spikes/01b-security-attack-module.md` (guardrails are MUST),
`docs/results/2026-10-12-s0-streak-auth-attack-list.md` (the 102-attack hand list that is the
deterministic generator's target), `docs/design/next-phases-plan.md`.

## What is being tested

`lattice/security/` (a separate package, NOT part of `lattice check`, never an A-X layer or a
rebuild gate). It starts the app itself on a loopback throwaway database, generates attacks
deterministically from the spec model (profile + contracts + scenarios + machines + trace),
runs them through the harness's own httpx transport (so the DSL's header/carried-value gap
does not apply to this module), and a judge replays every claimed finding on a fresh database
and seed. A finding is "confirmed" only if it reproduces.

Classes: `authz`, `auth`, `bounds`, `mass`, `state`, `leak`.

## Targets

- **In-repo (self-test, fast, fully controllable):** `tests/spec/minishop.py` — users (ann,
  ben, ada admin, eve inactive), ownership (`Order.user_id`, `OrderItem` via order), stated
  bounds (contracts added for this spike), a state machine (`Order.status`), private columns.
  Holed variants added as `SEC_BROKEN`. These back the tests and the mutants.
- **Pre-registered probe target:** a CLONE of Streak outside the protected repos:
  `git clone --no-hardlinks /Users/vivekgade/Documents/streak <scratch>/streak-sec`, then
  `git checkout a0679e9` (clean tip). Streak is Go+Gin+GORM on MySQL, served by its process
  runtime on `127.0.0.1:<free port>` against a throwaway MySQL container
  (`lattice-sec-*`). Env: `UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/streak/.venv`,
  `PYTHONPATH=<clone>:/Users/vivekgade/Documents/lattice-np1b`. The clone, any scratch venv and
  all probe output live under the scratch directory and are removed at the end; all
  `lattice-sec-*` containers are removed.

## Probes and pre-registered pass criteria

### S1 — seeded hole, black-box, deterministic attacker
Seed ONE hole on a throwaway commit of the clone: remove the ownership check of
`GET /v1/habits/{id}` so another user reads it. Run `lattice attack run --classes authz`.
- **H1a:** the hole is confirmed in **3 of 3** runs.
- **H1b:** **zero** confirmed findings on the clean tip in **3 of 3** runs.
- Report wall time per run.
- **Pass: H1a and H1b both hold.**

### S1b — LLM attacker, n=1, labelled
Same seeded hole. ONE real `claude -p` attacker (model sonnet), given only the app's loopback
address and the public spec text (black-box), tools limited to `curl http://127.0.0.1:<port>/*`
and Read; the SAME judge replays its claims.
- **Prediction (60%):** it finds the hole.
- Report cost and wall time. **A miss is a finding, not a failure to fix.** Not run by default;
  run once here only.

### S2 — swarm over 5 seeded holes
Seed 5 holes, each on its own throwaway commit: authz (the S1 hole), bounds (an upper bound
removed), mass-assignment (a body field accepted that must not be), state (a use-after-delete
read), and one of `leak`/`auth`. A race hole is OUT OF SCOPE for the deterministic generator
(stated here). Run ALL classes against each variant.
- **Pass:** recall **>= 4 of 5** seeded holes confirmed; precision (confirmed findings
  corresponding to a seeded hole / all confirmed) **>= 0.8** across the 5 variants; and **zero**
  confirmed findings on the clean tip over **3** runs.
- Report each variant's findings.

### S3 — system scope on the clean app
Whole-system plan and run on the clean tip.
- Report: attacks per class, wall time, not-refuted tallies, deduplication quality (distinct
  findings vs raw claims).
- Any confirmed finding on the clean tip is a finding to investigate (is it a real hole of the
  built app? say what was verified). Report only; no pass/fail threshold.

## Guardrails being asserted (each has a test)

1. The only target is the app the module itself starts on a loopback host with a throwaway DB;
   any other host, or an `--url` argument, is refused with a clear error (exit 2).
2. No shell/network tools for attackers (the LLM attacker's Bash is restricted to
   `curl http://127.0.0.1:<port>/*`; Read only).
3. CLI backend only; no Anthropic API key is ever used.
4. Attack logs and seed credentials are written only inside the run directory (`--out`, default
   a printed temp dir).
5. The CLI banner and `--help` say "for the owner's own systems only".
6. Every report states what a spec-driven attack cannot see: dependency CVEs, TLS, deployment
   config, secrets in the repo.

## What a spec-driven deterministic swarm is expected NOT to find (stated up front)

- Race / concurrency holes (no count-bound invariant): out of scope for the deterministic
  generator (S0 rows C06, F06, G04, I12).
- Collation/padding effects on unique columns, column/index length limits, framework routing
  and middleware order, the signing key, timing side channels, deployment topology (S0
  NEEDS-APP-KNOWLEDGE rows). These need code/schema/traffic knowledge the spec does not carry.
- Anything requiring the signing key to mint a token (D08, D09).

## Deviations from the plan I expect to make (flagged, confirmed in results)

- Streak's spec uses `access_matrix(...)` (scenarios), not `Contract` objects, so for Streak the
  endpoint model and per-caller expected statuses are read from scenarios + profile; minishop
  carries `Contract`s so the contract path (params/bounds) is also exercised. Both paths are
  tested.
- The descriptor-schema change needed to express a `custom` kind with a CLI entry is reported in
  the results (an optional `entry` manifest field).
