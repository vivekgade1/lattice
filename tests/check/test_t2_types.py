"""The T2 type checker: annotations, inference, widening, None narrowing, containers,
comprehensions, f-strings, for targets, try/except, return completeness, and
error collection without cascades (L900-L949)."""

import ast

import pytest
from t2_support import check, errors, first, ok, proc

from lattice.check.t2 import types as T

# ---- annotations -------------------------------------------------------------------


def test_annotation_forms_accepted():
    src = proc(
        "return 0",
        sig=(
            "(a: Int, b: Float, c: Str, d: Bool, e: Date, f2: DateTime, g: Duration, h: Uuid,"
            " i: Int | None, j: list[Int], k: dict[Str, list[Float]], l2: set[Str],"
            " m: tuple[Int, Str], n: Row[Items], o: Row(Items, private=True), p: Caller,"
            " q: Json, r: str, s: int) -> Int"
        ),
    )
    ok(src)


@pytest.mark.parametrize(
    "ann", ["Optional[Int]", "List[Int]", "Widget", "list[Int, Str]", "Row[Nope]", "Any"]
)
def test_bad_annotations(ann):
    assert "L902" in errors(proc("return 0", sig=f"(a: {ann}) -> Int"))


def test_row_type_needs_api_table():
    d = first(proc("return 0", sig="(a: Row[Nope]) -> Int"), "L902")
    assert "Items" in d.hint


# ---- inference and widening ---------------------------------------------------------


def _local_types(src: str, name: str = "f") -> dict:
    return dict(ok(src).program.t2.procedures[name].locals)


def test_local_inference():
    src = proc(
        """
        a = 1
        b = 2.5
        c = "s"
        d = [1, 2]
        e = {"k": [1.0]}
        g = (1, "a")
        h = a + b
        i = c.upper()
        return a
        """
    )
    loc = _local_types(src)
    assert loc["a"] == T.INT and loc["b"] == T.FLOAT and loc["c"] == T.STR
    assert loc["d"] == T.ListT(T.INT)
    assert loc["e"] == T.DictT(T.STR, T.ListT(T.FLOAT))
    assert loc["g"] == T.TupleT((T.INT, T.STR))
    assert loc["h"] == T.FLOAT and loc["i"] == T.STR


def test_int_widens_to_float():
    ok(proc("y: Float = x\nz = [1, 2.5]\nreturn y", sig="(x: Int) -> Float"))


def test_float_does_not_narrow_to_int():
    d = first(proc("y = 0\ny = 1.5\nreturn y"), "L901")
    assert "Float" in d.message and "0.0" in d.hint


def test_no_other_implicit_conversion():
    assert errors(proc('return "a"')) == ["L908"]
    assert errors(proc("return x + 's'")) == ["L903"]


def test_return_type_mismatch():
    assert errors(proc("return 1.5")) == ["L908"]
    ok(proc("return x", sig="(x: Int) -> Float"))


def test_empty_container_needs_annotation():
    assert errors(proc("xs = []\nreturn 0")) == ["L911"]
    ok(proc("xs: list[Int] = []\nreturn len(xs)"))
    ok(proc("return []", sig="(x: Int) -> list[Int]"))


def test_unknown_name():
    d = first(proc("return y"), "L900")
    assert "'y'" in d.message


def test_possibly_unbound():
    src = proc("if x > 0:\n    y = 1\nreturn y")
    assert errors(src) == ["L910"]
    ok(proc("if x > 0:\n    y = 1\nelse:\n    y = 2\nreturn y"))


# ---- None narrowing ---------------------------------------------------------------


def test_optional_use_without_narrowing():
    d = first(proc("return x + 1", sig="(x: Int | None) -> Int"), "L906")
    assert "is not None" in d.hint


def test_narrow_is_not_none():
    ok(proc("if x is not None:\n    return x + 1\nreturn 0", sig="(x: Int | None) -> Int"))


def test_narrow_is_none_early_return():
    ok(proc("if x is None:\n    return 0\nreturn x + 1", sig="(x: Int | None) -> Int"))


def test_narrow_else_branch():
    ok(proc("if x is None:\n    y = 0\nelse:\n    y = x\nreturn y", sig="(x: Int | None) -> Int"))


def test_narrow_by_assignment_in_branch():
    ok(proc("if x is None:\n    x = 0\nreturn x + 1", sig="(x: Int | None) -> Int"))


def test_narrow_and_chain():
    ok(proc("return x is not None and x > 2", sig="(x: Int | None) -> Bool"))


