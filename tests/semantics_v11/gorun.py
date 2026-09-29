"""Run semantics cases on the Go target (plan 5a Task 8).

`run_go(cases)` compiles every case with `lattice.gogen.t2.emit_go_t2` into one Go
program -- each case in its own package (`cases/c<i>`), a `main.go` that runs them all,
each under recover -- builds it once (offline, against lattice/gogen/rt through a
`replace` directive) and runs it. The program prints one JSON object: case name -> the
result, tagged so Python can rebuild it exactly (`{"t": "float", "v": repr}`, ...), or
`{"err": code, "msg": ...}` for a Lattice runtime diagnostic, `{"exc": kind, "msg":
...}` for a Python exception, `{"panic": text}` for anything else (a compiler bug).
Results come back in a strict tagged form (`strict`), which Python's results are put in
too, so the targets are compared type- and order-exactly, messages included.

The build is cached by the content hash of the generated sources and the runtime under
`.cache/gorun/<hash>/` at the repository root.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import struct
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from lattice.check import check_source
from lattice.check.t2 import types as T
from lattice.check.t2.types import T2Type
from lattice.gogen.t2 import emit_go_t2, go_float, go_string
from lattice.gogen.types import GoTypes
from lattice.runtime import LatticeInternalError
from lattice.t2ir.lower import lower_t2
from tests.check.t2_support import PROFILE
from tests.semantics_v11.cases import Case, Err, Exc

ROOT = Path(__file__).resolve().parents[2]
RT = ROOT / "lattice" / "gogen" / "rt"
CACHE = ROOT / ".cache" / "gorun"
GO_ENV = {"GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOWORK": "off", "GOTOOLCHAIN": "local"}


# ---- Go values of the cases' arguments ----------------------------------------------------


def go_literal(value: object, t: T2Type, types: GoTypes) -> str:  # noqa: PLR0911
    """A Go expression with exactly the Python value `value` of T2 type t."""
    match t:
        case T.Opt(inner=inner):
            if value is None:
                return f"rt.None[{types.spell(inner)}]()"
            return f"rt.Some[{types.spell(inner)}]({go_literal(value, inner, types)})"
        case T.ListT(elem=e):
            items = ", ".join(go_literal(x, e, types) for x in value)  # type: ignore[attr-defined]
            return f"rt.NewList[{types.spell(e)}]({items})"
        case T.SetT(elem=e):
            items = ", ".join(go_literal(x, e, types) for x in value)  # type: ignore[attr-defined]
            return f"rt.NewSet[{types.spell(e)}]({items})"
        case T.DictT(key=k, val=v):
            sets = "".join(
                f"d.Set({go_literal(a, k, types)}, {go_literal(b, v, types)}); "
                for a, b in value.items()  # type: ignore[attr-defined]
            )
            new = f"rt.NewDict[{types.spell(k)}, {types.spell(v)}]()"
            return f"func() {types.spell(t)} {{ d := {new}; {sets}return d }}()"
        case T.TupleT(elems=es):
            pairs = zip(value, es, strict=True)  # type: ignore[call-overload]
            items = ", ".join(go_literal(x, e, types) for x, e in pairs)
            return f"{types.spell(t)}{{{items}}}"
    if t == T.BOOL:
        return "true" if value else "false"
    if t == T.INT:
        return f"int64({int(value)})"  # type: ignore[call-overload]
    if t == T.FLOAT:
        bits = struct.unpack("<Q", struct.pack("<d", float(value)))[0]  # type: ignore[arg-type]
        return f"math.Float64frombits(0x{bits:016x})"
    if t in (T.STR, T.UUID):
        return go_string(str(value))
    if t == T.DATETIME:
        assert isinstance(value, datetime) and value.utcoffset() == timedelta(0), value
        return f"rt.DateTime({(value - _EPOCH) // timedelta(microseconds=1)})"
    if t == T.DATE:
        assert isinstance(value, date) and not isinstance(value, datetime), value
        return f"rt.Date({value.toordinal()})"
    if t == T.DURATION:
        assert isinstance(value, timedelta)
        return f"rt.Duration{{D: {value.days}, S: {value.seconds}, Us: {value.microseconds}}}"
    if t == T.JSON:
        return _json_literal(value)
    raise LatticeInternalError(f"gorun: no Go literal for {t.render()}")


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _json_literal(value: object) -> str:  # noqa: PLR0911
    """A Go Json value (`any`) holding exactly the Python value."""
    if value is None:
        return "any(nil)"
    if isinstance(value, bool):
        return f"any({'true' if value else 'false'})"
    if isinstance(value, int):
        return f"any(int64({value}))"
    if isinstance(value, float):
        return f"any({go_float(value)})"
    if isinstance(value, str):
        return f"any({go_string(value)})"
    if isinstance(value, list):
        return f"any(rt.NewList[any]({', '.join(_json_literal(x) for x in value)}))"
    if isinstance(value, dict):
        sets = "".join(f"d.Set({go_string(k)}, {_json_literal(v)}); " for k, v in value.items())
        new = "rt.NewDict[string, any]()"
        return f"any(func() *rt.Dict[string, any] {{ d := {new}; {sets}return d }}())"
    raise LatticeInternalError(f"gorun: no Json literal for {value!r}")


def go_encoder(t: T2Type, types: GoTypes) -> str:  # noqa: PLR0911
    """A Go func value encoding a value of type t as tagged JSON (a map / slice tree)."""
    go = types.spell(t)
    match t:
        case T.Opt(inner=inner):
            return (
                f'func(v {go}) any {{ if !v.Ok {{ return map[string]any{{"t": "none"}} }}; '
                f"return ({go_encoder(inner, types)})(v.V) }}"
            )
        case T.ListT(elem=e):
            return _seq_encoder(go, "v.Items", "list", go_encoder(e, types))
        case T.SetT(elem=e):
            return _seq_encoder(go, "v.Items()", "set", go_encoder(e, types))
        case T.DictT(key=k, val=val):
            pair = f"[]any{{({go_encoder(k, types)})(p.Key), ({go_encoder(val, types)})(p.Value)}}"
            return (
                f"func(v {go}) any {{ out := []any{{}}; for _, p := range v.Items() {{ "
                f"out = append(out, {pair}) }}; "
                f'return map[string]any{{"t": "dict", "v": out}} }}'
            )
        case T.TupleT(elems=es):
            items = ", ".join(f"({go_encoder(e, types)})(v.F{i})" for i, e in enumerate(es))
            tagged = f'map[string]any{{"t": "tuple", "v": []any{{{items}}}}}'
            return f"func(v {go}) any {{ return {tagged} }}"
        case T.RecordT(name=name):
            fields = types.records[name]
            items = ", ".join(f"({go_encoder(ft, types)})(v.F_{f})" for f, ft in fields)
            return (
                f'func(v {go}) any {{ return map[string]any{{"t": "record", '
                f'"name": {json.dumps(name)}, "v": []any{{{items}}}}} }}'
            )
    if t == T.INT:
        return (
            'func(v int64) any { return map[string]any{"t": "int", "v": strconv.FormatInt(v, 10)} }'
        )
    if t == T.FLOAT:
        return 'func(v float64) any { return map[string]any{"t": "float", "v": rt.ReprFloat(v)} }'
    if t == T.BOOL:
        return 'func(v bool) any { return map[string]any{"t": "bool", "v": v} }'
    if t in (T.STR, T.UUID):
        return 'func(v string) any { return map[string]any{"t": "str", "v": v} }'
    if t == T.NONE:
        return 'func(v struct{}) any { return map[string]any{"t": "none"} }'
    if t == T.DATE:
        return 'func(v rt.Date) any { return map[string]any{"t": "date", "v": v.ISO()} }'
    if t == T.DATETIME:
        return (
            'func(v rt.DateTime) any { return map[string]any{"t": "datetime", "v": v.Isoformat()} }'
        )
    if t == T.DURATION:
        return (
            'func(v rt.Duration) any { return map[string]any{"t": "duration", '
            '"v": []any{v.D, v.S, v.Us}} }'
        )
    if t == T.JSON:
        return "rt.JsonTagged"
    raise LatticeInternalError(f"gorun: no encoder for {t.render()}")


def _seq_encoder(go: str, items: str, tag: str, elem: str) -> str:
    return (
        f"func(v {go}) any {{ out := []any{{}}; for _, x := range {items} {{ "
        f'out = append(out, ({elem})(x)) }}; return map[string]any{{"t": "{tag}", "v": out}} }}'
    )


def strict(v: object) -> object:  # noqa: PLR0911
    """A Python result in the strict tagged form both targets' results are compared in:
    (tag, value) trees where bool, int and float differ, a float is its repr (-0.0 is not
    0.0), lists and tuples differ, dicts and sets are their items in order, a record is
    its name and fields, and an error is its code or kind and its message."""
    import dataclasses  # noqa: PLC0415

    from lattice.t2.support import T2Set  # noqa: PLC0415

    if isinstance(v, bool):
        return ("bool", v)
    if isinstance(v, int):
        return ("int", v)
    if isinstance(v, float):
        return ("float", repr(v))
    if isinstance(v, str):
        return ("str", v)
    if v is None:
        return ("none",)
    if isinstance(v, datetime):
        return ("datetime", v.isoformat())
    if isinstance(v, date):
        return ("date", v.isoformat())
    if isinstance(v, timedelta):
        return ("duration", v.days, v.seconds, v.microseconds)
    if isinstance(v, list):
        return ("list", tuple(strict(x) for x in v))
    if isinstance(v, tuple):
        return ("tuple", tuple(strict(x) for x in v))
    if isinstance(v, dict):
        return ("dict", tuple((strict(k), strict(x)) for k, x in v.items()))
    if isinstance(v, T2Set | set | frozenset):
        return ("set", tuple(strict(x) for x in v))
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        fields = tuple(strict(getattr(v, f.name)) for f in dataclasses.fields(v))
        return ("record", type(v).__name__, fields)
    raise ValueError(f"no strict form for {v!r}")


def decode(v: object) -> object:  # noqa: PLR0911
    """A tagged JSON result from Go -> the strict form (`strict`)."""
    if not isinstance(v, dict):
        raise ValueError(f"untagged result {v!r}")
    if "err" in v:
        return ("err", v["err"], v["msg"])
    if "exc" in v:
        return ("exc", v["exc"], v["msg"])
    if "panic" in v:
        return ("panic", v["panic"])
    tag = v["t"]
    if tag == "none":
        return ("none",)
    if tag == "int":
        return ("int", int(v["v"]))
    if tag in ("float", "str", "bool", "date", "datetime"):
        return (tag, v["v"])
    if tag == "duration":
        return ("duration", *(int(x) for x in v["v"]))
    if tag in ("list", "tuple", "set"):
        return (tag, tuple(decode(x) for x in v["v"]))
    if tag == "dict":
        return ("dict", tuple((decode(k), decode(x)) for k, x in v["v"]))
    if tag == "record":
        return ("record", v["name"], tuple(decode(x) for x in v["v"]))
    raise ValueError(f"unknown tag {tag!r}")


def expect_holds(result: object, expect: object) -> bool:
    """A case's expected value holds for a strict result: Err(code) / Exc(kind) match
    the code / kind (the message is compared between the targets), a value matches
    exactly in strict form."""
    if isinstance(expect, Err):
        return isinstance(result, tuple) and result[:2] == ("err", expect.code)
    if isinstance(expect, Exc):
        return isinstance(result, tuple) and result[:2] == ("exc", expect.kind)
    return result == strict(expect)


# ---- the program ------------------------------------------------------------------------


def case_package(case: Case, pkg: str) -> tuple[str, str]:
    """The Go sources of one case's package: its procedures (t2.go), and Run() (run.go).
    A case checks with the T2 test profile unless it carries its own (`case.profile`)."""
    source = case.source_with_header
    result = check_source(source, getattr(case, "profile", PROFILE), target="go")
    if not result.ok or result.program is None or result.program.t2 is None:
        raise LatticeInternalError(f"gorun: {case.name} does not check for Go")
    module = lower_t2(result.program.t2, ast.parse(result.program.source))
    code = emit_go_t2(module, package=pkg)
    proc = next(p for p in module.procedures if p.name == case.proc)
    types = GoTypes((r.name, r.fields) for r in module.records)
    args = ", ".join(
        go_literal(v, t, types) for v, (_, t) in zip(case.args, proc.params, strict=True)
    )
    call = f"P_{case.proc}({', '.join(['fuel', *([args] if args else [])])})"
    body = f"return ({go_encoder(proc.returns, types)})({call})"
    # the tuple and record types Run spells are declared by t2.go: the procedure's
    # signature uses them, and their names are derived from the types
    run = (
        f'package {pkg}\n\nimport (\n\t"math"\n\t"strconv"\n\n\t"lattice.dev/rt"\n)\n\n'
        f"var _ = math.Inf\nvar _ = strconv.Itoa\n\n"
        "// Fuel is the fuel of the last Run (what is left of it is reported).\n"
        "var Fuel *rt.Fuel\n\n"
        f"// Run runs the case {case.name} on a budget.\nfunc Run(budget int64) any {{\n"
        f"\tfuel := rt.NewFuel(budget)\n\tFuel = fuel\n\t{body}\n}}\n"
    )
    return code, run


_MAIN_HEAD = """package main

