# Implementation diff, round 2 (`workflow_find` ranking, agent exercise 2): pre-registration

## 1. Status, and the exact gap

2026-10-16, branch `np-c2` from `next-phases` bb3ff0c. **Written before any product code; no model was run.** Recon: Streak
a0679e9 (**S**), 1d00ede (**S4**), planted **S+** (round 1's PO plant) archived to scratch; inventories as round 1 (sha256
S-dyn 012a4790..., S4-stat 8f4f66d7..., S-plus 7fd227b9..., identical to its `setup.json`); a throwaway scratch prototype.
**(m)** measured, **(r)** recon-measured by that prototype (the build's own figure is the gated one), **(p)** prediction.
Results: `2026-10-16-implementation-diff-round2-results.md`, data `docs/results/data/2026-10-16-implementation-diff-round2/`.

**This is a development-set round.** Round 1 already saw both apps, the same inventories and the same frozen reports
(S-dyn report sha256 84624894..., S4-stat 80511cbb...; recomputed here, identical). Nothing here is a blind or held-out
claim; the only query set no designer has seen is PT3 (section 3), written by the orchestrator. Round 1's data is
preserved and is not re-scored: its results stand (PT 2/8, agent 8/13).

**Round 1's misses (m, `docs/results/data/2026-10-15-implementation-diff/`).** PT 2/8: a route's document held every
linking rule's text (~25), so the IDF overlap tied at its maximum and the 10% rule said `several` (BM25 post hoc: 2/8 too).
Agent 8/13: Q7/Q8 ("is there ...?") answered VERIFIED against key AMBIGUOUS; Q2/Q12 named group-relative paths
(`DELETE /devices/{}`) while citing the registration line (frozen B4 rejects them); Q9 cited `REQUIREMENTS.md:219`, the
source line of key rule R114, but no `R<n>` (frozen B5 rejects it). The agents' 13 own `workflow_find` queries
(transcripts) are development evidence below.

**What the recon shows, and the premise it contradicts (r).** Round 1's shipped ranker on the new query set G: exact
2/24, top target right 3/16. The design below (v2b): **exact 13/24 (54%) against the 75% gate**; the top candidate is the
right route in 13 of 16 positive rows. Ranking from names, paths, fields and codes fixes the ordering; the hint (`several`
on near-ties, `no_match` on wordy or synonym queries) is what fails. The premise that a lexical ranking redesign reaches
75% exact on natural queries is **not supported**: building this as written records a predicted gate miss (R1).

**Rulings requested.** **R1:** build v2b and record PT2 against 75% as pre-registered (expected miss), or stop PT2 and
re-scope. **R2:** PQ2 runs whatever R1 decides. **R3:** the orchestrator writes PT3 (>= 12 queries) before the build,
without running `workflow_find`. **R4:** the burned set F (D1) stays development data and a mutant catcher, never gated.

## 2. Decisions (fixed before coding)

**D1 Order of work (the development history, disclosed).** (1) v2a was designed from round-1 evidence only (the 8 PT
queries, raw PT rows, BM25 notes, the 13 agent queries); prototype: PT 8/8, agent queries 13/13 (r). (2) A first query
set F (27 rows, sha256 `2b3f1f5b686210cad25611000f4de48b9314a24005837bc49c9b726333397f8b`) was written, hashed, then run:
v2a 18/27 (r). (3) F was then **burned** (demoted to development data) and v2b made with two generic changes (D4
compounds, D5 resource penalty), checked on PT, the agent queries and F only: 8/8, 13/13, 20/27 (r; F is tuned-on).
(4) G (24 rows, quotas below) was written after v2b was frozen (prototype sha256 `1236d829...`), hashed, then run once:
13/24 (r). Nothing changed after that run.

**D2 Candidates.** Endpoints of `endpoints[]` with status `implemented`, `implemented_not_required` or `spec_only` (never
`test_only`, `plumbing`) and every machine; flows and rules only ride along on their route. N = number of candidates.

**D3 Fields of a candidate, with weight w** (terms: `ask.terms` of identifiers split at camelCase, `.`, `_`, `-`, `/`).
**path** 1.0: static template segments (`v1`, `api`, `{}` dropped). **handler** 1.0: handler package and function name
(implemented only; `handler`, `func` dropped). **field** 0.6: extract `params` names (body, query); for a spec-only
endpoint, the `json` and `params` keys of spec steps on it. **code** 0.6: the route's refusal codes. **flow** 0.4: inferred
names of flows through it, verb tokens removed. **table** 0.4: the route's extract tables. Machines: **name** 0.4
(`table.column`), **states** 0.8. **method** 0.5: matched only by a verb class token (D4) containing the method.

**D4 Query terms.** Q = `ask.terms(question)` minus a fixed **interface and filler list** (screen page endpoint route api
feature function functionality option button app application user way possible ability there request call support let
since whether several one each every also about all many much more most other own same just only very again once still
ever via using use like without), with **verb classes**: add create new register submit post -> POST; delete remove
unregister erase -> DELETE; rename -> PATCH; update edit modify -> PATCH/PUT; replace -> PUT; list view show see read fetch
browse -> GET. A verb (in the query or in a path/handler word) becomes its class token; a class token counts once, at 0.5,
when the method or a path/handler word carries it. **Compounds:** two adjacent alphabetic words whose joined stem is a
path or handler term of some candidate replace their parts ("log in" -> `login`).

**D5 Score and order.** S(c) = sum over t in Q of idf_P(t) x the highest w of a field of c containing t; idf_P(t) =
ln(1 + N/df) over the primary documents (all fields but method); a term in none weighs ln(1 + N). **Resource penalty:**
S x 0.8 when no query term is a term of the candidate's last static path segment (an empty segment such as `me` counts as
unmatched; machines are exempt). **Order:** exact templated `METHOD /path` mentions first (round 1, unchanged), then S
descending (6 decimals), then the **rule-text tie-break** T(c) = sum of idf_R over Q terms in the texts of the rules that
link c, divided by 1 + |terms|/200 (idf_R over those rule-text documents), then target. T orders equal S only; it never
changes S or the hint.

