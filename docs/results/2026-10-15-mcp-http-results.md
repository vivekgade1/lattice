# Results: connected-MCP rules R80/R81 over HTTP (G9)

Pre-registration: `2026-10-15-mcp-http-prereg.md` (with Amendment 1). Branch `np-mcphttp`.
Data: `docs/results/data/2026-10-15-mcp-http/`.

## What was built
`tests/studio/test_mcp_intake_http.py` (46 tests): a real `python -m lattice.studio` process with a fake
`claude`, server environment built from nothing. It checks, over HTTP, the `GET /api/mcp` bodies and
cache/refresh counts (11 server instances), the exact `--allowedTools`/`--disallowedTools` of 17 chat turns
per `allow_writes` setting, 14 invariants, and the credential scrub (32 fake calls). Plus dogfood rule R98
and scenario `chat_sessions_start_idle_and_refuse_bad_requests` (14 steps) and 14 mutants.
**No `lattice/` change; no defect found.**

## Result: every pre-registered prediction held
194 checks per pass, 10 identical passes (canonical sha256 `fe34cea7500a30cf...`; orchestrator re-run 3
passes identical, 46/46). Mutants MH-1..MH-14 14/14 caught by the new module (control passes); the dogfood
gate catches only MH-14, and the three old suites do **not** catch MH-9 (sessions wired without the
intake): only the new module sees that integration bug. `dogfood/studio/check.sh` PASS, 54 scenarios,
449 cases (was 435). `tests/studio tests/mcp`: 723 passed, 1 skipped, 0 failed.

## Findings (all predicted, none a defect against R80)
- F1: the stub view's note says "runs no Claude Code session", yet on a stub server a chat message runs
  `claude_bin` (fails closed: connector tools are denied).
- F2: `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_FOUNDRY_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK` reach `claude`
  in 32/32 calls (`cli_env` strips only three names and `CLAUDE_CODE_USE_*`). Owner's call.
- F3: `writes_allowed` is true for a listed server that is not connected (its counts are 0).
- F4: `search_and_replace`, `get_or_insert_row`, `fetch_and_archive` classify as read (matches API.md).
- F5: `allow_writes` matching is case-sensitive and a typo gives no warning.

## Limits
The fake `claude` enforces nothing: no real connector call, no OAuth, no real CLI enforcement of
`--allowedTools`; UI rendering of R81 needs a browser. The gate still cannot check R80 itself.

## Baseline anomalies (not caused by this change)
Layer-3 runners are sensitive to leftover `lattice-cli-cwd-*` directories in TMPDIR (a tmp-left guard):
with a fresh TMPDIR, exclude_probes 12/12 and linkedq_probes 10/10. `impact_probes` I8 flipped between
runs. One flake in `tests/studio/test_projects.py` (a commit-second boundary) did not recur.

## Integration note
Tool lists are pinned to `a4ab4a1`; R98 and the trace entry must be re-appended after other branches'
rules (re-run `Trace.from_json` and `extract_rules`).
