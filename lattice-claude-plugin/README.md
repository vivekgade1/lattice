# Lattice plugin for Claude Code

Makes a Lattice spec the contract for a Claude Code session: read-only spec tools over MCP, a guard hook that protects spec files, a check gate on Stop, two skills and three slash commands.

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

- PreToolUse on `Edit|Write|MultiEdit|NotebookEdit|Bash` runs `lattice mcp guard --project "$CLAUDE_PROJECT_DIR"` (equivalently `python -m lattice.mcp.guard`); it denies edits to requirements, spec and trace files, including shell writes.
- Stop runs `./check.sh` if it exists and the working tree has uncommitted changes. Default is non-blocking: a pass/fail summary goes to stderr. With `LATTICE_GATE=strict` in the environment a failing check exits 2, which makes Claude continue and fix. A re-entry guard (`stop_hook_active`) prevents infinite loops.

## Notes and fallbacks

- `CLAUDE_PROJECT_DIR` is set by Claude Code for hooks. In `.mcp.json` it is expanded with a `:-.` default, so if it is unset the server uses the current directory (the project root when you launch `claude` there). If your Claude Code version does not expand it in MCP config, edit `.mcp.json` and put an absolute path.
- The plugin uses `python` from PATH; to use a venv, change `command` to the venv's python.
- Claude Code namespaces a plugin's MCP tools: loaded through this plugin they are named `mcp__plugin_lattice_lattice__<tool>` (verified on Claude Code 2.1.296), registered by hand with `claude mcp add lattice` they are `mcp__lattice__<tool>`. The commands' `allowed-tools` list both spellings, so they run in `-p` mode without a permission prompt either way.
- Verified on 2.1.296: `${CLAUDE_PROJECT_DIR:-.}` in `.mcp.json` resolves to the project when `claude` is started in the project root.
- The MCP tools exposed are model_get, rule_get, spec_ask, spec_lint, check_run, code_links, graph_get, change_propose, change_get, change_list. There is deliberately no accept/reject tool; changes are accepted in Studio.
