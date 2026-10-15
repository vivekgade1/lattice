# Multi-project layer 4 (transitive contract impact): pre-registration

## 1. Status

Date: 2026-10-15. Branch `np-transitive` (worktree `lattice-transitive`, from `next-phases` a4ab4a1).
**Written BEFORE any code of this change exists**; nothing below was run or measured. Predictions come
from committed data only (`docs/results/data/2026-10-13-contracts-impact/{I1,I4}.json`: 4a's rows,
`extra.per_drift`, `extra.refs`) and the code at a4ab4a1. Results:
`docs/results/2026-10-15-transitive-impact-results.md`. No LLM, no API key, no claude session.

**The question first: what can "transitive" mean when a contract is derived from a spec?** A
provider's export is `export_spec(load_spec(B))`: it reads B's own scenarios and nothing else (no
lock, no import, no other project). So when A changes and B's spec does not, B's export, its
`spec_hash` and every check of C against B are unchanged **by construction**: at the contract level
A's candidate cannot reach C. Real transitivity exists only through something Lattice does not
model: B's code or spec changing because A did (B adapts to a broken reliance, or B's behaviour
relays A's data). No Lattice artefact declares "B's endpoint X relays A's field Y". Recomputing C
against B under A's candidate would be a tautology (identical to `contracts check C`) at the price of
one export child per hop. **Conclusion: the honest answer is a declared-reachability report, gated
at its root by a computed contradiction**, never a computed label beyond depth 1.

**Semantics.** With `--transitive`, each first-order `contradiction` consumer is a **root** (it must
change, re-pin or get a revert); each project that relies on a root through opted-in links, at any
depth, is **`downstream`**: "unverified; affected only if a root changes what it provides; re-check it
after the root moves". Never `contradiction`/`regression`, never in `counts` or the exit code.

**Beyond layer 2's `workspace impact`** (reachability over all links, blind to the candidate): (1)
gated: no contradiction, **0** rows (D6-D10), where `workspace impact` lists every dependent; (2)
contract links only; (3) each row names its roots and their BROKEN endpoints, its depth, the exposed
projects it relies on, what it imports from each (`relies_on`) and its pin of them. Predicted on
T-chain: `workspace impact` 3 projects for every D; this tier 1 for D1-D5, 0 for D6-D10.

**Scope.** CLI `lattice contracts impact PROVIDER --candidate REF|PATH --transitive [--json]` and the
library keyword `run_impact(..., transitive=False)`. Not changed: `notify`/4b (no question for a
downstream row), MCP `contract_impact`, `GET /api/contracts/impact`, API.md, Studio, `workspace impact`.

## 2. Decisions (fixed before coding)

1. **Code.** New `lattice/contracts/transitive.py`: `downstream(ws, rows, provider) -> dict`, pure over
   the manifest's links and the first-order rows, plus read-only `parse_imports`/`read_lock` of
   downstream consumers. `run_impact(..., transitive: bool = False)` (keyword-only);
   `Impact.transitive: dict | None`, emitted by `to_dict` **only when not None**; `--transitive` on
   `impact` only. `check.py`, `export.py`, `workspace.py`, `linked.py`: untouched. Tests:
   `tests/contracts/test_transitive.py`.
2. **Roots.** First-order rows with `label == "contradiction"`, computed exactly as 4a (candidate vs pin).
   `regression` does **not** propagate (as 4a's exit code and 4b's questions: 3 of 5 committed
   regressions are noise; risk 3 names the cost: D8 and Streak D10 real breaks do not propagate).
   `unaffected` never. **Error rows** (`no_pin`, `bad_pin`, ...) do not propagate; one with opted-in
   dependents (provider excluded) gets a note `root_error` naming it and the count (unknown, not clean).
3. **Graph.** Edges = manifest links with a `contract` key (`static` or `live`, either `kind`), both ends
   declared projects, `from != to`; edge `B -> C` when `C` links `to = B`. Links without `contract`, with
   a `bad_contract` value (kept with `contract` None by the parser) or with an unknown end: not followed.
   A manifest error: no first-order rows (as 4a), so `roots` and `downstream` are empty.
