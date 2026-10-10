"""Lattice's MCP surface for Claude Code: a zero-dependency stdio MCP server over a Studio
project (``python -m lattice.mcp serve --project DIR``) and a PreToolUse guard hook
(``python -m lattice.mcp.guard --project DIR``).

The principle: nothing changes without a checked change set, and accepting one is a human
click in Studio. So the server exposes reads, ``change_propose`` and ``check_run``, and no
tool that accepts, rejects or writes the spec files; the guard denies the agent's own
attempts to write them."""
