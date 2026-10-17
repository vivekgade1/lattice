"""Impact fixture status_changed: cancelling twice answers 400."""

from dataclasses import replace

from tests.spec.minishop import SPEC as BASE

CANCEL, ADD = BASE.scenarios

SPEC = replace(
    BASE,
    scenarios=(
        replace(CANCEL, steps=(CANCEL.steps[0], replace(CANCEL.steps[1], expect_status=400))),
        ADD,
    ),
)
