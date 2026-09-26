"""P4 — types and schemas. Stub: replaced by the types task."""

from __future__ import annotations

import ast
from collections.abc import Mapping

from lattice.check.context import CheckContext
from lattice.typesys import StepT


def run(module: ast.Module, ctx: CheckContext) -> Mapping[str, StepT]:
    return {}
