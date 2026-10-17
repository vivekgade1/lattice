"""Impact fixture bad_invariant: an invariant the seed already violates."""

from dataclasses import replace

from lattice.spec import Invariant
from tests.spec.minishop import SPEC as BASE

CANCEL, ADD = BASE.scenarios

SPEC = replace(
    BASE,
    invariants=(
        *BASE.invariants,
        Invariant("stock_at_least_six", "Stock never falls below six", "Product.stock >= 6"),
    ),
)
