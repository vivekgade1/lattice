"""Impact fixture conflict: a scenario contradicting another (L011)."""

from dataclasses import replace

from lattice.spec import Scenario, Step
from tests.spec.minishop import SPEC as BASE

CANCEL, ADD = BASE.scenarios

FIRST = Scenario(
    "ann cancels order 1 first",
    steps=(Step("POST", "/orders/1/cancel", user="ann", expect_status=409),),
)
SPEC = replace(BASE, scenarios=(CANCEL, ADD, FIRST))
