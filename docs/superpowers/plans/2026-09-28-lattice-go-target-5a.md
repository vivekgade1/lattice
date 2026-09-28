# Lattice Go Target (5a: compiler and equivalence gate) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile checked Lattice API programs (T0/T1 endpoints and T2 procedures) to a Go binary that behaves identically to the Python/FastAPI output, proven by a zero-mismatch differential gate.

**Architecture:** The front end and `EndpointPlan` are unchanged. A new language-neutral T2 IR (`lattice/t2ir`) replaces the Python-AST instrumentation in `lattice/t2/compile.py`; a Python emitter and a Go emitter both read it. Semantics v1.1 (`lattice/targets.py`) pins every Python/Go difference; a new checker pass `p8_target` rejects what a target cannot run. The Go side is a hand-written runtime module (`lattice/gogen/rt`) plus generated `main` package files, built with `go build`, and driven by the existing differential machinery through an ASGI→HTTP proxy.

**Tech Stack:** Python 3.14 (uv, pytest), Go 1.26 (`net/http`, `database/sql`, `modernc.org/sqlite`, `golang.org/x/text/cases`), existing Lattice checker, `lattice.api.ir`, `tests/api/test_codegen_differential.py` registry.

**Spec:** `docs/superpowers/specs/2026-09-28-lattice-go-target-design.md` (sub-project 5). This plan covers build-order steps 1–5 (through the equivalence gate). Steps 6–7 (benchmark, live runs) are plan 5b, written after this plan's gate passes.

## Global Constraints

- Semantics version string: `"1.1"`; programs with `# lattice: v1` and `# lattice: v1.1` both check and run under v1.1 rules.
- `Int` is signed 64-bit: `INT_MIN = -(1 << 63)`, `INT_MAX = (1 << 63) - 1`; overflow raises runtime error **R623** on both targets.
- New checker codes: **L890** natives on a target without natives; **L891** tabular program on a target without the tabular profile; **L892** a T2 call target not yet available on the chosen target; **L926** a regular expression outside the RE2 subset (every target). New runtime code: **R623**.
- Target names: `"python"` (default) and `"go"`. `check_source(source, profile, *, target="python")`.
- Go toolchain: `go1.26` at `go` on `PATH`. Go module path of the runtime: `lattice.dev/rt`. Generated app module: `latticeapp`.
- Go binary CLI: `app --db <path> --addr 127.0.0.1:0`; prints exactly one line `LISTENING <host:port>` on stdout when ready. With env `LATTICE_TRACE_SQL=1` every response carries header `X-Lattice-Sql-Count: <n>` (data statements only, same rule as `bench.api_runner._is_counted_statement`).
- No natives on Go in this plan. Tabular profile stays Python-only.
- Every task: TDD, `uv run pytest` green for the whole suite before commit, commit messages end with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Review Focus

1. **Non-ASCII text** (`'ß'.upper()`, `'İ'.lower()`, emoji, combining marks) in `len`, slicing, `upper/lower/casefold/title`: Python results must be reproduced exactly on Go → pinned by semantics cases in Task 6, run on Go in Tasks 8–9.
2. **Set iteration order of strings** differs between Python processes (hash seed): v1.1 makes sets insertion-ordered on both targets → pinned in Task 5 (`test_set_order_is_insertion_order_across_hash_seeds`) and Task 8.
3. **Int boundaries** (`INT_MIN // -1`, `abs(INT_MIN)`, `-INT_MIN`, `2 ** 63`, `int("9223372036854775808")`, `INT_MAX + 1` inside `sum`) → R623 on both targets; pinned in Task 6 cases, Task 7 Go tests.
4. **Float text** (`1.0`, `1e16`, `1000000.0`, `-0.0`, `0.0001`, `1e-05`, `nan`, `inf` in `str()`, f-strings and `json.dumps`) → pinned in Task 6 cases and Task 7 `TestReprFloat`.
5. **Hostile requests** to the Go binary (invalid UTF-8 body, non-object JSON, duplicate query parameters, 10 MB body, concurrent writes hitting `database is locked`) → same status/body as Python; pinned by the probe cases in Task 13 and `TestConcurrentWritesDoNotFail` in Task 11.

---

## File Structure

| File | Responsibility |
|---|---|
| `lattice/targets.py` | v1.1 constants, `Target` capabilities, `TARGETS`, `target_named`, `supports_call` |
| `lattice/check/p8_target.py` | L890/L891/L892 |
| `lattice/check/re2.py` | `re2_problem(pattern) -> str | None` (L926) |
| `lattice/t2ir/__init__.py`, `nodes.py`, `lower.py`, `dump.py` | T2 IR, lowering from `T2Program`, stable text dump for golden tests |
| `lattice/t2/emit_py.py` | T2 IR → Python module source (`T2Code`) |
| `lattice/t2/compile.py` | Reduced to `T2Code` + `compile_program` delegating to IR + `emit_py` (instrumentation deleted) |
| `lattice/t2/support.py` | + R623 checks, `T2Set` (insertion-ordered set), `T2Counter` tie order |
| `lattice/gogen/rt/` (Go module `lattice.dev/rt`) | Runtime: `errors.go fuel.go ints.go floats.go strs.go lists.go omap.go sets.go opt.go times.go regex.go jsonv.go try.go http.go params.go auth.go sqlitefn.go outbox.go` + `*_test.go` |
| `lattice/gogen/types.py` | T2 type → Go type spelling; generated tuple/row/record structs |
| `lattice/gogen/t2.py` | T2 IR → `t2.go` |
| `lattice/gogen/endpoints.py` | `EndpointPlan`s → `handlers.go` + `main.go` |
| `lattice/gogen/build.py` | Write module, `go build`, content-hash cache |
| `lattice/gogen/__main__.py` | `python -m lattice.gogen build <source> --profile <module:attr> --out <dir>` |
| `lattice/gogen/proxy.py` | Run a binary per case; ASGI proxy app for `verify`; `run_go_case` |
| `tests/check/test_targets.py`, `tests/check/test_re2.py` | Tasks 1–2 |
| `tests/t2ir/test_lower.py`, `tests/t2ir/golden/*.txt` | Tasks 3–4 |
| `tests/t2/test_emit_py_differential.py` | Task 5 |
| `tests/semantics_v11/cases.py`, `test_python.py`, `test_go.py` | Tasks 6, 8–10 |
| `tests/gogen/test_t2_go.py`, `test_build.py`, `test_go_differential.py`, `test_fuel_parity.py` | Tasks 8–13 |

---

### Task 0: Correct the spec

**Files:**
- Modify: `docs/superpowers/specs/2026-09-28-lattice-go-target-design.md`

- [ ] **Step 1: Apply the corrections found while planning**