import (
\t"encoding/json"
\t"fmt"
\t"os"

\t"lattice.dev/rt"
{imports}
)

func run(f func() any) (out any) {{
\tdefer func() {{
\t\tif r := recover(); r != nil {{
\t\t\tif e, ok := r.(*rt.Error); ok {{
\t\t\t\tif e.Code != "" {{
\t\t\t\t\tout = map[string]any{{"err": e.Code, "msg": e.Msg}}
\t\t\t\t\treturn
\t\t\t\t}}
\t\t\t\tout = map[string]any{{"exc": e.Kind, "msg": e.Msg}}
\t\t\t\treturn
\t\t\t}}
\t\t\tout = map[string]any{{"panic": fmt.Sprint(r)}}
\t\t}}
\t}}()
\treturn f()
}}

type entry struct {{
\trun  func(int64) any
\tfuel func() *rt.Fuel
}}

var cases = map[string]entry{{
{calls}
}}

// main reads {{case name: [budget, ...]}} and prints the result and the fuel left of
// each run, keyed "<name>@<budget>".
func main() {{
\tvar req map[string][]int64
\tif err := json.NewDecoder(os.Stdin).Decode(&req); err != nil {{
\t\tpanic(err)
\t}}
\tres := map[string]any{{}}
\tleft := map[string]int64{{}}
\tfor name, budgets := range req {{
\t\tc := cases[name]
\t\tfor _, b := range budgets {{
\t\t\tkey := fmt.Sprintf("%s@%d", name, b)
\t\t\tres[key] = run(func() any {{ return c.run(b) }})
\t\t\tleft[key] = c.fuel().Left
\t\t}}
\t}}
\tout := map[string]any{{"results": res, "left": left}}
\tif err := json.NewEncoder(os.Stdout).Encode(out); err != nil {{
\t\tpanic(err)
\t}}
}}
"""


def write_program(cases: Sequence[Case]) -> dict[str, str]:
    """The program's files (relative path -> text)."""
    files: dict[str, str] = {}
    imports, calls = [], []
    for i, case in enumerate(cases):
        pkg = f"c{i}"
        code, run = case_package(case, pkg)
        files[f"cases/{pkg}/t2.go"] = code
        files[f"cases/{pkg}/run.go"] = run
        imports.append(f'\t{pkg} "latticegen/cases/{pkg}"')
        calls.append(
            f"\t{json.dumps(case.name)}: {{{pkg}.Run, func() *rt.Fuel {{ return {pkg}.Fuel }}}},"
        )
    files["main.go"] = _MAIN_HEAD.format(imports="\n".join(imports), calls="\n".join(calls))
    files["go.mod"] = (
        "module latticegen\n\ngo 1.26.0\n\nrequire lattice.dev/rt v0.0.0\n\n"
        f"replace lattice.dev/rt => {RT}\n"
    )
    files["go.sum"] = (RT / "go.sum").read_text()
    return files


