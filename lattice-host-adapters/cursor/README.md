# Lattice on Cursor (prototype)

Copy `.cursor/hooks.json` and `.cursor/mcp.json` into the project (or `~/.cursor/`) and `AGENTS.md` to
the project root. `lattice` must be on PATH and `python` must be able to `import lattice`.

- `hooks.json` runs `python -m lattice.mcp.host_shim --host cursor` on `beforeShellExecution` and on
  `preToolUse` for `Write|Delete`. The shim translates Cursor's payload for `lattice.mcp.guard` and
  answers with `{"permission": "deny", ...}`. Add `"failClosed": true` only if you accept that a crash
  in the hook blocks the call (the shim itself exits 0 on malformed input).
- `studio.toml` can name more protected files:

      [mcp]
      protected = ["check.sh", "studio.toml"]

- Headless metrics: `cursor-agent -p "..." --output-format stream-json | python scripts/host_stream_metrics.py - --protected spec.py check.sh`.

Unverified facts and the choice of host are in the docstring of `lattice/mcp/host_shim.py`.
