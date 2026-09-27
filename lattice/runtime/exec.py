"""Execute a Plan node by node with polars: sandboxed paths, schema assertions, limits."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from lattice.check import CheckResult, check_source
from lattice.check.p5_effects import normalize_output
from lattice.diagnostics import Span
from lattice.ir import Node, Plan, lower
from lattice.profile import Profile
from lattice.runtime import (
    LatticeExprError,
    LatticeInternalError,
    LatticeRuntimeError,
    is_panic,
    ops,
)
from lattice.runtime.exprs import DTYPES, Guard


@dataclass(frozen=True)
class RuntimeIssue:
    code: str
    span: Span
    message: str
    count: int

    def render(self) -> str:
        return f"{self.span.line}:{self.span.col}: warning {self.code}: {self.message}"


@dataclass(frozen=True)
class RunResult:
    outputs: Mapping[str, pl.DataFrame]  # write path -> frame written
    warnings: tuple[RuntimeIssue, ...]


_GUARD_MESSAGES: Mapping[str, str] = {
    "R301": "division or modulo by zero produced null",
    "R302": "math domain error (log/sqrt of an invalid value) produced null",
    "R303": "failed conversion produced null",
}


def _resolve(root: Path, path: str, span: Span) -> Path:
    try:
        resolved = (root / path).resolve()
    except (ValueError, OSError) as err:  # e.g. an embedded NUL byte
        raise LatticeRuntimeError("R102", span, f"invalid path {path!r}: {err}") from err
    if not resolved.is_relative_to(root):
        raise LatticeRuntimeError(
            "R102", span, f"path '{path}' resolves outside the working directory"
        )
    return resolved


def _count_guards(df: pl.DataFrame, guards: Sequence[Guard], span: Span) -> list[RuntimeIssue]:
    if not guards:
        return []
    names = [f"__lattice_guard_{i}" for i in range(len(guards))]
    exprs = [
        g.fill_null(False).cast(pl.Int64).alias(n) for n, (_, g) in zip(names, guards, strict=True)
    ]
    totals = df.lazy().with_columns(exprs).select(pl.col(names).sum()).collect()
    counts: dict[str, int] = {}
    for n, (code, _) in zip(names, guards, strict=True):
        counts[code] = counts.get(code, 0) + int(totals[n][0] or 0)
    return [
        RuntimeIssue(code, span, f"{_GUARD_MESSAGES.get(code, code)} in {k} row(s)", k)
        for code, k in counts.items()
        if k > 0
    ]


def _assert_schema(node: Node, df: pl.DataFrame) -> None:
    if node.schema is None:
        raise LatticeInternalError(f"E901 node '{node.id}' has no predicted schema")
    expected = [(n, DTYPES[s]) for n, s in node.schema.columns]
    actual = list(df.schema.items())
    if actual != expected:
        raise LatticeInternalError(
            f"E901 node '{node.id}' ({node.op}) at {node.span.line}:{node.span.col}: "
            f"runtime schema {actual} != predicted {expected}"
        )


def _run_op(
    node: Node, inputs: list[pl.DataFrame], root: Path, max_rows: int
) -> tuple[pl.DataFrame, list[Guard]]:
    p = node.params
    try:
        match node.op:
            case "load":
                if node.schema is None:
                    raise LatticeInternalError(f"load node '{node.id}' has no schema")
                path = _resolve(root, p["path"], node.span)
                return ops.load(path, p["path"], node.schema, node.span), []
            case "where":
                return ops.where(inputs[0], p["predicate"])
            case "derive":
                return ops.derive(inputs[0], p["columns"])
            case "select":
                return ops.select(inputs[0], p["cols"]), []
            case "rename":
                return ops.rename(inputs[0], p["mapping"]), []
            case "group":
                return ops.group(inputs[0], p["by"], p["aggs"])
            case "join":
                # estimate first: a many-to-many join could exhaust memory before the
                # row limit is checked on its output
                n = ops.join_size(inputs[0], inputs[1], p["on"], p["how"])
                if n > max_rows:
                    raise LatticeRuntimeError(
                        "R201", node.span, f"join() would produce {n} rows (limit {max_rows})"
                    )
                return ops.join(inputs[0], inputs[1], p["on"], p["how"]), []
            case "sort":
                return ops.sort(inputs[0], p["by"], p["desc"]), []
            case "limit":
                return ops.limit(inputs[0], p["n"]), []
            case _:
                raise LatticeInternalError(f"no runtime implementation for op '{node.op}'")
    except LatticeExprError as err:
        message = f"{node.op}() failed: {err.message}"
        raise LatticeRuntimeError(err.code, node.span, message) from err
    except pl.exceptions.PolarsError as err:
        text = str(err).strip().splitlines()
        raise LatticeRuntimeError(
            "R103", node.span, f"{node.op}() failed: {text[0] if text else type(err).__name__}"
        ) from err
    except BaseException as err:
        if not is_panic(err):
            raise
        text = str(err).strip().splitlines()
        raise LatticeRuntimeError(
            "R103", node.span, f"{node.op}() failed: {text[0] if text else 'engine panic'}"
        ) from err


def execute(
    plan: Plan,
    io_root: Path,
    *,
    outputs: frozenset[str] | None = None,
    max_rows: int = 5_000_000,
    time_limit_s: float = 60.0,
) -> RunResult:
    """Run the plan. Limits: an op whose output exceeds `max_rows` raises R201 (a join is
    estimated from per-key counts and refused *before* it runs); the time limit is
    checked between ops (R202), so one long-running op is not interrupted. With
    `outputs`, a write to any other file is refused (R104) before anything is written."""
    root = io_root.resolve()
    last_use: dict[str, int] = {}
    for i, node in enumerate(plan.nodes):
        for inp in node.inputs:
            last_use[inp] = i
    frames: dict[str, pl.DataFrame] = {}
    written: dict[str, pl.DataFrame] = {}
    warnings: list[RuntimeIssue] = []
    start = time.perf_counter()
    for i, node in enumerate(plan.nodes):
        if i > 0 and time.perf_counter() - start > time_limit_s:
            raise LatticeRuntimeError(
                "R202", node.span, f"time limit of {time_limit_s:g}s exceeded"
            )
        try:
            inputs = [frames[x] for x in node.inputs]
        except KeyError as err:
            raise LatticeInternalError(f"node '{node.id}' input {err} is not available") from err
        if node.op == "write":
            if len(inputs) != 1:
                raise LatticeInternalError("write takes exactly one table")
            shown = node.params["path"]
            if outputs is not None and normalize_output(shown) not in outputs:
                allowed = ", ".join(sorted(outputs)) or "nothing"
                raise LatticeRuntimeError(
                    "R104", node.span, f"write to '{shown}' refused: may only write {allowed}"
                )
            path = _resolve(root, shown, node.span)
            ops.write(inputs[0], path, shown, node.span)
            written[shown] = inputs[0]
        else:
            df, guards = _run_op(node, inputs, root, max_rows)
            _assert_schema(node, df)
            if df.height > max_rows:
                raise LatticeRuntimeError(
                    "R201", node.span, f"{node.op}() produced {df.height} rows (limit {max_rows})"
                )
            if guards:
                warnings += _count_guards(inputs[0], guards, node.span)
            if node.id in last_use:
                frames[node.id] = df
        for inp in set(node.inputs):
            if last_use.get(inp) == i:
                frames.pop(inp, None)
    return RunResult(written, tuple(warnings))


def run_source(
    source: str, profile: Profile, io_root: Path
) -> tuple[CheckResult, RunResult | None]:
    """Check -> lower -> execute. Returns (check result, None) when the check fails.
    Runtime failures raise LatticeRuntimeError (code, span, message)."""
    result = check_source(source, profile)
    if result.program is None:
        return result, None
    return result, execute(lower(result.program), io_root, outputs=profile.outputs)
