# Lattice plugin for Claude Code

Makes a Lattice spec the contract for a Claude Code session: read-only spec tools over MCP, a guard hook that protects spec files, a check gate on Stop, two skills and three slash commands.

## Install once, use in every project (user level)

```sh
uv tool install -e /path/to/lattice          # puts `lattice` on your PATH (editable: follows the repo)
claude plugin marketplace add /path/to/lattice   # the repo root carries .claude-plugin/marketplace.json
claude plugin install lattice@lattice-local --scope user
```

After that every Claude Code session has the Lattice tools. In a directory without a spec the
server still starts: `project_status` reports what is missing (`REQUIREMENTS.md`, `spec.py`,
`spec.trace.json`) and the next steps, and the other tools start working as soon as
`lattice spec draft` has produced the spec, without restarting the session. The spec-file guard
hook is inert until a project has both `spec.py` and `spec.trace.json`, so other projects' own
`spec.py` or `REQUIREMENTS.md` stay editable.

Starting a new project: write `REQUIREMENTS.md`, run `lattice spec draft`, then `lattice studio .`.

## Quickstart

Prerequisite: the `lattice` package is importable by `python` (for example `uv pip install -e .` in the lattice repo, or run Claude from an activated venv).

```
cd /path/to/your/lattice/project
claude --plugin-dir /path/to/lattice/lattice-claude-plugin
# from the lattice repo root: claude --plugin-dir ./lattice-claude-plugin
```

Then try `/lattice:check`, `/lattice:ask what happens when a job fails?`, `/lattice:change allow retries up to 5`.

Run the MCP server standalone (for debugging, or to register it by hand):

```
lattice mcp serve --project /path/to/project
claude mcp add lattice -- lattice mcp serve --project /path/to/project
```

`lattice mcp ...` is the same as `python -m lattice.mcp ...` (the `lattice` command comes from
`uv tool install -e .` in the lattice repo; `python -m` works wherever the package is importable).

## What is inside

| Path | Purpose |
|---|---|
| `.claude-plugin/plugin.json` | Plugin manifest (name `lattice`, v0.1.0) |
| `.mcp.json` | Registers MCP server `lattice` (`python -m lattice.mcp serve --project ${CLAUDE_PROJECT_DIR:-.}`) |
| `hooks/hooks.json` | PreToolUse guard and Stop gate |
| `hooks/stop-check.sh` | Stop hook script |
| `skills/lattice-spec`, `skills/lattice-rebuild` | Spec-as-contract and rebuild-loop discipline |
| `commands/` | `/lattice:check`, `/lattice:ask`, `/lattice:change` |

Project-side template: `lattice/studio/templates/CLAUDE.md.tmpl` (copy to the project as `CLAUDE.md`).

## Hooks

- PreToolUse on `Edit|Write|MultiEdit|NotebookEdit|Bash` runs `lattice mcp guard --project "$CLAUDE_PROJECT_DIR"` (equivalently `python -m lattice.mcp.guard`); it denies edits to requirements, spec and trace files, and to the gate itself (`check.sh` and `studio.toml`, always), including shell writes. A project can protect more in `studio.toml`: `[mcp] protected = ["CONTEXT.md"]` for any file, `[mcp] harness = ["tests/spec_harness.py"]` for the spec test harness the gate runs (a directory protects everything under it). It stays inert until the project has a drafted spec.
- Stop is the completion gate and is **strict by default**: it runs `./check.sh` when the working tree is dirty, or when HEAD differs from the HEAD recorded at the last passing check (so committed work is checked too), and a failing check exits 2, which makes Claude continue and fix. `LATTICE_GATE=warn` restores the old non-blocking behaviour (summary on stderr, exit 0); `LATTICE_GATE=off` skips the hook. In a Lattice project (`spec.py` and `spec.trace.json`) a missing or non-executable `./check.sh` blocks with "check.sh missing or not executable"; other projects without `./check.sh` are untouched.
- The retry loop is bounded by the hook's own counter (kept per session under the git dir), not by Claude Code: a strict gate forces at most `LATTICE_GATE_MAX_BLOCKS` extra rounds per stop (default 3), not until green. After that it exits 0 with a "gate still failing" message, so read that message rather than assuming the run is green. `stop_hook_active` only tells a fresh stop (the counter restarts) from a forced continuation (the counter keeps counting); it is never a free pass. For headless runs (`claude -p`) always pass `--max-turns` as well.

## Notes and fallbacks

- `CLAUDE_PROJECT_DIR` is set by Claude Code for hooks. In `.mcp.json` it is expanded with a `:-.` default, so if it is unset the server uses the current directory (the project root when you launch `claude` there). If your Claude Code version does not expand it in MCP config, edit `.mcp.json` and put an absolute path.
- The plugin uses `python` from PATH; to use a venv, change `command` to the venv's python.
- Claude Code namespaces a plugin's MCP tools: loaded through this plugin they are named `mcp__plugin_lattice_lattice__<tool>` (verified on Claude Code 2.1.296), registered by hand with `claude mcp add lattice` they are `mcp__lattice__<tool>`. The commands' `allowed-tools` list both spellings, so they run in `-p` mode without a permission prompt either way.
- Verified on 2.1.296: `${CLAUDE_PROJECT_DIR:-.}` in `.mcp.json` resolves to the project when `claude` is started in the project root.
- The MCP tools exposed are model_get, rule_get, spec_ask, spec_lint, check_run, code_links, graph_get, change_propose, change_get, change_list. There is deliberately no accept/reject tool; changes are accepted in Studio.

## Several projects

When the session's directory is not under a `lattice.workspace.toml`, the MCP server also looks for one above the project directory and uses it if it declares that project. Set `LATTICE_WORKSPACE` to point at a manifest explicitly.
