# 2026-10-10: Studio improves Lattice, run 2 (repeat after the crash fixes)

**Outcome: A, passed in round 1 of 3.** This run repeats [run 1](2026-10-10-studio-improves-lattice.md)
with the same request. Since run 1, these fixes have landed: the junk-pathspec crash and the gate-env test
isolation (75ef3fb), and usage kept on a crash plus an honest crashed-round card (dc07886).
This time the whole loop ran without intervention: draft, accept, rebuild, both gates green, and
one commit on `rebuild/c1`. Studio's own record has the real agent usage. Wall time from the request
to the passed rebuild was 6 min 54 s. This attempt cost **~$0.63**; both attempts together cost
~$1.58 of the $8 cap.

## Setup

- Throwaway clone: `<scratchpad>/lattice-clone2` (`git clone --local`, branch `roadmap`, base dc07886,
  local identity "Lattice Studio <studio@lattice.local>"). Run 1's clone `lattice-clone` was kept.
  `<scratchpad>` = `/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/4a12ca95-cf29-48df-94c4-af809da90b17/scratchpad`.
- Studio was started from the owner's checkout venv with
  `uv run lattice studio <clone2>/dogfood/studio --port 8851`. There was no `--stub-llm`; the model was sonnet.
  `/api/projects` returned `lattice-studio` with `capabilities {session: true, rebuild: true}`.
- Credentials: the shell, the Studio process and the agent process (`ps eww`) had no
  `ANTHROPIC_*` or `CLAUDE_CODE_USE_*` variables. Every call went through the `claude` CLI subscription login.
- Before the run, `GET /api/changes` answered 404.

## Timeline

