---
description: Run the Lattice checks and summarize findings by layer with rule ids
allowed-tools: mcp__plugin_lattice_lattice__check_run, mcp__plugin_lattice_lattice__rule_get, mcp__lattice__check_run, mcp__lattice__rule_get
---

Call the `check_run` tool from the lattice MCP server. Summarize the result:

- Overall pass or fail, with counts.
- Findings grouped by layer; for each, the rule id, a one-line message, and the location.
- Do not edit anything. Suggest the next step (fix code, or propose a spec change with /lattice:change).
