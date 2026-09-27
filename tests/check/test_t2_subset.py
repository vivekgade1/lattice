"""T2 surface: @procedure / @record at module level, the statement and expression
subset, and the L1xx rejections (I/O, imports, eval/exec, reflection, globals,
threads, async, plain classes, parameter mutation)."""

import pytest
from t2_support import PROFILE, TABULAR, check, codes, errors, first, ok, proc

# ---- accepted surface ---------------------------------------------------------------


def test_spec_example_title_locked_checks():
    src = """
    from lattice.ops import procedure, Row, Caller, hours

    @procedure
    def title_locked(item: Row[Items], caller: Caller, now: DateTime) -> Bool:
        if caller.is_admin:
            return False
        age = now - item.created_at
        return age > hours(24)
    """
    result = ok(src)
    assert set(result.program.t2.procedures) == {"title_locked"}


def test_spec_example_digest_checks():
    src = """
    @procedure(effects=["mail"])
    def digest(rows: list[Row[Items]], caller: Caller) -> list[str]:
        lines: list[str] = []
        for r in rows:
            if r.title.strip():
                lines.append(f"{r.title} ({r.created_at:%Y-%m-%d})")
        return lines
    """
    ok(src)


def test_procedure_imports_are_optional_and_accepted():
    src = """
    from lattice.ops import procedure, record, Row, Caller, Duration, Json, days

    @procedure
    def f(x: Int) -> Int:
        return x + 1
    """
    ok(src)


def test_program_without_procedures_has_no_t2_part():
    src = """
    @endpoint("GET", "/items", auth=Public)
    def list_items(limit: Int(ge=1, le=100) = 20) -> Page:
        items = fetch(LoginEvents)
        result = page(items, limit=limit)
        return result
    """
    result = ok(src)
    assert result.program.t2 is None


def test_procedures_next_to_endpoints():
    src = """
    @procedure
    def double(x: Int) -> Int:
        return x * 2

    @endpoint("GET", "/items", auth=Public)
    def list_items(limit: Int(ge=1, le=100) = 20) -> Page:
        items = fetch(LoginEvents)
        result = page(items, limit=limit)
        return result
    """
    result = ok(src)
    assert "list_items" in result.program.endpoints
    assert "double" in result.program.t2.procedures
    # the T0 passes see only T0 statements
    assert all(getattr(s, "name", None) != "double" for s in result.program.module.body)


def test_all_allowed_statements():
    src = proc(
        """
        total = 0
        n: Int = 0
        total += x
        if x > 1:
            total -= 1
        elif x < 0:
            pass
        else:
            total *= 2
        for i in range(3):
            if i == 1:
                continue
            total += i
        while n < 10:
            n += 1
            if n == 5:
                break
        try:
            total = total // n
        except ZeroDivisionError:
            total = 0
        return total
        """
    )
    ok(src)


def test_stdlib_imports_allowed():
    src = """
    import math
    import re
    import json
    from datetime import timedelta, date
    from collections import Counter, defaultdict

    @procedure
    def f(x: Float, s: Str) -> Int:
        m = re.fullmatch(r"[a-z]+", s)
        d = timedelta(days=1)
        c = Counter(s.split())
        j = json.dumps([1, 2])
        return math.floor(x)
    """
    ok(src)


def test_module_constants_visible():
    src = """
    LIMIT = 3
    @procedure
    def f(x: Int) -> Bool:
        return x >= LIMIT
    """
    result = ok(src)
    # the constant is used (by the procedure): no L204 warning
    assert "L204" not in codes(src)
    assert result.program.t2 is not None


# ---- rejected: tabular programs, imports, I/O, reflection -------------------------


def test_procedure_in_tabular_program_rejected():
    src = proc("return x")
    assert "L148" in errors(src, TABULAR)


def test_non_allowlisted_import_rejected():
    src = "import os\n" + proc("return x")
    d = first(src, "L142")
    assert "math" in d.hint


def test_threading_import_rejected():
    src = "from threading import Thread\n" + proc("return x")
    assert "L142" in errors(src)


def test_non_allowlisted_name_from_allowlisted_module():
    src = "from math import frexp_nope\n" + proc("return x")
    assert "L142" in errors(src)


def test_import_inside_procedure_rejected():
    src = proc("import math\nreturn x")
    assert "L146" in errors(src)


@pytest.mark.parametrize("call", ["open('f')", "print(x)", "input()", "eval('1')", "exec('1')"])
def test_io_and_eval_rejected(call):
    src = proc(f"{call}\nreturn x")
    assert errors(src) == ["L140"]


