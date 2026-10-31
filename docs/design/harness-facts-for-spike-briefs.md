# Harness facts every real-agent spike brief must state (WP7 checklist)

Date: 2026-10-30. Derived only from tracked repo files, checked against the Claude Code CLI 2.1.309 on this
machine (`claude --version`, `claude --help`; no model call). A spike brief for any round that calls a real
agent copies the table in section 1 for the flags it uses and answers sections 2 to 6 explicitly. A brief that
leaves one of them open is not ready to freeze.

## 1. The flags the repo depends on, and where

| Flag | Used by | Why it matters |
|---|---|---|
| `-p --output-format stream-json --verbose` | `rebuild.py:1074`, `session.py:968-969`, `plan.py:711`, `mcp_intake.py:142` | The stream is the only source of per-message usage before the result event (`StreamUsage`, `rebuild.py:3884`). |
| `--output-format json` | `ClaudeCliClient._argv`, `cli_client.py:245-257`; H2 dispatch, `dispatch_author.sh:161-162` | One result object with `total_cost_usd`, `num_turns`, `subtype`, `is_error`. |
| `--include-partial-messages` | `session.py:969` (chat only) | Accepted together with `--verbose` (`lattice-claude-code-integration.md:49`). |
| `--resume <id>` | `rebuild.py:1076`, `session.py:972`, `dispatch_author.sh:136` | A repair or fix round continues the same session; the budget flag then applies to the resumed call alone. |
| `--session-id <uuid>` | `dispatch_author.sh:162` | A fixed id lets the audit find the transcript afterwards. |
| `--setting-sources ""` | all four argv builders; `cli_client.py:134-137` | Without it the owner's plugins and hooks enter every turn (an Ask turn cost about 8x more, `lattice-claude-code-integration.md:50`). `--settings <file>` still applies (the guard hook). |
| `--disable-slash-commands` | all four builders; `cli_client.py:134-137` | Removes skills and commands from the prompt. |
| `--strict-mcp-config --mcp-config <cfg>` | `rebuild.py:1081-1082`, `plan.py:714` (empty config); `cli_client.py:134-137` | Chat omits `--strict-mcp-config` on purpose (`session.py:952-958`), so the account's claude.ai connectors can appear; discovery reads them (`mcp_intake.py:140-147`). |
| `--tools <list>` | `rebuild.py:177,1086`, `session.py:979`, `plan.py:151,715`; `--tools ""` in `cli_client.py:134-137` | The set of built-ins that exist at all; this is the real tool boundary. |
| `--allowedTools / --disallowedTools` | `rebuild.py:1090-1093`, `session.py:981-982`, `plan.py:717-718` | Allow/deny layer on top of `--tools`; under `dontAsk` anything unlisted is denied. |
| `--permission-mode` | `acceptEdits` in `rebuild.py:1088`; `dontAsk` in `session.py:980`, `plan.py:716`, `mcp_intake.py:144` | `acceptEdits` lets file edits and shell commands in the worktree through; see section 4. |
| `--append-system-prompt` / `--system-prompt` | `rebuild.py:1094`, `session.py:983`, `plan.py:719`; `--system-prompt` in `cli_client.py:252` | Stack-level prompts; freeze them with the brief. |
| `--model`, `--effort` | `rebuild.py:1096`, `cli_client.py:251,256` | Record the resolved model id, not the alias. |
| `--no-session-persistence` | `plan.py:712`, `mcp_intake.py:143`, `cli_client.py:134-137` | Leaves no transcript to audit; do not use it for an audited round. |
| `--max-turns`, `--max-budget-usd` | not in the Studio builders; `mcp_intake.py:142` has `--max-turns 1`; shims and dispatch scripts add both (`dn_real_agent_shim.py:120-122`, `dispatch_author.sh:161-162`) | See section 2 and 4. |

The prompt always goes last behind `--` because `--allowedTools` and `--mcp-config` are variadic
(`session.py:949-950`, `rebuild.py:1098`).

## 2. `--max-turns` is accepted but not documented

`claude --help` on 2.1.309 lists `--max-budget-usd` but no `--max-turns`. The flag is accepted anyway: earlier
results recorded this on 2.1.296 and 2.1.299 (`docs/results/2026-10-12-k-r-mcp-visibility.md:46`,
`docs/results/2026-10-14-agent-run-prereg.md:91`), and the H2 author dispatch ran with `--max-turns 150` on 2.1.309
(`docs/results/data/2026-10-23-dotnet-h2/author/a3-claude-version.txt`, `dispatch_author.sh:20,162`) and
ended `success`. A brief must therefore (a) say the flag is undocumented and could vanish, (b) name the fallback
if the CLI rejects it (drop it for all sessions and report it, as `docs/results/2026-10-14-agent-run-prereg.md:91-92` did), and (c) count
a turn-cap stop (`subtype` other than `success`) as a result to report, not a crash.

## 3. The stripped environment

