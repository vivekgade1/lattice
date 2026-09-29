"""Header check and host parse of Lattice source text."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lattice.diagnostics import Diagnostic, Severity, Span

HEADER = "# lattice: v1"
# The accepted first lines: `v1` and `v1.1` programs both run under the v1.1 rules
# (docs/lattice-language-rules.md; spec 2026-09-28 go-target §3.3).
HEADERS = (HEADER, "# lattice: v1.1")


def split_header(source: str) -> tuple[str, str] | None:
    """(the header line, the rest) when `source` starts with an accepted header line,
    else None."""
    first, nl, rest = source.partition("\n")
    if first.rstrip() in HEADERS:
        return first, nl + rest
    return None


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
    if first_line not in HEADERS:
        diags.append(
            Diagnostic(
                "L001",
                Severity.ERROR,
                Span(1, 1),
                f"first line must be '{HEADER}' (or '{HEADERS[1]}')",
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