def _rt_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(RT.glob("*.go")):
        h.update(p.name.encode() + p.read_bytes())
    h.update((RT / "go.mod").read_bytes())
    return h.hexdigest()


def build(cases: Sequence[Case]) -> Path:
    """The built program (cached by content)."""
    files = write_program(cases)
    h = hashlib.sha256(_rt_digest().encode())
    for name in sorted(files):
        h.update(name.encode() + b"\0" + files[name].encode())
    out = CACHE / h.hexdigest()[:20]
    binary = out / "prog"
    if binary.exists():
        return binary
    tmp = out.with_name(f"{out.name}.tmp{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    for name, text in files.items():
        path = tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    env = {**os.environ, **GO_ENV}
    proc = subprocess.run(  # noqa: S603
        ["go", "build", "-o", "prog", "."],  # noqa: S607
        cwd=tmp,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"go build failed:\n{proc.stderr}")
    try:
        tmp.rename(out)
    except OSError:  # another process built the same program first: keep theirs
        shutil.rmtree(tmp, ignore_errors=True)
    return binary


def run_go(cases: Sequence[Case]) -> dict[str, object]:
    """Run the cases on Go: case name -> result, in strict form (`strict`)."""
    return run_go_detailed(cases)[0]


def run_go_detailed(cases: Sequence[Case]) -> tuple[dict[str, object], dict[str, int]]:
    """run_go, and the fuel each case had left when it ended (Fuel.Left)."""
    runs = run_go_budgets(cases, {c.name: [c.budget] for c in cases})
    results = {name: r for (name, _), (r, _) in runs.items()}
    left = {name: fl for (name, _), (_, fl) in runs.items()}
    return results, left


def run_go_budgets(
    cases: Sequence[Case], budgets: Mapping[str, Sequence[int]]
) -> dict[tuple[str, int], tuple[object, int]]:
    """Run each case on Go once per budget: (name, budget) -> (strict result, fuel
    left)."""
    binary = build(cases)
    proc = subprocess.run(  # noqa: S603
        [str(binary)],
        input=json.dumps({k: list(v) for k, v in budgets.items()}),
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"the Go program failed:\n{proc.stderr[-4000:]}")
    raw = json.loads(proc.stdout)
    out: dict[tuple[str, int], tuple[object, int]] = {}
    for key, v in raw["results"].items():
        name, _, b = key.rpartition("@")
        out[(name, int(b))] = (decode(v), raw["left"][key])
    return out


def run_python_budget(case: Case, budget: int) -> tuple[object, int]:
    """The case's result on Python with a budget, in strict form (an escaping error as
    ("err", code, message) or ("exc", kind, message)), and the fuel left."""
    from lattice.t2 import T2Fuel  # noqa: PLC0415
    from lattice.t2.support import T2Error  # noqa: PLC0415
    from tests.semantics_v11.cases import _EXC_TYPES  # noqa: PLC0415
    from tests.t2.util import load  # noqa: PLC0415

    load_fn = getattr(case, "load", None)
    fn = (load_fn() if load_fn is not None else load(case.source))[case.proc]
    fuel = T2Fuel(budget)
    try:
        result: object = strict(fn(fuel, *case.args))
    except T2Error as e:
        result = ("err", e.code, e.message)
    except Exception as e:  # noqa: BLE001 - every escaping exception is a result
        kinds = [k.__name__ for k in _EXC_TYPES if isinstance(e, k)] or [type(e).__name__]
        result = ("exc", kinds[0], str(e))
    return result, fuel.left


def python_fuel_left(case: Case) -> int:
    """The fuel a case has left after running on Python (as run_python runs it)."""
    return run_python_budget(case, case.budget)[1]