**D6 Hints (precise).** Let top and second be the first two candidates.
- **`exists` / `spec_only` (exact):** an exact mention ranks first: `spec_only` if that endpoint is spec-only, else `exists`.
- **`no_match`:** Q is empty; or S(top) = 0; or top matched no **anchor** (path, handler, a verb class through the method,
  or a machine's name/states); or coverage(top) < 0.40. coverage = sum idf_C(t) m(t) / sum idf_C(t) over Q, with m = 1 for
  a match in any field, 0.5 for a match only in top's linked rule texts, 0 otherwise; idf_C over fields plus rule texts;
  a term in neither weighs ln(1 + N). Rule texts thus enter only T and this bounded credit.
- **`several`:** not `no_match`, and S(second) >= 0.90 S(top).
- **`spec_only`:** otherwise, when top is a `spec_only` endpoint: in the spec (cell or scenario), absent from the extract.
- **`exists`:** otherwise (top is an implemented endpoint or a machine).

**D7 Output, compatibility.** Only `_find_docs`, `find_candidates` and their constants change (private helpers allowed);
`lattice/mcp/tools.py` is not edited, so `workflow_find`'s name, description, schema and errors are byte-identical. Kept
keys: `question`, `hint`, `candidates[{target, kind (route|machine), status, score, matched_terms, exact, evidence}]`,
`note` ("lexical match, not understanding", naming the fields); added: `fields {term: field}`, `flows[{id, name, status}]`
(<= 5), `rules` (spec-only), top-level `coverage`. Deterministic; reads only the report and spec; writes nothing.

**D8 Agent exercise 2 (new files; round 1's frozen runner, scorer and cases are never edited).**
- **World and tools:** as round 1 (S-dyn, S4-stat clones; MCP `--readonly`; Read, Grep, Glob, `model_get`, `rule_get`,
  `code_links`, `graph_get`, `spec_lint`, the 3 implementation tools; Bash, Edit, Write, MultiEdit, NotebookEdit and
  `spec_ask` disallowed; `[state] enabled = false`). `claude` CLI only, sonnet, no API key, `ANTHROPIC*` stripped.
- **Prompt (fixed; the label definitions are the same for every question and name no route):** "In this project, {Q}
  Answer for the app as it is implemented, not only the spec. Start with exactly one of these labels on the first line:
  IMPLEMENTED-VERIFIED (the code implements it and a replayed flow verified it), IMPLEMENTED-UNVERIFIED (the code
  implements it; no replayed flow verified it), REQUIRED-NOT-IMPLEMENTED (the spec requires it; the code does not
  implement it), NOT-SPECIFIED-NOT-IMPLEMENTED (neither the spec nor the code has it), AMBIGUOUS (the question's subject
  matches two or more different routes and the question does not say which; name each of them). Cite code as
  code:<path>:<line> and spec rules by id (R<n>). Do not change anything."
- **Key rules (decided before any key):** K1 one implemented referent: VERIFIED iff its route is `implemented_verified`
  (a machine: `confirmed`) in the frozen report, else UNVERIFIED. K2 the referent is a `spec_only` endpoint:
  REQUIRED-NOT-IMPLEMENTED; key rules = rules whose `missing` has it. K3 no endpoint, machine or rule covers it:
  NOT-SPECIFIED-NOT-IMPLEMENTED. **K4 a question ("is there ...?" included) whose subject matches >= 2 distinct endpoints
  and does not single one out: AMBIGUOUS, even when all are implemented**; key elements = those endpoints.
- **Scorer items** (`implementation_agent2_scorer.py`, pure): C1 = round-1 B1; C2 = B2; C3 = B3; **C4** every named
  `METHOD /path` is an extract or spec key, or is group-relative and derivable: exactly one key with that method ends with
  the named path at a segment boundary among the keys whose registration `code:<file>:<line>` (extract `file`, `line`) the
  answer cites; **C5** every `R<n>` exists, and a REQUIRED-NOT-IMPLEMENTED key needs a key rule named as `R<n>` or cited as
  `<trace source>:<line>` equal to that rule's trace `line`; C6 = B6; C7 = B7; **C8** an AMBIGUOUS key: >= 2 key elements
  named, each with its method in one sentence, full or C4-derivable.
- **Cases** (>= 40, `implementation_agent2_check.py`): synthetic rows per item, and round 1's 13 answers as scorer fixtures
  (round-1 Q2 now passes C4; Q7 still fails C2). Fixtures test the scorer; they are not a re-score of round 1.

**D9 Never.** Write anything in the read path; call a model outside PQ2; edit round-1 frozen files or data; run against
the owner's Streak checkout or any `job-scheduler*` directory; retry a session after a usage limit.

## 3. Probes (runner `dogfood/implementation_probes2.py`)

**PT2 (gated): the query set G.** Written after v2b froze, under quotas fixed before writing (mirroring F): 24 rows; S-dyn
15, S4-stat 7, S-plus 2; exists 12, spec_only 4, several 3, no_match 5; >= 3 rows where a parent/child route shares the
noun, >= 2 multi-word verbs, >= 2 field-only signals, >= 3 rows of >= 7 words; disjoint from every PT, F and round-1 agent
text. A row is right iff the hint equals the label and: exists/spec_only, `candidates[0].target` is the target; several,
the first two targets are in the key set. **Gate >= 18/24 (75%).** sha256 of `json.dumps(G, sort_keys=True, separators=
(",", ":"), ensure_ascii=False)`, id order, rows `{id, world, query, label, target | key_set (sorted)}`, targets
`impl:route:<key>`: **`64490f9aa62b05abd271b357df41b352f44376367e53c1b403e9476417641550`**.

| id | world | query | label: target / key set | v2b (r) |
|---|---|---|---|---|
| G01 | S-dyn | see the details of one habit | exists `GET /v1/habits/{}` | several |
| G02 | S-dyn | list a habit's check-ins | exists `GET /v1/habits/{}/completions` | several |
| G03 | S-dyn | edit my timezone | exists `PUT /v1/me` | ok |
| G04 | S-dyn | turn off push notifications for my account | exists `PUT /v1/me` | ok |
| G05 | S-dyn | view my profile | exists `GET /v1/me` | several |
| G06 | S-dyn | create an account with email and password | exists `POST /v1/auth/register` | ok |
| G07 | S-dyn | get a new access token after it expires | exists `POST /v1/auth/refresh` | several |
| G08 | S-dyn | change the period of a habit | exists `PATCH /v1/habits/{}` | several |
| G09 | S-dyn | remove a habit | exists `DELETE /v1/habits/{}` | ok |
| G10 | S-dyn | show the streak history of a habit | exists `GET /v1/habits/{}/history` | ok |
| G11 | S-dyn | habits | several: `GET`/`POST /v1/habits`, `GET`/`PATCH`/`DELETE /v1/habits/{}` | ok |
| G12 | S-dyn | login or register | several: `POST /v1/auth/login`, `POST /v1/auth/register` | exists |
| G13 | S-dyn | reset my streak to zero | no_match | ok |
| G14 | S-dyn | export my data as a spreadsheet | no_match | ok |
| G15 | S-dyn | block another user | no_match | ok |
| G16 | S4-stat | register a phone for push | spec_only `POST /v1/devices` | ok |
| G17 | S4-stat | the today view with habits and nudges | spec_only `GET /v1/home` | several |
| G18 | S4-stat | page through nudges | spec_only `GET /v1/nudges` | no_match |
| G19 | S4-stat | pull changes since the last sync | spec_only `GET /v1/sync` | no_match |
| G20 | S4-stat | see weekly progress for a habit | exists `GET /v1/habits/{}/progress` | ok |
| G21 | S4-stat | send an email reminder | no_match | ok |
| G22 | S4-stat | check-ins of a habit | several: `POST`/`GET /v1/habits/{}/completions`, `DELETE .../completions/{}`, `POST /v1/completions/batch` | exists |
| G23 | S-plus | usage statistics for admins | exists `GET /v1/admin/stats` | no_match |
| G24 | S-plus | metrics | no_match (`/metrics` is plumbing) | ok |

Recon (r): **13/24 exact (gate missed)**, target first in 13/16 positive rows (reading prediction before the run: 14-15/24).
Report-only beside the gate: hint accuracy, target@1, target@3, round 1's ranker on G (2/24, r).

| id | probe | prediction | pass |
|---|---|---|---|
| PT-dev | round-1 PT (8, hint as round 1) and the 13 round-1 agent queries (labels from round-1 keys: implemented -> exists, RNI -> spec_only, NSNI -> no_match, AMBIGUOUS -> several; targets as round-1 referents) | 8/8, 13/13 (r) | PT >= 6/8 (round 1's bar, as non-regression); agent rows report-only |
| PT-F | the burned set F (D1), stored beside G in the queries file, hash-checked | 20/27 (r, tuned-on) | report-only; mutant catcher |
| PT3 | held-out queries: the orchestrator writes >= 12 (labels and targets from the report and code, never from `workflow_find`), hashed into Addendum 1 before the build's code is final; run once after the build | 45-70% exact (p) | report-only; never tuned on |
| PR1 | round 1's `implementation_probes.py`, unchanged, at the new code (`--runs 3`; PF if Docker) | every row as round 1; PT now 8/8 (r) | all rows but PT as round 1; PT reported |
| PB2 | byte-compat vs bb3ff0c: MCP `instructions` and `tools/list` (round-1 PB's 8 configs + single toolbox); report JSON of S-dyn, S-stat, S4-stat, S-plus (S-dyn 84624894..., S4-stat 80511cbb...); PA hashes (digest, prompt, answer with and without inventory); AST of every top-level def of `implementation.py` except D7's; round-1 frozen file hashes (round-1 Addendum 1 C); `git diff bb3ff0c -- docs/results/data/2026-10-15-implementation-diff` empty; round-1 `test_workflow_find_*` (4) pass unedited | all identical | all |
| PD2 | `workflow_find` on G, PT-dev, PT-F: 10 processes (random hash seeds) and a second archive root | identical | 10/10 + root |
| PX2 | one call after load | 5-50 ms (p) | <= 1 s |
| REG | `pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck`; `dogfood/studio/check.sh` | same 31 failing ids as round 1; PASS 481 | same set; PASS |

**PQ2, the agent exercise (D8).** 13 scored sessions + 2 ablations without the 3 tools (A8, A11; information only). Keys
(r: read off the frozen reports by K1-K4; the build copies them before the freeze; a differing key follows the report and
is a finding). sha256 of `json.dumps({"questions": rows, "ablation": ["A8","A11"]}, sort_keys=True, separators=(",", ":"))`,
rows `{id, world, text, predicted_key, referent (null for A6-A9), key_elements (A8, A9 only)}`:
`bf06b9e92defaa7ed38365f056492e400651ba4bd0c359100f79e37e4a21a694`. VERIFIED/UNVERIFIED abbreviate IMPLEMENTED-*; key
elements in table order. The three canonical files are in `$SCRATCH/{pt2_queries,f_burned_queries,agent2_questions}.json`.

| q | world | question | predicted key (referent; key rules or elements) |
|---|---|---|---|
| A1 | S-dyn | Can a user archive a habit? | VERIFIED (`PATCH /v1/habits/{}`) |
| A2 | S-dyn | Can a user dismiss a nudge? | VERIFIED (`POST /v1/nudges/{}/dismiss`) |
| A3 | S-dyn | Can a user change their quiet hours for notifications? | UNVERIFIED (`PUT /v1/me`) |
| A4 | S-dyn | Can a client exchange a refresh token for a new access token? | UNVERIFIED (`POST /v1/auth/refresh`) |
| A5 | S-dyn | Can a user delete their whole account? | UNVERIFIED (`DELETE /v1/me`) |
| A6 | S-dyn | Can a user attach a photo to a check-in? | NOT-SPECIFIED-NOT-IMPLEMENTED |
| A7 | S-dyn | Can a user buy a paid plan in the app? | NOT-SPECIFIED-NOT-IMPLEMENTED |
| A8 | S-dyn | Is there an endpoint that pages through results with a cursor? | AMBIGUOUS (`GET /v1/habits`, `GET /v1/habits/{}/completions`, `GET /v1/nudges`, `GET /v1/sync`) |
| A9 | S-dyn | Is there an endpoint for a habit's check-ins? | AMBIGUOUS (`POST`/`GET /v1/habits/{}/completions`, `DELETE .../completions/{}`, `POST /v1/completions/batch`) |
| A10 | S4-stat | Can a user see a list of their nudges? | REQUIRED-NOT-IMPLEMENTED (`GET /v1/nudges`; R21 R27 R114 R125 R127-R129 R132-R135 R137-R139) |
| A11 | S4-stat | Can a user remove a device they registered earlier? | REQUIRED-NOT-IMPLEMENTED (`DELETE /v1/devices/{}`; R7 R21 R28 R33) |
| A12 | S4-stat | Can a user dismiss a nudge? | REQUIRED-NOT-IMPLEMENTED (`POST /v1/nudges/{}/dismiss`; R7 R21 R28 R138-R141 R143) |
| A13 | S4-stat | Can a user archive a habit? | UNVERIFIED (`PATCH /v1/habits/{}`, static run) |

**Freeze:** runner, scorer, check, cases, queries file, `claude --version`, workflows sha256 (af1383db...) into Addendum 1
before the first session; the runner refuses to start on a mismatch. **Pass:** >= 11 of 13 scored sessions satisfy every
applicable C-item, and C7 holds in 15 of 15. **Caps:** USD 0.50 and 8 turns per session, USD 5.00 in all (abort if the
first 4 cost > USD 1.50), <= 1.0M tokens counted as input + output + cache creation. A usage or rate limit records the
rest **NOT RUN (usage limit)**: never retried, never a miss; with n < 13 the rule is not evaluated and n is stated. No E2.
**Predictions (p):** 11/13 (9-13); C1 13/13; misses most likely A3-A5 (VERIFIED vs UNVERIFIED) or A9 (answered
IMPLEMENTED); ablation C2 1/2; USD 0.06-0.40 per session, 1.0-2.5 in all; 3-8 turns; 7k-90k counted tokens per session.

## 4. Mutants (`dogfood/implementation_mutants2.py`, scratch copy; control first; predicted 14/14 killed)

Killed when a gated probe passing on the control fails, any PT2, PT-dev or PT-F row differs from the control's, a
scorer case flips, or a PB2 hash differs. Mutant -> predicted catcher: **WM1** rule texts as a primary field (round 1's
document) -> PT2, PT-dev; **WM2** verbs literal -> PT-dev ("delete endpoint DELETE"); **WM3** no compounds -> PT-F (F04);
**WM4** no resource penalty -> PT2 (G09), PT-F (F01, F24); **WM5** out-of-vocabulary terms weigh 0 in coverage -> PT2
(G13-G15); **WM6** anchor rule off -> PT-dev (agent query Q5); **WM7** `several` on S + T -> PT2 (G11); **WM8** spec-only
endpoints not candidates -> PT2 (G16-G19); **WM9** spec-only top answered `exists` (round 1's IM15) -> PT2 (G16); **WM10**
flows as separate candidates -> PT2, PT-F; **WM11** plumbing/test_only as candidates -> PT2 (G24); **WM12** C4 accepts a
relative path without the registration citation -> scorer cases; **WM13** C2/C8 accept an AMBIGUOUS key with an
implemented label or one element -> scorer cases; **WM14** `workflow_find` description edited -> PB2.

## 5. Risks, and what this cannot show

1. **The recon predicts the PT2 gate fails (13/24)** (R1). Lexical limits seen: synonyms with no shared token
   (profile/`me`, sign out/`logout`, statistics/`stats`), one noun on a parent and its children, filler ("through").
2. **One author** designed the ranking and wrote F, G, labels, quotas, questions and keys; G was written knowing v2b.
   Quotas limit easy-row selection; PT3 is the only independent query set.
3. **Development set:** same apps and reports as round 1; weights, lexicon and stop lists are Streak-shaped (Gin, `/v1`).
4. **Labels are judgments** ("habits" = several; `/metrics` = no_match by D2); the gate counts them as truth.
5. **PQ2 is not comparable to round 1** (new questions, defined labels, C4/C5); n = 13, one model: 11 vs 13 is noise.
   C4/C5 could pass a wrong path or rule only through an exact registration or trace-line citation. Caps guard cost.

## 6. Blind vs post hoc, and commands

Fixed now: D1-D9, G, the questions, both hashes, the gates. A failed prediction is a finding; fixes are separate post hoc
commits with deltas; G is never edited or re-labelled; PT3 is never tuned on.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv PYTHONDONTWRITEBYTECODE=1 \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-c2 WF=docs/results/data/2026-10-15-workflow-layer/workflows.json
SCRATCH=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/c2
git -C /Users/vivekgade/Documents/streak status --short     # before and after: unchanged; Streak only via git archive
uv run python dogfood/implementation_probes2.py --workflows-dyn $WF --scratch "$SCRATCH/p2" --out "$SCRATCH/p2-data"
uv run python dogfood/implementation_probes.py --streak-ref a0679e9 --streak-ref2 1d00ede --runs 3 --workflows-dyn $WF \
    --scratch "$SCRATCH/r1" --out "$SCRATCH/r1-data"                                  # PR1, round 1's runner unchanged
uv run python dogfood/implementation_mutants2.py $(seq -f WM%g 1 14); dogfood/studio/check.sh
uv run python dogfood/implementation_agent2.py --write-freeze --out OUT --workflows-dyn $WF   # runs the check first
uv run python dogfood/implementation_agent2.py --scratch "$SCRATCH/a2" --workflows-dyn $WF --out OUT   # PQ2, once
```

**Pass:** PT2 (or R1's ruling), PT-dev, PR1, PB2, PD2, PX2, REG, 14/14 mutants; PQ2 by its rule or NOT RUN; PT-F, PT3 reported.

## 7. File ownership (build node)

**Edits:** `lattice/spec/implementation.py` (D7 region); this file, only by appending Addendum 1 (freeze and PT3 hashes,
open decisions) before the probes run. **Creates:** `dogfood/implementation_find2_queries.json` (`{"G", "F_burned"}`, both
hashes checked), `dogfood/implementation_{probes2,mutants2,agent2,agent2_scorer,agent2_check}.py` (agent2 may import round
1's runner helpers), `dogfood/implementation_agent2_scorer_cases.json`, `tests/spec/test_workflow_find2.py` (each D4-D6
rule on the unit fixture), the results doc and data.
**Not touched:** `lattice/mcp/tools.py` and all other product files; round 1's `dogfood/implementation_*` files, cases and
data; existing tests (a pinned-test edit is disclosed, counts against PB2); the Streak checkout, `job-scheduler*`, the main
checkout. Scratch only under `$SCRATCH`.

**F (burned, D1; same row form as G).** S-dyn exists: F01 create a new habit -> `POST /v1/habits`; F02 rename a habit, F03
archive a habit -> `PATCH /v1/habits/{}`; F04 log in with email and password, F05 sign out, F06 refresh the access token ->
`POST /v1/auth/{login,logout,refresh}`; F07 update notification preferences -> `PUT /v1/me`; F08 delete my account ->
`DELETE /v1/me`; F09 habit history, F12 progress toward the weekly target -> `GET /v1/habits/{}/{history,progress}`; F10
dismiss a nudge -> `POST /v1/nudges/{}/dismiss`; F11 remove a check-in -> `DELETE /v1/habits/{}/completions/{}`; F13 health
check -> `GET /health`. Several: F14 cursor pagination [the 4 cursor GETs of A8]; F15 nudges [`GET /v1/nudges`, its dismiss,
`impl:machine:nudges.delivery`]. No_match: F16 upload a profile photo, F17 pay for a premium subscription, F18 invite a
friend to a challenge, F19 sign in with Google. S4-stat spec_only: F20 list nudges, F21 dismiss a nudge, F22 remove a
device, F23 fetch changes for offline sync -> `GET /v1/nudges`, its dismiss, `DELETE /v1/devices/{}`, `GET /v1/sync`; F24
delete a habit -> exists `DELETE /v1/habits/{}`; F25 device -> several [both device routes]; F26 rename a habit -> exists
`PATCH /v1/habits/{}`. S-plus: F27 admin stats -> exists `GET /v1/admin/stats`. The file's F rows must hash to D1's value.

## Amendment 1 (orchestrator rulings, 2026-10-16, before any code)

**R1 ruled (the gate).** The recon shows the right route ranks first in 13 of the 16 single-target rows (81%) and
the exact label+route is 13/24 (54%): the ranker works, the hint (`several`/`no_match`) fails on near-ties,
wordy queries and synonyms. The gate for the build is therefore split, and the split is registered here before
any build: **PT2a (gated): the top-ranked route is the key route on >= 75% of the single-target rows of G**
(predicted 81%). **PT2b (reported, NOT gated, predicted to miss): exact label+route >= 75% on G** (predicted
54%). The original round-1 intent (an answer that is correct end to end) is PT2b, and the results state
plainly that it is predicted not to be met and report the observed figure; if PT2b is met that is reported as
exceeding the prediction. The 75% is not lowered and the PT2b miss is not hidden.
**Other rulings.** (1) The query files F, G and the agent-exercise question file are copied from
`.../scratchpad/c2/{pt2_queries.json,f_burned_queries.json,agent2_questions.json}` into the data directory by the
builder and their sha256 verified equal to the document's hashes before any run. (2) F is development data,
G was written after v2b was frozen (as D1 says), and neither is independent of the designer; **PT3 below is
the only query set the designer has not seen.** (3) The prototype `proto_b.py` (sha 1236d829...) is not shipped;
the build re-implements v2b in `lattice/spec/implementation.py` and the builder compares its output to the
prototype's on F and G (differences are reported). (4) `lattice/mcp/tools.py` is not edited; the tool name,
description and schema stay byte-identical. (5) Agent exercise 2: caps USD 0.50 and 8 turns per session, USD 5
total, tokens = input + output + cache creation, `claude` CLI only, NOT RUN on a usage/rate limit (never
retried in a loop, never scored as a miss); the pass rule (>= 11/13, B7 15/15) is then not evaluated; the
first four sessions must not exceed USD 1.5; scorer, questions, keys and cases frozen by hash before the first
session. Round-1 answers appear only as scorer test fixtures. (6) Byte-compat: the round-1 report JSON hashes
(84624894..., 80511cbb...) must be reproduced.

### PT3 (orchestrator-authored, hashed, report-only; written before the build, `workflow_find` not run)
sha256 `925d73f549af80020319454d0927561cfa3e9bccb7ed49f6743ca91dcdb83e6a`; project S = Streak a0679e9
inventory, S4 = 1d00ede inventory (the round-1 inventories). The file is
`docs/results/data/2026-10-16-implementation-diff-round2/pt3_queries.json` (copied by the builder from
`.../scratchpad/c2/pt3_queries.json`; the sha is verified before the run). Contents:

```json
[
 {
  "id": "PT3-1",
  "label": "implemented",
  "project": "S",
  "question": "update my account details",
  "routes": [
   "PUT /v1/me"
  ]
 },
 {
  "id": "PT3-2",
  "label": "implemented",
  "project": "S",
  "question": "how long is my streak on a habit",
  "routes": [
   "GET /v1/habits/{}/progress"
  ]
 },
 {
  "id": "PT3-3",
  "label": "implemented",
  "project": "S",
  "question": "delete a check-in I logged by mistake",
  "routes": [
   "DELETE /v1/habits/{}/completions/{}"
  ]
 },
 {
  "id": "PT3-4",
  "label": "implemented",
  "project": "S",
  "question": "log several check-ins in one request",
  "routes": [
   "POST /v1/completions/batch"
  ]
 },
 {
  "id": "PT3-5",
  "label": "implemented",
  "project": "S",
  "question": "dismiss a reminder",
  "routes": [
   "POST /v1/nudges/{}/dismiss"
  ]
 },
 {
  "id": "PT3-6",
  "label": "implemented",
  "project": "S",
  "question": "sync changes since the last time",
  "routes": [
   "GET /v1/sync"
  ]
 },
 {
  "id": "PT3-7",
  "label": "spec_only",
  "project": "S4",
  "question": "register a device for push notifications",
  "routes": [
   "POST /v1/devices"
  ]
 },
 {
  "id": "PT3-8",
  "label": "implemented",
  "project": "S",
  "question": "see the history of a habit",
  "routes": [
   "GET /v1/habits/{}/history"
  ]
 },
 {
  "id": "PT3-9",
  "label": "implemented",
  "project": "S",
  "question": "sign out",
  "routes": [
   "POST /v1/auth/logout"
  ]
 },
 {
  "id": "PT3-10",
  "label": "implemented",
  "project": "S",
  "question": "refresh my access token",
  "routes": [
   "POST /v1/auth/refresh"
  ]
 },
 {
  "id": "PT3-11",
  "label": "implemented",
  "project": "S",
  "question": "archive a habit",
  "routes": [
   "PATCH /v1/habits/{}"
  ]
 },
 {
  "id": "PT3-12",
  "label": "no_match",
  "project": "S",
  "question": "send a push notification to every user",
  "routes": []
 },
 {
  "id": "PT3-13",
  "label": "implemented",
  "project": "S",
  "question": "delete my account",
  "routes": [
   "DELETE /v1/me"
  ]
 },
 {
  "id": "PT3-14",
  "label": "implemented",
  "project": "S",
  "question": "show the home screen summary",
  "routes": [
   "GET /v1/home"
  ]
 }
]
```

Scoring for PT3: the top-ranked route in the row's `routes` (or no route for `no_match`) and the label equal to `label`. Reported only; PT3 never gates and is never tuned.