| time (local) | event |
|---|---|
| 12:44:58 | Studio up on :8851 |
| 12:45:04 | `POST /api/changes` returned c1 |
| 12:47:45 | c1 `ready` (161.3 s: edit 14.6 s, draft 126.4 s in 2 draft rounds, lint 0.03 s, impact 20.3 s) |
| 12:47:57 | accept: commit 1922115 in clone2 (the owner's HEAD was still dc07886) |
| 12:48:02 | `POST /api/rebuild {changes:[c1], max_rounds:3}` returned b1, on branch `rebuild/c1` |
| 12:51:58 | b1 `passed` in round 1 (236 s: agent 138.6 s, gates 24.5 s + 71.3 s) and committed ebea536 on `rebuild/c1` |

## Phase 1: the draft (c1)

- New rule **R5**: "GET /api/changes answers 200 with a JSON array of every change of the project as records
  with `id`, `status` and `request`, newest first, and `[]` when there are none." It sits under "Change sets".
- New scenario `the_changes_are_listed_newest_first`. It lists `[]`, posts c1 and waits for ready, lists
  `[c1]`, then posts c2 and waits for ready, and lists `[c2, c1]`. It covers the empty case, the field set
  and **the ordering with two changes**. It is the same shape as run 1's scenario (`the_changes_list_is_newest_first`).
  The view diff adds one cell: `no token x GET /api/changes: · -> 200`.
- `checks.valid = true`, no problems, **no conflicts**, no questions.
- Lint gave the same 4x L008 as run 1, all on existing rules (R6, R13, R14, R33). None is on R5.
- Impact was `fail`, with 1 target `not_built_yet`: `[D silent] GET /api/changes (step 1)`. That is correct.
- Accept made exactly one commit, 1922115. It touches only `dogfood/studio/{REQUIREMENTS.md (+1),
  spec.py (+17), spec.trace.json (+93/-84)}`.
- **Drafting defect (same in run 1, not caught by any check):** inserting R5 renumbers every later rule.
  The trace's `unchecked` keys were renumbered correctly. The free-text `Unchecked(...)` notes in spec.py
  were not, and they still say "R21/R22", "R24, R25, R26, R27", "R28", "R29", "R31", "R32-R39", "R40", "R16", "R17",
  "R7". Every one of those ids is now one too low. The drafter patched only one of them, by adding a new
  "R23: an answer's content ..." note that duplicates the stale "R21/R22" note. The result is that
  check.sh's "not checked" list now points at the wrong rules.

## Phase 2: the rebuild (b1)

| round | agent summary | files | gates (run by Studio) | findings | usage (Studio record) |
|---|---|---|---|---|---|
| 1 | "I added `GET /api/changes` to `lattice/studio/server.py`. It returns every change as `{id, status, request}`, newest first, and `[]` when there are none. check.sh: 0 findings across 166 cases; pytest: 175 passed, 1 skipped." | `lattice/studio/server.py` +10 | `dogfood/studio/check.sh` exit 0, 24.5 s (`PASS: 0 findings (D 0, X 0); 166 cases run`); `uv run pytest tests/studio -q` exit 0, 71.3 s (`175 passed, 1 skipped`) | none | **$0.0939**, 138.6 s, 12 turns, 20 in / 2 221 out tokens |

Usage is now recorded, which fixes bug 3 from run 1. One gap remains: the record and the card count only
input and output tokens (the card says "2241 tokens"). From the session transcript, the agent also used
**153 760 cache-read and 14 066 cache-write tokens**, so the card understates the token volume by about 75x.
The cost figure is the CLI's own.

Agent behaviour, from the transcript (10 assistant messages): it ran Grep x2 and Read, then sed and cat reads through Bash,
which the allowlist permits as read-only. A `python3` heredoc edit was **denied**, and the agent switched to Edit x2. It
ran both gates itself; one more `... >/dev/null; echo $?` call was **denied**. It used no lattice MCP tools.
It made 2 denied calls, against 5 in run 1.

## Diff review (`git -C <clone2> diff 1922115..rebuild/c1`)

- **Correct.** `Studio.list_changes()` returns `[{id, status, request}]` over
  `reversed(list(self.changes.values()))` under `self.lock`. The route is
  `if path == "/api/changes": return self._dispatch(st.list_changes)` in `do_GET`. It is placed after the
  `/api/changes/<id>` branch, which needs the trailing slash, so the order does not matter. The method body
  is **byte-identical to run 1's**. Only the route's position differs.
- **Minimal.** 1 file, +10 lines, no collateral edits, and the spec files are untouched (`spec_edited: []`).
- **No test added.** This is a regression from run 1, which added `test_changes_list_is_newest_first`, though
  that test checked only one change. Nothing in the agent prompt asks for a test, and the gates passed without
  one. The only ordering check is the dogfood scenario, which does check two changes in order.
- **Project prefix respected.** I probed it with the rebuilt worktree's code on a stub-LLM Studio
  (port 8852, a copy of `project_seed`, no LLM cost). `GET /api/changes` gave `[]`, then
  `[{c2,"second"},{c1,"first"}]`. `/p/v2-probe/api/changes` gave the same, `/p/nope/api/changes` gave 404 "no such project",
  and `?x=1` was ignored.
- **The gates ran with the rebuilt code.** check.sh went from `not_built_yet`, the impact on base, to `D 0`, so
  the worktree's server was the one imported. The gate env is unchanged from run 1, where a probe confirmed
  that `lattice` resolves to the worktree. The pytest gate now passes inside Studio's gate env: the
  isolation fix in 75ef3fb works under the real nested env.
- The commit ebea536 is **only on `rebuild/c1`**. Clone2's `roadmap` is at 1922115. The owner's checkout HEAD is
  still dc07886.

## UI (headless Chrome over CDP; the claude-in-chrome extension was not connected)

The Rebuilds view (`#rebuilds`, light mode) shows the real result:
"sonnet · 1 round of up to 3 · 3m 56s · $0.09", "Passed", the branch `rebuild/c1` "from 1922115", and the round 1
summary, file and "$0.09 · 2m 19s". It shows both gates with a check, `exit 0` and 25 s / 1m 11s, plus
"Checks not built yet: 1 of 1 passing", "Built on rebuild/c1", `ebea536 ...`, `M lattice/studio/server.py`, and
"Agent cost: $0.09 · 2m 19s · 2241 tokens". The merge hint with `git merge rebuild/c1` has a Copy command button.
**There were zero console errors or warnings.** Screenshot:
`<scratchpad>/v2-shots/rebuild-card-passed-light.png`.

## Side by side with run 1

| | run 1 | run 2 |
|---|---|---|
| draft time / cost | 161 s / ~$0.59 | 161 s / ~$0.54 ($0.482 draft "cli_list_estimate" + ~$0.058 edit step at list price, which has no cost field) |
| drafted rule / scenario | R5 / `the_changes_list_is_newest_first` (2 changes) | R5 / `the_changes_are_listed_newest_first` (2 changes) |
| conflicts / new lint | none / none | none / none |
| stale ids in `Unchecked` prose | yes (not noticed) | yes |
| rebuild outcome | crashed in round 1 (Studio `git add` bug), gates never ran | **passed in round 1**, both gates exit 0 |
| rebuild time | 6 min 35 s (agent only) | 3 min 56 s (agent 2 min 19 s + gates 1 min 36 s) |
| agent cost | ~$0.35 estimated; Studio recorded $0 | **$0.094 recorded by Studio** |
| agent turns / denials | 22 messages / 5 | 12 turns / 2 |
| code diff | server +10, test +11 (1 change only) | server +10, **no test** |
| commit | none (uncommitted worktree) | ebea536 on `rebuild/c1` only |
| attempt total | ~$0.95 | **~$0.63** |

Run 2's agent was cheaper and faster, mostly because the pytest gate passed on the first try. In run 1 the
agent spent many turns on a failing test it could not explain.

## Costs and metrics

| phase | wall time | cost | tokens | outcome |
|---|---|---|---|---|
| drafting c1 (sonnet) | 161 s | ~$0.54 | edit 3 947 in / 3 087 out; draft 76 568 in (50 136 cache-write, 26 428 cache-read) / 27 845 out | ready, valid, no conflicts |
| accept | <1 s | $0 | n/a | 1 commit, spec files only |
| rebuild b1 round 1 (sonnet) | 236 s (agent 139 s, gates 96 s) | $0.094 | 20 in / 2 221 out / 153 760 cache-read / 14 066 cache-write | passed |
| **total, run 2** | 6 min 54 s from request to green | **~$0.63** | | |
| cumulative, both attempts | | **~$1.58** of $8 | | |

Program size: +10 lines of code and 0 lines of test. Spec: +1 requirement line, +17 spec.py lines, and +93/-84 trace lines
(mostly renumbering). There was 1 rebuild round.

**What the agent got wrong:** nothing functional. It did not add a unit test. It tried 2 shell commands
the allowlist denies.
**What the gates caught:** on base, the impact and check.sh had the scenario as `not_built_yet`, a D finding. After the
change, both gates were green. In this run the gates caught no agent defect, because there was none to catch.

**What the spec/gates would NOT catch (critical):**
- **Extra fields.** `expect_body` uses `subset()`: object keys are a subset and lists are element-wise with the same
  length. If each item returned the full change record (markdown diff, usage, ...), the scenario would still
  pass, although R5 says "records with `id`, `status` and `request`". The field set is not enforced.
- **No test is required.** A passing rebuild can add zero regression tests. The ordering is guarded only by the
  dogfood scenario, which runs on the stub LLM.
- **Stale rule ids in `Unchecked` prose** (see Phase 1). Lattice validates the trace but not the ids in free text,
  and the drafter renumbers only the structured parts. This is a Lattice/drafter gap, not an agent error.
- **The token count on the card** leaves out cache tokens (see Phase 2).
- Not specified, so not checked: superseded, cancelled and rejected changes appear in the list; there is no
  pagination; ordering under concurrent POSTs; whether the UI should use the endpoint. The `checking` status is
  never observed with the stub.
- The gates are self-referential: Studio's tests gate a change to Studio, run by Studio. A change that broke
  rebuild.py in a way the fake-agent tests miss could still pass.

## Honest limitations

This is one run of one tiny change with one model and one agent attempt. The feature code is identical to run 1's,
so this run says more about Studio's loop working end to end than about the agent's range. The edit step's
cost is my estimate at list price. Studio records no cost for it.

## For the owner to review

```sh
C=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/4a12ca95-cf29-48df-94c4-af809da90b17/scratchpad/lattice-clone2
W=$C/.lattice/rebuilds/lattice-studio-b1-94d801
git -C $C log --oneline -3 rebuild/c1        # ebea536 build, 1922115 spec, dc07886 base
git -C $C show --stat 1922115                # the accepted spec change
git -C $C diff 1922115..rebuild/c1           # the agent's code change (= the .patch file)
git -C $C branch --contains ebea536          # only rebuild/c1
# to take it into your checkout when you decide to:
git fetch $C rebuild/c1:studio-c1-run2       # gets 1922115 + ebea536
# or only the code on top of your own branch:
git apply docs/results/2026-10-10-studio-improves-lattice-run2.patch
```
