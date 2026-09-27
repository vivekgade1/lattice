"""Sub-project 1b node N4 (spec 2026-09-27 lattice-loops §5): a `for`/`while` statement
keeps its code (L127) and a comprehension keeps L121, but the hint names the Lattice
construct matching the loop's shape:

* running / previous / rank -> `window`
* tree / parent walk        -> `traverse`
* repeat until stable       -> `iterate` (tabular; API programs: a native)
* do something per row      -> `for_each` (API; tabular programs: `write` only)
* anything else             -> a native function
"""

from __future__ import annotations

import pytest

from lattice.check import check_source
from lattice.profile import Profile
from tests.api import effects

H = "# lattice: v1\n"


def loop_diag(body: str, profile: Profile | None = None, code: str = "L127"):
    result = check_source(H + body, profile or Profile(schemas={}))
    found = [d for d in result.diagnostics if d.code == code]
    assert len(found) == 1, [d.render() for d in result.diagnostics]
    return found[0]


RUNNING = """for txn in txns:
    balance[txn.account_id] += txn.amount
    txn.balance = balance[txn.account_id]
"""
PREVIOUS = """for row in rows:
    row["delta"] = row["price"] - prev
    prev = row["price"]
"""
RANK = """for i, emp in enumerate(ranked):
    emp.rank = i + 1
"""
MOVING = """for reading in readings:
    recent.append(reading.value)
    reading.avg = sum(recent) / len(recent)
"""
PARENT_WALK = """while node is not None:
    path.append(node)
    node = node.parent
"""
TREE_BFS = """while queue:
    current = queue.popleft()
    for child in children_of[current]:
        queue.append(child)
"""
UNTIL_STABLE = """while changed:
    changed = False
    rows = prune(rows)
"""
CONVERGE = """while abs(estimate - target) > tolerance:
    estimate = (estimate + target) / 2
"""
PER_ROW = """for user in users:
    send_email(user.email, "welcome")
"""
STATE_MACHINE = """for event in events:
    if event.kind == "login":
        state = "active"
"""


@pytest.mark.parametrize(
    ("body", "construct"),
    [
        (RUNNING, "window("),
        (PREVIOUS, "window("),
        (RANK, "window("),
        (MOVING, "window("),
        (PARENT_WALK, "traverse("),
        (TREE_BFS, "traverse("),
        (UNTIL_STABLE, "iterate("),
        (CONVERGE, "iterate("),
        (PER_ROW, "for_each("),
        (STATE_MACHINE, "native function"),
    ],
)
def test_the_loop_hint_names_the_construct(body: str, construct: str) -> None:
    diag = loop_diag(body)
    assert diag.message == "loops and if-statements are not allowed"
    assert diag.hint is not None and construct in diag.hint, diag.hint


def test_the_window_hint_also_mentions_group_for_a_plain_total() -> None:
    hint = loop_diag(RUNNING).hint or ""
    assert "win.cum_sum" in hint and "win.lag" in hint and "agg.sum" in hint


def test_every_specific_hint_is_distinct() -> None:
    hints = {
        loop_diag(b).hint for b in (RUNNING, PARENT_WALK, UNTIL_STABLE, PER_ROW, STATE_MACHINE)
    }
    assert len(hints) == 5


def test_if_statement_keeps_its_hint() -> None:
    diag = loop_diag("if a:\n    x = 1\n")
    assert diag.hint == "use where/derive/when instead"


def test_api_profile_hints_follow_the_profile() -> None:
    api = effects.PROFILE
    # for_each is the API construct for a per-row action
    assert "for_each(" in (loop_diag(PER_ROW, api).hint or "")
    # iterate is tabular only: an API program keeps such a loop in a native
    hint = loop_diag(UNTIL_STABLE, api).hint or ""
    assert "iterate" in hint and "native function" in hint
    # window and traverse exist in both profiles
    assert "window(" in (loop_diag(RUNNING, api).hint or "")
    assert "traverse(" in (loop_diag(PARENT_WALK, api).hint or "")


def test_tabular_per_row_action_points_at_the_api_construct() -> None:
    hint = loop_diag(PER_ROW).hint or ""
    assert "for_each(" in hint and "API" in hint


def test_while_loops_are_classified_too() -> None:
    body = "while i < len(rows):\n    rows[i].running = total\n    i = i + 1\n"
    assert "window(" in (loop_diag(body).hint or "")


@pytest.mark.parametrize(
    ("expr", "construct"),
    [
        ("[send_email(u) for u in users]", "for_each("),
        ("[r.parent for r in rows]", "traverse("),
        ("[prev for prev in rows]", "window("),
    ],
)
def test_comprehension_hints_name_a_construct_when_the_shape_is_clear(
    expr: str, construct: str
) -> None:
    diag = loop_diag(f"x = f({expr})\n", code="L121")
    assert construct in (diag.hint or ""), diag.hint


def test_a_plain_comprehension_keeps_the_table_op_hint() -> None:
    diag = loop_diag("x = f([a * 2 for a in b])\n", code="L121")
    assert diag.hint == "use a table op (derive/where/group)"


def test_loops_inside_steps_and_endpoints_get_the_hint_too() -> None:
    step = (
        "@step\ndef s(t: Table) -> Table:\n"
        + "".join("    " + line + "\n" for line in PARENT_WALK.splitlines())
        + "    return t\n"
    )
    assert "traverse(" in (loop_diag(step).hint or "")
    endpoint = (
        '@endpoint("GET", "/x", auth=Public)\ndef x() -> Page:\n'
        + "".join("    " + line + "\n" for line in PER_ROW.splitlines())
        + "    return users\n"
    )
    assert "for_each(" in (loop_diag(endpoint, effects.PROFILE).hint or "")
