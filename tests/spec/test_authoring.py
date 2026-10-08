"""Proof that `access_matrix` (lattice.spec.authoring) can rebuild a real spec's
role x action access scenarios -- the job-scheduler's ticket 1
(/Users/vivekgade/Documents/job-scheduler/spec/spec.py) -- more cheaply than writing
them by hand, without losing any of the (caller, action, status) facts they state.

The real spec is never edited; this test only reads it (for the size comparison and
for its profile/users/answer bodies) and imports the real app read-only, through
sys.path, the way job-scheduler's own check.sh does (`spec.seed` for the seeded
database and login)."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.spec import Spec, access_matrix, check, validate

# The real app this proof runs against (its own repo); the test skips without it.
JOB_SCHEDULER = Path(
    os.environ.get("LATTICE_JOB_SCHEDULER", "/Users/vivekgade/Documents/job-scheduler")
)
if not (JOB_SCHEDULER / "spec" / "spec.py").exists():
    pytest.skip(f"the job-scheduler repo is not at {JOB_SCHEDULER} (set LATTICE_JOB_SCHEDULER)",
                allow_module_level=True)

# ---- import the real job-scheduler app and spec, read-only, via sys.path (as its own
# check.sh does: `--app job_scheduler:make_app --db spec.seed:build_seed_db`) ----------------
#
# job_scheduler goes on sys.path (its `src/`) and imports under its own name, exactly
# as check.sh's `--app job_scheduler:make_app` does. Its `spec/` package is loaded
# directly from its two files' paths instead of via `sys.path` + a dotted import, for
# two reasons: (1) this test lives under `tests/spec/`, and pytest's default import
# mode has already bound the bare name "spec" to *that* package
# (tests/spec/__init__.py) before this module's body runs; (2) job-scheduler's own
# root has a real `tests/__init__.py`, so adding that root to `sys.path` would shadow
# every sibling test module's own `from tests.spec import ...` (a regular package, so
# it does not merge with lattice's own namespace `tests` package the way a namespace
# package would). `spec/seed.py` and `spec/spec.py` have no internal `spec.*` imports
# of their own (only absolute `job_scheduler`/`lattice` ones), so loading them by path
# under distinct module names reaches the same two files check.sh names as
# `spec.seed:build_seed_db` and `spec.spec`, without either collision.


def _on_path(p: Path) -> None:
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _load_file(module_name: str, path: Path) -> Any:
    spec_ = importlib.util.spec_from_file_location(module_name, path)
    assert spec_ is not None and spec_.loader is not None
    module = importlib.util.module_from_spec(spec_)
    sys.modules[module_name] = module
    spec_.loader.exec_module(module)
    return module


_on_path(JOB_SCHEDULER / "src")

job_scheduler = importlib.import_module("job_scheduler")
seed = _load_file("job_scheduler_spec_seed", JOB_SCHEDULER / "spec" / "seed.py")
real_spec = _load_file("job_scheduler_spec_spec", JOB_SCHEDULER / "spec" / "spec.py")

DENIED = real_spec.DENIED
NOT_FOUND = real_spec.NOT_FOUND
ALPHA_MEMBERS = real_spec.ALPHA_MEMBERS

# ---- the matrix form of ticket 1's access scenarios -----------------------------------------
#
# app_admins_manage_their_members, executors_and_outsiders_cannot_manage_members,
# the_super_admin_manages_any_application, and the access parts of
# each_user_sees_only_their_applications (spec/spec.py), rebuilt from one compact table
# instead of four hand-written Scenarios.

ACTIONS = {
    "list_members": ("GET", "/applications/1/members", None, None),
    "add_member": ("PUT", "/applications/1/members/5", {"role": "executor"}, ("memberships", 1)),
    # user 3 (eve) is already alpha's executor in the seed, so removing her needs no
    # setup step; user 5 (zed) is not a member of alpha, which is why the hand-written
    # spec's own "DELETE .../members/3 as zed" (not 5) is the one that is 404 for a
    # reason other than "no such membership": zed is not a member of alpha at all.
    "remove_member": ("DELETE", "/applications/1/members/3", None, ("memberships", -1)),
    "add_member_beta": (
        "PUT",
        "/applications/2/members/5",
        {"role": "executor"},
        ("memberships", 1),
    ),
    "remove_member_beta": ("DELETE", "/applications/2/members/4", None, ("memberships", -1)),
    "list_apps": ("GET", "/applications", None, None),
    "view_app_1": ("GET", "/applications/1", None, None),
    "view_app_2": ("GET", "/applications/2", None, None),
    "view_app_99": ("GET", "/applications/99", None, None),
}

CALLERS: dict[str | None, dict[str, Any]] = {
    "ann": {
        "list_members": (200, ALPHA_MEMBERS),
        "add_member": (200, {"user_id": 5, "role": "executor"}),
        "remove_member": 204,
        "list_apps": (200, [{"id": 1, "name": "alpha"}]),
        "view_app_2": 404,
    },
    "eve": {
        "list_members": 403,
        "add_member": 403,
        "remove_member": 403,
        "list_apps": (200, [{"id": 1, "name": "alpha"}]),
        "view_app_1": (200, {"id": 1, "name": "alpha"}),
    },
    "bob": {
        "add_member": 404,
        "remove_member": 404,
        "view_app_2": (200, {"id": 2, "name": "beta"}),
    },
    "zed": {
        "remove_member": 404,
        "list_apps": (200, []),
    },
    None: {
        "list_members": 401,
        "add_member": 401,
        "remove_member": 401,
        "list_apps": 401,
        "view_app_1": 401,
    },
    "root": {
        "add_member_beta": (200, {"user_id": 5, "role": "executor"}),
        "remove_member_beta": 204,
        "list_apps": (200, [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]),
        "view_app_2": (200, {"id": 2, "name": "beta"}),
        "view_app_99": 404,
    },
}


def build_matrix_scenarios() -> tuple:
    """The matrix call under test; its own source (via `inspect.getsource`) is what the
    size-reduction test compares against the hand-written scenarios' source."""
    return access_matrix(
        "members_of_alpha",
        actions=ACTIONS,
        callers=CALLERS,
        answers={403: DENIED, 404: NOT_FOUND},
        baseline={"memberships": 3},
    )