@pytest.mark.parametrize(
    "expr", ["getattr(x, 'real')", "vars(x)", "globals()", "type(x)", "x.__class__"]
)
def test_reflection_rejected(expr):
    src = proc(f"y = {expr}\nreturn x")
    assert errors(src) == ["L141"]


def test_global_statement_rejected():
    src = "N = 1\n" + proc("global N\nreturn x")
    assert "L130" in errors(src)


def test_async_procedure_rejected():
    src = "@procedure\nasync def f(x: Int) -> Int:\n    return x\n"
    assert "L131" in errors(src)


def test_plain_class_rejected_with_record_hint():
    src = "class Point:\n    x: Int\n"
    d = first(src, "L128")
    assert "@record" in d.hint


def test_record_methods_rejected():
    src = """
    @record
    class Point:
        x: Int
        def norm(self) -> Int:
            return self.x
    """
    assert "L144" in errors(src)


def test_record_base_class_rejected():
    src = """
    @record
    class Point(Base):
        x: Int
    """
    assert "L144" in errors(src)


@pytest.mark.parametrize(
    "stmt",
    ["with x:\n    pass", "raise ValueError('x')", "assert x", "del x", "def g():\n    pass"],
)
def test_disallowed_statements(stmt):
    src = proc(f"{stmt}\nreturn x")
    assert "L146" in errors(src)


def test_lambda_outside_key_rejected():
    src = proc("g = lambda y: y\nreturn x")
    assert "L147" in errors(src)


def test_lambda_as_sort_key_allowed():
    src = proc("ys = sorted([3, 1, 2], key=lambda v: -v)\nreturn ys[0]")
    ok(src)


def test_walrus_rejected():
    src = proc("if (y := x) > 1:\n    return y\nreturn x")
    assert "L147" in errors(src)


def test_bare_except_rejected():
    src = proc("try:\n    y = 1 // x\nexcept:\n    y = 0\nreturn y")
    assert "L149" in errors(src)


def test_except_exception_rejected():
    src = proc("try:\n    y = 1 // x\nexcept Exception:\n    y = 0\nreturn y")
    assert "L149" in errors(src)


def test_procedure_signature_shape():
    assert "L148" in errors(proc("return 1", sig="(x: Int, *rest: Int) -> Int"))
    assert "L148" in errors(proc("return 1", sig="(x: Int = 1) -> Int"))
    assert "L148" in errors(proc("return 1", sig="(x) -> Int"))
    assert "L148" in errors(proc("return 1", sig="(x: Int)"))


def test_procedure_decorator_shape():
    assert "L148" in errors(proc("return x", deco="@procedure(fuel=3)"))
    assert "L148" in errors(proc("return x", deco="@procedure\n@step"))


# ---- rejected: mutation of values passed in -----------------------------------------


def test_param_mutation_rejected():
    src = proc("xs.append(1)\nreturn xs", sig="(xs: list[Int]) -> list[Int]")
    d = first(src, "L145")
    assert "copy" in d.hint or "new" in d.hint


def test_param_subscript_assignment_rejected():
    src = proc("d['a'] = 1\nreturn 0", sig="(d: dict[str, int]) -> Int")
    assert errors(src) == ["L145"]


def test_param_alias_mutation_rejected():
    src = proc("ys = xs\nys.append(1)\nreturn ys", sig="(xs: list[Int]) -> list[Int]")
    assert errors(src) == ["L145"]


def test_param_inner_mutation_rejected():
    src = proc("for g in gs:\n    g.append(1)\nreturn 0", sig="(gs: list[list[Int]]) -> Int")
    assert errors(src) == ["L145"]


def test_copy_then_mutate_allowed():
    src = proc("ys = list(xs)\nys.append(1)\nreturn ys", sig="(xs: list[Int]) -> list[Int]")
    ok(src)


def test_param_rebinding_allowed():
    src = proc("s = s.strip()\nreturn s", sig="(s: Str) -> Str")
    ok(src)


def test_record_field_assignment_rejected():
    src = """
    @record
    class P:
        x: Int

    @procedure
    def f(p: P) -> Int:
        p.x = 2
        return p.x
    """
    assert "L145" in errors(src)


def test_one_error_per_banned_construct():
    # the checker never descends into a rejected construct
    src = proc("eval(open('x').read())\nreturn x")
    assert errors(src) == ["L140"]


def test_profile_and_names_unchanged_for_t0():
    # an API program still rejects a plain import exactly as before
    assert "L103" in errors("import os\n")
    assert check("import os\n", PROFILE).program is None
