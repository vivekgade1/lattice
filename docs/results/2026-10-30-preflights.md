# Pre-flights P1, P2, P3 (2026-10-30)

Claude Code CLI 2.1.309, model haiku (resolves to claude-haiku-5-5), always `env -u ANTHROPIC_API_KEY`, `--setting-sources ""`, `--disable-slash-commands`, `--strict-mcp-config` unless noted. Scratch dirs and raw stream files live under the session scratchpad (`.../scratchpad/pf/`); the per-call ledger is `docs/results/data/2026-10-30-nc/spend-PF.jsonl`.

**Total spend: USD 0.0208** (P1 0.0019 of 0.05, P2 0.0119 of 0.08, P3 0.0070 of 0.12; combined cap 0.30). No call came near its cap.

## Verdicts for dependent nodes

- **R1 (needs subtype names):** budget exhaustion is `subtype: "error_max_budget_usd"` (`is_error: true`, `terminal_reason: "budget_exhausted"`, `errors: ["Reached maximum budget ($0.0001)"]`) and max turns is `subtype: "error_max_turns"` (`is_error: true`, `terminal_reason: "max_turns"`, `errors: ["Reached maximum number of turns (1)"]`); both guesses were right, both exit with code 1, neither has a `result` text field, and the budget is only checked between model calls so the final `total_cost_usd` can overshoot the cap.
- **R3 (needs the P2 syntax):** `--permission-mode dontAsk` with `--allowedTools "Edit(src/**)" "Write(src/**)" Read` (equivalently `permissions.allow` in a `--settings` file) scopes edits to the directory relative to the cwd: files inside `src/` were written, `other/`, an absolute path outside the repo, `src/../other/...`, a symlink out of `src/`, and every Bash `rm`/`mv` were denied; `Edit(/src/**)` and `Edit(./src/**)` behave identically.
- **H1 / X4 (need P3):** a Stop hook fires under `claude -p` and exit 2 forces exactly one more assistant turn when the script honours `stop_hook_active`, with `${CLAUDE_PLUGIN_ROOT}` (via `--plugin-dir` only) and `$CLAUDE_PROJECT_DIR` (both modes) resolving; `--setting-sources ""` changes nothing for the hook (it only removes the owner's other hooks), and a script that ignores `stop_hook_active` is blocked repeatedly until `--max-turns` ends the run with `error_max_turns`, so the script must gate on `stop_hook_active`.

## P1: result events for budget exhaustion and max-turns

Setup: scratch dir with `note.txt` ("alpha\nbeta"), `--tools Read --permission-mode dontAsk --allowedTools Read`, prompt "Read note.txt with the Read tool, then tell me its second line, then read it again and confirm." (3 turns when unconstrained).

Command template (`run1b.sh`):

```
env -u ANTHROPIC_API_KEY claude -p --model haiku --output-format stream-json --verbose <FLAGS> \
  --setting-sources "" --disable-slash-commands --strict-mcp-config \
  --tools Read --permission-mode dontAsk --allowedTools Read -- "<prompt>"
```

| run | FLAGS | exit | subtype | is_error | terminal_reason | stop_reason | num_turns | total_cost_usd |
|---|---|---|---|---|---|---|---|---|
| 1 | `--max-budget-usd 0.001` | 0 | success | false | completed | end_turn | 3 | 0.00098436 |
| 2 | `--max-budget-usd 0.0005` | 0 | success | false | completed | end_turn | 3 | 0.00047625 |
| 3 | `--max-budget-usd 0.0001` | 1 | **error_max_budget_usd** | true | budget_exhausted | tool_use | 1 | 0.00021693 |
| 4 | `--max-turns 1 --max-budget-usd 0.04` | 1 | **error_max_turns** | true | max_turns | tool_use | 2 | 0.00018253 |

Runs 1 and 2 finished under their budgets (prompt caching made the 3-turn run cost under a tenth of a cent), so the budget-exhausted case needed 0.0001. Facts for R1:

- `total_cost_usd`, `usage` and `modelUsage` are all present in both error results. In the budget-exhausted result the top-level `usage` is all zeros (and `iterations` empty) while `modelUsage` and `total_cost_usd` carry the real numbers, so read cost from `total_cost_usd`/`modelUsage`, not `usage`.
- The error results have an `errors` list of strings and **no `result` key**; success results have `result`.
- The budget cap is checked after a model call completes: run 3 spent 0.000217 against a 0.0001 cap (about 2x overshoot on a tiny cap; one observation, so assume up to roughly one model turn of overshoot).
- `max-turns 1` reported `num_turns: 2` (the counter includes the tool-result turn that tripped the limit); `stop_reason` is `tool_use`, i.e. the agent was cut off mid-work. The tool call in the single allowed turn did execute (Read result is in the stream).
- `--max-turns` works although absent from `claude --help`.
- Result keys (error case): duration_api_ms, duration_ms, errors, fast_mode_disabled_reason, fast_mode_state, is_error, modelUsage, model_usage_since_last_result, num_turns, permission_denials, queued_turn_count, result_index, safety_stops, session_id, stop_reason, subagent_stats, subtype, terminal_reason, total_cost_usd, type, usage, uuid.

Raw result events (`fast_mode_*`, `subagent_stats`, `model_usage_since_last_result` and `uuid` removed for length; they are not informative):

```
run3 (budget): {"duration_api_ms": 0, "stop_reason": "tool_use", "session_id": "c676d67a-65a1-4cb1-bfc6-ed5c7f910e14", "total_cost_usd": 0.00021693, "usage": {"output_tokens_details": {"thinking_tokens": 0}, "input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0, "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}, "service_tier": "standard", "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0}, "inference_geo": "", "iterations": [], "speed": "standard", "fallback_credit": null}, "modelUsage": {"claude-haiku-5-5": {"inputTokens": 2, "outputTokens": 185, "cacheReadInputTokens": 4383, "cacheCreationInputTokens": 402, "webSearchRequests": 0, "costUSD": 0.00021693, "contextWindow": 1000000, "maxOutputTokens": 128000, "thinkingTokens": 67, "canonicalModel": "claude-haiku-5-5", "provider": "firstParty", "costBasis": "list"}}, "permission_denials": [], "terminal_reason": "budget_exhausted", "safety_stops": 0, "is_error": true, "num_turns": 1, "subtype": "error_max_budget_usd", "errors": ["Reached maximum budget ($0.0001)"], "type": "result", "duration_ms": 1652, "queued_turn_count": 0, "result_index": 0}
run4 (turns):  {"duration_api_ms": 1242, "stop_reason": "tool_use", "session_id": "86d3cfe2-7017-4807-b5ba-5562b54a87e5", "total_cost_usd": 0.00018253, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 400, "cache_read_input_tokens": 4383, "output_tokens": 117, "output_tokens_details": {"thinking_tokens": 0}, "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}, "service_tier": "standard", "cache_creation": {"ephemeral_1h_input_tokens": 400, "ephemeral_5m_input_tokens": 0}, "inference_geo": "global", "iterations": [{"input_tokens": 2, "output_tokens": 117, "cache_read_input_tokens": 4383, "cache_creation_input_tokens": 400, "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 400}, "type": "message"}], "speed": "standard", "fallback_credit": null}, "modelUsage": {"claude-haiku-5-5": {"inputTokens": 2, "outputTokens": 117, "cacheReadInputTokens": 4383, "cacheCreationInputTokens": 400, "webSearchRequests": 0, "costUSD": 0.00018253, "contextWindow": 1000000, "maxOutputTokens": 128000, "thinkingTokens": 0, "canonicalModel": "claude-haiku-5-5", "provider": "firstParty", "costBasis": "list"}}, "permission_denials": [], "terminal_reason": "max_turns", "safety_stops": 0, "is_error": true, "num_turns": 2, "subtype": "error_max_turns", "errors": ["Reached maximum number of turns (1)"], "type": "result", "duration_ms": 1269, "queued_turn_count": 0, "result_index": 0}
run1 (ok):     {"duration_api_ms": 2579, "stop_reason": "end_turn", "session_id": "1a962d22-3acc-44bf-adf1-e9b4f682fe69", "total_cost_usd": 0.00098436, "usage": {"input_tokens": 6, "cache_creation_input_tokens": 3530, "cache_read_input_tokens": 11426, "output_tokens": 327, "output_tokens_details": {"thinking_tokens": 19}, "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}, "service_tier": "standard", "cache_creation": {"ephemeral_1h_input_tokens": 3530, "ephemeral_5m_input_tokens": 0}, "inference_geo": "global", "iterations": [{"input_tokens": 2, "output_tokens": 53, "cache_read_input_tokens": 4973, "cache_creation_input_tokens": 227, "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 227}, "type": "message"}], "speed": "standard", "fallback_credit": null}, "modelUsage": {"claude-haiku-5-5": {"inputTokens": 6, "outputTokens": 327, "cacheReadInputTokens": 11426, "cacheCreationInputTokens": 3530, "webSearchRequests": 0, "costUSD": 0.00098436, "contextWindow": 1000000, "maxOutputTokens": 128000, "thinkingTokens": 19, "canonicalModel": "claude-haiku-5-5", "provider": "firstParty", "costBasis": "list"}}, "permission_denials": [], "terminal_reason": "completed", "safety_stops": 0, "is_error": false, "num_turns": 3, "subtype": "success", "api_error_status": null, "result": "The second line is `beta`. I read the file a second time and it matches: line 1 is `alpha`, line 2 is `beta`, and line 3 is empty.", "ttft_ms": 895, "type": "result", "duration_ms": 2629, "ttft_stream_ms": 538, "time_to_request_ms": 32, "first_content_frame_ms": 538, "queued_turn_count": 0, "result_index": 0}
```

Fallback not needed: R1 can map `error_max_budget_usd` and `error_max_turns` directly; keep an in-process guard anyway because of the one-turn overshoot.

## P2: path-scoped Edit/Write under dontAsk

Setup: a scratch git repo with `src/a.txt` and `other/b.txt` (both "one"), cwd = repo root, `--permission-mode dontAsk --tools Read,Edit,Write,Bash`. One prompt asks for 7 steps, each its own tool call: (1) Edit src/a.txt, (2) Write src/new.txt, (3) Edit other/b.txt, (4) Write other/new.txt, (5) Write an absolute path outside the repo (`<scratch>/outside.txt`), (6) Bash `rm src/a.txt`, (7) Bash `mv src/new.txt src/moved.txt`. Bash is not in the allow list.

Command template (`run2.sh`):

```
env -u ANTHROPIC_API_KEY claude -p --model haiku --output-format stream-json --verbose --max-budget-usd 0.04 --max-turns 16 \
  --setting-sources "" --disable-slash-commands --strict-mcp-config --tools Read,Edit,Write,Bash --permission-mode dontAsk \
  --allowedTools "Edit(src/**)" "Write(src/**)" Read -- "<7-step prompt>"
```

Results (taken from `tool_result is_error` and the result event's `permission_denials`, plus the filesystem afterwards):

| rule syntax | (1) edit src | (2) write src | (3) edit other | (4) write other | (5) write outside | (6) rm | (7) mv |
|---|---|---|---|---|---|---|---|
| `--allowedTools "Edit(src/**)" "Write(src/**)" Read` | ALLOWED (a.txt = ONE) | ALLOWED (src/new.txt exists) | DENIED | DENIED | DENIED | DENIED | DENIED |
| `--allowedTools "Edit(/src/**)" "Write(/src/**)" Read` | ALLOWED | ALLOWED | DENIED | DENIED | DENIED | DENIED | DENIED |
| `--allowedTools "Edit(./src/**)" "Write(./src/**)" Read` | ALLOWED | ALLOWED | DENIED | DENIED | DENIED | DENIED | DENIED |

Denied calls return a `tool_result` with `is_error: true` ("Permission to use Edit has been denied because Claude Code is running in don't ask mode...") and a `system` event `subtype: permission_denied`; they are listed in the final result's `permission_denials`. After every run `other/b.txt` was still "one", `other/new.txt` and `outside.txt` did not exist, `src/a.txt` still existed (rm denied) and `src/moved.txt` did not exist. The result event itself is `subtype: success` (a denial does not fail the run), so a gate that wants to detect attempted escapes must read `permission_denials`.

Denial lists from the three runs:

```
v1 Edit(src/**):   [('Edit', '<S>/p2/v1/repo/other/b.txt'), ('Write', '<S>/p2/v1/repo/other/new.txt'), ('Write', '<S>/p2/v1/outside.txt'), ('Bash', 'rm src/a.txt'), ('Bash', 'mv src/new.txt src/moved.txt')]
v2 Edit(/src/**):  [('Edit', '<S>/p2/v2/repo/other/b.txt'), ('Write', '<S>/p2/v2/repo/other/new.txt'), ('Write', '<S>/p2/v2/outside.txt'), ('Bash', 'rm <S>/p2/v2/repo/src/a.txt'), ('Bash', 'mv <S>/p2/v2/repo/src/new.txt <S>/p2/v2/repo/src/moved.txt')]
v3 Edit(./src/**): [('Edit', '<S>/p2/v3/repo/other/b.txt'), ('Write', '<S>/p2/v3/repo/other/new.txt'), ('Write', '<S>/p2/v3/outside.txt'), ('Bash', 'rm src/a.txt'), ('Bash', 'mv src/new.txt src/moved.txt')]
```

Settings-file form plus escape attempts (`run2b.sh`): `--settings settings.json` with `{"permissions": {"allow": ["Edit(src/**)", "Write(src/**)", "Read"]}}`, repo containing symlink `src/link -> ../other`. Steps: (1) Write `src/ok.txt` ALLOWED; (2) Write `src/../other/esc1.txt` DENIED; (3) Write `src/link/esc2.txt` (through the symlink) DENIED; (4) Write absolute `<repo>/other/esc3.txt` DENIED; (5) Write absolute `<repo>/src/ok2.txt` ALLOWED. Filesystem afterwards: `src/{a.txt,link,ok.txt,ok2.txt}`, `other/b.txt` only.

```
v4 denials: [('Write', '<S>/p2/v4/repo/other/esc1.txt'), ('Write', '<S>/p2/v4/repo/src/link/esc2.txt'), ('Write', '<S>/p2/v4/repo/other/esc3.txt')]
```

Notes for R3: rules are relative to the cwd (the project root when spawned there), the glob `src/**` covers nested files, relative `..` traversal and symlink escapes are resolved before matching, and the single `--allowedTools` argument list is variadic (as already used in `build_rebuild_argv`). Not tested: comma-joined `--allowedTools` as the rebuild argv does (`",".join(...)`); each rule contains no commas so it should be equivalent, but R3 should keep a unit test on its argv builder. Bash was fully denied here because it was not allowed at all; a scoped Bash allow list (e.g. `Bash(./check.sh)`) was not tested.

## P3: Stop hook under `claude -p`

Throwaway plugin (`p3/<run>/plugin`): `.claude-plugin/plugin.json` (name, description, version, author, same shape as `lattice-claude-plugin`), `hooks/hooks.json` with the Stop entry `"\"${CLAUDE_PLUGIN_ROOT}/hooks/stop-probe.sh\""` (same form as the lattice plugin), and a stand-in `hooks/stop-probe.sh` that: appends `date + "stop hook fired"` to `marker.txt`; appends `CLAUDE_PLUGIN_ROOT`, a script-relative root, `CLAUDE_PROJECT_DIR`, `PWD` and the stdin JSON to `env.txt`; exits 2 the first time (state file) with a stderr message, and exits 0 afterwards (and whenever stdin has `"stop_hook_active":true`). Variant (b) uses `--settings` with `{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "\"<abs>/stop-probe.sh\""}]}]}}`, the same mechanism as `write_configs`/`build_rebuild_argv`. Prompt: "Reply with exactly the word hi, nothing else." Run in a scratch git repo (cwd = `proj/`); `--include-hook-events` was added so `hook_started`/`hook_response` records appear in stream-json.

Command (`run3.sh`):

```
env -u ANTHROPIC_API_KEY claude -p --model haiku --output-format stream-json --verbose --include-hook-events \
  --max-budget-usd 0.03 --max-turns 6 --disable-slash-commands --strict-mcp-config --tools Read --permission-mode dontAsk \
  [--plugin-dir <dir> | --settings <file>] [--setting-sources ""] -- "Reply with exactly the word hi, nothing else."
```

| run | hook delivery | `--setting-sources ""` | Stop hook fired | exit 2 blocked | fired how many times | `CLAUDE_PLUGIN_ROOT` | `CLAUDE_PROJECT_DIR` | result |
|---|---|---|---|---|---|---|---|---|
| a | `--plugin-dir` | yes | yes | yes, once | 2 (second had `stop_hook_active:true`, script exit 0) | resolved to the plugin dir | resolved to cwd (`proj`) | success, 2 turns |
| b | `--settings` | yes | yes | yes, once | 2 | unset (not a plugin) | resolved to cwd | success, 2 turns |
| c | `--plugin-dir` | no | yes | yes, once | 2 (plus the owner's own Stop and SessionStart hooks also ran) | resolved | resolved | success, 2 turns |
| d | `--settings` | no | yes | yes, once | 2 (plus owner hooks) | unset | resolved | success, 2 turns |
| e | `--plugin-dir`, script ignores `stop_hook_active` and always exits 2 | yes | yes | yes, **every time** | 5 (marker lines; every later stdin had `stop_hook_active:true`) | resolved | resolved | `error_max_turns` after `--max-turns 6`, 7 turns |

Marker file for run a (verbatim):

```
2026-10-30T17:57:27 stop hook fired
BLOCK (exit 2) first time
2026-10-30T17:57:29 stop hook fired
stop_hook_active seen -> exit 0
```

`env.txt` for run a, first entry (the second differs only in the time and `stop_hook_active:true`):

```
17:57:27
CLAUDE_PLUGIN_ROOT=<S>/p3/a/plugin
script_dir_root=<S>/p3/a/plugin
CLAUDE_PROJECT_DIR=<S>/p3/a/proj
PWD=<S>/p3/a/proj
stdin={"session_id":"9ffa78aa-982d-4db5-9f67-1b24badfeb22","transcript_path":"<projects>/-private-tmp-claude-501--Users-vivekgade-Documents-lattice-9f296a24-a081-4f07-944c-bf0e58075804-scratchpad-pf-p3-a-proj/9ffa78aa-982d-4db5-9f67-1b24badfeb22.jsonl","cwd":"<S>/p3/a/proj","prompt_id":"bddadb34-8300-425c-a136-67a307b3ae84","permission_mode":"dontAsk","effort":{"level":"medium"},"hook_event_name":"Stop","stop_hook_active":false,"last_assistant_message":"hi","background_tasks":[],"session_crons":[]}
```

Stream proof (run a, `system` records in order): `hook_started` (Stop) -> `hook_response` `exit_code: 2, outcome: "error", stderr: "Stop-hook probe: you must also reply with the word BLOCKED before stopping."` -> `user` record carrying the hook feedback -> a new `assistant` turn (text "BLOCKED") -> `hook_started` -> `hook_response` `exit_code: 0, outcome: "success"` -> `result` `success`, `num_turns: 2`, `result: "BLOCKED"`. Run b is the same shape. Marker file for run b is identical in shape (fired, BLOCK, fired, `stop_hook_active seen -> exit 0`).

Run e marker file (verbatim):

```
2026-10-30T17:57:57 stop hook fired
ALWAYS_BLOCK -> exit 2 (ignoring stop_hook_active)
2026-10-30T17:57:58 stop hook fired
ALWAYS_BLOCK -> exit 2 (ignoring stop_hook_active)
2026-10-30T17:58:00 stop hook fired
ALWAYS_BLOCK -> exit 2 (ignoring stop_hook_active)
2026-10-30T17:58:01 stop hook fired
ALWAYS_BLOCK -> exit 2 (ignoring stop_hook_active)
2026-10-30T17:58:05 stop hook fired
ALWAYS_BLOCK -> exit 2 (ignoring stop_hook_active)
```

Run e result: `{"duration_api_ms": 8353, "stop_reason": "end_turn", "session_id": "b205d656-5446-475f-a8cd-51a3fff8129d", "total_cost_usd": 0.0016766800000000003, "usage": {"input_tokens": 20, "cache_creation_input_tokens": 5005, "cache_read_input_tokens": 27718, "output_tokens": 793, "output_tokens_details": {"thinking_tokens": 554}, "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}, "service_tier": "standard", "cache_creation": {"ephemeral_1h_input_tokens": 5005, "ephemeral_5m_input_tokens": 0}, "inference_geo": "global", "iterations": [{"input_tokens": 2, "output_tokens": 273, "cache_read_input_tokens": 5763, "cache_creation_input_tokens": 507, "cache_creation": {"ephemeral_5m_input...`

Facts for H1/X4:

- Stop hooks run under `-p` in both delivery modes; `--setting-sources ""` does not stop `--plugin-dir` or `--settings` hooks. Without it the owner's own hooks also fire (SessionStart and an extra Stop hook appeared in c and d), which is why Studio keeps `--setting-sources ""`.
- Exit 2 plus stderr text makes the agent continue once; the stderr text is delivered to the model as feedback. The `Stop` stdin JSON has `stop_hook_active` (false on the first stop, true on every stop that follows a hook-forced continuation), `cwd`, `session_id`, `transcript_path`, `last_assistant_message`, `permission_mode`.
- The CLI does not itself limit repeated blocking: a hook that always exits 2 blocks every time until `--max-turns` (here) or `--max-budget-usd` stops the run (`error_max_turns`, exit 1). So `stop-check.sh`-style scripts must keep the `stop_hook_active` guard (the lattice `stop-check.sh` does), and the rebuild driver should always pass `--max-turns`. Note this also means a strict gate that blocks once and then exits 0 on `stop_hook_active:true` can only force one extra round per stop, not "until green".
- `${CLAUDE_PLUGIN_ROOT}` expands only for plugin-delivered hooks; in `--settings` hooks it is empty, so use an absolute script path there. `$CLAUDE_PROJECT_DIR` equals the cwd of the `claude` process in all four modes.
- Mismatch to note: the stream carries `hook_started`/`hook_response` records only because `--include-hook-events` was passed; without it the marker file is the reliable proof.

## Ledger

`docs/results/data/2026-10-30-nc/spend-PF.jsonl` has one row per call (node, question, label, cap_usd, cost_usd from `total_cost_usd`, session_id, subtype, num_turns): 13 calls, total USD 0.0208.

## R2 pre-flight: `--max-budget-usd` on a resumed session (2026-10-31)

Question (node R2): does the CLI's own `--max-budget-usd` on a `--resume`d session count the session's cumulative cost or only the new step? CLI 2.1.309, haiku (claude-haiku-5-5), the P1 setup (`note.txt`, `--tools Read --permission-mode dontAsk --allowedTools Read`), `env -u ANTHROPIC_API_KEY claude -p --model haiku --output-format stream-json --verbose --max-turns 2 --setting-sources "" --disable-slash-commands --strict-mcp-config`. Raw streams: `tests/fixtures/cost_replay/resume/pf-r2-resume-{a,b,c}-haiku-2.1.309.jsonl`; ledger `docs/results/data/2026-10-30-nc/spend-R2.jsonl`.

| step | flags | subtype | num_turns | `total_cost_usd` | `model_usage_since_last_result` costUSD |
|---|---|---|---|---|---|
| A | new session, `--max-budget-usd 0.02`; read note.txt, tell its second line | success | 2 | 0.00078993 | (A's own) |
| B | `--resume A --max-budget-usd 0.00083` (above A's cost, below A+B); read it again, tell its first line | **success** | 2 | 0.00101399 | 0.00022406 |
| C | `--resume A --max-budget-usd 0.00005` (below the step's own cost) | **error_max_budget_usd** | 1 | 0.00142205 | 0.00040806 |

**Answer: the budget counts only the new step (this process).** B made two model calls; after the first one the session's cumulative cost was already above its 0.00083 cap, yet it finished (`success`, its own cost 0.00022406). The control C shows the cap is enforced on a resumed session when the step itself passes it (one-turn overshoot as in P1). So a resumed rebuild round's `--max-budget-usd` is the per-step budget as it is (no session cost added).

Also confirmed: on `--resume`, `total_cost_usd` and `modelUsage` are the session's **cumulative** figures (B = A + 0.00022406, C = B + 0.00040806), while the top-level `usage` and the result's `model_usage_since_last_result` are the step's own; C's top-level `usage` is all zeros (budget-exhausted shape) while its `modelUsage` tokens are cumulative. Studio books the difference to the last cumulative total it saw for the session (`session_totals` on the rebuild record).

Spend: 3 calls, USD 0.00142 of the 0.05 cap.
