"""synthesize(): the generate -> check -> repair loop for the Lattice arm."""

from __future__ import annotations

from dataclasses import dataclass

from lattice.check import CheckResult, check_source
from lattice.diagnostics import Diagnostic, Severity, Span, render_for_llm
from lattice.llm.client import Generation, Generator, extract_code
from lattice.profile import Profile

_NO_CODE_DIAGNOSTIC = Diagnostic(
    code="L000",
    severity=Severity.ERROR,
    span=Span(1, 1),
    message="response contained no ```python code block",
)


@dataclass(frozen=True)
class Round:
    index: int
    source: str | None
    diagnostics: tuple[Diagnostic, ...]
    generation: Generation


@dataclass(frozen=True)
class SynthesisResult:
    check: CheckResult | None
    source: str | None
    rounds: tuple[Round, ...]

    @property
    def ok(self) -> bool:
        return self.check is not None and self.check.ok


def _repair_message(diagnostics: tuple[Diagnostic, ...]) -> str:
    return (
        "The program failed Lattice checks:\n"
        + render_for_llm(diagnostics)
        + "\nReturn the complete corrected program in one ```python block."
    )


def synthesize(
    task_message: str,
    profile: Profile,
    generator: Generator,
    *,
    system: str,
    max_repairs: int = 2,
) -> SynthesisResult:
    """Generate a Lattice program, check it, and repair it up to ``max_repairs`` times.

    Messages sent to the generator never include the system prompt (it is
    passed separately on every call as ``system``). Round indices are 1-based.
    """
    messages: list[dict[str, str]] = [{"role": "user", "content": task_message}]
    rounds: list[Round] = []
    check_result: CheckResult | None = None
    source: str | None = None

    for i in range(max_repairs + 1):
        generation = generator.generate(system, messages)
        code = extract_code(generation.text)

        if code is None:
            check_result = None
            source = None
            diagnostics: tuple[Diagnostic, ...] = (_NO_CODE_DIAGNOSTIC,)
        else:
            check_result = check_source(code, profile)
            source = code
            diagnostics = check_result.diagnostics

        rounds.append(
            Round(index=i + 1, source=source, diagnostics=diagnostics, generation=generation)
        )

        if check_result is not None and check_result.ok:
            return SynthesisResult(check_result, source, tuple(rounds))

        if i == max_repairs:
            break

        messages.append({"role": "assistant", "content": generation.text})
        messages.append({"role": "user", "content": _repair_message(diagnostics)})

    return SynthesisResult(check_result, source, tuple(rounds))
