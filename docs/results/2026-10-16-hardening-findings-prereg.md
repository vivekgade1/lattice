# Pre-registration: HD, hardening of the open G9/G3 findings (credential scrub, `entry` as CODE, `allow_writes`)

Written 2026-10-16 on `np-hd` (worktree `/Users/vivekgade/Documents/lattice-hd`, from `next-phases` `bb3ff0c`, which
contains np-mcphttp and np-market), before any code. Run so far: reading; `pytest --collect-only` (test_cli_client 44,
test_mcp_intake 82, test_mcp_intake_http 46, tests/modules 107, tests/security/test_module 3); `cli_env`/`describe()`
on bb3ff0c in a REPL; `claude auth status --json` (2.1.299, no model call) under planted variables. Results:
`docs/results/2026-10-16-hardening-findings-results.md`, data `docs/results/data/2026-10-16-hardening-findings/`. A
failed prediction is a **finding**, never fixed silently. No API key, no LLM call, no real connector.

## 1. Status (code evidence at bb3ff0c)

- **HD-1 (G9 F2).** `cli_client.py:110-124`: `_SCRUBBED_ENV` = `ANTHROPIC_API_KEY`, `_AUTH_TOKEN`, `_BASE_URL`;
  prefix `CLAUDE_CODE_USE_`. Measured: of 23 planted names `cli_env` drops only those 6; `ANTHROPIC_CUSTOM_HEADERS`,
  `_FOUNDRY_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, `ANTHROPIC_VERTEX_PROJECT_ID`, `ANTHROPIC_MODEL`, any other
  `ANTHROPIC_*` pass. Callers: `session._child_env`, `mcp_intake.discovery_env`, `ClaudeCliClient.generate`,
  `rebuild.gate_env`, `plan.py:1853`; the last two ALREADY drop every `ANTHROPIC_*`, and real rebuild/plan agents run
  without any. `dogfood/contracts/draft_agent.py:10` already claims "`cli_env` strips every ANTHROPIC* variable".
- **Login needs (measured, 2.1.299).** `env -i HOME PATH USER TMPDIR claude auth status` = `loggedIn true, claude.ai,
  firstParty` (Keychain): no `ANTHROPIC_*`/`CLAUDE_CODE_*` name is needed. Plus the wide three: unchanged (they expose,
  they do not switch). A fake `ANTHROPIC_API_KEY` gives `api_key`; `ANTHROPIC_AUTH_TOKEN` or `CLAUDE_CODE_OAUTH_TOKEN`
  gives `oauth_token`; `CLAUDE_CODE_USE_BEDROCK=1` gives `third_party/bedrock`.
- **HD-2 (G3 F2).** `modules/__init__.py:393-430` `describe()` tags an engine adapter `(CODE: imported into the checker
  when the engine is used)`; `Manifest.entry` shows only inside the verbatim manifest text. The repo's one entry
  module is `lattice/security/module` (`entry = "lattice.security"`). **Premise contradiction:** no host code reads
  `Manifest.entry` (`lattice attack` dispatches statically, `cli.py:371`; no custom module dir goes on `sys.path`), so
  an `entry` is declared Python that nothing imports today, and "imported into the checker when the engine is used"
  is the adapter's wording (an engine module cannot have an entry, `read_manifest:206`).
- **HD-3 (G9 F3).** `mcp_intake.py:391` `"writes_allowed": _writes_ok(...)` ignores status, while `external_tools:340`
  opens writes only when `connected`; the UI (`chat.js:1329`) then says "writes allowed in change" for a failed
  server whose writes are denied. Pinned by `R_NOTION_W`, `R_LINEAR_AUTH_W` (in `A3`, `B1`) and line 1152.
- **HD-4 (G9 F5).** `_writes_ok:316-319` matches display name or prefix form exactly; a wrong-case or misspelt name
  opens nothing and logs nothing (pinned at line 1160).
- **Left untouched.** G9 F1 (stub note wording) is the API.md stub body, `MCP_STUB` and dogfood R76/R77; the stub is
  TEST-ONLY and its turns fail closed, so a wording edit buys no safety and churns a pinned contract. G9 F4 (verb
  classifier) is the API.md first-word table; widening it changes every deployment's allow-list and needs a corpus of
  real connector tool names: its own pre-registration.

## 2. Decisions (with their edge cases)

**D1. Scrub rule (HD-1).** Drop a name that starts with `ANTHROPIC_` or `CLAUDE_CODE_USE_`, or is exactly
`AWS_BEARER_TOKEN_BEDROCK`; case-sensitive (the CLI reads upper case only). New code (body otherwise unchanged):
`_SCRUBBED_ENV = ("AWS_BEARER_TOKEN_BEDROCK",)`, `_SCRUBBED_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_")`, filter
`k not in _SCRUBBED_ENV and not k.startswith(_SCRUBBED_PREFIXES)`. A prefix for `ANTHROPIC_*`: every Anthropic
credential/endpoint the CLI reads lives there (`_FOUNDRY_*`, `_VERTEX_*`, `_BEDROCK_BASE_URL`, `_CUSTOM_HEADERS`,
future names) and plan/rebuild prove agents run without them. A precise list for cloud names: `AWS_ACCESS_KEY_ID`,
`AWS_PROFILE`, `GOOGLE_APPLICATION_CREDENTIALS` cannot route `claude` without the (dropped) `CLAUDE_CODE_USE_*`
switch, `gate_env` passes them to project gates that may need them, and `~/.aws` is readable by the child anyway.
**Kept, deliberately:** `HOME`, `PATH`, `USER`/`LOGNAME`, `TMPDIR` (Keychain login), `CLAUDE_CONFIG_DIR`,
`CLAUDE_CODE_OAUTH_TOKEN` (the `setup-token` subscription login: bills the plan, not the API), proxy/CA variables,
generic cloud credentials, lowercase `anthropic_*`, bare `ANTHROPIC`. Side effect: `ANTHROPIC_MODEL`/`_DEFAULT_*_MODEL`
stop reaching the child (callers pass `--model`). Out of scope: `CLAUDECODE`, `CLAUDE_CODE_MESSAGING_TOKEN` (seen here).

