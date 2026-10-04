"""PY808 state machines: a write of a known state to a machine column from a state the
function's own guard leaves it in, with no edge to the written state.

The demo app (tests/pycheck/conftest.py) gets an ``Item.status`` column (an enum) and the
policy a machine draft -> published -> archived; the precision gate is the multi-vendor
references (t31-t44) with the upstream ``VALID_TRANSITIONS`` as the ``Order.status``
machine.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
from pycheck_demo import MODELS

from bench.edit_slice.qcheck import check_files, repo_policy
from bench.edit_slice.reference import REFERENCE_ROOT, reference_files
from lattice.pycheck import Finding, Policy, derive_policy, describe_policy, run_pycheck

STATUS_MODELS = (
    MODELS.replace(
        "from datetime import datetime\n",
        "from datetime import datetime\nfrom enum import Enum\n",
    )
    .replace(
        "class User(SQLModel, table=True):",
        'class ItemStatus(str, Enum):\n    DRAFT = "draft"\n    PUBLISHED = "published"\n'
        '    ARCHIVED = "archived"\n\n\nclass User(SQLModel, table=True):',
    )
    .replace(
        '    owner_id: int = Field(foreign_key="user.id")\n',
        '    owner_id: int = Field(foreign_key="user.id")\n'
        "    status: ItemStatus = ItemStatus.DRAFT\n",
    )
)
ITEM_EDGES = frozenset({("draft", "published"), ("published", "archived")})
IMPORTS = "from demo.models import ItemStatus\n"

FETCH = """
@router.post("/{id}/move", response_model=ItemPublic)
def move_item(session: SessionDep, current_user: CurrentUser, id: int) -> Any:
    item = session.get(Item, id)
    if not item or item.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Item not found")
"""


@pytest.fixture
def mpolicy(demo_app) -> Policy:
    base = derive_policy(demo_app(models=STATUS_MODELS), "demo")
    return replace(base, machines={"Item": ("status", ITEM_EDGES)})


def py808(demo_app, policy: Policy, body: str, extra: str = "") -> list[Finding]:
    """PY808 findings with ``body`` (indented 4) appended to ``move_item``."""
    src = IMPORTS + extra + FETCH + body + "    session.commit()\n    return item\n"
    found = run_pycheck(demo_app(src, models=STATUS_MODELS), policy)
    return [f for f in found if f.code == "PY808"]


def test_policy_machines_default_empty(demo_app):
    assert dict(derive_policy(demo_app(models=STATUS_MODELS), "demo").machines) == {}


def test_the_original_app_has_no_findings_with_a_machine(demo_app, mpolicy):
    assert run_pycheck(demo_app(), mpolicy) == []
    assert run_pycheck(demo_app(models=STATUS_MODELS), mpolicy) == []


def test_describe_states_the_machine(mpolicy):
    text = describe_policy(mpolicy)
    assert "State machine: `Item.status`" in text and "draft -> published" in text
    assert "State machine" not in describe_policy(replace(mpolicy, machines={}))


# ---- positives ---------------------------------------------------------------------------


def test_flags_a_write_from_a_guarded_state_with_no_edge(demo_app, mpolicy):
    body = """\
    if item.status != ItemStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Not a draft")
    item.status = ItemStatus.ARCHIVED
"""
    (f,) = py808(demo_app, mpolicy, body)
    assert f.file == "routes.py"
    assert "draft" in f.message and "archived" in f.message
    assert "item.status" in f.hint and "published" in f.hint


def test_flags_a_membership_guard_with_one_illegal_state_and_string_literals(demo_app, mpolicy):
    body = """\
    if item.status not in ("draft", "published"):
        raise HTTPException(status_code=409, detail="Bad state")
    item.status = "archived"
"""
    (f,) = py808(demo_app, mpolicy, body)
    assert "draft" in f.message


def test_flags_inside_a_positive_branch(demo_app, mpolicy):
    body = """\
    if item.status == ItemStatus.DRAFT:
        item.status = ItemStatus.ARCHIVED
"""
    assert len(py808(demo_app, mpolicy, body)) == 1


def test_flags_a_skip_guard_in_a_loop(demo_app, mpolicy):
    body = """\
    for other in session.exec(select(Item)).all():
        if other.status not in (ItemStatus.DRAFT, ItemStatus.PUBLISHED):
            continue
        other.status = ItemStatus.ARCHIVED
"""
    assert len(py808(demo_app, mpolicy, body)) == 1


def test_flags_a_visible_update_dict(demo_app, mpolicy):
    body = """\
    if item.status != ItemStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Not a draft")
    item.sqlmodel_update({"status": ItemStatus.ARCHIVED})
"""
    assert len(py808(demo_app, mpolicy, body)) == 1


def test_machine_keyed_by_sql_table_name(demo_app, mpolicy):
    body = """\
    if item.status != ItemStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Not a draft")
    item.status = ItemStatus.ARCHIVED
"""
    policy = replace(mpolicy, machines={"item": ("status", ITEM_EDGES)})
    assert len(py808(demo_app, policy, body)) == 1


# ---- negatives ---------------------------------------------------------------------------


def test_accepts_a_legal_edge(demo_app, mpolicy):
    body = """\
    if item.status != ItemStatus.PUBLISHED:
        raise HTTPException(status_code=409, detail="Not published")
    item.status = ItemStatus.ARCHIVED
