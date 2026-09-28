"""Every T2 program the existing test suites check, gathered in one place so the T2 IR
tests can lower (and, later, emit and run) each of them.

`all_t2_programs()` yields `(name, CheckedProgram)` for:

* `tests/api/t2prog.py` and `tests/api/hardening.py` (their `PROGRAM`, with their own
  `PROFILE`);
* every `tests/fixtures/loops/*.py` item whose `LATTICE` program has a `@procedure`
  (with the item's `PROFILE`);
* every source in `SOURCES` of `tests/t2/test_fuel.py` and `tests/t2/test_size_fuel.py`
  (with the T2 test profile, as `tests.t2.util.checked` checks them).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator
from pathlib import Path

from lattice.check import CheckedProgram, check_source
from lattice.profile import Profile
from tests.t2.util import checked

_LOOPS = Path(__file__).resolve().parents[1] / "fixtures" / "loops"


def _program(source: str, profile: Profile) -> CheckedProgram:
    result = check_source(source, profile)
    assert result.ok, "\n".join(d.render() for d in result.diagnostics)
    assert result.program is not None and result.program.t2 is not None
    return result.program


def _api_programs() -> Iterator[tuple[str, CheckedProgram]]:
    for module_name in ("t2prog", "hardening"):
        module = importlib.import_module(f"tests.api.{module_name}")
        yield f"api/{module_name}", _program(module.PROGRAM, module.PROFILE)


def _loop_programs() -> Iterator[tuple[str, CheckedProgram]]:
    for path in sorted(_LOOPS.glob("l*.py")):
        module = importlib.import_module(f"tests.fixtures.loops.{path.stem}")
        source = getattr(module, "LATTICE", None)
        if isinstance(source, str) and "@procedure" in source:
            yield f"loops/{path.stem}", _program(source, module.PROFILE)


def _t2_test_programs() -> Iterator[tuple[str, CheckedProgram]]:
    for module_name in ("test_fuel", "test_size_fuel"):
        module = importlib.import_module(f"tests.t2.{module_name}")
        for i, source in enumerate(module.SOURCES):
            program = checked(source).program
            assert program is not None and program.t2 is not None
            yield f"t2/{module_name}[{i}]", program


# Each collector yields (name, program); add a collector here to widen the corpus.
_COLLECTORS: tuple[Callable[[], Iterator[tuple[str, CheckedProgram]]], ...] = (
    _api_programs,
    _loop_programs,
    _t2_test_programs,
)


def all_t2_programs() -> Iterator[tuple[str, CheckedProgram]]:
    """Every checked T2 program of the corpus, named by where it comes from."""
    for collect in _COLLECTORS:
        yield from collect()
