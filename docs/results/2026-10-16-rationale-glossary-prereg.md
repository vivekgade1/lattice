# Spike G: rationale notes and a business glossary (why a rule exists, what its words mean): pre-registration

## 1. Status, and the exact gap

2026-10-16, branch `np-rationale` (worktree `~/Documents/lattice-rg`, from `next-phases` 8931d46). **Written before any
product code; no model was run.** Plan: `docs/design/lattice-implemented-logic-spikes-plan.md`, Addendum, Spike G (added on
the owner's yes). **(m)** = measured, **(r)** = measured by a throwaway recon script in scratch (the build's own figure is
the gated one), **(p)** = prediction. Results go in `2026-10-16-rationale-glossary-results.md`, data in
`docs/results/data/2026-10-16-rationale-glossary/`.

**The gap.** A Lattice spec is a blueprint of behaviour: `REQUIREMENTS.md` (the owner's text), `spec.py` (the checks) and
`spec.trace.json` (rule -> checks). Nothing records **why** a rule exists, what it trades away, or who decided it, and
nothing pins down what the business words in a rule mean ("active window", "past period", "today", "nudge"). The chat
(`lattice/spec/ask.py`) can therefore only answer "what", and when asked "why" it can either refuse or make a reason up.
Spike G adds two owner-held files beside the requirements, a lint over them, deterministic coverage metrics, and chat
citations of the approved reason next to the rule. A reason drafted by a model is a **proposal** that the owner approves,
never an automatic fact.

**Corpus.** Streak `a0679e9` (**S**), taken with `git archive` into scratch, read-only. sha256 (m): `spec.trace.json`
`627a2b3f...82ed`, `REQUIREMENTS.md` `dcac6deb...b7b1`, `spec.py` `28dfc2e6...5624`. Facts (m): 148 rules, 2 context
(R1, R2), 5 unchecked, 18 questions. The trace's rules equal `rules.extract_rules(REQUIREMENTS.md)` exactly (ids and texts).
**R88 and R94 have the same text** ("A cursor that the app did not issue answers 422 with code `invalid_cursor`.").
Streak has no rationale and no glossary today. Some of its terms are defined inline in the rules (R45 "today", R97 "active
window", R115 "nudge", R145 "past period"). It also has an "Assumptions made on the owner's behalf" block (G1-M18), but
that block sits inside a code fence, so it is not a rule (`extract_rules` skips fences).

**Code facts the design rests on (m, read at 8931d46).**
1. `Trace.from_json` keeps only its dataclass fields, and `to_json` writes only those, so an unknown key put inside
   `spec.trace.json` is **silently dropped** the next time a draft or a Studio accept rewrites the trace. Rationale
   therefore cannot live inside the trace (D1).
2. Rule ids are positional (`R<n>` in document order). Inserting a rule renumbers every later rule. A note keyed only by
   id would move to the wrong rule (D3).
3. `lint()` (L001-L012) feeds the drafting repair loop, Studio's `lint_gaps` and the MCP `spec_lint`. Its exit code is 1
   when there is any finding. A glossary finding is about the owner's words, which the drafting model must not edit, so it
   must not enter `lint()` (D7).
4. `studio.server.files_hash` hashes `REQUIREMENTS.md`, `spec.py` and `spec.trace.json`, writing `<missing>` for an
   absent file. If new names were added to that loop, the hash would change even when the new files are absent (D13).
5. `mcp.guard` and `studio.rebuild` protect `SPEC_FILES` plus `[mcp] protected`. Nothing protects any other owner file.

**Recon (r)** with a scratch script over S's trace and spec. 9 tables. 20 distinct `one_of` values (Nudge kind,
delivery and delivery_reason, Habit period, Device platform). 1 service (`nudger`). 1 double-quoted phrase outside
backticks ("today", R45), 0 emphasised phrases. The inline-definition pattern of D6 C6 matches 11 rules, giving 10 distinct
terms: password (R9, R12), access token, refresh token, today, habit name, current period, active window, nudge, dismissed
nudge, past period. Enum values occur 79 times over 34 rules. The table nouns occur in 101 rules.

## 2. Decisions (fixed before coding)

**D1 Files and locations.** Four optional JSON files live in the **directory of the trace file**. Their names come from
the trace's `source` stem (`REQUIREMENTS.md` gives `REQUIREMENTS`):

| file | holds | written by |
|---|---|---|
| `<stem>.rationale.json` | approved rationale notes (authoritative) | `rationale approve` / `rationale add` (owner, D4) or the owner's own editor |
| `<stem>.rationale.proposed.json` | proposals (drafted by a model, or by hand) | `rationale draft --out` (or any author) |
| `<stem>.glossary.json` | approved terms and the `ignore` list (authoritative) | `glossary approve` / `glossary add` (owner) or the owner's editor |
| `<stem>.glossary.proposed.json` | term proposals | `glossary draft --out` |

For Streak these are `REQUIREMENTS.rationale.json` and so on, beside `REQUIREMENTS.md`. They are versioned in git with
the requirements. Every reader takes explicit `--rationale PATH` / `--glossary PATH` overrides (each also implies its
`.proposed.json` sibling). Why separate files: fact 1 (a key inside the trace is lost on the next accept), and Studio's
accept and the drafter keep rewriting the three spec files without ever touching these. Why JSON and not Markdown:
approval metadata, anchors and schema checks must be exact. The CLI renders the files as text for reading.

**D2 Rationale schema `lattice.rationale/1`.** Both files share the schema:
```json
{"schema": "lattice.rationale/1", "source": "REQUIREMENTS.md",
 "notes": [{"rule": "R63", "anchor": "<sha256 hex of the rule's normalised text>",
            "why": "1..600 chars", "tradeoff": "<= 600 chars or null", "owner": "who owns the decision, or null",
            "status": "approved | proposed | rejected", "author": "owner | llm:<model> | hand",
            "approved_by": "name or null", "approved_sha": "sha256 or null",
            "basis": [{"target": "rule:R63 | question:Q2 | scenario:<name> | ...", "quote": "..."}],
            "refs": ["free-text references: ADR-7, a ticket, a URL"]}]}
```
The **normalised rule text** is the text with whitespace runs collapsed to one space and stripped. `approved_sha` is the
sha256 of `json.dumps({"why", "tradeoff", "owner"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` at
approval. **Authoritative file:** every note has `status` `approved` and non-empty `owner`, `approved_by` and
`approved_sha`, and there is at most one note per rule id. **Proposals file:** `status` is `proposed` or `rejected`, and
`approved_by` and `approved_sha` are null. Anything else answers `bad_rationale: <reason>` (D14). `owner` in a proposal is
always null when `author` starts with `llm:`, because a model never names the owner.

**D3 Anchors and note states** (rationale only; glossary terms are not anchored). Each approved note resolves against the
current trace, in this order, first match wins:
(1) `approved_sha` does not match the note's current why/tradeoff/owner -> **edited**;
(2) the rule with the note's id exists and its anchor equals the note's -> **current**;
(3) exactly one rule has that anchor -> **moved** (current, under the new id; the move is reported);
(4) two or more rules have it -> **ambiguous**;
(5) none -> **stale** (the rule's wording changed, or the rule was removed).
Only `current` and `moved` count as approved: in coverage, in the digest, in answers and in tools. The `edited`,
`ambiguous` and `stale` states are reported (G003) and never shown as the owner's reason. Any wording change, typo fixes
included, makes a note stale. That is deliberate: the owner re-approves. The trace's `replaces` map is not used.

**D4 Approval (the only way a note or a term becomes authoritative).**
(a) `rationale approve --from PROPOSALS --rule R --approver NAME [--owner NAME] [--why T] [--tradeoff T]` copies one
proposal into the authoritative file. The owner may edit the text first with the flags. It fills `owner` (required,
prompted when not given), sets `status: approved`, `author` (kept as `llm:<model>` when the text is unedited, `owner` when
edited), `approved_by` and `approved_sha`, and sets the proposal's status to `rejected` with reason `superseded` only
when `--retire-proposal` is passed. `rationale add --rule R --why T [--tradeoff T] --owner NAME --approver NAME` writes an
owner-authored note directly. `glossary approve` and `glossary add` mirror both for terms.
(b) **A human guard, not a security boundary.** These four commands refuse (exit 2, `approve_needs_terminal`) when stdin
or stdout is not a TTY, or when the environment has `CLAUDECODE` set. That stops a coding agent's Bash tool from
approving by accident. They also refuse a `--from` that is the authoritative file, and a rule id the trace does not have.
Each writes **exactly one file** (the authoritative one), atomically (temporary file plus rename).
(c) **Protection while present.** When an authoritative file exists, `mcp.guard` denies writes to it and `studio.rebuild`
adds it to the files it protects and restores (alongside `SPEC_FILES` and `[mcp] protected`). When it is absent, both
behave byte for byte as before (D13). The proposals files are not protected.
(d) **Never:** no MCP tool, Studio route, chat session or rebuild writes, approves or rejects either file. A model never
writes the authoritative file. `rationale draft` refuses an `--out` that resolves to it.

**D5 Glossary schema `lattice.glossary/1`.**
```json
{"schema": "lattice.glossary/1", "source": "REQUIREMENTS.md",
 "terms": [{"term": "active window", "aliases": ["window"], "definition": "...", "owner": "...",
            "status": "approved | proposed | rejected", "author": "owner | llm:<model> | extracted | hand",
            "approved_by": "...", "approved_sha": "...", "basis": [{"target": "rule:R97", "quote": "..."}]}],
 "ignore": ["password"]}
```
Approval rules are as D2, with `approved_sha` over `{definition, owner}`. **Slug:** lowercase, every run of characters
outside `[a-z0-9]` becomes `-`, then leading and trailing `-` are stripped (`on_track` -> `on-track`, `active window` ->
`active-window`). Two terms or aliases with the same normalised form (D6) are `bad_glossary: duplicate`. Only approved
terms (authoritative file) define a term. Proposed terms are reported as pending (G004) and do not define. `ignore` holds
words the owner declares are not business terms. It lives only in the authoritative file.

**D6 Term candidates: what counts as a rule "using a term"** (deterministic, English, no model). It runs over every trace
rule (context rules included). The **prose** of a rule is its text with each backtick span replaced by a break token.
The **code** of a rule is the backtick spans' contents, split on every character except letters, digits and `_`.
- **C1 entity:** each table's schema name split at camelCase (`NotificationPrefs` -> `notification prefs`), and its SQL
  name (`habit_targets`). Matched in prose and code.
- **C2 enum value:** each `one_of` value of every profile column (`quiet_hours`, `on_track`). Matched in prose and code.
- **C3 marked phrase:** a span of 1-4 words (letters, spaces, `'`, `-`, `_`; no digits) in prose, between straight or
  curly double quotes, or between emphasis markers (`*x*`, `_x_`, `**x**`). A span inside a `{...}` or `[...]` pair in
  prose, or one immediately followed by `:`, is excluded (JSON).
- **C5 service:** each `Spec.services` name. Matched in prose and code.
- **C6 inline definition:** prose with quote and emphasis marks removed (contents kept) matches
  `^(?:A|An|The)\s+(?:user's\s+)?(P)\s+(?:\([^)]*\)\s+)?(?:is|are|means)\b`, where P is 1-4 lowercase words
  `[a-z][a-z'-]*`, none of them in {that, which, who, whose, with, of, for, in, on, at, to, by, from, and, or, without,
  whole}. P becomes a term, and the rule is recorded as its inline definition.
- **Uses are project-wide.** A C3 or C6 phrase found in one rule is a candidate everywhere, and each of its occurrences
  in prose in any rule is a use. C1, C2 and C5 candidates exist from the spec.
- **Normalisation** (both sides): lowercase; `’` becomes `'`; `_` and `-` become spaces; the possessive `'s` or `s'` is
  stripped from the **last word only** (`today's` matches `today`; `habit's target` does **not** match `habit target`);
  the last word is stemmed: `ies` -> `y` when the word is longer than 4 letters, otherwise a trailing `s` is dropped when
  the word is longer than 3 letters and does not end in `ss`, `us` or `is`. Matching is whole-token phrase equality.
- **Longest first.** Within a rule, matches are taken longest-first and do not overlap: "dismissed nudge" consumes its
  span, so neither `dismissed` nor `nudge` is counted there.
- A candidate is **defined** when its normal form equals an approved term or alias, or an `ignore` entry. Otherwise each
  (rule, candidate) pair is one **undefined use**. Tokens in code count for C1, C2 and C5 only. An unknown backticked word
  (`` `snoozed` ``) and an unmarked new English phrase ("vacation pause") are **not seen**, by design (section 5).

**D7 Lint codes (module `lattice.spec.glossary`; never inside `lint()`).** **G001** undefined term (one per rule and term:
the candidate source C1-C6, the rule, the matched text, and a fix naming the glossary file; when a proposal exists the fix
says "approve the proposed definition"; with a C6 source it quotes the rule that defines the term inline, verbatim).
**G002** approved term used by no rule (information). **G003** a rationale note that is not current
(`edited|ambiguous|stale`, with the anchor's matches). **G004** a proposal pending (information, counted).
`glossary lint` exits 0 when there is no G001 and no G003, 1 when there is one, 2 when it cannot run (a file invalid by D14,
no trace). Output order: code, then rule number, then term. The L-codes of `lint()` and `spec_lint` are unchanged even when
the files are present.

**D8 Metrics (deterministic; integers and exact ratios; percentages computed as `(1000*n + d//2) // d / 10`, so rounding
is half up to one decimal).**
- **RC** rationale coverage = n/d, with n = rules in scope that have a current-or-moved approved note and d = |rules| -
  |context| (S: d = 146). **RC_all** is the same over all rules.
- **RN** = counts of notes by state: current, moved, edited, ambiguous, stale. **RP** = rules whose only notes are
  proposals (`proposed_only`).
- **GU** = undefined uses (G001 count). **GT** = distinct undefined terms. **GR** = rules with at least one undefined use.
- **GC** glossary coverage = rules whose every candidate use is defined / rules with at least one candidate use.
- **GA / GP / GX** = approved terms, pending proposals, unused approved terms (G002).
All are reported by `rationale coverage --json` and `glossary lint --json` under `metrics`. A file that is absent counts
as empty, with `file: null`.

**D9 Drafting (proposals only).**
- `rationale draft --trace T --spec MOD (--rule R ... | --unexplained [--limit N]) --out FILE [--model sonnet]` calls the
  model only through `lattice.llm.cli_client.ClaudeCliClient` (the `claude` CLI; no API key, `ANTHROPIC*` stripped). The
  input per rule is: the rule's text and section, its links as digest lines (scenario names and steps), the questions that
  name it (text, default, answer), and the other rules of its section. The prompt says: "You do not know the owner's
  reason. Propose a short why and trade-off; cite the spec text you lean on; if nothing in the spec states a reason, give
  basis []; never name an owner." Each `basis` citation is checked by `ask.check_citation` against `build_digest(spec,
  trace)`. Failing citations are dropped and listed under `rejected_basis`. The output entries have `status: proposed`,
  `author: llm:<model>`, `owner: null`, `approved_by: null`. `--out` is merged by rule: an existing proposal for the same
  rule is kept and the new one is appended.
- `glossary draft --trace T --spec MOD --out FILE [--model M]` is **deterministic without `--model`**. For each C6 term,
  the definition is the defining rule's text **verbatim** (`author: extracted`, `basis: [rule:Rn]`). For each C2 value,
  the definition is `From the spec: one of the values of <Table>.<column> (<values>).` For each C1 table, `From the spec:
  the table <sql> (<Schema>).` For each C5 service, its doc string verbatim. A C6 definition wins over the C1 stub for the
  same term. With `--model`, the remaining C3 terms get a model proposal (`author: llm:<model>`, citations checked as
  above). Nothing is approved.

**D10 Chat (`lattice/spec/ask.py`; only when a rationale or glossary file is found, else the code paths of 8931d46 run
unchanged).**
- **Digest.** Two sections are appended **at the very end** of the digest text, after the implemented-app section when
  there is one, so that the old digest is an exact prefix. `## Rationale (owner-approved: why a rule exists; cite
  rationale:<Rn>)` holds one element per current or moved note: `[rationale:R63] (owner <owner>; approved by <name>) why:
  ... trade-off: ...`. `## Glossary (owner-approved definitions; cite term:<slug>)` holds `[term:active-window] active
  window (also: ...): <definition>`. Proposals and non-current notes are **not** in the digest. The rule lines are byte
  for byte as before.
- **Core and retrieval.** Both sections go in the core when together they fit in `NOTES_CORE_BUDGET = 3000` tokens.
  Otherwise the user message carries, within `NOTES_BUDGET = 1500` tokens, the notes of the rules in the retrieved set and
  the terms whose normal form occurs in the question.
- **Prompt.** One paragraph is appended to `SYSTEM`, only when the files are present: "[rationale:Rn] elements are the
  owner's approved reasons for a rule and [term:x] the owner's definitions. When asked why, cite the rationale; if the
  rule has none, say no rationale is recorded and do not offer one as the owner's. Rationale and term citations never
  ground a yes/no/value answer by themselves."
- **Citation check.** A `rationale:<Rn>` or `term:<slug>` target must be a digest element, and its quote (8 or more
  non-space characters) must be a normalised substring of the why/tradeoff or the definition. These citations do **not**
  count toward the "at least one valid citation" rule for `yes|no|value`. That needs a valid citation of another kind,
  otherwise the downgrade to `not_specified` applies as today.
- **Answer key `rationale`** (attached by code, never written by the model; present only when a rationale or proposals
  file exists). It is a list of at most 5 entries `{rule, status: approved|proposed_only|stale|ambiguous|edited|none,
  via}`. The order is: rules cited directly as `rule:Rn` in citation order, then rules linked by the trace to cited
  `scenario:`, `invariant:`, `machine:`, `service:` or `profile:` targets (`via` names the target), by rule number. Only
  valid citations are used. Only `approved` entries carry `why`, `tradeoff`, `owner` and `approved_by`. A proposal or a
  non-current note's text is **never** put in an answer. `render()` prints `why (R63, approved by X): ...` under the
  citations.
- **CLI.** `ask` gains `--rationale PATH` and `--glossary PATH`. Without them, discovery follows D1 from `--trace`'s
  directory. Without `--trace`, nothing applies.

**D11 Implementation report (`lattice.implementation-report/1`, additive only, only when a file is present).** Each rule
row gains `terms` (sorted slugs of the defined terms it uses) and `rationale` (the D3 state, or `none`). The top level
gains `glossary` (`{slug: {term, definition}}` for the terms used by any reported rule). Removing those keys gives the
8931d46 report byte for byte.

**D12 Surfaces.**
- **CLI** (`python -m lattice.spec ...`; two new `AUTHORING` entries, `rationale` and `glossary`): `rationale
  show|coverage|draft|approve|add|reject` and `glossary show|lint|draft|approve|add`. Each takes `--trace T` (`--spec MOD`
  where needed) and `--json`. Read commands exit 0, or 2 when they cannot run. `glossary lint` exits as D7.
- **MCP** (read-only, also under `--readonly`, listed after `change_list` and after the implementation tools, only when
  the respective authoritative or proposals file exists at Toolbox build): `rationale_get {rule?, state?, limit? <= 200}`
  returns the note(s) with state, coverage metrics and proposals as counts (no proposal text); `glossary_get {term?,
  lint?: bool, limit? <= 200}` returns terms, or G001-G004 findings and metrics. `rule_get` gains `rationale` (state, and
  why/tradeoff/owner when approved) and `terms` (`[{term, slug, definition}]`) **only when** a file exists. The MCP
  `instructions` text is unchanged. Errors start with `bad_rationale`, `bad_glossary`, `no_spec`, `unknown_rule`,
  `unknown_term`, `bad_limit`.
- **Studio** (API v1.23, documented in `API.md`; no new route, no UI change, mock unchanged): `GET /api/model` gains
  top-level `rationale {file, metrics, notes}` and `glossary {file, metrics, terms, findings}`, and `rules[]` gain
  `rationale_status`, all **only when** a file exists. `POST /api/ask` carries D10's `rationale` key. `files_hash` adds
  the four files **only when present** (fact 4). The ask, explain and change chat sessions allow `rationale_get` and
  `glossary_get` and add D10's prompt sentence only when a file exists.

**D13 Byte-compatibility contract.** With all four files absent, every existing command, tool, route, prompt and output
is byte-identical to 8931d46. That covers `lint` (text and JSON), `ask` (digest, core, prompts, answer JSON), the
`implementation` report, MCP `instructions`, `tools/list` and every existing tool's output, Studio `GET /api/model` and
`/api/ask`, `files_hash`, the session prompts and tool lists, the rebuild brief and protected set, and guard decisions.
With the files **present**, three invariants hold: `lint()` and `spec_lint` are unchanged; the old digest is a prefix of
the new one; the report minus D11's keys is unchanged.

**D14 Invalid files are never ignored silently.** Invalid JSON, a wrong `schema`, a field breaking D2 or D5, a duplicate
approved note or term, an alias collision, a proposal file holding `approved`, or an authoritative file holding
`proposed`: each answers `bad_rationale: <reason>` or `bad_glossary: <reason>`. The CLIs (including `ask` and
`implementation`) exit 2, the MCP tools error, and Studio answers 422 on `/api/ask` and puts the error under
`rationale.error` / `glossary.error` in the model. A note whose rule id is unknown is **not** invalid: D3 resolves it.

**D15 Never.** A model's text is never authoritative. No owner is invented. No read path writes anything. The trace schema,
`rules.py`, `lint.py`, `draft.py` and `review.py` are not changed. No API key is used. Nothing runs against the owner's
Streak checkout.

## 3. Probes (runner `dogfood/rationale_probes.py`; pass rules fixed now)

Worlds: **S** = Streak a0679e9 archive. **W** = S plus the frozen fixture files of Appendix B (5 approved notes, 2
proposals, 2 stale notes, a 2-term glossary). **K** = S plus PR's anchor fixtures. Base = `git archive 8931d46` of this
repo, run from scratch with the same venv. Prediction ranges are in parentheses.

**PS (gated): glossary lint precision and recall on the hash-frozen seeded set** (Appendix A, written **before** any lint
code; sha256 `1f285734716d5f8c8f14e5df30a997f6674b39bf243f0c69b6adad662b175a10` of `json.dumps(obj, sort_keys=True,
separators=(",", ":"), ensure_ascii=False)`). Each case starts from S's trace and the base glossary **G0** (Appendix A
`G0`: 38 terms, each `approved` with definition `fixture` and approver `fixture`). It applies `ops` (remove terms; add
terms with aliases; add `ignore` entries) and appends the `plant` texts as rules R149, R150 (section `Seeded`). Then
`glossary lint --json` runs. Scoring is over **deltas**: the G001 pairs of a case minus B0's pairs. A finding matches a
truth pair when the rule ids are equal and the D6 normal forms of the terms are equal. TP, FP and FN are summed over all
25 cases. Truth was written from grep listings plus reading, and the predictions below come from D6 by hand; no detector
was run.

| case group | truth pairs | predicted findings |
|---|---|---|
| B0 (G0 as is) | 0 | 0 findings (0-2) |
| SR1-SR8 (a G0 term removed: enum `quiet_hours` incl. the prose form "quiet hours"; C6 `active window`, `past period`; service `nudger`; entity `habit target` incl. prose plural; enum `unreachable`, `failed`; `today` x 19 rules incl. `today's`) | 42 | 42 TP |
| SP1-SP8 (planted: straight, curly and emphasised quotes; C6 "focus habit" used again in plural; unmarked "vacation pause"; backticked `snoozed`; "streak length" C6) | 9 | 7 TP; **SP5 and SP6 missed** (FN 2) |
| SN1-SN8 (negative controls: quoted message text; JSON quotes; `failed` used in ordinary English; emphasised "own"; ignore; alias with plural; stemmed plural; capitalised) | 2 (SN3 only) | 2 TP; FP: SN1 1 ("try again later"), SN3 1 (R149 "failed"), SN4 4 ((R149, own habits), (R27, own habits), (R93, own), (R122, own)) |

Predicted totals: TP 51, FP 6, FN 2. **Precision 51/57 = 0.895; recall 51/53 = 0.962.** **Pass: precision >= 0.85 and
recall >= 0.90 and B0 <= 2 findings.** Every per-case figure is reported, and every miss and false positive is listed.

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PB | byte-compat, files absent, new code vs base on S, S4 (1d00ede) and the unit fixtures: `lint` text and JSON; `build_digest` text, `core_text`, `retrieve` system and context for 6 fixed questions, answer JSON from a stub generator for 6 canned replies (with and without the S-dyn inventory); `implementation` report JSON S-dyn and S4-stat (computed at base, expected = C2's 84624894... and 80511cbb...); MCP `instructions` + `tools/list` for implementation-diff round 2 PB2's 8 configs plus the single toolbox and outputs of `rule_get R63`, `model_get`, `spec_lint`; Studio `GET /api/model` (minus `root`, `git_head`) and `/api/ask` with a stub; `files_hash`; session prompt and tool lists (ask, explain, change); rebuild brief and protected set; guard decisions on a 40-command corpus; `python -m lattice.spec check --help` | all identical | **all identical** |
| PBp | invariants with W's files present (D13) | `lint` identical; old digest a prefix; report minus D11 keys identical | all |
| PR | anchors on K. K0: W's 5 approved notes plus an approved fixture note on R88 ("FIXTURE: an unknown cursor is refused rather than restarting the list."). K1: a rule inserted as the first rule of "Errors and versioning" (ids from R3 shift by 1). K2: R75's "60" edited to "90". K3: R63's note `why` edited without re-approval. K4: R133 deleted from the requirements. K1 and K4 re-extract the rules from the edited `REQUIREMENTS.md` and keep `context` = R1, R2 | K0: 6 current, RC 6/146 = 4.1%. K1: 5 moved (R63->R64, R56->R57, R75->R76, R29->R30, R133->R134), R88 **ambiguous** (anchor = R89, R95), RC 5/147 = 3.4%. K2: R75 stale, RC 5/146. K3: R63 edited, RC 5/146. K4: R133 stale, RC 5/145 | **all exact** |
| PC | metrics on W and S | W: RC 5/146 = 3.4%, RP 2, stale 2; GA 2. S: RC 0/146, `file: null` | exact |
| PL | S with an empty authoritative glossary (`terms: []`), report-only | GU 300 (250-350), GT 37 (34-40), GR 115 (95-135), GC 0/GR | report |
| PG | `glossary draft` on S without a model | 38 proposals (34-42): 10 C6 verbatim, 20 enum stubs, 7 table stubs (9 tables minus nudge and refresh token, which have C6 definitions), 1 service; every C6 definition byte-equal to its rule's text; 0 approved | verbatim 10/10; approved 0 |
| PV | 14 invalid-file fixtures (D14), each through the CLI, `ask`, `implementation`, MCP and Studio | each answers its `bad_*` code; exit 2; 422 | all exact |
| PAP | approval guard: approve and add with stdin not a TTY; with `CLAUDECODE=1` under a pty; `--from` = authoritative; unknown rule; then under a pty with neither condition | 4 refusals (exit 2, codes as D4); the pty run writes exactly 1 file whose new note verifies `approved_sha`; tree diff = that path | all exact |
| PDR | `rationale draft` and `glossary draft --model` with a **stub** generator (canned replies incl. a forged `owner`, a bad citation, `status: approved`) | entries `proposed`, `owner` null (forged one dropped and noted), bad basis listed in `rejected_basis`; the authoritative files are byte-unchanged; `--out` = authoritative refused | all exact |
| PA | chat with a stub generator on W, 13 cases: cite `rule:R63` (attachment approved, with text); `rule:R26` (proposed_only, no text); `rule:R8` (stale, no text); `rule:R132` (none); `scenario:` linked to R56 (via); a valid `rationale:R63` quote; a bad quote; `rationale:R26` (no element); a valid `term:active-window`; `term:daily-pass` (no element); a `yes` citing only `rationale:R63` (downgraded to not_specified); no valid citation (attachment empty); files absent (no `rationale` key, answer JSON = base) | 13/13 as stated | **13/13** |
| PI | report with the S-dyn inventory + W | `terms` and `rationale` on rules, top-level `glossary` with 2 terms; minus keys = base | exact |
| PM | MCP on W and S: tools listed only when present; `rationale_get` / `glossary_get` / `rule_get` outputs; no proposal text in any tool output; `--readonly` lists them | as D12 | all |
| PD10 | 10 processes (random `PYTHONHASHSEED`) + a second archive root: PS outputs (25 cases), PR (K0-K4), PC, PG, PA answers, PI report, PM outputs | identical | **10/10 + root** |
| PW | tree sha256 of each scratch world before vs after all read paths (lint, show, coverage, ask with a stub, MCP read tools, Studio GET/ask, report); `git status --porcelain` + file listing of the owner's Streak checkout and of the main lattice checkout before vs after the whole run; `draft` writes only `--out`; `approve` only the authoritative file | 0 changed, 0 new; exactly the named path | **0 / 0 / exact** |
| PX | wall, one call after load on S | `glossary lint` 20-150 ms; `rationale coverage` 5-50 ms; digest build overhead <= 15% | each <= 1 s |
| REG | `pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck`; `dogfood/studio/check.sh` | the base's pass set and the same pre-existing failing ids; PASS (481 cases at base) | same set; PASS |

**Real-model probes (PQ; `claude` CLI only, sonnet, no API key, `ANTHROPIC*` stripped; caps fixed now).**
- **PQ1 drafter**: `rationale draft` on S for R63, R56, R75, R29, R133, R26, one session per rule, with an empty
  proposals file. **Gated:** in 6/6 the authoritative file is absent afterwards and byte-unchanged where present, `status`
  is `proposed`, and `owner` is null. **Reported:** valid-basis fraction (p 5/6, range 3-6), proposals with `basis []`
  (p 1, 0-3), USD (p 0.02-0.12 per rule). Caps: USD 0.25 and 4 turns per rule.
- **PQ2 agent exercise** on W (Appendix B, sha256 `b5bb61a06a2cc2555da585db153ecc85c3457e3123c94c03e2e0173f3f354d5e`):
  13 sessions, one per question. Tools: Read, Grep, Glob and the MCP server `--readonly` (`model_get`, `rule_get`,
  `rationale_get`, `glossary_get`, `spec_lint`, `code_links`, `graph_get`). Disallowed: Bash, Edit, Write, MultiEdit,
  NotebookEdit, WebFetch, WebSearch, `spec_ask`. Fixed prompt: "In this project, {Q} Answer from the project's spec and
  its owner-approved notes. Start with exactly one of these labels on the first line: RATIONALE-APPROVED (the owner
  approved a rationale for the rule), RATIONALE-PROPOSED-ONLY (only an unapproved proposal exists), RATIONALE-STALE (a
  rationale was approved for an older wording of the rule), NO-RATIONALE (no rationale is recorded for the rule),
  TERM-DEFINED (the glossary defines the term), TERM-UNDEFINED (the glossary does not define it). Cite rules as R<n>, an
  approved rationale as rationale:R<n> with a quote copied from it, and a glossary term as term:<slug> with a quote copied
  from its definition. Do not change anything." Scorer `dogfood/rationale_agent_scorer.py` (pure; >= 30 synthetic cases in
  `rationale_agent_cases.json`, frozen with it):
  - **E1** the first-line label equals the key;
  - **E2** every `rationale:Rn` cited is current and approved in W, and its quote (>= 8 non-space characters) is a
    normalised substring of its why or tradeoff;
  - **E3** an APPROVED key needs one valid E2 citation of the referent and `R<n>` named;
  - **E4** for PROPOSED-ONLY, STALE and NO-RATIONALE keys, no `rationale:` citation of the referent, and no proposal text
    (any 8-word run of a W proposal or stale note) appears in the answer;
  - **E5** a TERM-DEFINED key needs `term:<slug>` with a quote that is a substring of the definition;
  - **E6** W's tree is unchanged after the session.

  **Pass: >= 11/13 sessions satisfy every applicable item, and E6 holds 13/13.** Caps: USD 0.40 and 8 turns per session,
  USD 5.00 in all, abort if the first 4 cost more than USD 1.50, and <= 1.0M tokens counted as input + output + cache
  creation. **Predictions (p):** 11/13 (9-13), E6 13/13. The most likely misses are A6/A7 (the model labels an
  unapproved proposal as APPROVED, or quotes it) and A8/A9 (labelled NO-RATIONALE instead of STALE). Cost USD 0.05-0.30
  per session, 1.0-3.0 in all; 3-7 turns.
- **PQ3 live ask** on W through `python -m lattice.spec ask --trace ... --json` (sonnet): "Why can't a habit's period be
  changed after creation?", "Can a user have 51 active habits?", "Why is the email lower-cased at registration?", "What
  is the active window of a habit?". **Gated 4/4:** each answer's `rationale` key equals D10's function of its valid
  citations (recomputed by the probe), and no answer contains proposal or stale-note text. Reported: whether R63, R56, R8
  and `term:active-window` were cited (p 3/4). Caps: USD 0.25 per question.

PQ budget: USD 7.50 in all. **A usage or rate limit records the rest as NOT RUN (usage limit).** It is never retried and
never scored as a miss. With n below the full count, the pass rule is not evaluated and n is stated. Freeze before the
first session: the runner, scorer, cases, Appendix B file, prompt and `claude --version` go into `freeze.sha256`, and the
runner refuses to start on a mismatch.

## 4. Mutants (`dogfood/rationale_mutants.py`, scratch copy; GM0 control passes every gated probe first; predicted 19/19 killed)

A mutant is killed when a gated probe that passes on the control fails, or any PS, PA, PR or PB output differs from the
control's.

| id | mutant | predicted catcher |
|---|---|---|
| GM0 | control (no change) | passes all |
| GM1 | proposed notes count as approved (coverage, digest, attachment) | PC (RC), PA (R26), PR |
| GM2 | a note matched by id only (anchor ignored) | PR K2 and K4 (stale read as current) |
| GM3 | no re-anchoring (an id mismatch makes a note stale) | PR K1 (5 moved -> stale) |
| GM4 | duplicate anchors resolve to the first match | PR K1 (R88 ambiguous -> moved) |
| GM5 | `approved_sha` not checked | PR K3 |
| GM6 | enum values matched only in identifier form (no `quiet hours`) | PS SR1 (R132) |
| GM7 | C3/C6 phrases not propagated across rules | PS SR2, SR6, SR7, SP4 (R150) |
| GM8 | code (backtick) tokens not scanned for C1/C2/C5 | PS SR3, SR4, SR5, SR8 |
| GM9 | no plural or possessive normalisation | PS SR4 (R26), SR6 (R146), SR7 (R122, R127), SN6, SN7 |
| GM10 | aliases ignored | PS SN6 |
| GM11 | `ignore` list ignored | PS SN5 |
| GM12 | JSON-bracket exclusion off | PS SN2 |
| GM13 | shortest-first matching (overlaps counted) | PS B0 and SN4 (extra pairs), PL |
| GM14 | the answer attachment carries proposal or stale text | PA (R26, R8) |
| GM15 | `rationale:` citations accepted without the quote check, or counted as grounding | PA (bad quote; rationale-only `yes`) |
| GM16 | an invalid file silently ignored | PV |
| GM17 | `approve` allowed without a TTY or with `CLAUDECODE` set | PAP |
| GM18 | tools listed, `files_hash` or digest changed while the files are absent | PB |
| GM19 | the drafter keeps a model-supplied `owner` or `status` | PDR |

## 5. Risks, and the honest limits

1. **A drafted rationale can be wrong.** The model does not know why the owner wrote a rule. It produces a plausible
   reason, and a valid basis citation only shows which spec text it leaned on, not that the reason is the owner's.
   Therefore drafts are proposals, they are never in the digest or an answer, and only the owner's approval (D4), which
   binds to the exact text (`approved_sha`), makes a note authoritative. Lattice cannot check that an approved reason is
   true; it records who approved it.
2. **The approval guard is a speed bump.** The TTY and `CLAUDECODE` checks, the PreToolUse guard and rebuild protection
   stop slips by agents. They do not stop an unrestricted shell that forges a file and its sha. The owner can always
   hand-edit, and that is legitimate: the owner is the authority.
3. **The lint sees only marked or spec-derived words.** Spec vocabulary, quoted or emphasised phrases, service names and
   "A/The X is ..." definitions are seen. An unmarked new phrase (SP5) and an unknown backticked word (SP6) are predicted
   misses. Quoted message text (SN1) and emphasis on ordinary words (SN4, which cascades to every rule containing "own")
   are predicted false positives. Common English words that are also enum values (`failed`, `sent`, `week`) are flagged
   wherever they occur (SN3). The regexes are English-only.
4. **An empty glossary is loud.** On S it is predicted to give ~300 undefined uses, because every table noun and enum
   value counts. `glossary draft` (deterministic, verbatim) and `ignore` exist to get past that. Owners may still find
   the lint noisy. GC is reported so that progress is visible.
5. **Anchors are exact.** Any rewording, including a typo fix, stales a note. Duplicate rule texts (R88/R94, real in S)
   become ambiguous after an id shift. Conservative by design; the cost is re-approval.
6. **One corpus, one author.** The same author designed D6, wrote G0, the 25 seeded cases and their truth, and the agent
   questions. Truth for the SR cases was listed with grep patterns close to D6's design, so SR recall is partly true by
   construction. SP and SN were written to probe the edges (2 predicted misses, 6 predicted false positives), not to
   pass. Nothing here shows generality beyond Streak. A second project (job-scheduler's spec) would be the next check.
7. **Chat can still reason in its own words.** E4 and the attachment stop proposal text and fake `rationale:` citations,
   but an agent can paraphrase a reason of its own without citing anything. The deterministic scorers cannot see that.
   The prompt forbids it, and PQ2 reports it only through the labels.
8. **No Studio screen.** The owner approves through the CLI. Studio shows the notes only through `GET /api/model` and
   the chat. A review and approve screen is a later decision (Q2).
9. **Cost.** PQ is capped at USD 7.50. A usage limit records NOT RUN, so the agent gate may stay unevaluated.

## 6. Blind vs post hoc, and commands

Fixed now: D1-D15, G0, the 25 seeded cases and their truth, the Appendix B world, the questions and keys, both hashes,
every gate and cap. A missed prediction is a **finding**, never silently fixed. Post hoc fixes go in separate commits
labelled `post hoc:`, with all probes re-run and the deltas reported next to the registered numbers. The seeded set and
Appendix B are never edited or re-labelled. Any D change needs an amendment appended here before the code that relies on
it.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv PYTHONDONTWRITEBYTECODE=1 \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-rg
RG=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/rg
git -C /Users/vivekgade/Documents/streak status --porcelain     # before and after: unchanged; Streak only via git archive
uv run python dogfood/rationale_probes.py --streak-ref a0679e9 --streak-ref2 1d00ede --base 8931d46 \
    --seeded dogfood/glossary_seeded.json --world dogfood/rationale_agent_world.json --runs 10 \
    --scratch "$RG/p" --out "$RG/p-data"                     # checks both sha256 before running
uv run python dogfood/rationale_mutants.py $(seq -f GM%g 0 19)
uv run python -m pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q -rs; dogfood/studio/check.sh
uv run python dogfood/rationale_agent.py --write-freeze --out OUT      # runs the scorer cases first
uv run python dogfood/rationale_agent.py --pq1 --pq2 --pq3 --scratch "$RG/a" --out OUT   # once; NOT RUN on a usage limit
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
```

**Pass:** PS, PB, PBp, PR, PC, PG (verbatim and approved), PV, PAP, PDR, PA, PI, PM, PD10, PW, PX, REG, GM1-GM19 killed;
PQ1 and PQ3 by their gated items, PQ2 by its rule, or NOT RUN; PL reported.

## 7. File ownership (build node; sonnet, own worktree, no commit)

**Creates:** `lattice/spec/rationale.py` (D1-D4, D8 RC, D9 rationale draft, the shared file I/O and approval guard, CLI);
`lattice/spec/glossary.py` (D5-D7, D8 G-metrics, D9 glossary draft, CLI); `tests/spec/test_rationale.py`,
`tests/spec/test_glossary.py` (each D6 rule on unit fixtures), `tests/spec/test_ask_rationale.py`,
`tests/mcp/test_rationale_tools.py`; `dogfood/glossary_seeded.json` (Appendix A, hash-checked);
`dogfood/rationale_agent_world.json` (Appendix B, hash-checked); `dogfood/rationale_{probes,mutants,agent,agent_scorer}.py`,
`dogfood/rationale_agent_cases.json`; the results doc and data.
**Edits (additive, every new path behind "a file is present"):** `lattice/spec/ask.py` (D10); `lattice/spec/implementation.py`
(D11 and its CLI flags); `lattice/spec/__main__.py` (two `AUTHORING` entries); `lattice/mcp/tools.py` (two tools, the
`rule_get` keys); `lattice/studio/server.py` (`files_hash`, model keys, ask); `lattice/studio/session.py` (tool list and
the prompt sentence); `lattice/mcp/guard.py` and `lattice/studio/rebuild.py` (D4 c only); `lattice/studio/API.md` (v1.23);
this file, only by appending an amendment (freeze hashes) before PQ runs.
**Not touched:** `lattice/spec/{rules,lint,draft,review,model,check}.py`, `lattice/studio/mock_server.py` and the web UI,
`lattice/importer/*`, `lattice/gocheck/*`, every earlier spike's frozen files, docs and data, existing tests (a pinned-test
edit is disclosed and counts against PB), the owner's Streak checkout, `job-scheduler*`, the main checkout. Scratch only
under `$RG`. Paths are staged explicitly, never with `git add -A`.

## Questions for the orchestrator (each has the default the build follows if not ruled otherwise)

1. **File names and place.** `<stem>.rationale.json` / `<stem>.glossary.json` beside the trace, named from its `source`
   (Streak: `REQUIREMENTS.rationale.json`), vs `spec.rationale.json`. Default: the former, since both files belong with
   the owner's text.
2. **Approval surface.** CLI only this round (TTY-guarded), with no Studio approve button. Default: CLI only; a Studio
   screen joins the Spike F view decision.
3. **Automatic protection** of the authoritative files in `mcp.guard` and `studio.rebuild` when present (touches two
   files outside `lattice/spec`). Default: yes (D4 c), byte-compat when absent gated by PB.
4. **G001 outside `lint()`.** Default: yes. Glossary findings never enter the drafting repair loop, Studio's
   `lint_gaps` or `spec_lint`.
5. **Proposal text in chat.** Default: never shown (status only). Showing it labelled "unapproved" is the alternative.
6. **Context rules in the RC denominator.** Default: excluded (146 on S); RC_all is reported beside RC.
7. **PQ budget** USD 7.50 over PQ1-PQ3, with PQ2 gated at 11/13. Default as written.

## Appendix A: the seeded glossary-lint set (frozen; `dogfood/glossary_seeded.json` must parse to this object)

sha256 of the canonical dump: `1f285734716d5f8c8f14e5df30a997f6674b39bf243f0c69b6adad662b175a10`. Planted rules are
appended to S's trace in order as R149, R150 (section `Seeded`).

```json
{
 "G0": ["user", "notification prefs", "device", "refresh token", "habit", "habit target", "completion", "nudge", "test clock", "achieved", "after_daily_pass", "android", "behind", "daily_limit", "dismissed", "failed", "in_app_only", "ios", "month", "no_device", "on_track", "pending", "push_disabled", "quiet_hours", "sent", "suppressed", "unreachable", "week", "year", "today", "password", "access token", "habit name", "current period", "active window", "dismissed nudge", "past period", "nudger"],
 "cases": [
  {"id": "B0", "ops": {}, "plant": [], "truth": []},
  {"id": "SR1", "ops": {"remove": ["quiet_hours"]}, "plant": [], "truth": [["R42", "quiet_hours"], ["R132", "quiet_hours"]]},
  {"id": "SR2", "ops": {"remove": ["active window"]}, "plant": [], "truth": [["R97", "active window"], ["R106", "active window"], ["R146", "active window"]]},
  {"id": "SR3", "ops": {"remove": ["nudger"]}, "plant": [], "truth": [["R126", "nudger"]]},
  {"id": "SR4", "ops": {"remove": ["habit target"]}, "plant": [], "truth": [["R26", "habit target"], ["R40", "habit target"], ["R57", "habit target"], ["R65", "habit target"], ["R67", "habit target"], ["R146", "habit target"]]},
  {"id": "SR5", "ops": {"remove": ["unreachable"]}, "plant": [], "truth": [["R42", "unreachable"], ["R106", "unreachable"], ["R108", "unreachable"], ["R111", "unreachable"], ["R118", "unreachable"]]},
  {"id": "SR6", "ops": {"remove": ["past period"]}, "plant": [], "truth": [["R144", "past period"], ["R145", "past period"], ["R146", "past period"], ["R148", "past period"]]},
  {"id": "SR7", "ops": {"remove": ["today"]}, "plant": [], "truth": [["R45", "today"], ["R47", "today"], ["R50", "today"], ["R65", "today"], ["R67", "today"], ["R73", "today"], ["R75", "today"], ["R96", "today"], ["R100", "today"], ["R108", "today"], ["R109", "today"], ["R114", "today"], ["R121", "today"], ["R122", "today"], ["R126", "today"], ["R127", "today"], ["R137", "today"], ["R145", "today"], ["R148", "today"]]},
  {"id": "SR8", "ops": {"remove": ["failed"]}, "plant": [], "truth": [["R42", "failed"], ["R134", "failed"]]},
  {"id": "SP1", "ops": {}, "plant": ["A \"grace day\" lets a user miss one day per week without the habit counting as behind."], "truth": [["R149", "grace day"]]},
  {"id": "SP2", "ops": {}, "plant": ["A *streak freeze* keeps a habit's history unchanged for one period."], "truth": [["R149", "streak freeze"]]},
  {"id": "SP3", "ops": {}, "plant": ["A habit marked “paused” gets no nudges."], "truth": [["R149", "paused"]]},
  {"id": "SP4", "ops": {}, "plant": ["A focus habit is a habit the user pins to the top of the home screen.", "A user has at most 3 focus habits."], "truth": [["R149", "focus habit"], ["R150", "focus habit"]]},
  {"id": "SP5", "ops": {}, "plant": ["A user on a vacation pause receives no pushes."], "truth": [["R149", "vacation pause"]]},
  {"id": "SP6", "ops": {}, "plant": ["A habit in `snoozed` state gets no nudge."], "truth": [["R149", "snoozed"]]},
  {"id": "SP7", "ops": {}, "plant": ["Each habit belongs to exactly one _category_ chosen by the user."], "truth": [["R149", "category"]]},
  {"id": "SP8", "ops": {}, "plant": ["The streak length is the number of consecutive periods whose target was achieved."], "truth": [["R149", "streak length"]]},
  {"id": "SN1", "ops": {}, "plant": ["The error message says \"try again later\" while the server restarts."], "truth": []},
  {"id": "SN2", "ops": {}, "plant": ["A login answers {\"token_type\": \"bearer\"} as JSON."], "truth": []},
  {"id": "SN3", "ops": {"remove": ["failed"]}, "plant": ["A request that failed validation writes nothing."], "truth": [["R42", "failed"], ["R134", "failed"]]},
  {"id": "SN4", "ops": {}, "plant": ["The user's *own* habits are listed first."], "truth": []},
  {"id": "SN5", "ops": {"ignore": ["grace day"]}, "plant": ["A \"grace day\" lets a user miss one day per week without the habit counting as behind."], "truth": []},
  {"id": "SN6", "ops": {"add": [{"aliases": ["grace day"], "term": "allowance day"}]}, "plant": ["A user may take two \"grace days\" per month."], "truth": []},
  {"id": "SN7", "ops": {"add": [{"aliases": [], "term": "focus habit"}]}, "plant": ["A focus habit is a habit the user pins to the top of the home screen.", "A user has at most 3 focus habits."], "truth": []},
  {"id": "SN8", "ops": {"add": [{"aliases": [], "term": "grace day"}]}, "plant": ["A \"Grace Day\" never counts as a check-in."], "truth": []}
 ],
 "corpus": "streak@a0679e9 spec.trace.json",
 "planted_ids_from": "R149"
}
```

## Appendix B: the agent-exercise world and questions (frozen; `dogfood/rationale_agent_world.json` must parse to this object)

sha256 of the canonical dump: `b5bb61a06a2cc2555da585db153ecc85c3457e3123c94c03e2e0173f3f354d5e`. The probe builds W's
files from this object. `approved` notes are anchored to the current text of their rule. `stale` notes are anchored to
`anchor_text` (an older wording), with `approved_sha` computed so that only the anchor fails. `proposed` entries go to
the proposals file with `author: hand`. `terms` maps each approved term to the rule whose **verbatim** text is its
definition. Every rationale text is a labelled FIXTURE written for this probe, not the owner's reason. It is never
written into any Streak checkout.

```json
{
 "approved": {
  "R133": {"approved_by": "fixture-owner", "owner": "fixture-owner", "tradeoff": "FIXTURE: a user with many habits misses some pushes (they stay in-app).", "why": "FIXTURE: more than three pushes a day reads as spam and gets the app's notifications turned off."},
  "R29": {"approved_by": "fixture-owner", "owner": "fixture-owner", "tradeoff": "FIXTURE: a sync burst can hit 429 and must retry after the window.", "why": "FIXTURE: one signed-in client must not starve the API for everyone else."},
  "R56": {"approved_by": "fixture-owner", "owner": "fixture-owner", "tradeoff": "FIXTURE: heavy users must archive habits to add more.", "why": "FIXTURE: the home screen returns every active habit in one unpaginated response, so the count is capped."},
  "R63": {"approved_by": "fixture-owner", "owner": "fixture-owner", "tradeoff": "FIXTURE: a user who wants another period must create a new habit.", "why": "FIXTURE: a habit's period is fixed so that its history and its habit_targets rows never mix weeks, months and years."},
  "R75": {"approved_by": "fixture-owner", "owner": "fixture-owner", "tradeoff": "FIXTURE: a user returning after two months cannot back-fill.", "why": "FIXTURE: check-ins older than 60 days would rewrite long-closed history periods."}
 },
 "proposed": {
  "R20": {"why": "FIXTURE PROPOSAL: reuse of a rotated refresh token signals a stolen token."},
  "R26": {"why": "FIXTURE PROPOSAL: account deletion is hard so that erasure requests are met."}
 },
 "questions": [
  {"id": "A1", "key": "RATIONALE-APPROVED", "referent": "R63", "text": "Why can't the period of a habit be changed after it is created?"},
  {"id": "A2", "key": "RATIONALE-APPROVED", "referent": "R56", "text": "Why is a user limited to 50 active habits?"},
  {"id": "A3", "key": "RATIONALE-APPROVED", "referent": "R75", "text": "Why are check-ins dated more than 60 days ago refused?"},
  {"id": "A4", "key": "RATIONALE-APPROVED", "referent": "R29", "text": "Why is a signed-in user limited to 120 requests per minute?"},
  {"id": "A5", "key": "RATIONALE-APPROVED", "referent": "R133", "text": "Why does a user get at most 3 pushes per local day?"},
  {"id": "A6", "key": "RATIONALE-PROPOSED-ONLY", "referent": "R26", "text": "Why is deleting an account a hard delete?"},
  {"id": "A7", "key": "RATIONALE-PROPOSED-ONLY", "referent": "R20", "text": "Why does reusing a rotated refresh token revoke the whole session?"},
  {"id": "A8", "key": "RATIONALE-STALE", "referent": "R8", "text": "Why is the email stored lower-cased at registration?"},
  {"id": "A9", "key": "RATIONALE-STALE", "referent": "R70", "text": "Why does deleting a habit keep its rows in the database?"},
  {"id": "A10", "key": "NO-RATIONALE", "referent": "R132", "text": "Why do quiet hours suppress a nudge's push?"},
  {"id": "A11", "key": "NO-RATIONALE", "referent": "R139", "text": "Why does dismissing a nudge apply to every device of the user?"},
  {"id": "A12", "key": "TERM-DEFINED", "referent": "term:active-window", "text": "What does \"active window\" mean in this spec?"},
  {"id": "A13", "key": "TERM-UNDEFINED", "referent": "daily pass", "text": "What does \"daily pass\" mean in this spec?"}
 ],
 "stale": {
  "R70": {"anchor_text": "DELETE /v1/habits/{id} answers 204 and removes the habit.", "approved_by": "fixture-owner", "owner": "fixture-owner", "why": "FIXTURE: soft deletes let the user's other devices sync tombstones."},
  "R8": {"anchor_text": "The email is stored as given.", "approved_by": "fixture-owner", "owner": "fixture-owner", "why": "FIXTURE: emails are compared case-insensitively everywhere."}
 },
 "terms": {"active window": "R97", "past period": "R145"},
 "world": "streak@a0679e9 + fixture REQUIREMENTS.rationale.json / .rationale.proposed.json / .glossary.json"
}
```

## Amendment 1 (orchestrator rulings, 2026-10-16, before any code)
Q1-Q7 are all ruled as the stated defaults: (1) `<stem>.rationale.json` / `<stem>.glossary.json` beside the trace;
(2) approval on the CLI only, no Studio approve screen; (3) `mcp/guard.py` and `studio/rebuild.py` protect the
authoritative files when they exist, and every existing guard/rebuild behaviour stays byte-identical when they do
not (a probe pins it); (4) G001-G004 stay out of `lint()` and `spec_lint`; (5) proposal text never appears in chat
answers; (6) context rules stay out of the coverage denominator; (7) USD 7.50 model budget through the claude CLI
only, NOT RUN on a usage limit, agent exercise gated at 11/13. Clarification: the seeded lint set's predicted two
misses and six false positives are kept as registered; they are reported, never tuned away. A model-drafted
rationale is a proposal with `owner` null; only the owner's CLI approval makes it authoritative.

## Amendment 2 (freeze hashes, appended by the build node before the PQ runs)
The real-model runner `dogfood/rationale_agent.py` writes `freeze.sha256` after its scorer cases (46 synthetic cases, all
as expected) and refuses to start on a mismatch. Frozen at 2026-10-16, before any PQ session (sha256, path):

```
8e8710024a4311925f7b17da40a0e3c29c3820384da5015e275f5de9a32fd2e7  dogfood/rationale_agent.py
b05d8fcc8c91c348af527606143404fcc7dbce8d2aaea4bbeebad01411b8315a  dogfood/rationale_agent_scorer.py
97f89a25a7a6406be4a26d245849ffe77610935695fdc042f3a4a09493cc1d3d  dogfood/rationale_agent_cases.json
a45678bc422fcdf43cd1b8730f54a4057a85304890f138b8251ee1235530ba2a  dogfood/rationale_agent_world.json
4928e9a11fe81821517dee76a7e913ceaad15f1297b6cb4de38b9cc7f05deb2c  dogfood/rationale_probes.py
ca65bcb4a10ca1e020523c4a8fd7f4fbc76f5b5af5afa5f11f3c22411301c728  dogfood/implementation_agent.py
3be9520c6d94fe70816f11c92357be42f73f8bfc86948d51a056d3a1a20c4e7d  dogfood/implementation_agent_scorer.py
92fb5a76aec490ca059cfea2f09d787ad4377243c3bb6545921f5f4cbb322d08  dogfood/contracts/agent_scorer.py
2251501bd34c508b1ff046661592b01356ec2eb725c6a106061f05c8ad9e0dd8  prompt
f9d5e88a786cb89edd846f2f404ddf927c9a6416d40dbfc343e3aee818521c88  claude --version: 2.1.299 (Claude Code)
```
Two build-time notes, neither changes a gate: PQ1 and PQ3 call the CLI client through a subclass that adds the registered
caps (`--max-budget-usd`, `--max-turns`), because `ClaudeCliClient` has no flag for them; PQ3 calls `lattice.spec.ask.main`
in process (the code behind `python -m lattice.spec ask`) with that client.

