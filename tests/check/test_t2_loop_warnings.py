"""T2 body loop-shape warnings (spec 2026-09-27 lattice-t2-design §5, node T2c): a
`for`/`while` loop inside a checked `@procedure` is allowed -- T2 does not reject
loops -- but when its shape matches a T1 construct (window/traverse/for_each), the
checker warns (L924) instead of staying silent; the program still checks clean. A
state machine (or anything else no T1 construct fits) has no such suggestion, since
that is the reason the loop stays a procedure at all (tests/fixtures/loops/README.md,
l12)."""

from __future__ import annotations

from t2_support import PROFILE, check

from lattice.diagnostics import Severity


def warnings(src: str) -> list:
    result = check(src)
    assert result.ok, [d.render() for d in result.diagnostics]
    return [d for d in result.diagnostics if d.code == "L924"]


# ---- true positives: T1-shaped loops written as procedures --------------------------


def test_running_total_warns_window() -> None:
    src = """
    @procedure
    def total_of(txns: list[Int]) -> Int:
        balance = 0
        for txn in txns:
            balance += txn
        return balance
    """
    ws = warnings(src)
    assert len(ws) == 1
    assert ws[0].severity is Severity.WARNING
    assert "window" in (ws[0].hint or "") and "T1" in (ws[0].hint or "")


def test_previous_value_delta_warns_window() -> None:
    src = """
    @procedure
    def deltas(prices: list[Float]) -> list[Float]:
        out: list[Float] = []
        prev = 0.0
        for price in prices:
            out.append(price - prev)
            prev = price
        return out
    """
    assert "window" in (warnings(src)[0].hint or "")


def test_moving_average_warns_window() -> None:
    src = """
    @procedure
    def averages(readings: list[Float]) -> list[Float]:
        recent: list[Float] = []
        out: list[Float] = []
        for reading in readings:
            recent.append(reading)
            avg = sum(recent) / len(recent)
            out.append(avg)
        return out
    """
    assert "window" in (warnings(src)[0].hint or "")


def test_parent_walk_warns_traverse() -> None:
    src = """
    @record
    class Node:
        id: Int
        parent_id: Int | None


    @procedure
    def depth_of(start: Node, by_id: dict[Int, Node]) -> Int:
        node = start
        n = 0
        while node.parent_id is not None:
            nxt = by_id.get(node.parent_id)
            if nxt is None:
                break
            node = nxt
            n += 1
        return n
    """
    assert "traverse" in (warnings(src)[0].hint or "")


def test_per_row_effect_warns_for_each() -> None:
    src = """
    @procedure(effects=["mail"])
    def notify_all(emails: list[Str]) -> None:
        for email in emails:
            native.send_mail(email, "hi")
    """
    assert "for_each" in (warnings(src)[0].hint or "")


# ---- false positives: no T0/T1 construct expresses these ----------------------------


def test_login_lockout_state_machine_has_no_warning() -> None:
    """The loop corpus original (l12), a @procedure over several accumulators updated
    by branches that read them back: no T0/T1 construct expresses it
    (tests/fixtures/loops/README.md), so the checker must stay silent."""
    from tests.fixtures.loops.l12_login_state_machine import LATTICE
    from tests.fixtures.loops.l12_login_state_machine import PROFILE as L12_PROFILE

    result = check(LATTICE, L12_PROFILE)
    assert result.ok
    assert [d for d in result.diagnostics if d.code == "L924"] == []


def test_a_plain_loop_with_no_t1_shape_has_no_warning() -> None:
    src = """
    @procedure
    def combine(xs: list[Int]) -> list[Int]:
        out: list[Int] = []
        for x in xs:
            y = x * 2
            out.append(y)
        return out
    """
    assert warnings(src) == []


def test_a_helper_call_loop_has_no_warning() -> None:
    """Calling another procedure per element is neither a running total, a walk nor a
    bare effect call -- no T1 construct fits, so no warning."""
    src = """
    @procedure
    def doubled(x: Int) -> Int:
        return x * 2


    @procedure
    def combine(xs: list[Int]) -> list[Int]:
        out: list[Int] = []
        for x in xs:
            out.append(doubled(x))
        return out
    """
    assert warnings(src) == []


def test_the_warning_never_turns_into_an_error() -> None:
    """L924 stays a warning at every severity check: the program is still `ok`."""
    src = """
    @procedure
    def total_of(txns: list[Int]) -> Int:
        balance = 0
        for txn in txns:
            balance += txn
        return balance
    """
    result = check(src, PROFILE)
    assert result.ok
    assert result.program is not None
    assert result.program.t2 is not None
    assert "total_of" in result.program.t2.procedures