Every Lattice-spawned `claude -p` runs with `cli_env` (`lattice/llm/cli_client.py:107-132`): all `ANTHROPIC_*`,
all `CLAUDE_CODE_USE_*` and `AWS_BEARER_TOKEN_BEDROCK` removed; `HOME`, `PATH`, `CLAUDE_CONFIG_DIR` and
`CLAUDE_CODE_OAUTH_TOKEN` kept, so the call bills the logged-in plan. The output cap is the env var
`CLAUDE_CODE_MAX_OUTPUT_TOKENS` (no flag exists, `cli_client.py:117,187`). Dispatch scripts go further and unset
`LATTICE_*`, `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `PYTHONPATH`, `VIRTUAL_ENV` and others
(`dispatch_author.sh:84-91`). A brief states which of the two it uses, the working directory (a neutral empty
dir for the library client, `cli_client.py:238-243`; a fixed author dir outside the repo for the H2 dispatches),
and that stdin is `/dev/null` (`session.py:1232`, `rebuild.py:3187`).

## 4. Budget and turn caps: where they exist and where they do not

| Cap | Enforced today | Not enforced |
|---|---|---|
| Wall time | `DEFAULT_SESSION_TIMEOUT` 900 s (`session.py:104,1259`); `round_timeout` 1800 s and `node_timeout` (`rebuild.py:157,3209`); `ClaudeCliClient` 600 s (`cli_client.py:81`) | |
| Output tokens | `node_max_tokens` for plan nodes (`rebuild.py:449,3211,3287`) | counts output tokens only; an earlier run understated cost about 75x by ignoring cache tokens (`docs/HANDOFF-2026-10-24.md:49`). Only the stream-json `usage.input_tokens` is missing cache tokens (`lattice-claude-code-integration.md:52`); `total_cost_usd` has them. |
| USD | `implement --usd-cap` on the list-price estimate (`implement.py:784`); shims and dispatch scripts | None of the four Studio argvs passes `--max-budget-usd` or `--max-turns` |
| Edits | guard hook; `AGENT_TOOLS` (`rebuild.py:177`) | the guard is "not a sandbox", and `acceptEdits` allows `rm`/`mv`/`cp` in the worktree (`docs/HANDOFF-2026-10-24.md:49`) |

A brief must set its own `--max-budget-usd` and `--max-turns` per call and a total cap, because the product
code will not.

## 5. Spend ledger and transcript audit conventions

- **Ledger.** One append-only JSONL line per `claude` call, written by the wrapper from the CLI's own result,
  never from the prompt: cost (`total_cost_usd`), turns, `subtype`, `is_error`, exit code, session id, the cap it
  was given, wall time (`dogfood/dn_real_agent_shim.py:132-152`; H2: `dispatch_author.sh:55-81`, file
  `docs/results/data/2026-10-23-dotnet-h2/spend.jsonl`). It is `fsync`ed (`dn_real_agent_shim.py:45-50`).
- **Remaining-cap rule.** The budget flag is `min(remaining total minus a 0.10 margin, remaining of the phase,
  per-call ceiling)`; below 0.05 the call is refused with exit 3 and no model call
  (`dn_real_agent_shim.py:28-29,91-108`). The CLI can overshoot a budget slightly, hence the margin.
- **Stop rule.** Any result that is not `success`, or whose cost reaches the cap, stops the round; the owner
  decides; nothing is re-dispatched (`dispatch_author.sh:55-81,168-169`; prereg 9.4,
  `2026-10-23-dotnet-h2-prereg.md:269-273`).
- **Transcript audit.** The session id is fixed up front, the transcript is copied from
  `~/.claude/projects/` after the call and read by `audit_transcript.py` (`dispatch_author.sh:94-111`;
  classes forbidden, outside and review, prereg 9.5, `2026-10-23-dotnet-h2-prereg.md:275-284`). The audit is
  heuristic and a hit voids the deliverable only after a human reads the call.
- Never pass `--no-session-persistence` in a round that is audited.

## 6. Once-only, ledger and freeze rules (H2 prereg sections 7 to 10)

- **Ledger (section 7, `docs/results/2026-10-23-dotnet-h2-prereg.md:206-212`).** Append-only, committed empty with the freeze file; `scored` once
  per arm, `rerun` once and only after `crash_before_metric` with the same bytes, `verify` once afterwards.
- **Freeze (section 8, `:214-238`).** Every runner re-hashes the freeze file before any metric and refuses on a
  mismatch, a missing listed file or an unlisted file under `lattice/`; no flag skips it. Anything learned later
  goes to the results document, never into a frozen file; a needed change voids the freeze and the owner decides.
- **Process (section 10, `:289-300`).** One scored run per arm; a miss is reported, its cause localised, not
  tuned; later findings go to a labelled post hoc section; NOT RUN only for a named missing prerequisite; never an
  API key, a model call outside the dispatch, an edit after the freeze, or `git add -A`.
- **Author protocol (section 9, `:240-287`).** Dispatch by a later node, one fix round, spend caps with a total,
  transcript audit, independent verification with 0 differing verdicts.

## 7. Lessons from the H2 round that a brief must absorb (`2026-10-23-dotnet-h2-results.md:71-77`)

1. The transcript auditor produced false positives twice (C# `//` in heredocs, a `../` path that resolved inside
   the author dir). Say who reads each flagged call, and that the script must not stop copying deliverables on
   exit 5.
2. A freeze that lists "every file under `lattice/`" refused to run in the main checkout because of an ignored
   `node_modules/` directory. State which checkout runs the frozen runners (a clean worktree of the committed tree).
3. A usage error (a missing `--out`) happened before the ledger was touched; say that argument errors must exit
   before any ledger line.
4. A runner row that compares error text passes when ten runs crash identically; a brief must require that a
   raising row is a FAIL, not a match.
5. Pre-flight the flags with one cheap call (H2 spent USD 0.01) before the freeze, and record the CLI version in
   the results (`a3-claude-version.txt`).
6. Spend from the author and mutant-writer calls counts against the same total as the scored work.
