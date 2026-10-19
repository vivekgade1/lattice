"""gocheck's rules on Gin + GORM fixture apps (``fixtures/<rule>``): every ``Bad*`` handler
of a fixture must have a finding of the rule, every ``Ok*`` handler -- the correct idioms
-- none. Skipped when ``go`` is not on PATH."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from lattice.gocheck import (
    GO_CODES,
    GO_GATED,
    GoPolicy,
    all_go_findings,
    build_gocheck,
    run_gocheck,
)
from lattice.pycheck import CODES, Finding

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

FIXTURES = Path(__file__).parent / "fixtures"
EDGES = frozenset({("queued", "running"), ("queued", "cancelled"), ("running", "succeeded"),
                   ("running", "failed"), ("running", "cancelled")})  # fmt: skip


def handlers_in(fixture: str) -> tuple[set[str], set[str]]:
    """(the Bad* handlers, the Ok* handlers) a fixture declares."""
    text = "\n".join(p.read_text() for p in (FIXTURES / fixture).rglob("*.go"))
    names = set(re.findall(r"^func (?:\([^)]*\) )?((?:Bad|Ok)\w+)\(", text, re.M))
    return {n for n in names if n.startswith("Bad")}, {n for n in names if n.startswith("Ok")}


def named(findings: list[Finding]) -> set[str]:
    out: set[str] = set()
    for f in findings:
        out |= set(re.findall(r"\b((?:Bad|Ok)\w+)\b", f.message))
    return out


def judge(fixture: str, policy: GoPolicy, code: str) -> list[Finding]:
    found = all_go_findings(FIXTURES / fixture, policy)
    assert {f.code for f in found} <= {code}, [f.render() for f in found]
    bad, ok = handlers_in(fixture)
    hit = named(found)
    assert bad, "the fixture declares no Bad* handler"
    assert ok, "the fixture declares no Ok* handler"
    assert bad - hit == set(), f"{code} missed: {sorted(bad - hit)}"
    assert hit & ok == set(), f"{code} fired on a correct idiom: {sorted(hit & ok)}"
    for f in found:
        assert f.line > 0 and f.file.endswith(".go") and f.hint, f
        assert f.key.startswith(code + "|"), f.key
    return found


# ---- GO807 partial writes -------------------------------------------------------------------


def test_go807_partial_writes():
    tables = {"Job": "jobs", "JobEvent": "job_events", "User": "users"}
    found = judge("partial", GoPolicy(tables=tables, rules=("GO807",)), "GO807")
    by: dict[str, Finding] = {}
    for f in found:
        m = re.search(r"\((Handler\.\w+)\)", f.message)
        assert m is not None, f.message
        by[m.group(1)] = f
    f = by["Handler.BadCreateThenValidate"]
    assert (f.file, f.line) == ("handlers.go", 44)
    assert "at line 49 (c.JSON(422, ...))" in f.message
    assert "POST /api/bad/create-then-validate" in f.message
    assert "db.Transaction" in f.hint
    # a helper that commits, then returns an error its caller refuses with
    assert "then returns an error" in by["Handler.BadHelperError"].message
    # a refusal in a helper, its status passed as an argument
    assert "respond() (it refuses with 409)" in by["Handler.BadRespondHelper"].message
    # loop-carried: this pass writes, the next refuses
    assert "on a later pass of the same loop" in by["Handler.BadLoop"].message
    # a write on the outer db inside a transaction function is not rolled back
    assert "h.db.Create" in by["Handler.BadDBInsideTransaction"].message


# ---- GO808 state machines -------------------------------------------------------------------


def test_go808_state_machines():
    policy = GoPolicy(tables={"Job": "jobs"}, machines={"jobs": ("status", EDGES)},
                      rules=("GO808",))  # fmt: skip
    found = judge("machine", policy, "GO808")
    retry = next(f for f in found if "BadRetrySucceeded" in f.message)
    assert "'failed' -> 'queued', 'succeeded' -> 'queued'" in retry.message
    assert "the check at line 33 leaves it 'failed' or 'succeeded'" in retry.message
    assert "never (no state moves to it)" in retry.hint  # nothing moves to queued
    where = next(f for f in found if "BadWhereGuard" in f.message)
    assert (
        "the update's `.Where(...)` at line 64 leaves it 'queued' or 'succeeded'" in where.message
    )
    assert "'succeeded' -> 'running'" in where.message and "'queued' -> " not in where.message


def test_go808_needs_machines():
    assert all_go_findings(FIXTURES / "machine", GoPolicy(tables={"Job": "jobs"},
                                                          rules=("GO808",))) == []  # fmt: skip


# ---- GO802 private columns ------------------------------------------------------------------

PRIVATE = GoPolicy(
    tables={"User": "users", "Account": "accounts", "Session": "sessions", "Key": "keys"},
    users_table="User",
    private={
        "User": ("api_token", "password_hash"),
        "Account": ("secret",),
        "Session": ("token",),
        "Key": ("secret",),
    },  # fmt: skip
    admin_routes=(("GET", "/admin/users"),),
    rules=("GO802",),
)


def test_go802_private_columns():
    found = judge("private", PRIVATE, "GO802")
    user = next(f for f in found if "(App.BadUser)" in f.message)
    assert "User.api_token" in user.message and "`APIToken`" in user.message
    assert 'tag the field `json:"-"`' in user.hint
    value = next(f for f in found if "BadSecretValue" in f.message)
    assert "carries `acc.Secret`" in value.message and "leave Account.secret out" in value.hint


def test_go802_admin_routes_are_exempt_only_when_the_spec_says_so():
    found = all_go_findings(
        FIXTURES / "private", GoPolicy(**{**PRIVATE.__dict__, "admin_routes": ()})
    )
    assert "OkAdminSpec" in named(found)
    assert "OkAdminGroup" not in named(found)  # its group's middleware is an admin check


# ---- GO801 ownership ------------------------------------------------------------------------

OWNER = GoPolicy(
    tables={"User": "users", "Job": "jobs", "JobEvent": "job_events", "Config": "configs"},
    users_table="User",
    owners={"Job": "user_id"},
    owner_via={"JobEvent": ("job_id", "Job")},
    admin_routes=(("GET", "/api/admin/jobs"),),
    open_routes=(("GET", "/api/public/jobs"),),
    rules=("GO801",),
)


def test_go801_ownership():
    found = judge("owner", OWNER, "GO801")
    events = next(f for f in found if "BadListEvents" in f.message)
    assert "JobEvent rows" in events.message and "through JobEvent.job_id" in events.message
    helper = next(f for f in found if "BadHelperLookup" in f.message)
    assert "(in H.findJob)" in helper.message
    assert '.Where("user_id = ?"' in next(f for f in found if "BadGetJob" in f.message).hint


def test_go801_without_the_spec_exemptions():
    found = all_go_findings(FIXTURES / "owner", GoPolicy(**{**OWNER.__dict__, "admin_routes": (),
                                                             "open_routes": ()}))  # fmt: skip
    assert {"OkAdminList", "OkOpenRoute"} <= named(found)


# ---- GO803 bounds ---------------------------------------------------------------------------


def test_go803_bounds():
    found = judge("bounds", GoPolicy(tables={"Job": "jobs"}, rules=("GO803",)), "GO803")
    keys = {f.key for f in found}
    assert "GO803|POST /bad/jobs|CreateJob.Name" in keys
    assert "GO803|POST /bad/jobs|CreateJob.Priority" in keys
    assert not any(k.endswith("CreateJob.Kind") for k in keys)  # oneof= bounds it
    assert not any(k.endswith("Job.Kind") for k in keys if "whole" in k)  # set after binding
    limit = next(f for f in found if "BadLimit)" in f.message)
    assert "`.Limit(...)`" in limit.message and 'binding:"min=1,max=100"' in limit.hint


def test_go803_dominating_set_and_regex_checks():
    """A refusal that returns whenever the value is not one of a fixed set of constants
    (`!=` chains, `!(== || ==)`, a value switch with a refusing default, `slices.Contains`
    of a constant list, a helper doing one of these) or does not match an anchored regex
    of finite length bounds the value; a refusal that falls through, a missing default, a
    non-constant, a check after the write, and unanchored, open-ended (`{1,}`, `*`, `+`)
    or multi-line patterns do not."""
    policy = GoPolicy(tables={"Job": "jobs", "Membership": "memberships"}, rules=("GO803",))
    found = judge("boundsets", policy, "GO803")
    assert all("req.UserID" not in f.message for f in found)  # binding:"max=..." bounds it


def test_go807_nothing_written_flag():
    """A local bool a transaction function sets to true only on paths that wrote nothing
    (then returns) makes the refusal under `if flag` follow no commit; a flag set after a
    write, set without returning, set in a loop, or also set outside the transaction does
    not."""
    policy = GoPolicy(tables={"User": "users", "Membership": "memberships"}, rules=("GO807",))
    found = judge("txflag", policy, "GO807")
    assert all("the transaction commits" in f.message for f in found)


# ---- the multi-package app, the parse gate, the baseline -----------------------------------

APP = GoPolicy(
    tables={"User": "users", "Task": "tasks", "Comment": "comments"},
    users_table="User",
    owners={"Task": "owner_id"},
    owner_via={"Comment": ("task_id", "Task")},
    private={"User": ("password_hash",)},
    machines={
        "Task": (
            "state",
            frozenset(
                {("todo", "doing"), ("doing", "done"), ("todo", "dropped"), ("doing", "dropped")}
            ),
        )
    },  # fmt: skip
)


def test_a_multi_package_app():
    found = all_go_findings(FIXTURES / "app", APP)
    got = {(f.code, f.file, f.line) for f in found}
    assert got == {
        ("GO808", "internal/handlers/handlers.go", 88),
        ("GO807", "internal/handlers/handlers.go", 106),
        ("GO802", "internal/handlers/handlers.go", 121),
    }, [f.render() for f in found]
    assert all(f.code in GO_CODES for f in found)


def test_go800_a_file_that_does_not_parse(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module x\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text("package main\n\nfunc main() {\n\tif {\n}\n")
    found = all_go_findings(tmp_path, APP)
    assert [(f.code, f.file) for f in found] == [("GO800", "main.go")]
    assert found[0].line >= 4 and "does not parse" in found[0].message


def test_the_baseline_is_accepted(tmp_path: Path):
    found = run_gocheck(FIXTURES / "app", APP)
    assert len(found) == 3
    accepted = GoPolicy(**{**APP.__dict__, "baseline": frozenset(f.key for f in found[:2])})
    assert run_gocheck(FIXTURES / "app", accepted) == found[2:]


def test_rules_select_codes():
    only = GoPolicy(**{**APP.__dict__, "rules": ("GO802",)})
    assert {f.code for f in all_go_findings(FIXTURES / "app", only)} == {"GO802"}


def test_gated_rules_are_explicit_and_never_reported():
    assert set(GO_GATED) == {"GO804", "GO806"}  # round 3 (K8): GO805 is carried over
    assert {c.replace("PY", "GO") for c in CODES} == set(GO_CODES) | set(GO_GATED)
    fixtures = ("partial", "machine", "private", "owner", "bounds", "boundsets", "txflag", "app")
    for fixture in fixtures:
        assert not {f.code for f in all_go_findings(FIXTURES / fixture, APP)} & set(GO_GATED)


def test_the_binary_is_cached():
    assert build_gocheck() == build_gocheck()
    assert build_gocheck().is_file()


def test_findings_render_like_pycheck():
    f = run_gocheck(FIXTURES / "app", APP)[0]
    assert f.render().startswith(f"{f.file}:{f.line}: {f.code} ")
    assert "\n  fix: " in f.render()


# ---- the blind evaluation's patterns (fixtures/runs) ---------------------------------------

RUN_EDGES = frozenset({("queued", "running"), ("queued", "cancelled"), ("running", "succeeded"),
                       ("running", "failed"), ("running", "cancelled")})  # fmt: skip
RUNS = GoPolicy(tables={"JobRun": "job_runs"}, machines={"JobRun": ("status", RUN_EDGES)})


def test_runs_patterns():
    """GO807 is silent on a refusal driven by the transaction's own error (inline, in an
    `if` init, in a tagless switch, or handed to a helper that refuses only when it is not
    nil) and on a helper's "nothing written" bool (`return res.RowsAffected == 1, ...`);
    GO808 fires on a compound `Where` whose states are constants (this package's, another
    package's, a package var slice, `IN (?)`) and on an update by id of a guarded row."""
    found = all_go_findings(FIXTURES / "runs", RUNS)
    bad, ok = handlers_in("runs")
    hit = named(found)
    assert bad - hit == set(), f"missed: {sorted(bad - hit)}"
    assert hit & ok == set(), f"fired on a correct idiom: {sorted(hit & ok)}"
    codes = {n: f.code for f in found for n in named([f])}
    assert codes["BadTxThenRefuse"] == codes["BadAnswerAfterCommit"] == "GO807"
    for n in ("BadCancelAny", "BadFinishQueued", "BadCancelGuarded", "BadCancelGuardedByRowID",
              "BadCancelPkgVar", "BadCancelOtherPkgConsts"):  # fmt: skip
        assert codes[n] == "GO808", n
    guarded = next(f for f in found if "BadCancelGuarded sets" in f.message)
    assert (
        "'failed' -> 'cancelled'" in guarded.message
        and "'succeeded' -> 'cancelled'" in guarded.message
    )