MATRIX_SCENARIOS = build_matrix_scenarios()

MATRIX_SPEC = Spec(
    profile=real_spec.PROFILE,
    scenarios=MATRIX_SCENARIOS,
    users=real_spec.USERS,
)


# ---- the spec is internally consistent ------------------------------------------------------


def test_matrix_scenarios_validate():
    problems = validate(MATRIX_SPEC)
    assert problems == [], "\n".join(p.render() for p in problems)


def test_scenario_names_are_deterministic_and_readable():
    names = {s.name for s in MATRIX_SCENARIOS}
    assert "access__add_member__as_eve" in names
    assert "access__list_members__as_anon" in names


# ---- it covers at least every (caller, action, status) the hand-written spec states --------

# The hand-written facts (spec/spec.py), mapped onto the matrix's action labels. The one
# hand-written fact left out is "setting a membership's role again changes it, never
# adds a second one" (app_admins_manage_their_members' third step): a state-dependent
# cell -- its expectation depends on a previous *successful* write by the same caller
# within the same scenario, which is exactly what the matrix form (one fresh seed per
# (caller, action) cell) cannot express. See the report for this limit.
HAND_WRITTEN_FACTS = {
    ("ann", "list_members", 200),
    ("ann", "add_member", 200),
    ("ann", "remove_member", 204),
    ("eve", "add_member", 403),
    ("eve", "list_members", 403),
    ("bob", "add_member", 404),
    ("zed", "remove_member", 404),
    ("root", "add_member_beta", 200),
    ("root", "remove_member_beta", 204),
    ("root", "list_apps", 200),
    ("ann", "list_apps", 200),
    ("eve", "list_apps", 200),
    ("zed", "list_apps", 200),
    ("eve", "view_app_1", 200),
    ("ann", "view_app_2", 404),
    ("root", "view_app_2", 200),
    ("root", "view_app_99", 404),
    (None, "list_apps", 401),
}


def _status(cell: Any) -> int:
    return cell if isinstance(cell, int) else cell[0]


def test_matrix_covers_every_hand_written_access_fact():
    covered = {
        (caller, action, _status(cell))
        for caller, cells in CALLERS.items()
        for action, cell in cells.items()
    }
    missing = HAND_WRITTEN_FACTS - covered
    assert not missing, f"the matrix does not cover: {sorted(missing)}"


# ---- size reduction ---------------------------------------------------------------------

HAND_WRITTEN_SCENARIO_NAMES = (
    "app_admins_manage_their_members",
    "executors_and_outsiders_cannot_manage_members",
    "the_super_admin_manages_any_application",
    "each_user_sees_only_their_applications",
)


def _scenario_source_lengths(module_file: str, names: tuple[str, ...]) -> dict[str, int]:
    """{scenario name: characters of its `Scenario(...)` call in the module's source}."""
    text = Path(module_file).read_text()
    tree = ast.parse(text)
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "Scenario" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value in names:
            segment = ast.get_source_segment(text, node)
            if segment is not None:
                found[first.value] = len(segment)
    return found


def _named_assignment(text: str, name: str) -> str:
    """The source of the (possibly annotated) top-level assignment to `name`."""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name) and target.id == name:
            segment = ast.get_source_segment(text, node)
            if segment is not None:
                return segment
    raise AssertionError(f"no top-level assignment to {name!r} found")


def test_size_reduction_report():
    import inspect

    hand = _scenario_source_lengths(real_spec.__file__, HAND_WRITTEN_SCENARIO_NAMES)
    assert set(hand) == set(HAND_WRITTEN_SCENARIO_NAMES), (
        f"could not find all hand-written scenarios in {real_spec.__file__}: {sorted(hand)}"
    )
    hand_chars = sum(hand.values())
    # the matrix source: the full compact table (actions + callers), not just the call
    # that builds scenarios from it -- the table *is* the authored content.
    own_text = Path(__file__).read_text()
    actions_src = _named_assignment(own_text, "ACTIONS")
    callers_src = _named_assignment(own_text, "CALLERS")
    call_src = inspect.getsource(build_matrix_scenarios)
    matrix_chars = len(actions_src) + len(callers_src) + len(call_src)
    reduction = 1 - matrix_chars / hand_chars
    print(
        f"\nhand-written: {hand_chars} chars across {len(hand)} Scenario(...) blocks "
        f"({len(MATRIX_SCENARIOS)} scenarios covered by the matrix)\n"
        f"matrix (ACTIONS + CALLERS + the access_matrix call): {matrix_chars} chars\n"
        f"reduction: {reduction:.0%}"
    )
    assert matrix_chars < hand_chars


# ---- the generated scenarios pass on the real app (layer D only) ---------------------------


def test_matrix_scenarios_pass_on_the_real_app():
    report = check(
        MATRIX_SPEC,
        job_scheduler.make_app,
        seed.build_seed_db,
        layers="D",
        login=seed.login,
    )
    assert report.findings == [], report.render()
    assert report.verdict == "pass"
    assert len(report.cases) == sum(len(s.steps) for s in MATRIX_SCENARIOS)