4. **Traversal, depth, cycles.** Multi-source BFS from the roots (depth 1) over dependents, visited set
   seeded with roots + PROVIDER; each newly reached project is one row at its BFS distance + 1. So a
   root or the provider is never a row, a project appears once, and the visited set ends every `uses`
   cycle (`depends_on` cycles are a manifest error). **No depth cap:** rows <= projects - 2, cost linear
   in links; a cap would hide exposure silently, `depth` lets a caller filter. PROVIDER linking (with
   `contract`) to an exposed project gives a note `provider_downstream` naming it, no row.
5. **Diamonds and several providers.** One row per project. `via` = sorted names of **every** exposed
   project (root or downstream) the row's project has an opted-in link to, not only BFS parents. `roots`
   = sorted roots from which the project is reachable inside the exposed set (per-root BFS, provider
   excluded). A project that is also a first-order consumer of PROVIDER keeps its first-order row
   (computed, any label but contradiction) **and** gets a downstream row: different questions. Links of
   the row's project to non-exposed providers are not listed.
6. **Row** (all keys always present): `consumer`, `label: "downstream"`, `depth`, `via`, `roots`,
   `links` (one per `via` entry, by provider): `{provider, contract, relies_on, pinned, notes}`.
   `relies_on` = sorted endpoints it imports from that provider (`[]` none declared; null import file
   missing or invalid); `pinned` = `version_of` its lock entry, or null; `notes` in {`no_imports`,
   `bad_imports`, `no_pin`, `bad_pin`} (layer 3's readers), never an error or an exit change. `live`
   links listed, nothing run; the provider's `exclude` is not applied (no downstream export).
7. **JSON.** `format` stays 1 (additive). No flag: **no new key**, bytes of a4ab4a1. Flag: one key
   `transitive` before `problems`, every sub-key always present: `{basis: "declared_links", verified:
   false, propagates_from: ["contradiction"], roots: [{consumer, endpoints (sorted BROKEN), dependents
   (opted-in, provider excluded)}], downstream: [rows by (depth, consumer)], notes: [{code, message}]
   sorted, counts: {roots, downstream}}`. Its notes never enter `problems`: flag-on JSON minus
   `transitive` == flag-off JSON (`elapsed_s` dropped).
8. **Text.** Flag-off text, then `downstream (declared links, unverified; from contradiction only): R
   root(s), N project(s)`; per row `CONSUMER  DOWNSTREAM  depth D via A[, B] (roots: X[, Y])`, per link
   `  PROVIDER CONTRACT: relies on E1, E2|nothing|unreadable; pinned <short>|no pin [notes]`; notes.
9. **Exit codes unchanged:** 0 / 1 (a first-order contradiction) / 2 (cannot run, a row in error). The
   downstream tier never changes them.
10. **MCP/API: not extended in this slice.** `contract_impact` and the API route keep a4ab4a1's bytes
    (their files belong to parallel nodes; an API change needs an API.md version). Parity = CLI
    `--transitive --json` == the library call, and flag-off CLI == MCP == API (4a). Risk 5; follow-up.
11. **Advisory, read-only, deterministic.** The tier adds **0 subprocesses** (no downstream export;
    still <= 2 export children per call), writes nothing; everything sorted: same inputs, same bytes.

## 3. Probes

**Global.** Runner `dogfood/contracts/transitive_probes.py` (4a's helpers). Each deterministic probe
runs **10 times**: canonical JSON (`elapsed_s` dropped, scratch -> `$WORK`) and text identical 10/10.
`scheduler` = clone of job-scheduler at ef17cf8, `spec = "spec.tickets:t5"`; refs `d1..d10` as 4a. **`ci-bot`** = scratch copy of
`dogfood/contracts/ci-bot` + `golden/lattice.contracts.lock.json` + a generated minimal `spec.py`
(one scenario: anonymous `GET /builds/1` -> 200 with body `{"id", "state"}`) so it can be pinned as a
provider (if `lattice.spec` refuses the minimal Spec, the smallest valid one: post hoc). **`ci-dash`**:
`lattice.imports.toml` with one import of `ci-bot` `GET /builds/{}` (status [200], public,
`response_fields = ["state"]`), pinned with `lattice contracts pin ci-dash` before the window. **`ops`**:
an empty directory, link `ops -> ci-bot` kind `uses`, **no** `contract`. Links: `ci-bot -> scheduler`
static, `ci-dash -> ci-bot` static, `ops -> ci-bot`. "Row = committed" = the first-order row equals
4a's `I1.json` `runs[0].Dn.doc.consumers[0]` (findings, gaps, label, counts).

**T-basis (the premise, existing code).** `contracts export ci-bot` with the scheduler clone checked
out at HEAD and at each of d1..d5 (outside any impact call): `spec_hash` identical 6 of 6. Shows B's
export does not depend on A. Pass 6/6.

**T-chain** (`impact scheduler --candidate dN --transitive`, N = 1..10). Pass: 10/10, each row:
first-order = committed, exit as 4a; `transitive` as below; `ops` never in `transitive`. Contrast:
`workspace impact scheduler --json` `impacted` = `[ci-bot, ci-dash, ops]` (3) in the same window.

| D | ci-bot (tier 1) | exit | roots (endpoints) | downstream |
|---|---|---|---|---|
| D1 | contradiction | 1 | ci-bot (GET /applications/{}/jobs/{}/runs) | ci-dash |
| D2 | contradiction | 1 | ci-bot (POST /applications/{}/jobs/{}/runs) | ci-dash |
| D3 | contradiction | 1 | ci-bot (POST /applications/{}/runs/{}/cancel) | ci-dash |
| D4 | contradiction | 1 | ci-bot (POST /auth/login) | ci-dash |
| D5 | contradiction | 1 | ci-bot (GET /applications/{}/runs/{}) | ci-dash |
| D6, D7, D10 | unaffected | 0 | none | none |
| D8, D9 | regression | 0 | none | none |

ci-dash rows: depth 2, via and roots [ci-bot], one link `{ci-bot, static, relies_on [GET /builds/{}],
pinned = the setup pin, notes []}`; root `dependents` 1; notes []. Totals: 5 roots, 5 rows.

**T-diamond.** `ci-bot-2` = 4a I4's (ci-bot's `POST /auth/login` + `GET /applications` tables, pinned
on the clean clone, plus the same minimal spec); `dash` imports `GET /builds/{}` from both, pinned on
both. Pass 5/5.

| row | setup | prediction |
|---|---|---|
| a | D1 | tier 1 = 4a I4-a (ci-bot contradiction, ci-bot-2 unaffected); roots [ci-bot]; one row `dash` depth 2, via [ci-bot], roots [ci-bot], 1 link; exit 1 |
| b | D4 | both contradiction (4a I4-c); roots [ci-bot, ci-bot-2]; **one** row `dash`, via and roots [ci-bot, ci-bot-2], 2 links; exit 1 |
| c | D8 | both regression; roots [], downstream []; exit 0 |
| d | a + `dash` also imports scheduler `GET /applications` (eve), pinned on the clean clone | tier 1 adds `dash` unaffected with exactly 1 unrelied CHANGED `endpoint_removed` and 0 gaps; `dash` also a downstream row as in a; `checked` 3; exit 1 |
| e | a + T-chain's `ci-dash` and an empty `dash2`; `dash2 -> ci-bot`, `dash2 -> ci-dash` (static) | rows (depth 2, sorted): ci-dash, dash, dash2; `dash2.via` [ci-bot, ci-dash], `roots` [ci-bot], notes [no_imports, no_pin] |

**T-cycle.** On the T-chain workspace, D1. Pass 4/4, each terminating (runner timeout 60 s).
(a) `ci-bot -> ci-dash` static added (a `uses` cycle through the root): downstream [ci-dash] only,
ci-bot not re-listed. (b) `scheduler -> ci-bot` static added: downstream [ci-dash]; one note
`provider_downstream` naming ci-bot; scheduler never a row; exit 1. (c) project `x`, `x -> ci-dash` and
`ci-dash -> x` static: rows ci-dash (depth 2), x (depth 3), each once; `ci-dash.via` [ci-bot, x]
(x is exposed: rule 5), `x.via` [ci-dash], both `roots` [ci-bot]. (d) two `depends_on` links forming a cycle:
manifest error `depends_on_cycle`, exit 2, `consumers` [], roots [], downstream [] (flag-off: exit 2 too).

**T-long.** `scheduler <- ci-bot <- p2 <- p3 <- p4 <- p5 <- p6` (static; p2..p6 empty directories).
D1: 5 rows, depths 2..6 in that order, each `via` its predecessor, `roots` [ci-bot], link `relies_on`
null, `pinned` null, notes [no_imports, no_pin]; exit 1. D6: 0 rows. Pass 2/2.

**T-edge** (T-chain workspace unless stated). Pass 7/7.
(a) ci-dash lock deleted, D1: link `pinned` null, notes [no_pin], `relies_on` still [GET /builds/{}];
exit 1. (b) ci-dash's import file not TOML, D1: `relies_on` null, notes [bad_imports]; exit 1.
(c) ci-bot's lock deleted, D1: tier-1 row error `no_pin`, exit 2, roots [], downstream [], one note
`root_error` naming ci-bot and 1 dependent. (d) `ci-dash -> ci-bot` contract `live`, D1: link
`contract` live, the same row, 2 export children (nothing live run). (e) provider `nobody`: exit 2
`unknown_project`, `transitive` present and empty. (f) provider `ci-dash` (no opted-in dependent):
exit 0, `no_consumers`, `transitive` empty. (g) stale pin (4a I0c analog: scheduler clone HEAD at d2,
`--candidate HEAD`): ci-bot contradiction, downstream [ci-dash] (risk 4, observed by design).

**T-additive.** Every call above: flag-on JSON minus `transitive` == flag-off JSON; flag-on text = the
flag-off bytes + a block starting `downstream (`; exit codes equal. Pass: every pair.

**T-flagoff (byte identity with a4ab4a1).** Base code = `git archive a4ab4a1 lattice` extracted into
`$SCRATCH/base` (never the main checkout). On T-chain D1..D10, T-diamond a..e, T-edge c, e, f and the
regenerated 4a I4 rows a..i: `contracts impact` text and `--json` (worktree, no flag) == base
(`elapsed_s` dropped), and `contracts notify --dry-run --json` on T-chain D1 == base. Plus
`impact_probes.py --runs 10` 12/12 and `linkedq_probes.py` as committed, `pytest tests/contracts
tests/workspace tests/mcp tests/studio` 0 failures. Pass: all.

**T-parity.** CLI `--transitive --json` == `run_impact(ws, ..., transitive=True).to_dict()` in-process
(T-chain D1, T-diamond b, T-cycle c); MCP `contract_impact` (stdio) and `GET /api/contracts/impact`
(real server) on T-chain D1 == CLI without the flag, and contain no `transitive` key. Pass 4/4.

**T-safety** (every call, as 4a's I6). sha256 of every file of the workspace, consumers (locks,
imports), provider clone (`.git` included) and worktree unchanged, `find -newer` 0; `TMPDIR` empty;
`sitecustomize` log: 0 connects, only `git` {rev-parse, ls-tree, cat-file, status} and export
children, **export children equal with and without the flag** (2; 0 for T-cycle d, T-edge e, f); no
`linked_questions/` or state dir created; real job-scheduler and Streak `git status` unchanged.

**T-cost (reported).** Median wall, 10 runs, T-chain D1: flag-on minus flag-off <= 0.2 s
(predicted < 0.05 s: no subprocess, 2 small file reads per link).

## 4. Mutants (`transitive_probes.py --mutants`: one-line edits of a scratch copy; worktree untouched)

Each must be killed by `tests/contracts/test_transitive.py`; predicted killer named. Prediction: 14/14.

| id | mutant | killed by |
|---|---|---|
| TR1 | `regression` rows are roots too | T-chain D8, D9 (1 row, predicted 0) |
| TR2 | every non-error row is a root | T-chain D6 |
| TR3 | downstream rows counted in `counts.contradiction` | T-additive, T-chain counts |
| TR4 | downstream label `contradiction` | T-chain row label |
| TR5 | a downstream link note (`no_pin`) raises the exit code to 2 | T-edge a (exit 1), T-additive |
| TR6 | no visited set (revisits) | T-cycle a, c (duplicates or timeout) |
| TR7 | provider not excluded | T-cycle b (scheduler row) |
| TR8 | roots not seeded as visited | T-cycle a (ci-bot listed) |
| TR9 | links without `contract` followed | T-chain (`ops` listed) |
| TR10 | `via` = BFS parents only | T-diamond e, T-cycle c (`ci-dash.via`) |
| TR11 | depth from the first root only / off by one | T-long depths 2..6 |
| TR12 | `transitive` key emitted with the flag off | T-flagoff |
| TR13 | downstream providers exported (`compute_export`) | T-safety export-child count |
| TR14 | error rows propagate | T-edge c (ci-dash listed, no `root_error`) |

## 5. Risks, and what this cannot show

1. **No real chain exists.** No committed consumer is itself a provider; ci-bot's spec and ci-dash
   are synthetic. The probes show traversal, gating and safety, never a real break predicted downstream.
2. **Exposure, not breakage.** Whether B's adaptation touches what C uses is unknown (no relay
   declaration; field-name heuristics rejected as unsound). Fatigue: every dependent, any depth.