**D2. `entry` announced as CODE (HD-2).** In `describe()`, right after the `requires:` line (no engine block can
coexist): `if m.entry is not None:` append `f"  entry:    {shown} (CODE: a Python module, main(argv); not imported by
install)"`, `shown = m.entry if _ENTRY_NAME.fullmatch(m.entry) else json.dumps(m.entry)`, `_ENTRY_NAME =
re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")`. A non-identifier entry (`""`, a newline) is
JSON-quoted so it cannot forge summary lines; the wording claims nothing about when it runs (nothing runs it today).
No `entry`: byte-identical output, directory and git installs. **Delta for the existing tree:** only the `security`
module's summary gains one line, after `  requires: (nothing) (pip; not installed by Lattice)`:
`  entry:    lattice.security (CODE: a Python module, main(argv); not imported by install)`. `info|list` unchanged.

**D3. `writes_allowed` (HD-3): a defect, fixed.** It must say what the session gets. New `view()` line:
`"writes_allowed": s.get("status") == "connected" and _writes_ok(s, self.allow_writes)})`, inline because the text
`connected = s.get("status") == "connected"` must keep occurring once (MH-7's old text); `external_tools` and
`_writes_ok` stay byte-identical (MH-1, MH-6). `pending`, `needs-auth`, `failed`, unknown: false; counts unchanged.

**D4. `allow_writes` (HD-4): exact matching kept, warning added.** It is a write allow-list: fail closed, and two
servers may differ only by case. New `McpIntake(..., warn=None)`; the server passes `warn=log` (`server.py:1537`).
`discovery()` calls `_warn_unmatched(d)` before returning: each name matching no listed server (by `_writes_ok`)
emits one line, at most once per (name, sorted tuple of listed names), under a lock:
`studio.toml: [mcp] allow_writes names "claude.ai linear", which matches no server the discovery listed (names are
case-sensitive; did you mean "claude.ai Linear"?)`. Hint = the first listed display name whose display or prefix form
equals the name under `casefold()`; none: `(names are case-sensitive)`. Quoting: `json.dumps(..., ensure_ascii=False)`.
**Silent** if `warn` is None, on a discovery `error`, no listed server, any `pending` server, and with the stub. Body
unchanged; duplicates warn once; `"lattice"` warns (never listed); a listed, not connected name does not warn (D3).

**D5. Docs.** API.md v1.13: a sentence each for D3/D4, no header bump (the integrator renumbers); `docs/lattice-
reference.md:190` one clause; the `ClaudeCliClient`/`cli_env` docstrings name the rule.

## 3. Probes (blind; "pred" = a prediction, not measured)

**P1 H-env, extended (real Studio, fake `claude`, harness of `tests/studio/test_mcp_intake_http.py`).** Every server
also gets **EXTRA** (scrubbed): `ANTHROPIC_VERTEX_PROJECT_ID`, `ANTHROPIC_BEDROCK_BASE_URL`,
`ANTHROPIC_FOUNDRY_RESOURCE`, `ANTHROPIC_MODEL`, `ANTHROPIC_G9_UNLISTED`, each with a value holding `G9WIDE`. It also
gets **KEEP**: `CLAUDE_CODE_OAUTH_TOKEN=G9KEEP-oauth`, `AWS_ACCESS_KEY_ID=G9KEEP-aws`,
`GOOGLE_APPLICATION_CREDENTIALS=/nonexistent/G9KEEP.json`. New instance **I11** (fixture A, `allow_writes =
["claude.ai Nope", "claude_ai_linear", "claude.ai Linear", "claude.ai Linear"]`): (1) `GET /api/mcp` = A1, disc 1;
(2) `?refresh=1` = A1, disc 2; (3) fixture D, `?refresh=1` = D0, disc 3; (4) turn `I11.change`: allowed `L_CHG + X4`
(24), denied `BD + X3` (8), disc 3. Totals: 18 turns, 18 discoveries, 36 calls. **Pass** iff in 36/36 calls of each
of 10 runs: 0 of the 6 CORE, 3 WIDE and 5 EXTRA names is present; no env value contains `G9PROBE` or `G9WIDE`; the 3
KEEP names are present with byte-equal values; and every old env check still holds (counts 36/18/18). All other
checks pass, and the 10 canonical outputs are identical (new sha; `G9KEEP` is also forbidden in the canonical text).
Pred: pass 10/10, about 90%. **Blind baseline:** the new harness on bb3ff0c's `lattice/` fails exactly
`env.wide_absent`, `env.extra_absent`, `env.no_g9wide`, `I1.3.body`, `I3.1.body`, `I5.warn` and `I11.warn`, and
passes every other check (pred, about 75%).

**P2 unit table (`tests/llm/test_cli_env_scrub.py`).** `cli_env` on the 23 names of section 1 (value `v`,
`max_tokens=None`) keeps exactly: `CLAUDE_CODE_OAUTH_TOKEN`, `AWS_ACCESS_KEY_ID`, `GOOGLE_APPLICATION_CREDENTIALS`,
`anthropic_api_key`, `ANTHROPIC`, `HOME`, `PATH`, `CLAUDE_CONFIG_DIR` and `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, in input
order. The same holds through `ClaudeCliClient` (fake binary), `discovery_env` and `rebuild.gate_env`. Pass 4/4.

**P3 real CLI, no model call (machine-specific; NOT RUN if `claude` is absent or logged out).** `claude auth status
--json` with env = `cli_env({HOME, PATH, USER, TMPDIR} + CORE + WIDE + EXTRA, None)`; fields `loggedIn,
authMethod, apiProvider`. Arms: (a) new `cli_env`: `true, claude.ai, firstParty`; (b) the same env without
`cli_env`: `apiProvider != firstParty`; (c) (a) plus `CLAUDE_CODE_OAUTH_TOKEN=<fake>`: `authMethod oauth_token` (the
kept token reaches the CLI and is used; `auth status` does not validate it, so this is no functional login test).
Pass: 3/3 arms, 10x identical. Pred: 3/3 ((a), (c) measured once while writing on equivalent envs; (b) predicted).

**P4 install summary.** A script calls `describe()` (and `install(..., confirm=no, out=...)` for (a) and (c)) under
bb3ff0c's tree (`git archive bb3ff0c lattice docs/examples` into scratch) and under the worktree, with fixed paths
and digests, on: (a) `gitfix.module_files()` mariadb-engine (built-in adapter); (b) an engine with `adapter =
"evil:Adapter"`; (c) custom `audit` with `provides`, no entry; (d) custom with a description, no entry; (e) (a) with
`commit=` and `dropped=`; (f) `lattice/security/module`; (g) `custom-mod`, `entry = "evilentry"`; (h) `entry =
"a\nb"`. **Pass:** (a)-(e) byte-identical; (f), (g), (h) differ from bb3ff0c by exactly the one D2 line at the D2
position (with `lattice.security`, `evilentry`, `"a\nb"` JSON-quoted); 10x identical. Also:
`dogfood/module_market_probes.py --groups tamper,regress --skip-heavy` gives M-tamper-T12 pass and M-regress-R1 15/15
byte-identical to a4ab4a1, with `KNOWN_PROBE_FAILURES = ()`; `git diff bb3ff0c -- tests/modules/test_modules.py` is
empty, 107+ passed. Pred: pass, about 90%.

**P5 HTTP bodies and warnings (F3/F5), same harness, 10 runs.** I3.1 = **A0** (was A3), I1.3 = **B0** (was B1); all
other bodies as at bb3ff0c. Unmatched-name warning lines on stderr (count of `allow_writes names "` after the
`[studio hh:mm:ss]` prefix): I5 = 1, the D4 line exactly. I11 = 5: step 1 `"claude.ai Nope"` (no hint) then
`"claude_ai_linear"` (hint `"claude.ai Linear"`); step 2 none (same listed set); step 3 `"claude.ai Nope"`,
`"claude_ai_linear"`, `"claude.ai Linear"`, no hint; the turn none. I0-I4, I6-I10: 0 (I2's prefix form matches).
**Pass:** all 10 runs exactly so, identical. Pred: pass, about 85% (the risk is the I11 order and dedup).

**P6 unit (`tests/studio/test_mcp_allow_writes_warn.py`, injected `discover_fn`, list-collecting `warn`).** No line
on `error`, empty `servers` or a `pending` server; `warn=None` silent; the same discovery 3x gives 1 line; a changed
listed set warns again; the hint from the prefix form; `writes_allowed` over 4 statuses x {listed, not} is true only
for (connected, listed). Pass 6/6.

**P7 regression.** `tests/studio tests/mcp tests/llm tests/modules tests/security`: 0 failures beyond the named pinned
changes; `dogfood/studio/check.sh` PASS with the bb3ff0c case count; MH-1..MH-14 control passes and 14/14 caught.

## 4. Mutants (blind; one edit each, applied to a copy; catcher = the pytest SUITE below)

| id | edit | caught by |
|---|---|---|
| HD-M1 | prefixes back to `("CLAUDE_CODE_USE_",)` + the three old names | P1 env.wide_absent/extra_absent, P2 |
| HD-M2 | drop `AWS_BEARER_TOKEN_BEDROCK` from `_SCRUBBED_ENV` | P1 env.wide_absent, P2 |
| HD-M3 | over-scrub: prefix `"CLAUDE_CODE_"` | P1 env.keep_present (OAuth token), P2 |
| HD-M4 | over-scrub: add prefix `"AWS_"` | P1 env.keep_present, P2 |
| HD-M5 | case-insensitive match (`k.upper().startswith`) | P2 (lowercase kept) |
| HD-M6 | `describe()` without the entry line | P4 (g), test_entry_summary, T12 |
| HD-M7 | entry line for every custom module (`if m.kind == "custom"`) | P4 (c)/(d) byte identity |
| HD-M8 | entry line placed before `provides:` | P4 (f)/(g) exact position |
| HD-M9 | entry printed raw (no JSON quoting) | P4 (h) |
| HD-M10 | `writes_allowed` back to `_writes_ok(...)` | P5 I3.1.body, I1.3.body; P6 |
| HD-M11 | `writes_allowed` = `status != "failed" and ...` | P5 I1.3.body (needs-auth); P6 |
| HD-M12 | server builds the intake without `warn=log` | P5 I5 = 0, I11 = 0 |
| HD-M13 | unmatched test by display name only | P5 I2 = 1 (prefix form wrongly warned) |
| HD-M14 | no dedup | P5 I5 = 2, I11 > 5; P6 |
| HD-M15 | warn while a server is `pending` | P6 |
| HD-M16 | `_writes_ok` compares `casefold()`ed names | P5 I5.1.body (A1, not A0), argv I5.change |

**Prediction:** control passes, 16/16 caught; HD-M5, HD-M15 only by the unit modules (pred, about 85%). Runner
`tests/studio/test_hardening_mutants.py`, modelled on `test_mcp_intake_mutants.py` (old text occurs exactly once;
SUITE = the HTTP module + the three new unit modules); a run over 600 s is TIMEOUT, a finding, not retried.

## 5. Risks, and what this cannot show

- **A scrub that breaks login.** Shown only by `auth status` (P3) and the plan/rebuild precedent, never by a model
  call (none here). `ANTHROPIC_*` logins (`_API_KEY`, `_AUTH_TOKEN`, `_BASE_URL`) were already cut; a gateway needing
  `ANTHROPIC_CUSTOM_HEADERS` is now cut too, by intent ("never use the API key"). The kept `CLAUDE_CODE_OAUTH_TOKEN`
  wins over the Keychain (measured), so a stray token for another account would be used.
- **Under-scrub by choice.** Generic cloud credentials still reach the child and its tools (D1); an env-only provider
  route not named here would be a finding (`--setting-sources ""` already stops a settings `apiKeyHelper`).
- **Over-warning.** At most one line per (name, listed set). A partial listing (a connector not yet listed, none
  `pending`) can warn about a name a later discovery matches; the line says "the discovery listed", which is true.
- **UI meaning / integration.** A listed but failed server no longer shows "writes allowed" (its row shows failed).
  API.md and the harness's L_ASK/L_CHG are shared with sibling branches; MH old texts constrain D3's spelling.

## 6. Blind vs post hoc, and exact commands

Nothing goes into the main checkout, Streak or job-scheduler; no `git stash/checkout/reset`; temp only under `$S`.
Fixed now: sections 1-4. Post hoc, only in the results' "Post hoc" section: expectations corrected while coding
(old and new value each), timings, mutants' exact old texts. The named pinned replacements in
`test_mcp_intake_http.py` are pre-registered here, not post hoc: `R_NOTION_W`, `R_LINEAR_AUTH_W`, `A3`, `B1` deleted
(I3.1 expects A0, I1.3 expects B0); line 1151 `(32, 32)` -> `(0, 36)`; 1152 `is True` -> `is False`; 1160 `is False`
-> `is True`; asserts added for the warning counts; `GROUPS` = `range(12)`, `len(TURNS) == 18`, counts 36/18/18;
`test_h_env` needs >= 11 checks; the docstring's "32 calls (15 discoveries, 17 turns)" -> 36 (18, 18).

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-hd PYTHONDONTWRITEBYTECODE=1
export S=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/hd
export TMPDIR="$S/tmp"; mkdir -p "$TMPDIR"; cd /Users/vivekgade/Documents/lattice-hd
dogfood/studio/check.sh                                          # BEFORE the first edit: record the case count
mkdir -p "$S/base" && git archive bb3ff0c lattice docs/examples | tar -x -C "$S/base"   # P4 old tree; P1 baseline:
#   + the NEW tests/, from $S/base with PYTHONPATH=$S/base: -m tests.studio.test_mcp_intake_http --runs 1
uv run python -m tests.studio.test_mcp_intake_http --runs 10 --scratch "$S/h" --out "$S/h-data"      # P1, P5
uv run pytest -q -p no:cacheprovider --basetemp "$S/pt" tests/llm/test_cli_env_scrub.py \
  tests/modules/test_entry_summary.py tests/studio/test_mcp_allow_writes_warn.py                    # P2, P4, P6
uv run python "$S/p3_auth.py" --runs 10 > "$S/p3.json"; uv run python "$S/p4_summary.py" --old "$S/base" --new . \
  --runs 10 > "$S/p4.json"                                        # P3 (no model call), P4; scripts live in $S
uv run python dogfood/module_market_probes.py --scratch "$S/mm" --runs 2 --groups tamper,regress \
  --skip-heavy --base-tree "$S/a4ab4a1" --out "$S/mm-data"   # $S/a4ab4a1 = git archive a4ab4a1 lattice docs/examples
uv run pytest tests/studio tests/mcp tests/llm tests/modules tests/security -q -p no:cacheprovider --basetemp "$S/pt7"
dogfood/studio/check.sh                                          # PASS, same case count
uv run python tests/studio/test_mcp_intake_mutants.py --out "$S/mh" --jobs 3   # control + MH 14/14; then the same
uv run python tests/studio/test_hardening_mutants.py --out "$S/hdm" --jobs 3   # for HD-M1..16 (control + 16/16)
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
```

## 7. File ownership (build node)

**Edits:** `lattice/llm/cli_client.py` (constants, `cli_env`, two docstrings); `lattice/studio/mcp_intake.py`
(`McpIntake.__init__`, `discovery`, new `_warn_unmatched`, the `view()` `writes_allowed` line, one docstring
sentence); `lattice/studio/server.py` (`warn=log` in `mcp_intake` only); `lattice/modules/__init__.py`
(`_ENTRY_NAME`, `describe()`); `lattice/studio/API.md` (v1.13 section), `docs/lattice-reference.md:190`;
`tests/studio/test_mcp_intake_http.py` (the named changes); `dogfood/module_market_probes.py`
(`KNOWN_PROBE_FAILURES = ()` and its comment).

**Creates:** `tests/llm/test_cli_env_scrub.py`, `tests/modules/test_entry_summary.py`, `tests/studio/
test_mcp_allow_writes_warn.py`, `tests/studio/test_hardening_mutants.py`, the results doc and data directory.

**Must not change:** `tests/modules/test_modules.py`, `tests/llm/test_cli_client.py`, `tests/studio/test_mcp_intake.py`,
`tests/studio/test_mcp_intake_mutants.py`; `external_tools`, `_writes_ok`, `classify_tool`, `STUB_NOTE` (F1/F4); the
redundant `ANTHROPIC_` filters in `plan.py`/`rebuild.py`; `dogfood/studio/*`. Commit only on the orchestrator's
word, staging explicit paths (never `git add -A`).

## Amendment 1 (orchestrator rulings, 2026-10-16, before any code)

**Approved as written:** HD-1 (`cli_env` drops every name starting with `ANTHROPIC_` or `CLAUDE_CODE_USE_` plus
exactly `AWS_BEARER_TOKEN_BEDROCK`, case-sensitive; keeps `CLAUDE_CODE_OAUTH_TOKEN`, HOME, PATH, USER, TMPDIR,
`CLAUDE_CONFIG_DIR` and proxy variables); HD-2 (the one `entry:` line after `requires:`, shown only when an
`entry` is declared, JSON-quoted when not a dotted name; manifests without `entry` byte-identical); HD-3 fixed
(`writes_allowed` needs status `connected` and the server listed); HD-4 (exact case-sensitive matching kept,
fail-closed; a once-per-(name, listed-set) stderr warning with a 'did you mean' hint, silent when discovery
failed, listed nothing or has a pending server; `GET /api/mcp` body unchanged); G9 F1 and F4 left untouched with
the stated reasons; the pinned-test changes named in the document; no API version bump (the integrator
renumbers).
**Premise correction accepted:** nothing reads a custom module's `entry` today, so the G3 F2 wording 'imported
into the checker' belongs to adapters, not `entry`; the new line says `not imported by install` and the
results record that the original finding was partly mis-stated. **Rulings.** (1) The blind baseline (the new
harness against bb3ff0c's `lattice/` fails exactly the 7 named checks) runs before any code. (2) The 'login
still works' proof stays `auth status` in three arms plus the plan/rebuild precedent; no model call is made
and the results do not claim one. (3) `CLAUDECODE` and `CLAUDE_CODE_MESSAGING_TOKEN` reaching the child when
Studio is started from inside a Claude Code session are reported as an out-of-scope observation, not fixed.
(4) `ANTHROPIC_MODEL` no longer reaching the child is disclosed as a deliberate side effect. (5) Existing
mutants MH-1..MH-14 and the module-market mutants still find their text once and are re-run as regression.