Edit §3.1, §3.2, §4, §6 so that:
- §3.2 codes read L890 (natives), L891 (tabular), L892 (T2 call not yet on target, the staging mechanism); drop the `decimal` rule (T2's allowlist has no `decimal`: `lattice/catalog.py` `T2_STDLIB`).
- §3.1 adds rows: `set` — insertion-ordered iteration on every target (Python's string hashing is seeded per process); `Counter.most_common` — ties in insertion order; Regex — also rejects possessive repeats and atomic groups, and `\d \w \s \b` are ASCII (Python compiles with `re.ASCII`); `str.upper/lower/casefold/title` — full Unicode case mapping (`'ß'.upper() == 'SS'`); `str()` / f-string of a list, dict, set or tuple — L892 on Go in this sub-project.
- §4 `IndexError`/`KeyError` row: "raised as a Lattice error value (`*rt.Error`) that `try` blocks catch and the handler turns into a 500; never an unrecovered panic".
- §6 equivalence gate: "the API codegen differential registry (`tests/api/test_codegen_differential.py` `PROGRAMS`: the 40 task programs with probes, plus the t2/hardening/loops/gaps/mine/round2/partb/n1b/solo programs) — every program that passes `check_source(..., target="go")`; programs rejected only with L890 are listed and asserted as the expected exclusions. The FastAPI template suite is excluded: its auth is a native (`NativeToken("decode_token")`)."

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-09-28-lattice-go-target-design.md
git commit -m "docs(spec): 5 — codes L890-L892, v1.1 rows found while planning, gate suites

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Targets and the `p8_target` pass

**Files:**
- Create: `lattice/targets.py`, `lattice/check/p8_target.py`, `tests/check/test_targets.py`
- Modify: `lattice/check/__init__.py` (`check_source`, `_check_source`: add `target` kw; run p8 after p7), `lattice/catalog.py` (L-code descriptions dict near line 676: add L890–L892, L926)

**Interfaces:**
- Produces:
  - `lattice.targets.SEMANTICS_VERSION: str = "1.1"`, `INT_MIN: int`, `INT_MAX: int`
  - `@dataclass(frozen=True) class Target: name: str; natives: bool; tabular: bool; t2_calls: frozenset[str] | None` (`None` = every call target)
  - `TARGETS: Mapping[str, Target]` with `"python"` and `"go"`; `GO_T2_CALLS: set[str]` (module-level, grows in Tasks 8–10)
  - `target_named(name: str) -> Target` (raises `LatticeInternalError` for unknown)
  - `supports_call(target: Target, call: str) -> bool` — `proc:`, `record:` and `helper:` prefixes are always supported; otherwise `t2_calls is None or call in t2_calls`
  - `check_source(source: str, profile: Profile, *, target: str = "python") -> CheckResult`
  - `lattice.check.p8_target.run(module: ast.Module, ctx: CheckContext, t2: T2Program | None, target: Target) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/check/test_targets.py
from textwrap import dedent

import pytest

from bench.api_datasets import api_profile
from lattice.check import check_source
from lattice.runtime import LatticeInternalError
from lattice.targets import INT_MAX, INT_MIN, TARGETS, supports_call, target_named
from tests.check.t2_support import PROFILE as T2_PROFILE

PLAIN = dedent('''\
    # lattice: v1
    @endpoint("GET /products")
    def products(limit: Int(ge=1, le=100) = 20) -> Page:
        return page(Products, limit=limit)
''')


def codes(result):
    return [d.code for d in result.diagnostics]


def test_int_bounds():
    assert INT_MIN == -(2**63) and INT_MAX == 2**63 - 1


def test_known_targets():
    assert set(TARGETS) == {"python", "go"}
    assert target_named("go").natives is False
    with pytest.raises(LatticeInternalError):
        target_named("cobol")


def test_supports_call_prefixes_always():
    go = target_named("go")
    assert supports_call(go, "proc:anything")
    assert supports_call(go, "record:R")
    assert supports_call(go, "helper:days")
    assert supports_call(target_named("python"), "std:json.dumps")


def test_default_target_is_python_and_plain_program_passes_on_go():
    assert check_source(PLAIN, api_profile()).ok
    assert check_source(PLAIN, api_profile(), target="go").ok


def test_l890_native_call_rejected_on_go(realapps_source, realapps_profile):
    ok = check_source(realapps_source, realapps_profile)
    assert ok.ok
    go = check_source(realapps_source, realapps_profile, target="go")
    assert not go.ok
    assert "L890" in codes(go)


def test_l892_unsupported_t2_call_on_go():
    src = dedent('''\
        # lattice: v1
        import json

        @procedure
        def enc(x: Int) -> Str:
            return json.dumps(x)
    ''')
    assert check_source(src, T2_PROFILE).ok
    go = check_source(src, T2_PROFILE, target="go")
    assert "L892" in codes(go)
```

Add the two fixtures to `tests/conftest.py` (shared by `tests/check` and `tests/gogen`):

```python
import pytest


@pytest.fixture
def realapps_source() -> str:
    from tests.api import realapps
    return realapps.PROGRAM


@pytest.fixture
def realapps_profile():
    from tests.api import realapps
    return realapps.PROFILE
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/check/test_targets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lattice.targets'`

- [ ] **Step 3: Implement `lattice/targets.py`**

```python
"""Semantics v1.1 and target capabilities (spec 2026-09-28 go-target §3).

One meaning for every target: the rules below are implemented by each emitter
(`lattice.t2.emit_py`, `lattice.gogen`). A target that cannot run a construct says so
here, and `lattice.check.p8_target` rejects the program for it before code is generated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from lattice.runtime import LatticeInternalError

SEMANTICS_VERSION = "1.1"
INT_MIN = -(1 << 63)
INT_MAX = (1 << 63) - 1

_ALWAYS = ("proc:", "record:", "helper:")

# T2 call targets (CheckedProcedure.targets values) the Go emitter implements. Grows as
# the Go runtime gains them (plan 5a Tasks 8-10); anything else is L892 on Go.
GO_T2_CALLS: set[str] = set()


@dataclass(frozen=True)
class Target:
    name: str
    natives: bool
    tabular: bool
    t2_calls: frozenset[str] | None  # None: every call target the checker accepts


def _go() -> Target:
    return Target("go", natives=False, tabular=False, t2_calls=frozenset(GO_T2_CALLS))


def target_named(name: str) -> Target:
    if name == "python":
        return TARGETS["python"]
    if name == "go":
        return _go()  # rebuilt: GO_T2_CALLS may have grown since import
    raise LatticeInternalError(f"unknown target {name!r} (known: python, go)")


def supports_call(target: Target, call: str) -> bool:
    if call.startswith(_ALWAYS):
        return True
    return target.t2_calls is None or call in target.t2_calls


TARGETS: Mapping[str, Target] = MappingProxyType(
    {"python": Target("python", natives=True, tabular=True, t2_calls=None), "go": _go()}
)
```

- [ ] **Step 4: Implement `lattice/check/p8_target.py`**

```python
"""P8 — target capabilities (spec 2026-09-28 go-target §3.2): reject programs the chosen
code target cannot run with v1.1 semantics.

* L890 — a `native.<name>(...)` call, `for_each(t, native.x, ...)`, or NativeToken auth,
  on a target without natives.
* L891 — a program of the tabular profile on a target without it.
* L892 — a T2 call (builtin, method or stdlib function) the target does not implement yet.

The Python target supports everything, so none of these fire for it."""

from __future__ import annotations

import ast

from lattice.check.context import CheckContext
from lattice.check.t2 import T2Program
from lattice.profile import NativeToken
from lattice.targets import Target, supports_call


def _is_native(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "native"
    )


def run(module: ast.Module, ctx: CheckContext, t2: T2Program | None, target: Target) -> None:
    if target.name == "python":
        return
    api = ctx.profile.api
    if api is None and not target.tabular:
        ctx.error(
            "L891",
            module.body[0] if module.body else ast.Pass(lineno=1, col_offset=0),
            f"tabular programs cannot be compiled to target '{target.name}' yet",
            "compile this program for target 'python'",
        )
        return
    if not target.natives:
        if api is not None and isinstance(api.auth, NativeToken):
            ctx.error(
                "L890",
                module.body[0],
                f"the profile's auth calls native '{api.auth.native}', and target "
                f"'{target.name}' has no natives",
                "use token-table auth, or compile for target 'python'",
            )
        for node in ast.walk(module):
            if _is_native(node):
                ctx.error(
                    "L890",
                    node,
                    f"'{ast.unparse(node)}' is a native; target '{target.name}' has no natives",
                    "express it in Lattice (a @procedure), or compile for target 'python'",
                )
    if t2 is not None:
        for proc in t2.procedures.values():
            for node in ast.walk(proc.node):
                call = proc.targets.get(id(node))
                if call is None:
                    continue
                if call.startswith("native:") and not target.natives:
                    ctx.error("L890", node, f"'{call[7:]}' is a native; target "
                              f"'{target.name}' has no natives",
                              "compile for target 'python'")
                elif not call.startswith("native:") and not supports_call(target, call):
                    ctx.error(
                        "L892",
                        node,
                        f"'{call.split(':', 1)[1]}' is not available on target "
                        f"'{target.name}' yet",
                        "rewrite with a supported call, or compile for target 'python'",
                    )
```


- [ ] **Step 5: Wire into `check_source`**

In `lattice/check/__init__.py`: change `check_source(source, profile)` to `check_source(source: str, profile: Profile, *, target: str = "python")`, pass `target` to `_check_source`, and after `p7_backend.run(module, ctx)` add:

```python
    p8_target.run(module, ctx, t2_program, target_named(target))
```

with imports `from lattice.check import p8_target` and `from lattice.targets import target_named`. In `lattice/catalog.py`, add to the L-code description mapping:

```python
    "L890": "a native (call, effect or NativeToken auth) on a target without natives",
    "L891": "a tabular-profile program on a target without the tabular profile",
    "L892": "a T2 call the chosen target does not implement yet",
    "L926": "a regular expression outside the RE2 subset (lookaround, backreference, "
    "conditional, possessive repeat or atomic group)",
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/check/test_targets.py -v && uv run pytest -q`
Expected: all PASS (the L892 test passes because `GO_T2_CALLS` is empty).

- [ ] **Step 7: Commit**

```bash
git add lattice/targets.py lattice/check/p8_target.py lattice/check/__init__.py lattice/catalog.py tests/check/test_targets.py tests/conftest.py
git commit -m "feat(check): v1.1 targets and the p8_target pass (L890-L892)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: RE2-only regular expressions (L926) and ASCII classes

**Files:**
- Create: `lattice/check/re2.py`, `tests/check/test_re2.py`
- Modify: `lattice/check/t2/body.py:1874` (next to the `redos_risk` call), the `Str(pattern=...)` constraint check (find it: `grep -n "redos_risk" -r lattice/check`), `lattice/t2/support.py` and `lattice/api/runtime.py` / codegen prelude (compile patterns with `re.ASCII`)

**Interfaces:**
- Produces: `lattice.check.re2.re2_problem(pattern: str) -> str | None` (a human-readable reason, or `None` when RE2 accepts it)

- [ ] **Step 1: Failing tests**

```python
# tests/check/test_re2.py
import pytest

from lattice.check.re2 import re2_problem


@pytest.mark.parametrize("pattern", [
    r"^[a-z0-9_]+$", r"(?P<user>\w+)@(?P<host>\w+)", r"\d{3}-\d{4}", r"a|b|c", r"(ab)+?",
])
def test_accepted(pattern):
    assert re2_problem(pattern) is None


@pytest.mark.parametrize("pattern,word", [
    (r"foo(?=bar)", "lookahead"),
    (r"(?<!x)y", "lookbehind"),
    (r"(a)\1", "backreference"),
    (r"(?P<q>')x(?P=q)", "backreference"),
    (r"(a)?(?(1)b|c)", "conditional"),
    (r"a++", "possessive"),
    (r"(?>ab)", "atomic"),
])
def test_rejected(pattern, word):
    reason = re2_problem(pattern)
    assert reason is not None and word in reason
```

Plus, in `tests/check/test_re2.py`, an end-to-end check:

```python
from textwrap import dedent
from lattice.check import check_source
from tests.check.t2_support import PROFILE

def test_l926_in_procedure():
    src = dedent('''\
        # lattice: v1
        import re

        @procedure
        def ok(s: Str) -> Bool:
            return re.fullmatch(r"(?<=a)b", s) is not None
    ''')
    result = check_source(src, PROFILE)
    assert "L926" in [d.code for d in result.diagnostics]
```

- [ ] **Step 2: Run** `uv run pytest tests/check/test_re2.py -v` → FAIL (module missing)

- [ ] **Step 3: Implement `lattice/check/re2.py`**

```python
"""RE2 subset (spec 2026-09-28 go-target §3.1, L926): patterns every target can run.

Go's regexp is RE2: no lookaround, backreferences, conditionals, possessive repeats or
atomic groups. v1.1 refuses them on every target so a program means the same
everywhere. Character classes (\\d \\w \\s \\b) are ASCII on every target: the Python
runtime compiles patterns with re.ASCII."""

from __future__ import annotations

try:  # Python 3.11+
    from re import _constants as _c  # type: ignore[attr-defined]
    from re import _parser as _p  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import sre_constants as _c  # type: ignore[no-redef]
    import sre_parse as _p  # type: ignore[no-redef]

__all__ = ["re2_problem"]

_REASONS = {
    "ASSERT": "lookahead or lookbehind",
    "ASSERT_NOT": "negative lookahead or lookbehind",
    "GROUPREF": "backreference",
    "GROUPREF_EXISTS": "conditional group",
    "POSSESSIVE_REPEAT": "possessive repeat",
    "ATOMIC_GROUP": "atomic group",
}


def _walk(items):  # noqa: ANN001, ANN202 - sre_parse internals
    for op, av in items:
        name = str(op)
        if name in _REASONS:
            reason = _REASONS[name]
            if name == "ASSERT" and av[0] < 0:
                reason = "lookbehind"
            elif name == "ASSERT" and av[0] > 0:
                reason = "lookahead"
            yield reason
        for sub in _children(op, av):
            yield from _walk(sub)


def _children(op, av):  # noqa: ANN001, ANN202
    name = str(op)
    if name in ("SUBPATTERN",):
        yield av[-1]
    elif name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
        yield av[2]
    elif name == "BRANCH":
        yield from av[1]
    elif name in ("ASSERT", "ASSERT_NOT"):
        yield av[1]
    elif name == "ATOMIC_GROUP":
        yield av
    elif name == "GROUPREF_EXISTS":
        yield av[1]
        if av[2] is not None:
            yield av[2]


def re2_problem(pattern: str) -> str | None:
    try:
        parsed = _p.parse(pattern)
    except Exception:  # the existing checks report invalid patterns
        return None
    for reason in _walk(parsed):
        return f"{reason} is not supported (Lattice regular expressions are the RE2 subset)"
    return None
```

- [ ] **Step 4: Report L926 at both pattern sites**

At `lattice/check/t2/body.py` next to `risk = redos_risk(node.value)` add:

```python
        problem = re2_problem(node.value)
        if problem is not None:
            self.err("L926", node, problem,
                     "rewrite without lookaround/backreferences, e.g. two matches or a split")
```

(and `from lattice.check.re2 import re2_problem`). Do the same at the `Str(pattern=...)` site found by `grep -rn "redos_risk" lattice/check`, using that module's `ctx.error(...)` style.

- [ ] **Step 5: ASCII classes on the Python target**

In `lattice/t2/support.py`, wherever `re` functions are exposed to procedures (`_t2_std_module("re", ...)` / `T2Fuel.sub` / `scan`), compile patterns with `re.ASCII`: wrap the module namespace so `re.fullmatch(p, s)` calls `re.fullmatch(p, s, flags=re.ASCII)` (same for `match`, `search`, `sub`, `findall`, `split`). For `Str(pattern=...)` parameter constraints, add `re.ASCII` in both `lattice/api/runtime.py::_constrain` and the codegen prelude's `_constrain` (`lattice/api/codegen.py` `_PRELUDE`). Add a test in `tests/check/test_re2.py`:

```python
from tests.t2.util import load

def test_digit_class_is_ascii():
    ns = load('''
        import re

        @procedure
        def digits(s: Str) -> Bool:
            return re.fullmatch(r"\\d+", s) is not None
    ''')
    from lattice.t2 import T2Fuel
    assert ns["digits"](T2Fuel(10_000), "123") is True
    assert ns["digits"](T2Fuel(10_000), "١٢٣") is False  # Arabic-Indic digits
```

- [ ] **Step 6: Run** `uv run pytest tests/check/test_re2.py -v && uv run pytest -q` → PASS

- [ ] **Step 7: Commit**

```bash
git add lattice/check/re2.py lattice/check/t2/body.py lattice/check lattice/t2/support.py lattice/api/runtime.py lattice/api/codegen.py tests/check/test_re2.py
git commit -m "feat(check): RE2-only regular expressions (L926), ASCII classes on every target

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: T2 IR nodes and dump

**Files:**
- Create: `lattice/t2ir/__init__.py`, `lattice/t2ir/nodes.py`, `lattice/t2ir/dump.py`, `tests/t2ir/__init__.py`, `tests/t2ir/test_nodes.py`

**Interfaces:**
- Produces (all `@dataclass(frozen=True)`; every expression has `type: T2Type`):
  - Expressions: `Const(value: object, type)`, `Var(name: str, type)`, `Attr(obj: Expr, name: str, type)`, `Index(obj: Expr, index: Expr, type)`, `SliceOf(obj: Expr, lo: Expr | None, hi: Expr | None, step: Expr | None, type)`, `Unary(op: str, operand: Expr, type)` (`op` ∈ `"-" "+" "not" "~"`), `Binary(op: str, left: Expr, right: Expr, type)` (`op` ∈ `"+" "-" "*" "/" "//" "%" "**" "<<" ">>" "&" "|" "^"`), `Compare(ops: tuple[str, ...], operands: tuple[Expr, ...], type)` (`"==" "!=" "<" "<=" ">" ">=" "in" "not in" "is" "is not"`), `BoolOp(op: str, values: tuple[Expr, ...], type)`, `IfExp(test, body, orelse, type)`, `Call(target: str, args: tuple[Expr, ...], kwargs: tuple[tuple[str, Expr], ...], type)` (`target` = the checker's resolved string; methods pass the receiver as `args[0]`), `ListLit/SetLit/TupleLit(elts, type)`, `DictLit(keys, values, type)`, `Comp(kind: str, elt: Expr, value: Expr | None, gens: tuple[Gen, ...], type)` (`kind` ∈ `"list" "set" "dict" "gen"`), `Gen(target: AssignTarget, iter: Expr, ifs: tuple[Expr, ...])`, `FStr(parts: tuple[str | FPart, ...], type)`, `FPart(value: Expr, spec: str, conversion: str)`, `Lambda(params: tuple[str, ...], body: Expr, type)`, `Walrus(name: str, value: Expr, type)`
  - Fuel-bearing expressions: `Each(iter: Expr, type)` (charge per element), `Bulk(value: Expr, type)` (charge its length up front), `Charged(call: Call, type)` (charge one step, then call), `Grow(op: str, left: Expr, right: Expr, type)` (size-checked `+ * ** <<`), `Sized(kind: str, args: tuple[Expr, ...], type)` (`kind` ∈ `"replace" "join" "sub" "dumps" "pad"`), `Scan(value: Expr, type)` (regex input charged by length)
  - Targets: `NameT(name: str)`, `TupleTarget(elts: tuple[AssignTarget, ...])`, `IndexT(obj: Expr, index: Expr)`, `AttrT(obj: Expr, name: str)`
  - Statements: `Assign(target: AssignTarget, value: Expr)`, `AugAssign(target: AssignTarget, op: str, value: Expr, grow: bool)`, `If(test, body, orelse)`, `For(target, iter, body, orelse)`, `While(test, body, orelse)`, `Break()`, `Continue()`, `Return(value: Expr | None)`, `ExprStmt(value)`, `Try(body, handlers: tuple[Handler, ...], orelse, final)`, `Handler(kinds: tuple[str, ...], name: str | None, body)`, `Raise(kind: str, message: Expr | None)`, `Pass()`, `Tick()` (first statement of every loop body)
  - Module: `Procedure(name: str, params: tuple[tuple[str, T2Type], ...], returns: T2Type, locals: Mapping[str, T2Type], body: tuple[Stmt, ...], recursive: bool)`, `Record(name: str, fields: tuple[tuple[str, T2Type], ...], defaults: Mapping[str, object])`, `T2Module(procedures: tuple[Procedure, ...], records: tuple[Record, ...], constants: tuple[tuple[str, T2Type, Expr], ...], imports: Mapping[str, str], natives: tuple[str, ...])`
  - `lattice.t2ir.dump.dump(module: T2Module) -> str` — deterministic, one node per line, two-space indent, types rendered with `T2Type.render()`

- [ ] **Step 1: Failing test**

```python
# tests/t2ir/test_nodes.py
from lattice.check.t2.types import BOOL, INT
from lattice.t2ir.dump import dump
from lattice.t2ir.nodes import (Binary, Compare, Const, If, Procedure, Return, T2Module,
                                Tick, Var, While)


def test_dump_is_deterministic_and_typed():
    body = (
        While(Compare((">",), (Var("n", INT), Const(0, INT)), BOOL),
              (Tick(), ), ()),
        If(Var("flag", BOOL), (Return(Binary("+", Var("n", INT), Const(1, INT), INT)),), ()),
        Return(Const(0, INT)),
    )
    mod = T2Module((Procedure("f", (("n", INT), ("flag", BOOL)), INT,
                              {"n": INT, "flag": BOOL}, body, False),), (), (), {}, ())
    assert dump(mod) == (
        "procedure f(n: Int, flag: Bool) -> Int\n"
        "  While\n"
        "    test: Compare(>) : Bool\n"
        "      Var n : Int\n"
        "      Const 0 : Int\n"
        "    body:\n"
        "      Tick\n"
        "  If\n"
        "    test: Var flag : Bool\n"
        "    body:\n"
        "      Return\n"
        "        Binary(+) : Int\n"
        "          Var n : Int\n"
        "          Const 1 : Int\n"
        "  Return\n"
        "    Const 0 : Int\n"
    )
```

- [ ] **Step 2: Run** `uv run pytest tests/t2ir -v` → FAIL (module missing)

- [ ] **Step 3: Implement `nodes.py`** with exactly the dataclasses in the Interfaces block (type aliases `Expr = Union[...]`, `Stmt = Union[...]`, `AssignTarget = Union[NameT, TupleTarget, IndexT, AttrT]` (not `Target`, which is `lattice.targets.Target`)). Expressions list `type` as their last field. No field holds a Python `ast` object.

- [ ] **Step 4: Implement `dump.py`**

```python
"""A stable text form of T2 IR for golden tests (one node per line)."""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from lattice.t2ir import nodes as n

_LEAF = {n.Const: lambda x: f"Const {x.value!r}", n.Var: lambda x: f"Var {x.name}"}


def _head(x: object) -> str:
    for cls, fn in _LEAF.items():
        if isinstance(x, cls):
            return fn(x)
    name = type(x).__name__
    op = getattr(x, "op", None) or getattr(x, "target", None) if not isinstance(x, (n.Assign, n.For, n.AugAssign)) else None
    if isinstance(x, n.Compare):
        return f"Compare({' '.join(x.ops)})"
    if isinstance(op, str):
        return f"{name}({op})"
    return name


def _lines(x: object, depth: int, label: str = "") -> list[str]:
    pad = "  " * depth
    typ = getattr(x, "type", None)
    text = f"{pad}{label}{_head(x)}" + (f" : {typ.render()}" if typ is not None else "")
    out = [text]
    if isinstance(x, (n.Const, n.Var)):
        return out
    for f in fields(x):  # type: ignore[arg-type]
        if f.name in ("type", "op", "ops", "name", "target") and not isinstance(getattr(x, f.name), (tuple,)) or f.name in ("type", "op", "ops"):
            continue
        v = getattr(x, f.name)
        if isinstance(v, tuple) and v and all(is_dataclass(e) for e in v):
            if f.name in ("body", "orelse", "final", "handlers"):
                out.append(f"{pad}  {f.name}:")
                for e in v:
                    out += _lines(e, depth + 2)
            else:
                for e in v:
                    out += _lines(e, depth + 1)
        elif is_dataclass(v):
            if f.name in ("test",):
                sub = _lines(v, depth + 1, "test: ")
                out += sub
            else:
                out += _lines(v, depth + 1)
    return out


def dump(module: n.T2Module) -> str:
    out: list[str] = []
    for r in module.records:
        out.append(f"record {r.name}(" + ", ".join(f"{k}: {t.render()}" for k, t in r.fields) + ")")
    for p in module.procedures:
        params = ", ".join(f"{k}: {t.render()}" for k, t in p.params)
        out.append(f"procedure {p.name}({params}) -> {p.returns.render()}")
        for s in p.body:
            out += _lines(s, 1)
    return "\n".join(out) + "\n"
```

Adjust `_lines` until the golden string in Step 1 matches exactly (the test is the contract; keep the output format, simplify the code).

- [ ] **Step 5: Run** `uv run pytest tests/t2ir -v` → PASS

- [ ] **Step 6: Commit**

```bash
git add lattice/t2ir tests/t2ir
git commit -m "feat(t2ir): language-neutral T2 IR nodes and a stable dump

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Lowering checked T2 to IR

**Files:**
- Create: `lattice/t2ir/lower.py`, `tests/t2ir/test_lower.py`, `tests/t2ir/golden/` (one `.lattice.py` + `.txt` pair per construct)

**Interfaces:**
- Consumes: `T2Program`, `CheckedProcedure.types/targets/locals` (`lattice/check/t2/model.py`), nodes from Task 3
- Produces: `lattice.t2ir.lower.lower_t2(t2: T2Program, source: ast.Module | None) -> T2Module`; raises `LatticeInternalError(f"T2 IR: unsupported {type(node).__name__}")` for any node the checker should not have accepted

Fuel placement **must mirror `_Instrument` exactly** (read `lattice/t2/compile.py` top docstring and `visit_*` methods before writing):

| Checked AST | IR |
|---|---|
| every `for`/`while` body | `Tick()` prepended |
| comprehension iterable | `Each(iter)` |
| call whose target is not `proc:` | `Charged(Call(...))`; if target in `_CONSUMERS` (copy the table) at the listed positions, those args wrapped in `Bulk` (one-arg form only for `min`/`max`) |
| `a + b`, `a * b`, `a ** b`, `a << b` (and aug forms) | `Grow(op, a, b)` / `AugAssign(..., grow=True)` |
| `Str.ljust/rjust/center/zfill` | `Sized("pad", (recv, width, fill?))` |
| `Str.replace`, `Str.join`, `re.sub`, `json.dumps` | `Sized("replace"|"join"|"sub"|"dumps", args)` |
| `re.fullmatch/match/search/findall/split` | second argument wrapped in `Scan` |
| f-string `{x:spec}` / `!r` | `FPart(value, spec, conversion)` (the emitter charges width) |

- [ ] **Step 1: Golden tests**

Create golden inputs by construct, each a minimal procedure: `assign_if.lattice.py`, `loops.lattice.py` (for/while/break/continue/else), `comprehensions.lattice.py` (list/set/dict/gen with ifs), `calls.lattice.py` (builtins incl. `sum`, `sorted(key=lambda)`, `min(a,b)` vs `min(xs)`), `strings.lattice.py` (f-strings with spec, `replace`, `join`, `ljust`), `regex.lattice.py`, `try_except.lattice.py`, `records.lattice.py`, `recursion.lattice.py`, `aug_assign.lattice.py`, `subscripts.lattice.py` (index, negative index, slice, dict index store).

```python
# tests/t2ir/test_lower.py
from pathlib import Path

import pytest

from lattice.t2ir.dump import dump
from lattice.t2ir.lower import lower_t2
from tests.t2.util import checked

GOLDEN = Path(__file__).parent / "golden"
CASES = sorted(p.name.removesuffix(".lattice.py") for p in GOLDEN.glob("*.lattice.py"))


@pytest.mark.parametrize("name", CASES)
def test_golden(name, request):
    src = (GOLDEN / f"{name}.lattice.py").read_text()
    program = checked(src).program
    text = dump(lower_t2(program.t2, None))
    expected = GOLDEN / f"{name}.txt"
    if request.config.getoption("--update-golden", default=False):
        expected.write_text(text)
    assert text == expected.read_text()


def test_every_existing_t2_program_lowers():
    """Coverage: every procedure the T2 test suites check lowers without error."""
    from tests.t2ir.corpus import all_t2_programs  # collects sources used by tests/t2, tests/api/t2prog.py, tests/api/hardening.py, tests/fixtures/loops
    for name, program in all_t2_programs():
        lower_t2(program.t2, None)  # raises on an unsupported node
```

Add the `--update-golden` option in `tests/conftest.py`:

```python
def pytest_addoption(parser):
    parser.addoption("--update-golden", action="store_true", default=False)
```

(If `tests/conftest.py` already defines `pytest_addoption`, add the option inside it.)

Create `tests/t2ir/corpus.py` with `all_t2_programs() -> Iterator[tuple[str, CheckedProgram]]` that checks: every source string in `tests/api/t2prog.py` and `tests/api/hardening.py` (with the profile those modules use), every `tests/fixtures/loops/*.py` item with a `@procedure`, and every `load("""...""")` source in `tests/t2/test_fuel.py` and `tests/t2/test_size_fuel.py` (extract them by importing a `SOURCES` tuple you add to each of those test modules — move the inline strings into module-level constants, no behaviour change).

- [ ] **Step 2: Run** `uv run pytest tests/t2ir -v` → FAIL (`lower_t2` missing)

- [ ] **Step 3: Implement `lower.py`**

Structure (complete the `match` arms for every node kind listed in the Interfaces of Task 3 — the checker's accepted kinds are those in `lattice/check/t2/body.py` `case ast.X` arms: `AnnAssign Assign AugAssign Attribute BinOp BoolOp Break Call Compare Constant Dict DictComp Expr For FormattedValue GeneratorExp If IfExp JoinedStr Lambda List ListComp NamedExpr Pass Return SetComp Starred Subscript Try Tuple UnaryOp While`):

```python
"""Checked T2 AST -> T2 IR (spec 2026-09-28 go-target §2). Types come from the
checker's side table (`CheckedProcedure.types`), calls from its resolved targets;
fuel and size checks are placed here, exactly where `lattice.t2.compile` used to
instrument them, so every emitter charges the same amounts at the same points."""

from __future__ import annotations

import ast
from collections.abc import Mapping

from lattice.check.t2 import CheckedProcedure, T2Program
from lattice.check.t2.types import T2Type
from lattice.runtime import LatticeInternalError
from lattice.t2ir import nodes as n

_CONSUMERS: Mapping[str, tuple[int, ...]] = {
    # copied verbatim from lattice/t2/compile.py (this file becomes the only copy)
    "builtin:sum": (0,), "builtin:sorted": (0,), "builtin:list": (0,), "builtin:set": (0,),
    "builtin:dict": (0,), "builtin:any": (0,), "builtin:all": (0,), "builtin:min": (0,),
    "builtin:max": (0,), "std:collections.Counter": (0,), "method:Str.join": (0,),
    "method:list.extend": (0,), "method:set.update": (0,), "method:set.union": (0,),
    "method:set.intersection": (0,), "method:set.difference": (0,),
    "method:set.issubset": (0,), "method:set.issuperset": (0,),
}
_ONE_ARG_ONLY = frozenset({"builtin:min", "builtin:max"})
_GROWING = {ast.Add: "+", ast.Mult: "*", ast.Pow: "**", ast.LShift: "<<"}
_OPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//",
        ast.Mod: "%", ast.Pow: "**", ast.LShift: "<<", ast.RShift: ">>",
        ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^"}
_CMP = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">",
        ast.GtE: ">=", ast.In: "in", ast.NotIn: "not in", ast.Is: "is", ast.IsNot: "is not"}
_PADDING = frozenset({"method:Str.ljust", "method:Str.rjust", "method:Str.center",
                      "method:Str.zfill"})
_SIZED = {"method:Str.replace": "replace", "method:Str.join": "join",
          "std:re.sub": "sub", "std:json.dumps": "dumps"}
_SCANS = frozenset({"std:re.fullmatch", "std:re.match", "std:re.search",
                    "std:re.findall", "std:re.split"})


class _Proc:
    def __init__(self, proc: CheckedProcedure) -> None:
        self.proc = proc

    def ty(self, node: ast.expr) -> T2Type:
        t = self.proc.types.get(id(node))
        if t is None:
            raise LatticeInternalError(f"T2 IR: no type for {ast.dump(node)[:80]}")
        return t

    def block(self, stmts: list[ast.stmt], loop: bool = False) -> tuple[n.Stmt, ...]:
        out: list[n.Stmt] = [n.Tick()] if loop else []
        for s in stmts:
            out.extend(self.stmt(s))
        return tuple(out)

    def stmt(self, s: ast.stmt) -> list[n.Stmt]:
        match s:
            case ast.Assign(targets=[t], value=v):
                return [n.Assign(self.target(t), self.expr(v))]
            case ast.AnnAssign(target=t, value=v) if v is not None:
                return [n.Assign(self.target(t), self.expr(v))]
            case ast.AugAssign(target=t, op=op, value=v):
                return [n.AugAssign(self.target(t), _OPS[type(op)], self.expr(v),
                                    type(op) in _GROWING)]
            case ast.If(test=t, body=b, orelse=o):
                return [n.If(self.expr(t), self.block(b), self.block(o))]
            case ast.For(target=t, iter=it, body=b, orelse=o):
                return [n.For(self.target(t), self.expr(it), self.block(b, loop=True),
                              self.block(o))]
            case ast.While(test=t, body=b, orelse=o):
                return [n.While(self.expr(t), self.block(b, loop=True), self.block(o))]
            case ast.Break():
                return [n.Break()]
            case ast.Continue():
                return [n.Continue()]
            case ast.Pass():
                return [n.Pass()]
            case ast.Return(value=v):
                return [n.Return(None if v is None else self.expr(v))]
            case ast.Expr(value=v):
                return [n.ExprStmt(self.expr(v))]
            case ast.Try(body=b, handlers=hs, orelse=o, finalbody=f):
                return [n.Try(self.block(b), tuple(self.handler(h) for h in hs),
                              self.block(o), self.block(f))]
            case ast.Raise(exc=ast.Call(func=ast.Name(id=kind), args=args)):
                return [n.Raise(kind, self.expr(args[0]) if args else None)]
            case _:
                raise LatticeInternalError(f"T2 IR: unsupported {type(s).__name__}")

    # handler(), target(), expr(), call() follow the tables above; expr() dispatches on
    # every expression kind in the Task 3 Interfaces block, wrapping per the fuel table.


def lower_t2(t2: T2Program, source: ast.Module | None) -> n.T2Module:
    ...  # records from t2.records, procedures via _Proc, constants from `source`
         # exactly as compile.py `_constants` selects them, natives = union of proc.natives
```

Implement `handler`, `target`, `expr` and `call` fully; `call(node)` reads `self.proc.targets[id(node)]`, builds `n.Call(target, args, kwargs, type)` (method calls: receiver first), then applies `Bulk`/`Scan`/`Sized`/`Charged` per the tables (a `proc:` call is never `Charged` — the callee's entry charges). `lower_t2` copies the constant selection from `compile.py::_constants` (same order).

- [ ] **Step 4: Generate goldens, review them by hand, run**

Run: `uv run pytest tests/t2ir --update-golden -q` then open each `tests/t2ir/golden/*.txt` and check fuel nodes sit where `compile.py` instruments (Tick first in loop bodies, `Each` on comprehension iterables, `Bulk` on `sum(xs)` but not on `min(a, b)`). Then `uv run pytest tests/t2ir -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add lattice/t2ir tests/t2ir tests/conftest.py tests/t2
git commit -m "feat(t2ir): lower checked procedures to T2 IR with fuel placement

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Python emitter, v1.1 runtime changes, and removal of the instrumentation path

**Files:**
- Create: `lattice/t2/emit_py.py`, `tests/t2/test_emit_py_differential.py`
- Modify: `lattice/t2/support.py` (R623, `T2Set`, `T2Counter`), `lattice/t2/compile.py` (becomes a thin shim), `lattice/t2/__init__.py`

**Interfaces:**
- Consumes: `lower_t2`, `T2Module`
- Produces: `lattice.t2.emit_py.emit_python(module: T2Module) -> T2Code`; `lattice.t2.compile_program(program) -> T2Code | None` keeps its signature (now `emit_python(lower_t2(program.t2, ast.parse(program.source)))`); `lattice.t2.support.T2IntOverflow(T2Error)` with code `"R623"`; `lattice.t2.support.T2Set`; `T2Fuel.int(v: int) -> int` (range check)

- [ ] **Step 1: Failing tests for the v1.1 runtime rules**

```python
# tests/t2/test_v11_python.py
import subprocess
import sys

import pytest

from lattice.t2 import T2Fuel
from lattice.t2.support import T2Error
from tests.t2.util import load

INT_MAX = 2**63 - 1


def run(src, fn, *args):
    return load(src)[fn](T2Fuel(100_000), *args)


def test_overflow_is_r623():
    src = '''
        @procedure
        def inc(x: Int) -> Int:
            return x + 1
    '''
    assert run(src, "inc", 1) == 2
    with pytest.raises(T2Error) as e:
        run(src, "inc", INT_MAX)
    assert e.value.code == "R623"


@pytest.mark.parametrize("body", [
    "return -x", "return abs(x)", "return x // -1", "return x - 1",
])
def test_int_min_edges(body):
    src = f'''
        @procedure
        def f(x: Int) -> Int:
            {body}
    '''
    with pytest.raises(T2Error) as e:
        run(src, "f", -(2**63))
    assert e.value.code == "R623"


def test_int_parse_out_of_range_is_r623():
    src = '''
        @procedure
        def p(s: Str) -> Int:
            return int(s)
    '''
    with pytest.raises(T2Error) as e:
        run(src, "p", "9223372036854775808")
    assert e.value.code == "R623"


def test_set_order_is_insertion_order_across_hash_seeds():
    code = (
        "from tests.t2.util import load\n"
        "from lattice.t2 import T2Fuel\n"
        "ns = load('''\n"
        "@procedure\n"
        "def order(xs: list[Str]) -> list[Str]:\n"
        "    return [x for x in set(xs)]\n"
        "''')\n"
        "print(ns['order'](T2Fuel(10_000), ['pear', 'apple', 'fig', 'kiwi', 'apple']))\n"
    )
    outs = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={"PYTHONHASHSEED": str(seed), "PATH": ""}, check=True).stdout
        for seed in (1, 2, 3, 4)
    }
    assert outs == {"['pear', 'apple', 'fig', 'kiwi']\n"}


def test_counter_most_common_ties_in_insertion_order():
    src = '''
        from collections import Counter

        @procedure
        def top(xs: list[Str]) -> list[Str]:
            return [k for k, _ in Counter(xs).most_common()]
    '''
    assert run(src, "top", ["b", "a", "b", "a", "c"]) == ["b", "a", "c"]
```

(If `subprocess` with an empty `PATH` cannot import the repo, set `cwd` to the repo root and `env={"PYTHONHASHSEED": ..., "PYTHONPATH": repo_root}`.)

- [ ] **Step 2: Failing differential test (old path vs new path)**

```python
# tests/t2/test_emit_py_differential.py
"""emit_py must reproduce the instrumented compiler: same values, same R620/R621/R622
at the same points, over every T2 program the test suites use. Deleted with the old
path at the end of Task 5 (then the regular suites guard behaviour)."""

import ast

import pytest

from lattice.t2 import T2Fuel, t2_load
from lattice.t2.compile import _legacy_compile_t2  # renamed old entry point, deleted in Step 7
from lattice.t2.emit_py import emit_python
from lattice.t2ir.lower import lower_t2
from tests.t2ir.corpus import all_t2_calls  # (name, program, proc, args, budget) samples


@pytest.mark.parametrize("name,program,proc,args,budget", list(all_t2_calls()))
def test_same_outcome(name, program, proc, args, budget):
    old = t2_load(_legacy_compile_t2(program.t2, ast.parse(program.source)).source)
    new = t2_load(emit_python(lower_t2(program.t2, ast.parse(program.source))).source)

    def outcome(ns):
        fuel = T2Fuel(budget)
        try:
            return ("ok", ns[proc](fuel, *args), fuel.left)
        except Exception as e:  # compare the error kind/code, not the message text
            return ("err", getattr(e, "code", type(e).__name__), fuel.left)

    assert outcome(new) == outcome(old)
```

Add `all_t2_calls()` to `tests/t2ir/corpus.py`: for each program from `all_t2_programs()`, sample calls from the existing tests' own invocations (collect the `(proc, args, budget)` triples the tests in `tests/t2/test_fuel.py` and `tests/t2/test_size_fuel.py` use — move them into a `CALLS` constant in each module) plus, for every procedure whose params are all `Int/Str/Bool/list[Int]/list[Str]`, 5 generated argument tuples from a fixed `random.Random(0)` (small ints incl. 0 and negatives, short strings incl. `""` and non-ASCII). Budgets: the test's budget, and 10 (to exercise R620).

Note: this differential compares **pre-v1.1** behaviour. Run it with the v1.1 changes (Step 4) switched off by a module flag `lattice.t2.support.V11 = False` inside the test (`monkeypatch.setattr`), because R623 and ordered sets intentionally differ.

- [ ] **Step 3: Run** `uv run pytest tests/t2/test_v11_python.py tests/t2/test_emit_py_differential.py -q` → FAIL

- [ ] **Step 4: v1.1 runtime in `support.py`**

```python
V11 = True  # tests switch it off only to compare against the legacy compiler

class T2IntOverflow(T2Error):
    def __init__(self, value: int) -> None:
        super().__init__("R623", f"integer {value} is outside the 64-bit range")


_INT_MIN, _INT_MAX = -(1 << 63), (1 << 63) - 1


def t2_int(v: int) -> int:
    if V11 and not isinstance(v, bool) and isinstance(v, int) and not (_INT_MIN <= v <= _INT_MAX):
        raise T2IntOverflow(v)
    return v


class T2Set:
    """An insertion-ordered set (v1.1: set iteration order is insertion order)."""
    __slots__ = ("_d",)

    def __init__(self, items=()):  # noqa: ANN001
        self._d = dict.fromkeys(items)
    # implement: __iter__, __len__, __contains__, __eq__ (as sets), __repr__ (Python's
    # set repr order = insertion order), add, discard, remove (KeyError), clear, update,
    # union, intersection, difference, issubset, issuperset, copy, and | & - operators
```

`T2Counter`: subclass `collections.Counter` overriding `most_common(n=None)` with `sorted(self.items(), key=lambda kv: -kv[1])[:n]` (Python's `sorted` is stable → insertion order among ties). Expose `set`/`Counter` to procedures as `T2Set`/`T2Counter` in the preamble builtins when `V11`.

- [ ] **Step 5: Implement `emit_py.py`**

Emit the same module shape `compile.py` produced (preamble binding exactly the reachable names; each procedure `def name(<fuel>, params...)` with `<fuel>.enter()` / `try: ... finally: <fuel>.leave()`), but from IR:

| IR | Python |
|---|---|
| `Tick()` | `<fuel>.tick()` |
| `Each(it)` | `<fuel>.each(it)` |
| `Bulk(v)` | `<fuel>.bulk(v)` |
| `Charged(Call)` | `<fuel>.call(f)(args)` |
| `Grow(op,a,b)` | `<fuel>.add/mul/pow/lshift(a, b)` |
| `AugAssign(grow=True)` | `<fuel>.aug(op, target, value)` pattern exactly as `compile.py::visit_AugAssign` |
| `Sized(kind, args)` | `<fuel>.replace/join/sub/dumps(...)`; `pad` → `<fuel>.width(n)` on the width argument |
| `Scan(v)` | `<fuel>.scan(v)` |
| `FPart(v, spec, conv)` | `<fuel>.fmt(v, spec, conv)` |
| any `Binary`/`Unary`/`Grow` whose `type` is `Int`, `abs(...)`/`int(...)`/`sum(...)` calls typed `Int` | wrapped in `t2_int(...)` |
| `SetLit`, `Comp(kind="set")`, `Call("builtin:set", ...)`, set methods returning sets | `T2Set(...)` |

Build Python source text with `ast.unparse` of an `ast` you construct from IR (simplest: construct `ast` nodes, then `ast.unparse`), so quoting and precedence are right.

- [ ] **Step 6: Switch and verify**

Rename the old entry point in `compile.py` to `_legacy_compile_t2` (keep it temporarily), and make `compile_program` / `compile_t2` delegate to IR + `emit_python`. Run: `uv run pytest tests/t2 tests/t2ir tests/api/test_t2_runtime.py tests/api/test_codegen_differential.py tests/loops -q` → PASS, then `uv run pytest -q` → PASS.

- [ ] **Step 7: Delete the legacy path**

Delete `_Instrument`, `_legacy_compile_t2` and every helper only they use from `lattice/t2/compile.py` (keep `T2Code`, `compile_program`, `compile_t2` signatures); delete `tests/t2/test_emit_py_differential.py`. Run `uv run pytest -q` → PASS.

- [ ] **Step 8: Commit**

```bash
git add lattice/t2 lattice/t2ir tests/t2 tests/t2ir
git commit -m "feat(t2): emit Python from T2 IR; v1.1 R623, ordered sets and Counter ties; drop AST instrumentation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The shared v1.1 semantics suite (Python side)

**Files:**
- Create: `tests/semantics_v11/__init__.py`, `tests/semantics_v11/cases.py`, `tests/semantics_v11/test_python.py`

**Interfaces:**
- Produces: `cases.CASES: tuple[Case, ...]` where `@dataclass(frozen=True) class Case: name: str; source: str; proc: str; args: tuple[object, ...]; expect: object | Err`; `@dataclass(frozen=True) class Err: code: str` (a Lattice runtime code like `"R623"`) or `Exc(kind: str)` (`"KeyError"` etc. escaping a procedure). `cases.run_python(case) -> object | Err | Exc`.

- [ ] **Step 1: Write the cases (≈150)** — grouped, each a one-procedure source. Required groups and at least these entries:

```python
# tests/semantics_v11/cases.py (excerpt; write every group fully)
INT = [
    Case("int_add_overflow", P("f(x: Int) -> Int", "return x + 1"), "f", (INT_MAX,), Err("R623")),
    Case("int_neg_min", P("f(x: Int) -> Int", "return -x"), "f", (INT_MIN,), Err("R623")),
    Case("int_floordiv_neg", P("f(a: Int, b: Int) -> Int", "return a // b"), "f", (-7, 2), -4),
    Case("int_mod_neg", P("f(a: Int, b: Int) -> Int", "return a % b"), "f", (-7, 2), 1),
    Case("int_mod_neg_divisor", P("f(a: Int, b: Int) -> Int", "return a % b"), "f", (7, -2), -1),
    Case("int_div_zero", P("f(a: Int) -> Int", "return a // 0"), "f", (1,), Exc("ZeroDivisionError")),
    Case("int_pow_overflow", P("f(a: Int) -> Int", "return a ** 63"), "f", (2,), Err("R623")),
    Case("int_parse_big", P("f(s: Str) -> Int", "return int(s)"), "f", ("9223372036854775808",), Err("R623")),
    Case("int_parse_space", P("f(s: Str) -> Int", "return int(s)"), "f", (" 42 ",), 42),
    Case("int_parse_bad", P("f(s: Str) -> Int", "return int(s)"), "f", ("4x",), Exc("ValueError")),
    Case("true_div_is_float", P("f(a: Int, b: Int) -> Float", "return a / b"), "f", (7, 2), 3.5),
    Case("round_half_even", P("f(x: Float) -> Int", "return round(x)"), "f", (2.5,), 2),
]
FLOAT_TEXT = [  # str() and f"{x}" of floats: Python repr
    Case(f"float_str_{i}", P("f(x: Float) -> Str", "return str(x)"), "f", (v,), s)
    for i, (v, s) in enumerate([(1.0, "1.0"), (1e16, "1e+16"), (1e15, "1000000000000000.0"),
                                (1000000.0, "1000000.0"), (-0.0, "-0.0"), (0.0001, "0.0001"),
                                (1e-05, "1e-05"), (0.1 + 0.2, "0.30000000000000004"),
                                (float("inf"), "inf"), (float("nan"), "nan")])
] + [
    Case("fstr_spec", P("f(x: Float) -> Str", 'return f"{x:.2f}|{x:>8.1f}|{x:e}"'), "f", (3.14159,),
         "3.14|     3.1|3.141590e+00"),
]
STR = [
    Case("len_emoji", P("f(s: Str) -> Int", "return len(s)"), "f", ("héllo👋",), 6),
    Case("slice_runes", P("f(s: Str) -> Str", "return s[1:4]"), "f", ("héllo👋",), "éll"),
    Case("neg_index", P("f(s: Str) -> Str", "return s[-1]"), "f", ("héllo👋",), "👋"),
    Case("step_slice", P("f(s: Str) -> Str", "return s[::-2]"), "f", ("abcdef",), "fdb"),
    Case("upper_sz", P("f(s: Str) -> Str", "return s.upper()"), "f", ("straße",), "STRASSE"),
    Case("lower_dotted_i", P("f(s: Str) -> Str", "return s.lower()"), "f", ("İ",), "i̇"),
    Case("casefold", P("f(s: Str) -> Str", "return s.casefold()"), "f", ("Straße",), "strasse"),
    Case("title", P("f(s: Str) -> Str", "return s.title()"), "f", ("hello wORLD o'neil",), "Hello World O'Neil"),
    Case("split_none", P("f(s: Str) -> list[Str]", "return s.split()"), "f", ("  a \t b\n",), ["a", "b"]),
    Case("strip_chars", P("f(s: Str) -> Str", 'return s.strip("xy")'), "f", ("xyaxy",), "a"),
    Case("isdigit_unicode", P("f(s: Str) -> Bool", "return s.isdigit()"), "f", ("²",), True),
    Case("index_error", P("f(s: Str) -> Str", "return s[10]"), "f", ("abc",), Exc("IndexError")),
]
LIST = [
    Case("slice_copies", P("f(xs: list[Int]) -> list[Int]", "ys = xs[0:2]\n    ys.append(9)\n    return xs"), "f", ([1, 2, 3],), [1, 2, 3]),
    Case("alias_mutates", P("f(n: Int) -> list[Int]", "a = [1]\n    b = a\n    b.append(n)\n    return a"), "f", (2,), [1, 2]),
    Case("neg_index_list", P("f(xs: list[Int]) -> Int", "return xs[-2]"), "f", ([1, 2, 3],), 2),
    Case("sorted_key_stable", P("f(xs: list[Str]) -> list[Str]", "return sorted(xs, key=lambda s: len(s))"), "f", (["bb", "a", "cc", "d"],), ["a", "d", "bb", "cc"]),
    Case("pop_empty", P("f(n: Int) -> Int", "xs: list[Int] = []\n    return xs.pop()"), "f", (0,), Exc("IndexError")),
]
DICT_SET = [
    Case("dict_order", P("f(n: Int) -> list[Str]", 'd = {"b": 1, "a": 2}\n    d["c"] = n\n    return list(d.keys())'), "f", (3,), ["b", "a", "c"]),
    Case("set_order", P("f(xs: list[Str]) -> list[Str]", "return list(set(xs))"), "f", (["pear", "apple", "pear", "fig"],), ["pear", "apple", "fig"]),
    Case("key_error", P("f(k: Str) -> Int", 'd = {"a": 1}\n    return d[k]'), "f", ("z",), Exc("KeyError")),
    Case("counter_ties", P("f(xs: list[Str]) -> list[Str]", "return [k for k, _ in Counter(xs).most_common()]"), "f", (["b", "a", "b", "a", "c"],), ["b", "a", "c"]),
    Case("defaultdict_list", P("f(xs: list[Str]) -> list[Int]", "d: dict[Str, list[Int]] = defaultdict(list)\n    for i, x in enumerate(xs):\n        d[x].append(i)\n    return d[xs[0]]"), "f", (["a", "b", "a"],), [0, 2]),
]
# Each remaining group lists its required cases; write each as a Case like the above,
# with `expect` taken from Python 3.14 (Step 3).
TIME = [  # DateTime/Duration arguments are passed as ISO strings and parsed with fromisoformat in P()
    "dt_plus_hours", "dt_minus_dt_total_seconds", "duration_negative_days_seconds",
    "dt_isoformat_no_micro", "dt_isoformat_micro", "date_isoformat", "date_weekday",
    "date_isoweekday", "strftime_ymd_hms", "strftime_a_b_j", "strftime_p_y",
    "timedelta_kwargs_weeks", "date_constructor", "dt_date", "dt_timestamp",
]
REGEX = [
    "fullmatch_named_group", "match_prefix_only", "search_start_end_codepoints",
    "sub_backref_1", "sub_named_g", "sub_dollar_literal", "findall_no_group",
    "findall_one_group", "split_keeps_empty", "digit_class_ascii_only",
]
JSON = [
    "dumps_ensure_ascii", "dumps_astral_surrogates", "dumps_separators", "dumps_sort_keys",
    "dumps_indent_2", "dumps_float_repr", "dumps_nan_infinity", "loads_int_vs_float",
    "loads_key_order", "loads_invalid_value_error", "dumps_nested_list_dict",
]
FUEL = [  # each with its own `budget`
    "while_true_r620", "recursion_depth_r620", "string_repeat_r622", "list_repeat_r622",
    "sum_bulk_charged", "comprehension_each_charged",
]
CASES = tuple(INT + FLOAT_TEXT + STR + LIST + DICT_SET)  # + the TIME/REGEX/JSON/FUEL Cases once written
# (the names above are the required minimum; the final CASES includes all of them)
```

`P(sig, body)` builds `"@procedure\ndef " + sig + ":\n    " + body` (with the needed `import` lines added automatically when the body mentions `re.`, `json.`, `math.`, `Counter`). Every `expect` value is **what Python 3.14 actually returns** — verify each by running it in Step 3; if Python disagrees with the value written here, Python wins and the case is corrected (the suite pins behaviour, it does not invent it).

- [ ] **Step 2: Python runner test**

```python
# tests/semantics_v11/test_python.py
import pytest

from tests.semantics_v11.cases import CASES, run_python


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_python(case):
    assert run_python(case) == case.expect
```

`run_python` = `load(case.source)[case.proc](T2Fuel(budget), *args)` with budget 100_000 (FUEL cases carry their own budget in a `budget` field defaulting to 100_000), mapping `T2Error` → `Err(code)` and `KeyError/IndexError/ValueError/ZeroDivisionError` → `Exc(name)`.

- [ ] **Step 3: Run** `uv run pytest tests/semantics_v11 -q`; fix any case whose expected value was wrong (Python is the reference); → PASS

- [ ] **Step 4: Commit**

```bash
git add tests/semantics_v11
git commit -m "test(semantics): shared v1.1 semantics suite, Python side

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Go runtime core (`lattice.dev/rt`)

**Files:**
- Create: `lattice/gogen/__init__.py` (empty docstring module), `lattice/gogen/rt/go.mod`, `errors.go`, `fuel.go`, `ints.go`, `floats.go`, `strs.go`, `lists.go`, `omap.go`, `sets.go`, `opt.go`, `try.go`, and `rt_test.go`

**Interfaces (Go, package `rt`):**
- `type Error struct{ Code, Kind, Msg string }` — `Code` is `"R620"…"R623"` for Lattice runtime errors, else `""`; `Kind` is `"KeyError" "IndexError" "ValueError" "ZeroDivisionError"` for catchable ones. `func Raise(kind, msg string)`, `func Fail(code, msg string)` (both `panic(&Error{...})`)
- `type Fuel struct{ Left, Depth, MaxDepth int64; MaxSize int64 }`; `NewFuel(budget int64) *Fuel`; methods `Tick() Enter() Leave() Each(n int) Bulk(n int) Grow(size int64)` — same unit costs as `lattice/t2/support.py::T2Fuel` (read it; copy constants `T2_MAX_DEPTH=200`, `T2_DEFAULT_MAX_SIZE=10_000_000`, `T2_SIZE_UNIT`)
- Ints: `Add, Sub, Mul, Neg, Abs, FloorDiv, Mod, Pow, Shl(a, b int64) int64` (R623 on overflow, `ZeroDivisionError` on zero divisor), `ParseInt(s string) int64` (Python `int(str)` rules: surrounding whitespace, `_` separators, sign; ValueError / R623)
- Floats: `ReprFloat(f float64) string` (Python `repr`), `FormatFloat(f float64, spec string) string` (Python format-spec mini-language for `f e g % ,` width/align/sign), `RoundHalfEven(f float64) int64`, `FloorDivF, ModF`
- Strings: `Len(s) int64`, `IndexStr(s, i) string`, `SliceStr(s, lo, hi, step *int64) string`, `Upper, Lower, Casefold, Title, Capitalize(s) string` (full Unicode mapping via `golang.org/x/text/cases` + `language.Und`, special-case `Title` to Python's word rule), `Split(s string, sep *string, max int64) []string`, `Strip/LStrip/RStrip(s string, chars *string) string`, `IsDigit/IsAlpha/IsAlnum/IsSpace/IsLower/IsUpper(s) bool` (Python's Unicode categories)
- Lists: `type List[T any] struct{ Items []T }`; `NewList[T](xs ...T) *List[T]`; `(*List[T]) Get(i int64) T` (negative index, IndexError), `Set(i int64, v T)`, `Slice(lo, hi, step *int64) *List[T]` (**copies**), `Append, Extend, Insert, Pop(i *int64) T, Remove(v T, eq func(a, b T) bool), Clear, Reverse, Len() int64`, `SortStable(less func(a, b T) bool)`
- Ordered map: `type Dict[K comparable, V any] struct{...}`; `NewDict`, `Get(k) V` (KeyError), `Lookup(k) (V, bool)`, `Set(k, v)`, `Delete(k) bool`, `Keys() []K`, `Values() []V`, `Items() []Pair[K,V]`, `Len() int64`, `Copy() *Dict[K,V]` — insertion order preserved; re-setting an existing key keeps its position; delete then re-insert moves it to the end (Python dict semantics)
- `type Set[T comparable]` — same ordered semantics as `lattice.t2.support.T2Set`
- `type Opt[T any] struct{ V T; Ok bool }`; `Some[T](v) Opt[T]`; `None[T]() Opt[T]`
- `func Try(body func(), handlers ...Handler)` where `type Handler struct{ Kinds []string; Fn func(e *Error) }` — recovers only `*Error` with a matching `Kind` (Lattice runtime errors with a `Code` are never caught by `except`, matching Python where `T2Error` is not an allowlisted exception)

- [ ] **Step 1: Module and failing tests**

`lattice/gogen/rt/go.mod`:

```
module lattice.dev/rt

go 1.26

require (
	golang.org/x/text v0.21.0
	modernc.org/sqlite v1.34.4
)
```

`rt_test.go` (write all of these; values are Python's):

```go
package rt

import (
	"math"
	"testing"
)

func mustFail(t *testing.T, code string, f func()) {
	t.Helper()
	defer func() {
		r := recover()
		e, ok := r.(*Error)
		if !ok || (e.Code != code && e.Kind != code) {
			t.Fatalf("want %s, got %#v", code, r)
		}
	}()
	f()
}

func TestIntOverflow(t *testing.T) {
	mustFail(t, "R623", func() { Add(math.MaxInt64, 1) })
	mustFail(t, "R623", func() { Neg(math.MinInt64) })
	mustFail(t, "R623", func() { Abs(math.MinInt64) })
	mustFail(t, "R623", func() { FloorDiv(math.MinInt64, -1) })
	mustFail(t, "R623", func() { Pow(2, 63) })
	mustFail(t, "ZeroDivisionError", func() { FloorDiv(1, 0) })
	if FloorDiv(-7, 2) != -4 || Mod(-7, 2) != 1 || Mod(7, -2) != -1 {
		t.Fatal("floor semantics")
	}
}

func TestParseInt(t *testing.T) {
	if ParseInt(" 42 ") != 42 || ParseInt("-1_000") != -1000 {
		t.Fatal("parse")
	}
	mustFail(t, "ValueError", func() { ParseInt("4x") })
	mustFail(t, "R623", func() { ParseInt("9223372036854775808") })
}

func TestReprFloat(t *testing.T) {
	for f, want := range map[float64]string{
		1.0: "1.0", 1e16: "1e+16", 1e15: "1000000000000000.0", 1000000.0: "1000000.0",
		0.0001: "0.0001", 1e-05: "1e-05", 0.1 + 0.2: "0.30000000000000004",
		math.Inf(1): "inf", math.Inf(-1): "-inf",
	} {
		if got := ReprFloat(f); got != want {
			t.Errorf("ReprFloat(%v) = %q, want %q", f, got, want)
		}
	}
	if ReprFloat(math.Copysign(0, -1)) != "-0.0" || ReprFloat(math.NaN()) != "nan" {
		t.Error("signed zero / nan")
	}
}

func TestStrings(t *testing.T) {
	s := "héllo👋"
	if Len(s) != 6 || IndexStr(s, -1) != "👋" {
		t.Fatal("runes")
	}
	one, four := int64(1), int64(4)
	if SliceStr(s, &one, &four, nil) != "éll" {
		t.Fatal("slice")
	}
	if Upper("straße") != "STRASSE" || Casefold("Straße") != "strasse" {
		t.Fatal("case mapping")
	}
	if Title("hello wORLD o'neil") != "Hello World O'Neil" {
		t.Fatal("title")
	}
	if !IsDigit("²") {
		t.Fatal("isdigit")
	}
}

func TestListSliceCopies(t *testing.T) {
	a := NewList[int64](1, 2, 3)
	zero, two := int64(0), int64(2)
	b := a.Slice(&zero, &two, nil)
	b.Append(9)
	if a.Len() != 3 || a.Get(2) != 3 {
		t.Fatal("slice must copy")
	}
	mustFail(t, "IndexError", func() { a.Get(5) })
}

func TestDictOrder(t *testing.T) {
	d := NewDict[string, int64]()
	d.Set("b", 1)
	d.Set("a", 2)
	d.Set("b", 3) // keeps position
	d.Delete("a")
	d.Set("a", 4) // moves to end
	if got := d.Keys(); got[0] != "b" || got[1] != "a" {
		t.Fatalf("order %v", got)
	}
	mustFail(t, "KeyError", func() { d.Get("zz") })
}

func TestTryCatchesOnlyMatchingKinds(t *testing.T) {
	caught := ""
	Try(func() { Raise("KeyError", "k") },
		Handler{Kinds: []string{"KeyError"}, Fn: func(e *Error) { caught = e.Kind }})
	if caught != "KeyError" {
		t.Fatal("not caught")
	}
	mustFail(t, "R620", func() {
		Try(func() { Fail("R620", "fuel") }, Handler{Kinds: []string{"ValueError"}, Fn: func(*Error) {}})
	})
}
```

- [ ] **Step 2: Run** `cd lattice/gogen/rt && go mod tidy && go test ./...` → FAIL (undefined functions). `go mod tidy` downloads `x/text` and `modernc.org/sqlite` once (network); commit `go.sum`.

- [ ] **Step 3: Implement.** Key pieces:

```go
// errors.go
package rt

type Error struct{ Code, Kind, Msg string }

func (e *Error) Error() string {
	if e.Code != "" {
		return e.Code + ": " + e.Msg
	}
	return e.Kind + ": " + e.Msg
}

func Raise(kind, msg string) { panic(&Error{Kind: kind, Msg: msg}) }
func Fail(code, msg string)  { panic(&Error{Code: code, Msg: msg}) }
```

```go
// ints.go
package rt

import (
	"math"
	"math/bits"
	"strconv"
	"strings"
)

func overflow() { Fail("R623", "integer is outside the 64-bit range") }

func Add(a, b int64) int64 {
	s := a + b
	if (a > 0 && b > 0 && s < 0) || (a < 0 && b < 0 && s >= 0) {
		overflow()
	}
	return s
}

func Sub(a, b int64) int64 {
	if b == math.MinInt64 {
		if a >= 0 {
			overflow()
		}
		return a - b
	}
	return Add(a, -b)
}

func Mul(a, b int64) int64 {
	if a == 0 || b == 0 {
		return 0
	}
	hi, lo := bits.Mul64(uint64(abs64(a)), uint64(abs64(b)))
	neg := (a < 0) != (b < 0)
	if hi != 0 || (!neg && lo > math.MaxInt64) || (neg && lo > 1<<63) {
		overflow()
	}
	if neg {
		return -int64(lo)
	}
	return int64(lo)
}

func abs64(a int64) int64 { // callers guarantee a != MinInt64 or accept wrap into uint64
	if a < 0 {
		return -a
	}
	return a
}

func Neg(a int64) int64 {
	if a == math.MinInt64 {
		overflow()
	}
	return -a
}

func Abs(a int64) int64 {
	if a == math.MinInt64 {
		overflow()
	}
	return abs64(a)
}

func FloorDiv(a, b int64) int64 {
	if b == 0 {
		Raise("ZeroDivisionError", "integer division or modulo by zero")
	}
	if a == math.MinInt64 && b == -1 {
		overflow()
	}
	q := a / b
	if (a%b != 0) && ((a < 0) != (b < 0)) {
		q--
	}
	return q
}

func Mod(a, b int64) int64 {
	if b == 0 {
		Raise("ZeroDivisionError", "integer division or modulo by zero")
	}
	if b == -1 {
		return 0
	}
	m := a % b
	if m != 0 && ((m < 0) != (b < 0)) {
		m += b
	}
	return m
}

func Pow(a, b int64) int64 {
	if b < 0 {
		Raise("ValueError", "negative exponent for an Int power") // T2 checker types a**-n as Float; Int**negative is refused
	}
	r := int64(1)
	for i := int64(0); i < b; i++ {
		r = Mul(r, a)
		if r == 0 || r == 1 && a == 1 {
			return r
		}
	}
	return r
}

func ParseInt(s string) int64 {
	t := strings.TrimSpace(s)
	clean := strings.ReplaceAll(t, "_", "")
	if t == "" || strings.HasPrefix(t, "_") || strings.HasSuffix(t, "_") || strings.Contains(t, "__") {
		Raise("ValueError", "invalid literal for int() with base 10: '"+s+"'")
	}
	v, err := strconv.ParseInt(clean, 10, 64)
	if err != nil {
		if ne, ok := err.(*strconv.NumError); ok && ne.Err == strconv.ErrRange {
			overflow()
		}
		Raise("ValueError", "invalid literal for int() with base 10: '"+s+"'")
	}
	return v
}
```

(Check `Pow`'s negative-exponent rule against the T2 checker's typing of `**` before finalising; if the checker types `Int ** Int` as `Int` only for literal non-negative exponents, match that. Replace the loop with square-and-multiply.)

```go
// floats.go — Python repr: shortest digits, exponent form iff exp < -4 or exp >= 16
package rt

import (
	"math"
	"strconv"
	"strings"
)

func ReprFloat(f float64) string {
	switch {
	case math.IsNaN(f):
		return "nan"
	case math.IsInf(f, 1):
		return "inf"
	case math.IsInf(f, -1):
		return "-inf"
	}
	e := strconv.FormatFloat(f, 'e', -1, 64) // e.g. "-1.2345e+06"
	mant, expPart, _ := strings.Cut(e, "e")
	exp, _ := strconv.Atoi(expPart)
	neg := strings.HasPrefix(mant, "-")
	digits := strings.Replace(strings.TrimPrefix(mant, "-"), ".", "", 1)
	sign := ""
	if neg {
		sign = "-"
	}
	if exp < -4 || exp >= 16 {
		m := digits[:1]
		if len(digits) > 1 {
			m += "." + digits[1:]
		}
		es := "+"
		if exp < 0 {
			es = "-"
			exp = -exp
		}
		return sign + m + "e" + es + pad2(exp)
	}
	if exp < 0 {
		return sign + "0." + strings.Repeat("0", -exp-1) + digits
	}
	if len(digits) <= exp+1 {
		return sign + digits + strings.Repeat("0", exp+1-len(digits)) + ".0"
	}
	return sign + digits[:exp+1] + "." + digits[exp+1:]
}

func pad2(n int) string {
	if n < 10 {
		return "0" + strconv.Itoa(n)
	}
	return strconv.Itoa(n)
}
```

Implement the remaining files to make every test pass: `strs.go` (runes; `cases.Upper(language.Und)`, `cases.Lower`, `cases.Fold()` for casefold; `Title` = Python rule: uppercase a cased char that follows an uncased char, lowercase others; `IsDigit` via `unicode.IsDigit || unicode.Is(unicode.No, r)` for superscripts — mirror Python `str.isdigit` which is Numeric_Type=Digit|Decimal: use a table test against Python for `0-9 ² ³ ¹ ٣ ½` and adjust), `lists.go`, `omap.go` (map[K]int index + slice of entries with tombstones compacted on growth), `sets.go`, `opt.go`, `try.go`:

```go
// try.go
package rt

type Handler struct {
	Kinds []string
	Fn    func(e *Error)
}

func Try(body func(), handlers ...Handler) {
	func() {
		defer func() {
			r := recover()
			if r == nil {
				return
			}
			e, ok := r.(*Error)
			if !ok || e.Code != "" {
				panic(r)
			}
			for _, h := range handlers {
				for _, k := range h.Kinds {
					if k == e.Kind {
						h.Fn(e)
						return
					}
				}
			}
			panic(r)
		}()
		body()
	}()
}
```

`fuel.go`: port `T2Fuel` method by method (read `lattice/t2/support.py` lines 111–345): `Tick` charges 1 and fails `R620` when `Left < 0`; `Enter` charges 1, `Depth++`, fails `R620` past `MaxDepth`; `Grow(size)` fails `R622` when `size > MaxSize`, then charges `ceil(size / T2_SIZE_UNIT)`. Messages must equal Python's text (the gate compares 500 bodies).

- [ ] **Step 4: Run** `cd lattice/gogen/rt && go vet ./... && go test ./...` → PASS

- [ ] **Step 5: Commit**

```bash
git add lattice/gogen
git commit -m "feat(gogen): Go runtime core — checked Int, Python float repr, runes, ordered dict/set, fuel, try

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Go T2 emitter — core language and builtins

**Files:**
- Create: `lattice/gogen/types.py`, `lattice/gogen/t2.py`, `tests/gogen/__init__.py`, `tests/gogen/test_t2_go.py`, `tests/semantics_v11/test_go.py`, `tests/semantics_v11/gorun.py`
- Modify: `lattice/targets.py` (`GO_T2_CALLS` += core builtins)

**Interfaces:**
- Consumes: `T2Module` (Task 3), `rt` (Task 7)
- Produces:
  - `lattice.gogen.types.GoTypes` with `spell(t: T2Type) -> str` and `decls() -> str` (generated `type tupN struct{...}` / record / row structs, deduplicated by rendered type; tuples get a generated `cmp_tupN` for ordering and are comparable for map keys)
  - `lattice.gogen.t2.emit_go_t2(module: T2Module, package: str = "main") -> str` — one Go file; each procedure `func P_<name>(fuel *rt.Fuel, <params>) <ret>` with `fuel.Enter(); defer fuel.Leave()`
  - `tests/semantics_v11/gorun.run_go(cases: Sequence[Case]) -> dict[str, object]` — builds one Go test program for many cases (one binary), runs it, returns JSON-decoded results keyed by case name (errors as `{"err": code}` / `{"exc": kind}`)

Type mapping (`types.py`):

| T2 | Go |
|---|---|
| `Int` / `Float` / `Str` / `Bool` / `Uuid` | `int64` / `float64` / `string` / `bool` / `string` |
| `Date` / `DateTime` / `Duration` | `rt.Date` / `rt.DateTime` / `rt.Duration` (Task 10) |
| `Json` | `any` |
| `T \| None` | `rt.Opt[T]` |
| `list[T]` / `dict[K,V]` / `set[T]` / `Counter[T]` | `*rt.List[T]` / `*rt.Dict[K,V]` / `*rt.Set[T]` / `*rt.Dict[T,int64]` |
| `tuple[...]` | generated `tupN` struct |
| `Iter[T]` | `iter.Seq[T]` |
| `Row[S]` / record / `Caller` | generated `row_<table>` / `rec_<name>` struct / `rt.Caller` |

Core `GO_T2_CALLS` added in this task: `builtin:len abs round min max sum sorted range enumerate zip reversed any all int float str(scalars only) list set dict` and the error constructors. `str()` / f-string of a list/dict/set/tuple value stays unsupported: add a check in `p8_target` that reports L892 for `builtin:str` (and `FPart`) whose argument type is a container — implement it in `p8_target.run` by consulting `proc.types[id(arg)]`.

- [ ] **Step 1: Failing tests**

```python
# tests/semantics_v11/test_go.py
import pytest

from lattice.check import check_source
from tests.check.t2_support import PROFILE
from tests.semantics_v11.cases import CASES
from tests.semantics_v11.gorun import run_go

GO_CASES = [c for c in CASES if check_source(c.source_with_header, PROFILE, target="go").ok]


@pytest.fixture(scope="module")
def go_results():
    return run_go(GO_CASES)


@pytest.mark.parametrize("case", GO_CASES, ids=lambda c: c.name)
def test_go_matches_expectation(case, go_results):
    assert go_results[case.name] == case.expect


def test_coverage_floor():
    """Each Go task raises this floor; Task 10 ends at 100%."""
    assert len(GO_CASES) >= 60
```

```python
# tests/gogen/test_t2_go.py
from lattice.gogen.t2 import emit_go_t2
from lattice.t2ir.lower import lower_t2
from tests.t2.util import checked


def test_emits_compilable_go(tmp_path):
    program = checked('''
        @procedure
        def total(xs: list[Int]) -> Int:
            s = 0
            for x in xs:
                if x > 0:
                    s += x
            return s
    ''').program
    src = emit_go_t2(lower_t2(program.t2, None))
    assert "func P_total(fuel *rt.Fuel, xs *rt.List[int64]) int64" in src
    assert "fuel.Tick()" in src
```

- [ ] **Step 2: Run** `uv run pytest tests/gogen/test_t2_go.py tests/semantics_v11/test_go.py -q` → FAIL

- [ ] **Step 3: Implement `types.py` and `t2.py`.** The emitter is a straightforward recursive printer over IR:

```python
# lattice/gogen/t2.py (structure)
class _Emit:
    def __init__(self, types: GoTypes) -> None:
        self.types = types
        self.tmp = 0

    def expr(self, e: n.Expr) -> str:
        match e:
            case n.Const(value=bool() as b):
                return "true" if b else "false"
            case n.Const(value=int() as i):
                return f"int64({i})"
            case n.Const(value=float() as f):
                return _go_float(f)                 # math.Inf etc. for inf/nan
            case n.Const(value=str() as s):
                return _go_string(s)                # strconv.Quote-compatible
            case n.Const(value=None, type=t):
                return f"rt.None[{self.types.spell(t.inner if isinstance(t, Opt) else t)}]()"
            case n.Var(name=name):
                return "v_" + name
            case n.Binary(op=op, left=l, right=r, type=t) | n.Grow(op=op, left=l, right=r, type=t):
                return self.binary(op, l, r, t, grow=isinstance(e, n.Grow))
            case n.Charged(call=c):
                return f"func() {self.types.spell(c.type)} {{ fuel.Tick(); return {self.call(c)} }}()"
            ...
```

Rules the emitter must follow:
- `Int` arithmetic always through `rt.Add/Sub/Mul/FloorDiv/Mod/Pow/Neg/Abs`; `Float` uses Go operators except `//`/`%` → `rt.FloorDivF/ModF`; `/` on two Ints → `float64(a)/float64(b)` with `ZeroDivisionError` check (`rt.TrueDiv`).
- `Grow` additionally calls `fuel.Grow(size)` with the same size formula as `T2Fuel.add/mul/pow/lshift` (port them to `rt` as `rt.GrowAdd(fuel, a, b)` etc. for strings/lists; ints use bit-length).
- `Compare` chains evaluate each operand once (temporaries), `in` dispatches on the container type (`rt.ContainsStr`, `(*List).Contains(eq)`, `Dict.Lookup`, `Set.Has`).
- `BoolOp` returns the Python value, not a bool, when operands are non-Bool (`x or default`): emit an IIFE with truthiness helper `rt.Truthy_<kind>`.
- `For` over a `list` iterates a **snapshot index loop** matching Python (mutation during iteration is refused by the T2 checker — confirm with `grep -n "mutat" lattice/check/t2/body.py`; if not refused, iterate live by index like CPython).
- `Try` → `rt.Try(func(){...}, rt.Handler{...})`; `return` inside a try body sets a captured result + flag, and the emitter returns after `rt.Try` (Go closures cannot return from the outer function).
- `Raise(kind, msg)` → `rt.Raise("<kind>", <msg as Go string>)`.
- Comprehensions → IIFE building `rt.NewList`, `rt.NewSet`, `rt.NewDict`, or an `iter.Seq` for generators, with `fuel.Tick()` per element (`Each`).
- `Lambda` (only as `key=`) → `func(a T) K {...}` and `sorted` uses `SortStable` with a generated comparator on the key type (tuples use `cmp_tupN`).
- f-strings: `rt.FormatValue(v, spec, conv)` for scalars (`ReprFloat` for `Float` without spec; `str(True) == "True"`; `None` → `"None"`).

- [ ] **Step 4: Implement `gorun.py`**: write a temp module (`go.mod` with `require lattice.dev/rt v0.0.0` + `replace lattice.dev/rt => <abs path to lattice/gogen/rt>`), one `t2.go` per case namespace (prefix procedure names with the case index to avoid clashes: emit each case in its own package directory `c<i>`, plus a `main.go` that calls each case in `rt.Try`-style recover and prints one JSON object `{name: result}`). Values are marshalled as JSON with type tags so Python can rebuild `list/dict/tuple/float` exactly (floats via `ReprFloat` strings). Build once per test session with `go build`, cache by content hash under `.cache/gorun/`.

- [ ] **Step 5: Update `GO_T2_CALLS`** in `lattice/targets.py` with the core builtins list, then run `uv run pytest tests/gogen tests/semantics_v11 -q` → PASS (Go runs the INT, FLOAT_TEXT, LIST, DICT_SET cases that use only core builtins; the floor assertion holds).

- [ ] **Step 6: Commit**

```bash
git add lattice/gogen lattice/targets.py lattice/check/p8_target.py tests/gogen tests/semantics_v11
git commit -m "feat(gogen): Go T2 emitter — core statements, expressions, builtins; semantics suite on Go

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Go T2 — strings and regular expressions

**Files:**
- Create: `lattice/gogen/rt/regex.go`, `lattice/gogen/rt/regex_test.go`
- Modify: `lattice/gogen/rt/strs.go`, `lattice/gogen/t2.py`, `lattice/targets.py`, `tests/semantics_v11/test_go.py` (floor → 100)

**Interfaces:**
- Produces (Go): every `method:Str.*` in `lattice/catalog.py` `T2_METHODS["Str"]` as `rt.Str<Method>(s string, ...)`; `string.*` constants; `type Match struct{...}` with `Group(i)`, `GroupName(name)`, `Start()`, `End()`; `ReFullmatch/ReMatch/ReSearch(pattern, s string) rt.Opt[*Match]`, `ReSub(pattern, repl, s string) string`, `ReFindall(pattern, s string) *List[string]` (Python's rule: whole match with no groups, group 1 with one group, tuples with several — T2 types `findall` as `list[Str]`, so patterns with 2+ groups: check `grep -n findall lattice/check/t2/body.py` for how the checker types it and match that), `ReSplit`
- Pattern translation (compile time, in `t2.py`, literal patterns only — the catalog marks pattern arguments `literal=(0,)`): Python `\Z` → RE2 `\z`; `(?P<n>...)` kept; flags none. Every pattern compiled once per binary into a package-level `var re_N = regexp.MustCompile(...)`, using `(?-s)` default and ASCII classes (RE2 `\d` is already ASCII).
- `re.sub` replacement (literal): translate `\1` → `${1}`, `\g<name>` → `${name}`, `\g<1>` → `${1}`, `\\` → `\`, `$` → `$$`; a non-literal replacement → L892 (add the check to `p8_target`).
- Offsets: `Match.Start/End` return **code-point** offsets (convert from Go byte offsets).

- [ ] **Step 1: Failing Go tests** (`regex_test.go`) — include: named groups, `\1` replacement, `$` in replacement text, `findall` with one group, `start()` on `"héllo"` after `é` equals 2, `split` keeping empty strings like Python (`re.split(",", "a,,b") == ["a", "", "b"]`).

- [ ] **Step 2: Run** `cd lattice/gogen/rt && go test ./...` → FAIL

- [ ] **Step 3: Implement** regex and remaining Str methods (`split(sep, maxsplit)`, `rsplit`, `splitlines` with Python's line boundaries `\n \r \r\n \v \f \x1c \x1d \x1e \x85    `, `partition`, `rpartition`, `find/rfind/count` on code points, `zfill` sign handling, `ljust/rjust/center` with fill char and Python's centre bias (`"ab".center(5) == "  ab "`), `is*` per Python Unicode categories).

- [ ] **Step 4:** Add to `GO_T2_CALLS` every `method:Str.*`, `std:re.*`, `std:string.*`, `method:Match.*`; raise the floor in `test_coverage_floor` to 100; run `uv run pytest tests/semantics_v11 tests/gogen -q && (cd lattice/gogen/rt && go test ./...)` → PASS

- [ ] **Step 5: Commit**

```bash
git add lattice/gogen lattice/targets.py lattice/check/p8_target.py tests/semantics_v11
git commit -m "feat(gogen): strings and RE2 regular expressions on Go

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Go T2 — collections, math, datetime, json

**Files:**
- Create: `lattice/gogen/rt/times.go`, `lattice/gogen/rt/jsonv.go`, `lattice/gogen/rt/mathx.go` + tests
- Modify: `lattice/gogen/rt/lists.go omap.go sets.go`, `lattice/gogen/t2.py`, `lattice/targets.py`, `tests/semantics_v11/test_go.py`

**Interfaces:**
- Produces (Go): all `method:list.* dict.* set.* Counter.*`, `std:collections.Counter/defaultdict` (defaultdict → `rt.Dict` with a factory closure used by `Get`/index-read), `std:math.*` (constants and functions; `math.floor/ceil/trunc` return `int64` with R623 on out-of-range and `ValueError` on nan/inf like Python), `std:datetime.timedelta/date`, `helper:days/hours/minutes/seconds`, `method:Date.* DateTime.* Duration.*`, attrs in `T2_ATTRS`, `std:json.dumps/loads`
- `rt.DateTime`: UTC instant with microsecond precision (`int64` µs since epoch); `rt.Duration`: `int64` µs, `Days()`/`Seconds()` follow Python's normalisation (`seconds` in `[0, 86400)`, `days` may be negative); `Isoformat()` identical to Python's (`"2026-09-28T10:00:00"` without µs when µs==0, else 6 digits; no offset for naive-UTC values — check how the T2 runtime represents `DateTime` today in `t2_decode` and match it exactly); `Strftime` supports `%Y %m %d %H %M %S %f %y %a %A %b %B %j %p %%` (a literal format using any other directive → L892 via `p8_target`; a non-literal format → L892).
- JSON (`jsonv.go`): `Dumps(v any, sortKeys bool, indent *int64) string` byte-identical to Python `json.dumps` defaults: `ensure_ascii=True` (`\uXXXX`, surrogate pairs above U+FFFF), separators `", "`/`": "` (and `","`/`": "` with indent), floats via `ReprFloat` but `NaN`/`Infinity`/`-Infinity` spelled as Python does, ints as-is, `True/False/None` → `true/false/null`; `Loads(s string) any` decoding numbers as `int64` when integral in the text (no `.`/`e`) else `float64`, objects as `*rt.Dict[string, any]` preserving key order, invalid → `ValueError` with Python's message prefix `"Expecting value"` / `"Extra data"` (compare the catalogue of messages the Python side can produce for the semantics cases).

- [ ] **Step 1:** Write the TIME and JSON groups of `tests/semantics_v11/cases.py` fully (≥ 15 cases each) if Task 6 left them short, and Go unit tests for `Dumps`/`Loads`/`Isoformat`/`Strftime`/`Duration` normalisation.
- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Add every remaining call target to `GO_T2_CALLS` so that `set(T2 call targets in lattice/catalog.py) - GO_T2_CALLS` is empty; add a test:

```python
# tests/gogen/test_t2_go.py
def test_go_implements_every_t2_call():
    from lattice.catalog import T2_METHODS, T2_STDLIB
    from lattice.t2.support import T2_BUILTIN_NAMES
    from lattice.targets import GO_T2_CALLS
    wanted = {f"builtin:{b}" for b in T2_BUILTIN_NAMES if b[0].islower()}
    wanted |= {f"method:{k}.{m}" for k, ms in T2_METHODS.items() for m in ms}
    wanted |= {f"std:{mod}.{name}" for mod, names in T2_STDLIB.items() for name in names}
    assert wanted - GO_T2_CALLS == set()
```

and change `test_coverage_floor` to `assert len(GO_CASES) == len(CASES)` (every semantics case runs on Go, except cases that use container `str()`, which are expected L892: filter those by name prefix `repr_container_` and assert they are rejected).

- [ ] **Step 5:** Run `uv run pytest -q && (cd lattice/gogen/rt && go test ./...)` → PASS
- [ ] **Step 6: Commit**

```bash
git add lattice/gogen lattice/targets.py lattice/check/p8_target.py tests
git commit -m "feat(gogen): collections, math, datetime and json on Go — every T2 call implemented

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Go request runtime

**Files:**
- Create: `lattice/gogen/rt/http.go`, `params.go`, `auth.go`, `sqlitefn.go`, `outbox.go`, `db.go`, `http_test.go`

**Interfaces:**
- Consumes: the Python prelude (`lattice/api/codegen.py` `_PRELUDE`, lines 119–~940) as the behavioural reference — port it function by function; keep function names in Go camel case (`parse_params` → `ParseParams`)
- Produces (Go):
  - `type ParamSpec struct{ Name, Scalar, Source string; Required, Nullable bool; Default any; Ge, Le *float64; MinLen, MaxLen *int64; Pattern *regexp.Regexp; Enum []string }` (mirror `lattice.api.ir` `ParamSpec`/codegen `Param` fields exactly — read them)
  - `type Endpoint struct{ Name, Method, Path string; Params []ParamSpec; Roles []string; Public bool; ... }`
  - `type Caller struct{ ID any; Role string; IsAdmin bool; Cols map[string]any }`
  - `type Ctx struct{ Tx *sql.Tx; Caller *Caller; Fuel *Fuel; Now DateTime; queries int }` with `(*Ctx) Query(sql string, args ...any) *Rows` / `Exec(...)` counting data statements
  - `func Serve(eps []Route, settings Settings)` — parses `--db` and `--addr`, opens SQLite via `modernc.org/sqlite` with `_pragma=busy_timeout(5000)`, `_pragma=foreign_keys(1)` (match the Python `connect()` pragmas exactly), registers the SQLite functions (`sqlitefn.go`: port `SqliteDialect.register_functions` — `lower`, `upper`, `strip`, `mod`, `uuid_text`, `to_uuid`, `to_datetime`, `datetime_add`, `datetime_diff`, `uuid4` … read `lattice/api/backend.py:558`), listens, prints `LISTENING <addr>`, and serves
  - `type Route struct{ EP Endpoint; Handle func(c *Ctx, p map[string]any) Response }`, `type Response struct{ Status int; Body any }`
  - Errors: `ErrorResponse(status int, code, detail string)`; error body style `settings.ErrorBody` (`"lattice"` / `"fastapi"`) exactly as `error_body` in the prelude; 422 validation envelope identical
  - Wire JSON: `WriteJSON` using the same float/int/Uuid/DateTime wire rules as the prelude (`wire_datetime`, `datetime_text`, `_finite`)
  - Every handler runs inside `BEGIN` (`BEGIN IMMEDIATE` when the endpoint writes, matching `SqliteDialect.begin(conn, writes)`), `COMMIT` on success, `ROLLBACK` on any error; a panic of `*Error` with a `Code` → 500 `{"code": "internal", ...}` with the located message exactly as `t2_report` formats it; any other panic → 500 `internal`; server never exits
  - SQL counting header when `LATTICE_TRACE_SQL=1`
  - Outbox: `DispatchOutbox` mirrors `dispatch_outbox`/`retry_outbox` (effects run only after commit; in this plan no effect natives exist on Go, so `for_each` programs are rejected by L890 — still port the table DDL and insert so the code path compiles and is tested with a fake dispatcher)

- [ ] **Step 1: Failing Go tests** (`http_test.go`) using `httptest`: parameter coercion and 422 bodies for missing/mistyped/out-of-range params (compare against strings captured from the Python prelude — record them with a small Python script into `lattice/gogen/rt/testdata/prelude_answers.json` and commit it), unknown token → 401 body, handler panic → 500 and the next request still succeeds, `X-Lattice-Sql-Count` counts only data statements, and:

```go
func TestConcurrentWritesDoNotFail(t *testing.T) {
	// 32 goroutines each POST an insert; all must answer 2xx (busy_timeout, IMMEDIATE tx)
}
```

- [ ] **Step 2:** `cd lattice/gogen/rt && go test ./...` → FAIL
- [ ] **Step 3:** Implement by porting the prelude.
- [ ] **Step 4:** `go vet ./... && go test ./...` → PASS
- [ ] **Step 5: Commit**

```bash
git add lattice/gogen/rt
git commit -m "feat(gogen): Go request runtime — params, auth, errors, transactions, SQL counting, SQLite functions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Go endpoint emitter, build, CLI

**Files:**
- Create: `lattice/gogen/endpoints.py`, `lattice/gogen/build.py`, `lattice/gogen/__main__.py`, `tests/gogen/test_build.py`

**Interfaces:**
- Consumes: `EndpointPlan` and its steps (`lattice/api/ir.py:113-360`), `rt` request runtime, `emit_go_t2`, `lower_api`, `check_source(..., target="go")`
- Produces:
  - `lattice.gogen.endpoints.emit_go_endpoints(plans: Sequence[EndpointPlan], types: GoTypes) -> tuple[str, str]` → (`handlers.go`, `main.go`)
  - `@dataclass(frozen=True) class GoBuild: binary: Path; check: CheckResult; module_dir: Path`
  - `class GoBuildError(Exception)` (carries `go build` stderr)
  - `lattice.gogen.build.build_go_app(source: str, profile: Profile, out_dir: Path) -> GoBuild` — checks for target `go` (raises `GoBuildError` with diagnostics when rejected), lowers, emits, writes `go.mod` (`module latticeapp`, `require lattice.dev/rt v0.0.0`, `replace lattice.dev/rt => <abs rt path>`, plus `go.sum` copied from `rt`), runs `go build -trimpath -o app`, caches by sha256 of (source, profile repr, rt tree hash) under `out_dir/.cache/<hash>/app`
  - CLI: `uv run python -m lattice.gogen build <file.lattice.py> --profile <module:attr> --out <dir>` printing the binary path

Per step, emit exactly what `lattice/api/codegen.py::_EndpointGen` emits for Python (read it in full first; the Go handler is a transliteration):

| Step | Go |
|---|---|
| `ReadStep` | `rows := c.Query(<sql const>, <args>)`; scan into `[]row_<id>` (pointer fields for nullable columns); flags appended |
| `CheckStep(one)` | 0 rows → `ErrorSpec` response; >1 → `rt.Fail("R310", ...)` |
| `CheckStep(require)` | any flag false/null → `ErrorSpec` response |
| `WriteStep` | insert/update/delete SQL with `RETURNING`; `unique_or` → catch SQLite unique violation → `conflict` response; `missing` → its response |
| `ForEachStep` | outbox insert (unreachable on Go in this plan: L890) |
| `PostStep` / `T2CheckStep` / `CallStep` | call the generated `P_<proc>` with the request's `c.Fuel`, argument decoding as `t2_arg`/`t2_decode` do, results as `t2_result` |
| response | `respond_one` / `respond_page` / `respond_list` equivalents in `rt` |

Routing: same route specificity order as `_specificity(plan)`; 404/405 app-level handlers identical to the generated FastAPI app (`target="app"`).

- [ ] **Step 1: Failing test**

```python
# tests/gogen/test_build.py
import json
import subprocess

from bench.api_datasets import api_profile, build_db
from lattice.gogen.build import GoBuildError, build_go_app
from tests.api.test_codegen_differential import PROGRAMS

SRC = PROGRAMS["c01"][0]  # PROGRAMS: pid -> (source, ApiTask)


def test_builds_and_serves(tmp_path):
    built = build_go_app(SRC, api_profile(), tmp_path)
    db = tmp_path / "app.db"
    build_db(db)
    proc = subprocess.Popen([str(built.binary), "--db", str(db), "--addr", "127.0.0.1:0"],
                            stdout=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline().strip()
        assert line.startswith("LISTENING ")
    finally:
        proc.terminate()
        proc.wait(5)


def test_rejected_program_raises(tmp_path, realapps_source, realapps_profile):
    import pytest
    with pytest.raises(GoBuildError) as e:
        build_go_app(realapps_source, realapps_profile, tmp_path)
    assert "L890" in str(e.value)
```

(Use `setup_of("c01")` from `tests/api/test_codegen_differential.py` for the profile and DB builder if it differs from `api_profile()` / `build_db`.)

- [ ] **Step 2:** `uv run pytest tests/gogen/test_build.py -v` → FAIL
- [ ] **Step 3:** Implement `endpoints.py`, `build.py`, `__main__.py`.
- [ ] **Step 4:** `uv run pytest tests/gogen -v` → PASS; `uv run python -m lattice.gogen build tests/fixtures/api/c01/lattice.py --profile bench.api_datasets:api_profile --out /tmp/lg` prints a path to an executable.
- [ ] **Step 5: Commit**

```bash
git add lattice/gogen tests/gogen
git commit -m "feat(gogen): EndpointPlan to Go handlers, build with content-hash cache, CLI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: The equivalence gate

**Files:**
- Create: `lattice/gogen/proxy.py`, `tests/gogen/test_go_differential.py`, `tests/gogen/test_fuel_parity.py`
- Modify: `bench/api_runner.py` (add `run_cases_go`)

**Interfaces:**
- Produces:
  - `lattice.gogen.proxy.GoServer` — context manager: `GoServer(binary: Path, db: Path, trace_sql: bool = True)`, attribute `base_url: str`; starts the binary, waits for `LISTENING`, terminates on exit (kill after 5 s)
  - `lattice.gogen.proxy.go_app_factory(binary: Path) -> AppFactory` — returns `factory(db_path) -> ASGI app` that starts a `GoServer` for that DB and forwards each request with `httpx` (method, path, query, headers, body), returning status, headers and body unchanged; stops the server on ASGI lifespan shutdown (usable by `lattice.importer.verify.verify`)
  - `bench.api_runner.run_cases_go(binary: Path, task: ApiTask, *, db_builder: DbBuilder) -> CaseResults` — same semantics as `run_cases_fastapi` (fresh DB copy per case, `evaluate_case`, `queries` from `X-Lattice-Sql-Count`)

- [ ] **Step 1: Failing gate test**

```python
# tests/gogen/test_go_differential.py
"""Equivalence gate (spec 2026-09-28 go-target §6): every program of the API codegen
differential registry that passes check_source(..., target="go") behaves identically when
compiled to Go and when served by the Python runtime: status, full JSON body, SQL
statement count, after_sql rows and a digest of the whole database, on the task's cases
and the generated probes. Programs rejected for Go only with L890 are the expected
exclusions, asserted by name."""

import pytest

from lattice.check import check_source
from lattice.gogen.build import build_go_app
from tests.api.test_codegen_differential import (PROGRAMS, _probes, _run_runtime,
                                                 db_digest, normalize, setup_of)

EXPECTED_EXCLUSIONS = {"realapps", "effects", "solo", "solo_runtime", "solo_answers"}
# ^ verify this set in Step 3: it must equal the programs whose only go-target errors are L890


def _go_ok(pid):
    source, task = PROGRAMS[pid]
    setup = setup_of(pid)
    return check_source(source, setup.profile, target="go")


def test_exclusions_are_exactly_the_native_programs():
    rejected = {}
    for pid in PROGRAMS:
        r = _go_ok(pid)
        if not r.ok:
            rejected[pid] = {d.code for d in r.diagnostics if d.severity.name == "ERROR"}
    assert set(rejected) == EXPECTED_EXCLUSIONS
    assert all(codes == {"L890"} for codes in rejected.values()), rejected


@pytest.mark.parametrize("pid", sorted(set(PROGRAMS) - EXPECTED_EXCLUSIONS))
def test_go_equals_runtime(pid, tmp_path):
    source, task = PROGRAMS[pid]
    setup = setup_of(pid)
    built = build_go_app(source, setup.profile, tmp_path)
    cases = task.cases + _probes(task.cases)
    runtime = _run_runtime(pid, cases, tmp_path / "rt")          # existing helper: records per case
    go = _run_go(built.binary, pid, cases, tmp_path / "go")       # same record shape, via GoServer
    mismatches = [(a.name, a, b) for a, b in zip(runtime, go) if normalize(a) != normalize(b)]
    assert mismatches == []
```

Write `_run_go` in the test module by mirroring `_run_runtime` (read it: it produces per-case records of status, body, query count, after rows, DB digest) but issuing requests through `GoServer(built.binary, case_db)` with `httpx`. The records must be produced by the same normalising code path so the comparison is apples to apples.

```python
# tests/gogen/test_fuel_parity.py
"""Procedures built to exhaust fuel at a known step fail at the same step on both
targets (same R620/R621/R622 message, same 500 body)."""
# Use the PROGRAMS["t2"] and PROGRAMS["hardening"] endpoints that call procedures; add
# three cases per program with a loop sized to exhaust the budget at step 1, at the
# budget exactly, and one step after; assert the Go and runtime 500 bodies are equal.
```

- [ ] **Step 2:** `uv run pytest tests/gogen/test_go_differential.py -x -v` → FAIL (proxy / `_run_go` missing)
- [ ] **Step 3:** Implement `proxy.py`, `run_cases_go`, `_run_go`; run the gate; fix **emitter/runtime** bugs until every program shows 0 mismatches. Do not add tolerances: a difference is a bug in Go, or a v1.1 rule missing from Task 5/6 (then add the rule and a semantics case on both sides).
- [ ] **Step 4:** Full run: `uv run pytest -q && (cd lattice/gogen/rt && go vet ./... && go test ./...)` → PASS
- [ ] **Step 5: Commit**

```bash
git add lattice/gogen bench/api_runner.py tests/gogen
git commit -m "test(gogen): equivalence gate — Lattice to Go equals the runtime on every non-native program

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Record the result

**Files:**
- Modify: `docs/results/2026-09-26-bench-smoke.md` (append section "Go target — equivalence gate (5a)"), `docs/lattice-vision.md` (§2 status table row "Go target", §6 roadmap: sub-project 5 with 5a done, 5b next)

- [ ] **Step 1:** Append to the results file: programs and case counts compared, mismatches (0), exclusions with reasons (L890), semantics suite size and Python/Go pass counts, v1.1 behaviour changes (R623, ordered sets, ASCII regex classes, RE2-only), binary size of one built app (`ls -l`), and cold-start time (median of 20 starts of `GoServer` until `LISTENING`).
- [ ] **Step 2:** Update the vision status row and roadmap.
- [ ] **Step 3: Commit**

```bash
git add docs/results/2026-09-26-bench-smoke.md docs/lattice-vision.md
git commit -m "docs(results): Go target equivalence gate (5a)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## After this plan

Plan 5b (spec §5, build-order steps 6–7): CLI transport in `bench/cli.py`, the Go platform kit, arms G1/G/B/C/X and GS/LS, Go-specific trap tasks, the T2 script suite and MultiPL-E subset, the vegeta load driver, dry runs and cost estimate, live runs and the write-up. Write it once Task 13 is green, against the real `lattice.gogen` interfaces.