3. **Under-reporting.** Regression roots (2 of 5 committed regressions are real breaks), error roots
   (a note only), links without `contract`, outside consumers: not propagated.
4. **Stale pins.** A stale-pin contradiction (I0c) yields downstream rows even for HEAD;
   the root's `current_label` is the tell.
5. **Parity gap.** MCP, API, Studio and `notify` do not see the tier (ownership, API versioning).
6. **Builder bias, circularity.** Tier-1 predictions equal 4a's committed rows (same code); tier-2
   predictions are graph facts read off the fixture manifests. Builders will read this document.
7. **Vocabulary.** `downstream` is a new word beside Studio's three; it is not an impact label.

## 6. Blind vs post hoc, and verification

Fixed now. A failed prediction is a **finding** (output shown; prediction error or defect), never
fixed silently; blind numbers stay as first measured, any fix is a separate post-hoc commit. Runner
choices (the minimal spec's exact form, fixture names), timings and test edits go in "Post hoc".

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-transitive PYTHONDONTWRITEBYTECODE=1
SCRATCH=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad
cd /Users/vivekgade/Documents/lattice-transitive
mkdir -p "$SCRATCH/base" && git archive a4ab4a1 lattice | tar -x -C "$SCRATCH/base"   # T-flagoff old code
uv run python dogfood/contracts/transitive_probes.py --runs 10 --scratch "$SCRATCH/t" \
       --base "$SCRATCH/base" --out "$SCRATCH/t-data"
uv run python dogfood/contracts/transitive_probes.py --mutants --scratch "$SCRATCH/m"  # TR1-TR14 killed
uv run python dogfood/contracts/impact_probes.py --runs 10 --scratch "$SCRATCH/i" --out "$SCRATCH/i-data"
uv run pytest tests/contracts tests/workspace tests/mcp tests/studio -q   # 0 failures
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
git -C /Users/vivekgade/Documents/job-scheduler status --short; git -C /Users/vivekgade/Documents/streak status --short
```

**Pass:** T-basis 6/6, T-chain 10/10, T-diamond 5/5, T-cycle 4/4, T-long 2/2, T-edge 7/7, T-additive
all pairs, T-flagoff all, T-parity 4/4, T-safety all, T-cost within threshold, 14/14 mutants, every
deterministic probe identical 10/10. Recorded run: `docs/results/data/2026-10-15-transitive-impact/`.
