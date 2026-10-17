"""Impact fixture removed_rule: the stock rule and its invariant removed."""

from dataclasses import replace

from tests.spec.minishop import SPEC as BASE

CANCEL, ADD = BASE.scenarios

SPEC = replace(BASE, invariants=(BASE.invariants[0],))
