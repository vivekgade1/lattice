"""pycheck findings: one guarantee violation in the edited Python, with a fix hint."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

# code -> the guarantee (round-5 spec 2026-09-28 §3, items 1-6; PY800 is the parse gate)
CODES: dict[str, str] = {
    "PY800": "the app's code does not parse",
    "PY801": "ownership: an owned table's rows reach a caller who was not checked to own them",
    "PY802": "private columns: a private column reaches a response",
    "PY803": "bounds: a request value is written to the database or used as a limit unbounded",
    "PY804": "route order: a literal path is declared after a parameter path that swallows it",
    "PY805": "error conventions: an HTTP error differs from the app's answer for the same case",
    "PY806": "datetimes: a naive and an aware datetime are compared",
    "PY807": "partial writes: a commit that a later failure of the same request can follow",
}


@dataclass(frozen=True)
class Finding:
    """``file`` is relative to the app package directory (``api/routes/items.py``);
    ``hint`` names the fix in the code's own names. ``key`` identifies the finding
    across edits (code, route and subject, never a line): the policy's baseline holds the
    keys of the original app's own findings, which are its accepted conventions."""

    code: str
    file: str
    line: int
    message: str
    hint: str
    key: str = field(default="", compare=False)

    def render(self) -> str:
        return f"{self.file}:{self.line}: {self.code} {self.message}\n  fix: {self.hint}"


def render_findings(findings: Iterable[Finding], prefix: str = "") -> str:
    """The findings as repair feedback, one block per finding (``prefix`` before each
    file, e.g. ``"app/"``)."""
    return "\n".join(prefix + f.render() for f in findings)


__all__ = ["CODES", "Finding", "render_findings"]
