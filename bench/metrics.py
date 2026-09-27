"""Efficiency and program-quality metrics: final-program size and token cost.

Pure functions only; the runners record these per outcome and the report
aggregates them.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

# USD per million (input, output) tokens. Served model ids are matched by
# prefix (longest first), e.g. "claude-haiku-4-5-20251001" -> claude-haiku-4-5.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
# Prompt-cache multipliers on the input price.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


@dataclass(frozen=True)
class ProgramMetrics:
    lines: int  # non-blank lines that are not comment-only
    statements: int  # ast.stmt nodes at every nesting level
    ast_nodes: int  # every node yielded by ast.walk
    chars: int  # len(code)


def program_metrics(code: str) -> ProgramMetrics:
    """Size of one program. Unparseable code gets lines/chars only (0 statements/nodes)."""
    if not code:
        return ProgramMetrics(lines=0, statements=0, ast_nodes=0, chars=0)
    lines = 0
    for line in code.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines += 1
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return ProgramMetrics(lines=lines, statements=0, ast_nodes=0, chars=len(code))
    nodes = list(ast.walk(tree))
    statements = sum(1 for node in nodes if isinstance(node, ast.stmt))
    return ProgramMetrics(lines=lines, statements=statements, ast_nodes=len(nodes), chars=len(code))


def price_for(model: str) -> tuple[float, float] | None:
    """(input, output) USD per million tokens for a served model id, by longest prefix."""
    if not model:
        return None
    for prefix in sorted(PRICES, key=len, reverse=True):
        if model.startswith(prefix):
            return PRICES[prefix]
    return None


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """USD cost of a call; ``input_tokens`` is the total prompt size (incl. cache
    reads and writes). Unknown model -> 0.0."""
    price = price_for(model)
    if price is None:
        return 0.0
    p_in, p_out = price
    uncached = input_tokens - cache_read_tokens - cache_creation_tokens
    return (
        uncached * p_in
        + cache_read_tokens * CACHE_READ_MULTIPLIER * p_in
        + cache_creation_tokens * CACHE_WRITE_MULTIPLIER * p_in
        + output_tokens * p_out
    ) / 1_000_000