def test_narrow_attribute_path():
    src = proc(
        "if item.due is not None:\n    return item.due.year\nreturn 0",
        sig="(item: Row[Items]) -> Int",
    )
    ok(src)
    bad = proc("return item.due.year", sig="(item: Row[Items]) -> Int")
    assert errors(bad) == ["L906"]


def test_narrowing_undone_in_loop_that_reassigns():
    src = proc(
        """
        y: Int | None = 1
        if y is not None:
            for i in range(3):
                z = y + 1
                y = None
        return 0
        """
    )
    assert errors(src) == ["L906"]


def test_or_default():
    ok(proc('return s or "anon"', sig="(s: Str | None) -> Str"))


def test_ifexp_narrowing():
    ok(proc("return x if x is not None else 0", sig="(x: Int | None) -> Int"))


# ---- records ---------------------------------------------------------------------


RECORDS = """
@record
class Point:
    x: Int
    y: Int
    label: Str | None = None

@record
class Path:
    points: list[Point]
"""


def test_record_construction_and_fields():
    src = RECORDS + proc(
        "p = Point(x=1, y=2)\nq = Point(3, 4, 'a')\npath = Path(points=[p, q])\n"
        "return path.points[0].x + q.y",
    )
    ok(src)


def test_record_construction_errors():
    assert "L904" in errors(RECORDS + proc("p = Point(x=1)\nreturn 0"))
    assert "L904" in errors(RECORDS + proc("p = Point(x=1, y=2, z=3)\nreturn 0"))
    assert "L904" in errors(RECORDS + proc("p = Point(x='a', y=2)\nreturn 0"))


def test_record_unknown_field():
    d = first(RECORDS + proc("p = Point(x=1, y=2)\nreturn p.z"), "L905")
    assert "x" in d.hint


def test_record_field_type_unknown():
    src = "@record\nclass A:\n    b: Nope\n"
    assert errors(src) == ["L902"]


def test_record_optional_field_narrowing():
    src = RECORDS + proc(
        "p = Point(x=1, y=2)\nif p.label is None:\n    return 0\nreturn len(p.label)"
    )
    ok(src)


# ---- lists, dicts, sets, comprehensions ----------------------------------------------


def test_list_ops():
    src = proc(
        """
        xs: list[Int] = []
        xs.append(x)
        xs.extend([1, 2])
        ys = sorted(xs, reverse=True)
        head = ys[0]
        tail = ys[1:]
        n = len(tail) + sum(xs) + max(xs) + min(1, 2)
        return head + n
        """
    )
    ok(src)


def test_list_element_type_enforced():
    assert errors(proc("xs = [1]\nxs.append('a')\nreturn 0")) == ["L904"]
    assert errors(proc("xs = [1, 'a']\nreturn 0")) == ["L903"]


def test_dict_ops():
    src = proc(
        """
        d: dict[Str, Int] = {}
        d["a"] = 1
        d["a"] += 2
        v = d.get("b")
        w = d.get("b", 0)
        for k, n in d.items():
            w += n + len(k)
        if v is not None:
            w += v
        return w
        """
    )
    ok(src)


def test_dict_value_type_enforced():
    assert errors(proc("d = {'a': 1}\nd['b'] = 'x'\nreturn 0")) == ["L901"]


def test_dict_get_is_optional():
    assert errors(proc("d = {'a': 1}\nreturn d.get('a') + 1")) == ["L906"]


def test_unhashable_key_rejected():
    assert "L923" in errors(proc("d = {[1]: 2}\nreturn 0"))


def test_counter_and_defaultdict():
    src = """
    from collections import Counter, defaultdict

    @procedure
    def f(words: list[Str]) -> Int:
        c = Counter(words)
        top = c.most_common(1)
        groups: dict[Str, list[Str]] = defaultdict(list)
        for w in words:
            groups[w[0]].append(w)
        return c["a"] + len(top) + len(groups)
    """
    ok(src)


def test_comprehensions_and_scopes():
    src = proc(
        """
        sq = [i * i for i in range(x)]
        ev = {i for i in sq if i % 2 == 0}
        m = {str(i): i for i in sq}
        t = sum(i for i in sq)
        return len(ev) + len(m) + t
        """
    )
    loc = _local_types(src)
    assert loc["sq"] == T.ListT(T.INT)
    assert loc["ev"] == T.SetT(T.INT)
    assert loc["m"] == T.DictT(T.STR, T.INT)
    assert "i" not in loc  # comprehension targets do not leak


