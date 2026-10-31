"""Reading MCP results that a real server process fenced (lattice/mcp/server.py fence_result):
the decoders of the stdio tests take the fences off, so their assertions compare the same values
the CLI or the model prints. The nonce is the server process's own, so this reads the layout
(``fencing.unfence`` only knows the current process's nonces)."""

from __future__ import annotations

import re
from typing import Any

_FENCED = re.compile(
    r"\A<<<DATA kind=[a-z-]+ nonce=([A-Za-z0-9]{6,64})>>>\n(.*)\n<<<END DATA nonce=\1>>>\Z", re.S
)


def strip_fences(node: Any) -> Any:
    """``node`` with every string that is exactly one data fence replaced by its payload."""
    if isinstance(node, str):
        m = _FENCED.match(node)
        return m.group(2) if m else node
    if isinstance(node, dict):
        return {k: strip_fences(v) for k, v in node.items()}
    if isinstance(node, list):
        return [strip_fences(v) for v in node]
    return node
