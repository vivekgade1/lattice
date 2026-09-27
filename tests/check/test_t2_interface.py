"""The T2b interface: CheckedProcedure (signature, effects, typed side table, called
procedures, recursion) exposed from the checker result, and the l12 login-lockout
state machine written as a type-checked @procedure."""

import ast
from pathlib import Path

from t2_support import errors, ok

from lattice.check.t2 import CheckedProcedure, T2Program
from lattice.check.t2 import types as T


def test_checked_procedure_fields():
    src = """
    @procedure
    def helper(x: Int) -> Int:
        return x + 1

    @procedure(effects=["mail"])
    def main(xs: list[Int], to: Str) -> Int:
        native.send_mail(to, "n")
        return sum(helper(v) for v in xs)
    """
    t2 = ok(src).program.t2
    assert isinstance(t2, T2Program)
    cp = t2.procedures["main"]
    assert isinstance(cp, CheckedProcedure)
    assert cp.sig.params == (("xs", T.ListT(T.INT)), ("to", T.STR))
    assert cp.sig.returns == T.INT
    assert cp.effects == frozenset({"mail"})
    assert cp.calls == frozenset({"helper"})
    assert cp.natives == frozenset({"send_mail"})
    assert not cp.is_recursive
    assert isinstance(cp.node, ast.FunctionDef) and cp.node.name == "main"
    call = next(
        n
        for n in ast.walk(cp.node)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "sum"
    )
    assert cp.types[id(call)] == T.INT
    assert cp.targets[id(call)] == "builtin:sum"
    assert t2.procedures["helper"].calls == frozenset()


def test_recursion_allowed_and_flagged():
    src = """
    @procedure
    def fact(n: Int) -> Int:
        if n <= 1:
            return 1
        return n * fact(n - 1)

    @procedure
    def is_even(n: Int) -> Bool:
        if n == 0:
            return True
        return is_odd(n - 1)

    @procedure
    def is_odd(n: Int) -> Bool:
        if n == 0:
            return False
        return is_even(n - 1)

    @procedure
    def uses(n: Int) -> Int:
        return fact(n) if is_even(n) else 0
    """
    procs = ok(src).program.t2.procedures
    assert procs["fact"].is_recursive
    assert procs["is_even"].is_recursive and procs["is_odd"].is_recursive
    assert not procs["uses"].is_recursive
    assert procs["uses"].calls == frozenset({"fact", "is_even"})


def test_procedure_call_typed():
    src = """
    @procedure
    def inc(x: Int) -> Int:
        return x + 1

    @procedure
    def g(s: Str) -> Int:
        return inc(s)
    """
    assert errors(src) == ["L904"]


def test_duplicate_procedure_name():
    src = """
    @procedure
    def f(x: Int) -> Int:
        return x

    @procedure
    def f(x: Int) -> Int:
        return x
    """
    assert "L914" in errors(src)


def test_procedure_name_clashes_with_record_and_endpoint():
    src = """
    @record
    class f:
        x: Int

    @procedure
    def f(x: Int) -> Int:
        return x
    """
    assert "L914" in errors(src)


def test_records_exposed():
    src = """
    @record
    class P:
        x: Int
        tag: Str | None = None
    """
    rec = ok(src).program.t2.records["P"]
    assert rec.fields == (("x", T.INT), ("tag", T.Opt(T.STR)))
    assert rec.defaults == {"tag": None}


def test_imports_exposed():
    src = """
    import math
    from datetime import timedelta

    @procedure
    def f(x: Float) -> Duration:
        return timedelta(minutes=math.floor(x))
    """
    t2 = ok(src).program.t2
    assert t2.imports == {"math": "math", "timedelta": "datetime.timedelta"}


# ---- l12: the login-lockout state machine as a procedure --------------------------------

L12 = '''
from lattice.ops import procedure, record

LOCK_AFTER = 3
LOCK_MINUTES = 30


@record
class LoginEvent:
    id: Int
    user_id: Int
    minute: Int
    kind: Str


@record
class LoginState:
    failures: Int
    locked_until: Int | None
    sessions: Int


@record
class LoginResult:
    id: Int
    user_id: Int
    minute: Int
    kind: Str
    status: Str
    sessions: Int


@procedure
def replay_logins(events: list[LoginEvent]) -> list[LoginResult]:
    """Account state after each login event: lock after LOCK_AFTER consecutive
    failures for LOCK_MINUTES; a success clears the failures; an admin reset clears
    the lock; attempts during a lock are refused and do not count."""
    state: dict[Int, LoginState] = {}
    out: list[LoginResult] = []
    for ev in sorted(events, key=lambda e: (e.user_id, e.minute, e.id)):
        s = state.get(ev.user_id)
        if s is None:
            s = LoginState(failures=0, locked_until=None, sessions=0)
        failures = s.failures
        locked_until = s.locked_until
        sessions = s.sessions
        if locked_until is not None and ev.minute >= locked_until:
            locked_until = None
            failures = 0
        if ev.kind == "reset":
            locked_until = None
            failures = 0
            status = "reset"
        elif locked_until is not None:
            status = "refused"
        elif ev.kind == "fail":
            failures += 1
            if failures >= LOCK_AFTER:
                locked_until = ev.minute + LOCK_MINUTES
                status = "locked"
            else:
                status = "failed"
        else:
            failures = 0
            sessions += 1
            status = "ok"
        state[ev.user_id] = LoginState(
            failures=failures, locked_until=locked_until, sessions=sessions
        )
        out.append(
            LoginResult(
                id=ev.id,
                user_id=ev.user_id,
                minute=ev.minute,
                kind=ev.kind,
                status=status,
                sessions=sessions,
            )
        )
    return out
'''


def test_l12_login_state_machine_type_checks():
    t2 = ok(L12).program.t2
    cp = t2.procedures["replay_logins"]
    assert cp.sig.returns == T.ListT(T.RecordT("LoginResult"))
    assert cp.effects == frozenset()
    assert cp.locals["status"] == T.STR
    assert cp.locals["locked_until"] == T.Opt(T.INT)
    assert set(t2.records) == {"LoginEvent", "LoginState", "LoginResult"}


def test_l12_fixture_is_the_original():
    # the procedure above re-expresses this fixture (execution is verified in T2b/T2c)
    fixture = Path(__file__).parents[1] / "fixtures" / "loops" / "l12_login_state_machine.py"
    assert "def replay_logins" in fixture.read_text()