def test_comprehension_target_does_not_leak():
    assert errors(proc("ys = [i for i in range(3)]\nreturn i")) == ["L900"]


def test_comprehension_condition_narrows():
    ok(
        proc(
            "return [v + 1 for v in xs if v is not None]", sig="(xs: list[Int | None]) -> list[Int]"
        )
    )


def test_generator_outside_consumer_rejected():
    assert "L147" in errors(proc("g = (i for i in range(3))\nreturn 0"))


# ---- f-strings, for targets, try/except -------------------------------------------------


def test_fstrings():
    ok(proc('return f"{x:>5} {x!r} {[x]}"', sig="(x: Int) -> Str"))
    assert errors(proc('return f"{y}"', sig="(x: Int) -> Str")) == ["L900"]


def test_for_targets():
    src = proc(
        """
        total = 0
        for i, v in enumerate(xs):
            total += i * v
        for a, b in zip(xs, xs):
            total += a - b
        for k in {"a": 1}:
            total += len(k)
        for ch in "abc":
            total += len(ch)
        return total
        """,
        sig="(xs: list[Int]) -> Int",
    )
    ok(src)


def test_for_over_non_iterable():
    assert errors(proc("for i in x:\n    pass\nreturn 0")) == ["L909"]


def test_unpack_arity_mismatch():
    assert errors(proc("a, b = (1, 2, 3)\nreturn a")) == ["L917"]


def test_try_except_declared_types():
    ok(
        proc(
            "try:\n    y = int(s)\nexcept ValueError as e:\n    y = len(str(e))\nreturn y",
            sig="(s: Str) -> Int",
        )
    )
    assert "L918" in errors(proc("try:\n    y = 1\nexcept OSError:\n    y = 0\nreturn y"))


def test_break_outside_loop():
    assert "L919" in errors(proc("break\nreturn x"))


# ---- return completeness -----------------------------------------------------------------


def test_missing_return():
    d = first(proc("if x > 0:\n    return 1"), "L907")
    assert "Int" in d.message


def test_all_paths_return():
    ok(proc("if x > 0:\n    return 1\nelif x < 0:\n    return -1\nelse:\n    return 0"))
    ok(proc("while True:\n    if x > 0:\n        return 1"))
    ok(proc("try:\n    return 1 // x\nexcept ZeroDivisionError:\n    return 0"))


def test_none_return_needs_no_return():
    ok(proc("y = x", sig="(x: Int) -> None"))


def test_loop_body_return_is_not_complete():
    assert errors(proc("for i in range(x):\n    return i")) == ["L907"]


# ---- operators and calls --------------------------------------------------------------


def test_datetime_arithmetic():
    src = """
    from datetime import timedelta

    @procedure
    def f(a: DateTime, b: DateTime, d: Date) -> Bool:
        age = a - b
        later = a + timedelta(hours=2)
        tomorrow = d + days(1)
        return age > hours(1) and later > b and tomorrow.year > 0 and age.total_seconds() > 0
    """
    ok(src)


def test_uuid_not_ordered():
    assert errors(proc("return a < b", sig="(a: Uuid, b: Uuid) -> Bool")) == ["L903"]


def test_call_arity_and_types():
    assert errors(proc("return len(x)")) == ["L904"]
    assert errors(proc("return abs(x, x)")) == ["L904"]
    assert errors(proc("return nope(x)")) == ["L900"]


def test_unknown_method():
    d = first(proc("return s.uppercase()", sig="(s: Str) -> Str"), "L905")
    assert "upper" in d.hint


def test_regex_typed_and_validated():
    src = """
    import re

    @procedure
    def f(s: Str) -> Str:
        m = re.search(r"(\\d+)", s)
        if m is None:
            return re.sub(r"\\s+", " ", s)
        g = m.group(1)
        return g if g is not None else ""
    """
    ok(src)
    bad = "import re\n" + proc("return re.sub('(', '', s)", sig="(s: Str) -> Str")
    assert errors(bad) == ["L916"]
    dyn = "import re\n" + proc("return re.sub(s, '', s)", sig="(s: Str) -> Str")
    assert errors(dyn) == ["L916"]


def test_json_typed():
    src = """
    import json

    @procedure
    def f(s: Str) -> Str:
        j = json.loads(s)
        return json.dumps({"k": [1, 2], "j": j["x"]})
    """
    ok(src)


