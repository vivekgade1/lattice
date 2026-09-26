"""Header check and host parse of Lattice source text."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lattice.diagnostics import Diagnostic, Severity, Span

HEADER = "# lattice: v1"


@dataclass(frozen=True)
class ParseResult:
    module: ast.Module | None
    diagnostics: tuple[Diagnostic, ...]
    source: str


def too_deep() -> Diagnostic:
    return Diagnostic(
        "L003",
        Severity.ERROR,
        Span(1, 1),
        "program is nested too deeply",
        "split deep expressions into several derive steps",
    )


def _normalize(source: str) -> str:
    if source.startswith("﻿"):
        source = source[1:]
    return source.replace("\r\n", "\n").replace("\r", "\n")


def parse(source: str) -> ParseResult:
    text = _normalize(source)
    diags: list[Diagnostic] = []
    first_line = text.split("\n", 1)[0].rstrip()
    if first_line != HEADER:
        diags.append(
            Diagnostic(
                "L001",
                Severity.ERROR,
                Span(1, 1),
                f"first line must be '{HEADER}'",
                f"add '{HEADER}' as the first line",
            )
        )
    module: ast.Module | None
    try:
        module = ast.parse(text)
    except SyntaxError as e:
        module = None
        diags.append(Diagnostic("L002", Severity.ERROR, Span(e.lineno or 1, e.offset or 1), e.msg))
    except (RecursionError, MemoryError):  # parser stack overflow on absurd nesting
        return ParseResult(None, (too_deep(),), text)
    except ValueError as e:  # e.g. source contains NUL bytes
        module = None
        diags.append(Diagnostic("L002", Severity.ERROR, Span(1, 1), str(e)))
    return ParseResult(module, tuple(diags), text)
