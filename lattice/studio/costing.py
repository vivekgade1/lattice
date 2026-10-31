"""List-price cost estimates of Claude model calls from their token counts (gap G2).

A ``claude -p`` step that ends with a ``result`` event carries the CLI's own estimate
(``total_cost_usd``, ``modelUsage[*].costUSD``): that figure is ``cost_basis``
``cli_list_estimate``. A step that is killed (a timeout, a token or USD cap, a crash) has no
result event; its cost is then estimated here from the usage its stream showed, all four
token kinds (input, output, cache reads, cache writes: ignoring the cache understated one
earlier run about 75x), with ``cost_basis`` ``stream_estimate``. An unknown model has no
estimate (None), never a made-up number.

Prices are USD per million tokens, keyed by served-model id prefix (longest prefix wins, as
``bench/metrics.py`` ``price_for``; this module copies rather than imports ``bench``, which
``lattice`` must not depend on). ``input_tokens`` is the uncached input, as in the API's
``usage``.

* ``claude-sonnet-5-5`` and ``claude-haiku-5-5`` are **verified**: an exact least-squares
  fit of ``modelUsage.costUSD`` over the archived ``claude -p`` result events in
  ``docs/results/data`` (CLI 2.1.296-2.1.309; 99 sonnet-5-5 and 26 haiku-5-5 events,
  largest relative residual 2e-14). Every cache write in those streams was a 1-hour one
  (``ephemeral_1h_input_tokens``), priced at 2x input.
* The other rows are **unverified** (no archived stream of theirs): input and output from
  ``bench/metrics.py`` ``PRICES``; cache reads at 0.1x input (``CACHE_READ_MULTIPLIER``
  there) and cache writes at 2x input (the CLI's 1-hour write rate seen above, not the
  bench's 1.25x five-minute rate), both on the high side for a cap.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

COST_BASIS_CLI = "cli_list_estimate"  # the result event's total_cost_usd
COST_BASIS_STREAM = "stream_estimate"  # estimated here from the stream's usage
COST_BASIS_UNPRICED = "unpriced"  # no result event and a model this table does not know

TOKEN_KINDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


class Price(NamedTuple):
    """USD per million tokens of each kind."""

    input: float
    output: float
    cache_read: float
    cache_creation: float
    verified: bool  # True: matches the CLI's own costUSD on archived streams


def _unverified(p_in: float, p_out: float) -> Price:
    return Price(p_in, p_out, round(p_in * 0.1, 6), round(p_in * 2.0, 6), False)


PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.10, 4.00, True),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01, 0.20, True),
    "claude-opus-5-5": _unverified(4.00, 20.00),
    "claude-opus-5": _unverified(5.00, 25.00),
    "claude-sonnet-5": _unverified(2.00, 10.00),
    "claude-haiku-4-5": _unverified(1.00, 5.00),
}


def price_for(model: str | None) -> Price | None:
    """The price row of a served model id, by longest prefix; None when unknown."""
    if not model:
        return None
    for prefix in sorted(PRICES, key=len, reverse=True):
        if model.startswith(prefix):
            return PRICES[prefix]
    return None


def estimate_cost(model: str | None, usage: Mapping[str, Any]) -> float | None:
    """The list-price USD of ``usage`` (the API's token keys, :data:`TOKEN_KINDS`) on
    ``model``; None when the model is unknown. Zero usage costs 0.0 on any model (the CLI's
    ``<synthetic>`` messages)."""
    tokens = [int(usage.get(k) or 0) for k in TOKEN_KINDS]
    if not any(tokens):
        return 0.0
    p = price_for(model)
    if p is None:
        return None
    rates = (p.input, p.output, p.cache_read, p.cache_creation)
    return sum(n * r for n, r in zip(tokens, rates, strict=True)) / 1_000_000