def test_math_typed():
    src = "import math\n" + proc(
        "return math.floor(math.sqrt(x) + math.pi) + math.ceil(1.5)", sig="(x: Float) -> Int"
    )
    ok(src)


def test_round_overloads():
    ok(proc("return round(x)", sig="(x: Float) -> Int"))
    ok(proc("return round(x, 2)", sig="(x: Float) -> Float"))


# ---- error collection ---------------------------------------------------------------------


def test_multi_error_program_yields_all_errors_no_cascades():
    src = """
    @procedure
    def a(x: Int) -> Int:
        y = x + "s"
        z = y + 1
        return q

    @procedure
    def b(s: Str | None) -> Str:
        n = s.upper()
        return 1

    @procedure
    def c(xs: list[Int]) -> Int:
        xs.append(1)
        if xs:
            return 1
    """
    result = check(src)
    got = [(d.code, d.span.line) for d in result.diagnostics]
    assert got == [
        ("L903", 4),
        ("L900", 6),
        ("L906", 10),
        ("L908", 11),
        ("L907", 14),
        ("L145", 15),
    ], got
    assert result.program is None


def test_types_side_table_covers_expressions():
    src = proc("y = x + 1\nreturn y * 2")
    cp = ok(src).program.t2.procedures["f"]
    for node in ast.walk(cp.node):
        if isinstance(node, ast.expr) and not _in_annotation(cp.node, node):
            assert id(node) in cp.types, ast.dump(node)


def _in_annotation(fn: ast.FunctionDef, node: ast.AST) -> bool:
    anns = [a.annotation for a in fn.args.args] + [fn.returns, *fn.decorator_list]
    return any(node is n for a in anns if a is not None for n in ast.walk(a))


def test_bad_annotation_does_not_cascade():
    src = "from collections import Counter\n" + proc("c: Counter = Counter()\nreturn 0")
    assert errors(src) == ["L902"]
    assert errors(proc("xs: Widget = []\nreturn len(xs)")) == ["L902"]


@pytest.mark.parametrize("stmt", ["ys[0:1] += [x]", "ys[:] *= 2", "ys[::2] -= ys", "s[1:] += 'a'"])
def test_augmented_slice_assignment_is_l913(stmt):
    """v1.1: `xs[a:b] op= v` is slice assignment, rejected on every target like `xs[a:b] = v`."""
    src = proc(f"ys = [x]\ns = 'ab'\n{stmt}\nreturn len(ys)")
    assert errors(src) == ["L913"]
    assert "slice assignment" in first(src, "L913").message


# ---- re.findall / re.split follow the pattern's groups (R3/R9, 2026-09-29) ---------------


def _re_proc(body: str, sig: str = "(s: Str) -> Int") -> str:
    return "import re\n\n" + proc(body, sig=sig)


@pytest.mark.parametrize(
    ("call", "local"),
    [
        ('re.findall(r"\\d+", s)', "list[Str]"),
        ('re.findall(r"(\\d+)x", s)', "list[Str]"),
        ('re.findall(r"(?:a)(?:b)", s)', "list[Str]"),
        ('re.findall(r"(a)(b)", s)', "tuple[Str, Str]"),
        ('re.findall(r"(a)(?P<n>b)(c)?", s)', "tuple[Str, Str, Str]"),
        ('re.split(r",", s)', "list[Str]"),
        ('re.split(r"(?:,)", s)', "list[Str]"),
        ('re.split(r"(,)|;", s)', "list[Str | None]"),
    ],
)
def test_findall_and_split_types_follow_the_groups(call, local):
    types = _local_types(_re_proc(f"xs = {call}\nreturn len(xs)"))
    want = local if local.startswith("list") else f"list[{local}]"
    assert types["xs"].render() == want


def test_findall_of_two_groups_is_not_a_list_of_str():
    """The hole: this used to check, then raise an uncatchable AttributeError."""
    src = _re_proc('xs = re.findall(r"(a)(b)", s)\nreturn len(xs[0].upper())')
    assert errors(src)
    ok(_re_proc('xs = re.findall(r"(a)(b)", s)\nx, y = xs[0]\nreturn len(x.upper() + y)'))


def test_split_with_groups_may_hold_none():
    src = _re_proc('xs = re.split(r"(,)|;", s)\nreturn len(xs[0].upper())')
    assert errors(src)
    ok(_re_proc('xs = re.split(r"(,)|;", s)\nreturn len((xs[0] or "").upper())'))
