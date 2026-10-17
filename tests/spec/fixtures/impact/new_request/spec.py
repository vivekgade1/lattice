"""Impact fixture new_request: a new scenario with a request nothing observed."""

from dataclasses import replace

from lattice.spec import Scenario, Step
from tests.spec.minishop import SPEC as BASE

CANCEL, ADD = BASE.scenarios

READ = Scenario(
    "ann reads her order",
    steps=(Step("GET", "/orders/1", user="ann", expect_status=200, expect_body={"id": 1}),),
)
SPEC = replace(BASE, scenarios=(CANCEL, ADD, READ))
