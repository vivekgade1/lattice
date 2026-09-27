# Lattice importer (sub-project 2) Implementation Plan

> **For agentic workers:** execute node by node with TDD. Agents never run tree-modifying git commands; the orchestrator commits after each node's gate.

**Goal:** implement `docs/superpowers/specs/2026-09-27-lattice-importer-design.md`: FastAPI + SQLModel repo → verified Lattice import, with the close-the-gap loop reaching 100% where possible.

**Architecture:** new package `lattice/importer/`, with modules as in spec §6. Verification is generalised from `bench/template_suite.py` + `tests/template/test_template_differential.py`. LLM calls reuse `lattice/llm/` (client, repair loop, API guide).

**Tech stack:** Python 3.12, `ast`, FastAPI TestClient, sqlite3, hypothesis, the Anthropic SDK (via `lattice/llm/client.py`), pytest, ruff.

## Global constraints
- The soundness gate: nothing is emitted as Lattice unless it passed verification.
- `--no-llm` runs are deterministic and need no API key. CI tests never call the live API; the LLM is tested with recorded or fake responses.
- Held-out repos are never used while developing lifters (only in I6).
- `uv run pytest -q` green and `uv run ruff check .` clean at every gate.

## Review focus
1. Owner inference picks a wrong owner column, e.g. `created_by` vs `owner_id`: the import must fail verification or report "owner unclear", never silently lift.
2. A route with auth via a custom dependency the extractor doesn't know: it must become unclear or an island, never a Public endpoint.
3. Suite generation misses a status branch: a lifted route could look equal. Mitigation: coverage of the route's `raise HTTPException` sites; each must be hit by at least one case, or the route is reported "unverified branch".
4. A carve-out native with side effects wrongly declared pure.
5. Budget exhaustion mid-loop must leave a consistent `lattice_import/` + report.

## Nodes

| Node | Level | Deliverable | Gate |
|---|---|---|---|
| I1 Extract + profile | 0 | `extract.py`, `profile_gen.py`: template repo → `RepoModel` → profile source | the generated profile checks the hand-written template programs identically to `bench/template_profile.py` (same diagnostics: none); unit tests per extractor rule |
| I2 Suite + verify | 0 | `suite.py` (request suite + seeded DB from `RepoModel`-like inputs), `verify.py` (differential of original app vs Lattice programs, with representation rules from the profile, placeholders keeping spelling, and branch coverage of `raise` sites) | re-verifying the hand-written template programs through `verify.py` reproduces 49/53 + policy deviations; the generated suite hits every `raise` site of the template routes |
| I6a Held-out repos | 0 | 2 public MIT/Apache FastAPI + SQLModel repos, pinned by commit, vendored under `bench/import_repos/<name>/` runnable on SQLite in-process (like `bench/template_app`), each with a README (source, commit, licence, routes) | both apps start in-process and answer a smoke request; licence files kept |
| I3 Lifters | 1 | `lifters/*` for the template idioms; `lift.py` orchestration | the template imported with `--no-llm`: all 9 routes lifted and verified (49/53 + the same policy deviations) |
| I4 Report + CLI | 2 | `report.py`, `cli.py`, `lattice_import/` writer | end to end with `--no-llm` on the template via the CLI; report.json schema test |
| I5 LLM + close-the-gap | 3 | `llm_lift.py`, loop R1–R4 with budgets, carve-out natives, corpus log | fake-LLM tests for every rung; soundness gate test (injected lifter bug → R4 or verified repair, never unverified Lattice); budget-exhaustion consistency |
| I5b Loop corpus | 4 | lift the l07/l10 originals, l12 → R3 carve-out or R4 with reason | corpus test |
| I6b Held-out run | 5 | live import of both repos with the loop; results in `docs/results/` | report: % R0–R2 / R3 / R4, cost, gaps; every emitted route verified |
| I7 Review | 6 | independent review + fixes | no open critical or important findings |