"""
    assert py808(demo_app, mpolicy, body) == []


def test_silent_on_an_unguarded_write(demo_app, mpolicy):
    assert py808(demo_app, mpolicy, "    item.status = ItemStatus.ARCHIVED\n") == []


def test_silent_on_a_transition_table_guard(demo_app, mpolicy):
    extra = """
ALLOWED = {
    ItemStatus.DRAFT: {ItemStatus.PUBLISHED, ItemStatus.ARCHIVED},
    ItemStatus.PUBLISHED: {ItemStatus.ARCHIVED},
}
"""
    body = """\
    if ItemStatus.ARCHIVED not in ALLOWED.get(item.status, set()):
        raise HTTPException(status_code=409, detail="Bad transition")
    item.status = ItemStatus.ARCHIVED
"""
    assert py808(demo_app, mpolicy, body, extra) == []
    body = """\
    allowed = ALLOWED[item.status]
    if ItemStatus.ARCHIVED not in allowed:
        raise HTTPException(status_code=409, detail="Bad transition")
    item.status = ItemStatus.ARCHIVED
"""
    assert py808(demo_app, mpolicy, body, extra) == []


def test_silent_on_an_unknown_value(demo_app, mpolicy):
    body = """\
    if item.status != ItemStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Not a draft")
    item.status = item.title
"""
    assert py808(demo_app, mpolicy, body) == []


def test_silent_after_the_row_is_rebound_or_handed_to_a_helper(demo_app, mpolicy):
    body = """\
    if item.status != ItemStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Not a draft")
    item = session.get(Item, id + 1)
    item.status = ItemStatus.ARCHIVED
"""
    assert py808(demo_app, mpolicy, body) == []
    extra = """
def publish(item):
    item.status = ItemStatus.PUBLISHED
"""
    body = """\
    if item.status != ItemStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Not a draft")
    publish(item)
    item.status = ItemStatus.ARCHIVED
"""
    assert py808(demo_app, mpolicy, body, extra) == []


def test_silent_when_the_guard_does_not_dominate(demo_app, mpolicy):
    body = """\
    if item.status != ItemStatus.DRAFT:
        item.title = "x"
    item.status = ItemStatus.ARCHIVED
"""
    assert py808(demo_app, mpolicy, body) == []


def test_silent_without_a_machine(demo_app, mpolicy):
    body = """\
    if item.status != ItemStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Not a draft")
    item.status = ItemStatus.ARCHIVED
"""
    assert py808(demo_app, replace(mpolicy, machines={}), body) == []


# ---- the precision gate: the multi-vendor references ---------------------------------------

ORDER_EDGES = frozenset(
    {
        ("pending", "confirmed"),
        ("pending", "cancelled"),
        ("confirmed", "shipped"),
        ("confirmed", "cancelled"),
        ("shipped", "delivered"),
    }
)
MULTIVENDOR = sorted(
    p.name
    for p in REFERENCE_ROOT.iterdir()
    if (m := re.match(r"t(\d+)_", p.name)) and 31 <= int(m.group(1)) <= 44
)
APP = Path(__file__).resolve().parents[2] / "bench/import_repos/multivendor/app"


@pytest.fixture(scope="module")
def order_policy() -> Policy:
    return replace(repo_policy("multivendor"), machines={"Order": ("status", ORDER_EDGES)})


def test_the_multivendor_tickets_are_all_there():
    assert len(MULTIVENDOR) == 14


@pytest.mark.parametrize("tid", ["original", *MULTIVENDOR])
def test_py808_precision_on_the_multivendor_references(order_policy, tid):
    files = {} if tid == "original" else reference_files(tid)
    found = check_files("multivendor", files, strict=True, policy=order_policy)
    assert [f for f in found if f.code == "PY808"] == []


def test_py808_flags_cancelling_a_shipped_order(order_policy):
    rel = "services/order_service.py"
    src = (
        (APP / rel).read_text()
        + """

def cancel_shipped(session: Session, order_id: int) -> Order:
    order = get_order(session, order_id)
    if order.status != OrderStatus.SHIPPED:
        raise OrderError("Only shipped orders")
    order.status = OrderStatus.CANCELLED
    session.add(order)
    session.commit()
    return order
"""
    )
    found = [
        f
        for f in check_files("multivendor", {rel: src}, strict=True, policy=order_policy)
        if f.code == "PY808"
    ]
    assert len(found) == 1, found
    assert found[0].file == rel and "shipped" in found[0].message


def test_py808_flags_the_close_shop_reference_widened_to_shipped(order_policy):
    rel = "services/vendor_service.py"
    src = reference_files("t41_close_shop")[rel]
    guard = "        if order.status != OrderStatus.PENDING:\n            continue\n"
    assert guard in src
    widened = src.replace(
        guard,
        "        if order.status not in (OrderStatus.PENDING, OrderStatus.SHIPPED):\n"
        "            continue\n",
    )
    files = {**reference_files("t41_close_shop"), rel: widened}
    found = [
        f
        for f in check_files("multivendor", files, strict=True, policy=order_policy)
        if f.code == "PY808"
    ]
    assert len(found) == 1, found
