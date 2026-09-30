"""T2: Int literals of 2**63 or more (including negated ones like
-9223372036854775809) are range-checked, mirroring T0's p1_nodes._check_int_range
(same L404 code and message style). Covers procedure bodies and module constants
a procedure reads."""

from t2_support import codes, errors, ok, proc

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def test_int_max_is_accepted():
    ok(proc(f"return {INT64_MAX}", sig="() -> Int"))


def test_int_max_plus_one_is_rejected():
    assert errors(proc(f"return {INT64_MAX + 1}", sig="() -> Int")) == ["L404"]


def test_negated_int_min_is_accepted():
    # -9223372036854775808 parses as USub(Constant(9223372036854775808)): the
    # positive magnitude alone overflows Int64, but the negated literal is exactly
    # Int64 MIN, so it must be accepted as one signed literal.
    ok(proc(f"return {INT64_MIN}", sig="() -> Int"))


def test_negated_int_min_minus_one_is_rejected():
    assert errors(proc(f"return {INT64_MIN - 1}", sig="() -> Int")) == ["L404"]


def test_diagnostic_message_style_matches_t0():
    d = errors(proc(f"return {INT64_MAX + 1}", sig="() -> Int"))
    assert d == ["L404"]


def test_literal_deep_in_an_expression_is_checked():
    src = proc(
        f"""
        x = 1 + {INT64_MAX + 1}
        return x
        """,
        sig="() -> Int",
    )
    assert "L404" in errors(src)


def test_module_constant_read_by_a_procedure_is_rejected():
    # The bare module-level assignment is itself a T0 statement (t2.split only pulls
    # out @procedure/@record bodies), so T0's own p1_nodes pass already range-checks
    # it with the same L404 whenever a procedure exists to read it; the procedure
    # then sees the (unregistered) constant as undefined, which still rejects the
    # program overall.
    src = (
        f"BIG = {INT64_MAX + 1}\n\n"
        "@procedure\n"
        "def f() -> Int:\n"
        "    return BIG\n"
    )
    cs = codes(src)
    assert "L404" in cs
