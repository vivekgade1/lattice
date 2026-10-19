# ruff: noqa: E501
"""Typed rule conditions: the None literal in a row_limit ``where`` (found by the Streak arm's preflight).

``{"where": {"deleted_at": null}}`` on a nullable column must type-check (it was F004) and be generated as
``is_null(Table.column)`` in the count invariant (``Table.column == None`` is S008, so the spec was invalid).
Every other forms file generates byte-for-byte what it did.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lattice.spec import forms as F
from lattice.spec.rules import Trace
from lattice.spec.validate import validate
from lattice.typesys import Scalar as S
from lattice.typesys import Schema
from tests.spec import minishop as M

REPO = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "forms" / "minishop"
STREAK_REPO = Path.home() / "Documents" / "streak"
STREAK_SHA = "a0679e9044486c98611febcd050a1fd23b6cd756"
STREAK_TGZ = REPO / "docs/results/data/2026-10-18-typed-conditions-streak-arm/streak-set.tgz"
ADD = {"path": "/orders/10/items", "json": {"product_id": 50, "quantity": 5}}
GIVEN = {
    "orders": [{"id": 10, "user_id": 1, "status": "pending", "total": 0.0}],
    "products": [{"id": 50, "name": "bulk", "price": 1.0, "stock": 1000}],
}


def mk(form: dict, fid: str = "F9") -> F.Form:
    return F.Form(
        fid,
        "R10",
        "a" * 64,
        "row_limit",
        form,
        "approved",
        "hand",
        "t",
        F.form_sha("row_limit", form),
    )


def limit(**where) -> dict:
    form = {
        "table": "OrderItem", "by": ["order_id"], "max": 5, "endpoint": "POST /orders/{}/items",
        "caller": "ann", "request": ADD, "given": GIVEN, "accepted": {"status": 201},
        "refused": {"status": 409},
    }  # fmt: skip
    if where:
        form["where"] = where
    return form


def nullable_shop(nullable: bool = True):
    """The mini-shop with an ``OrderItem.removed_at`` DateTime column (nullable or NOT NULL)."""
    old = M.ORDER_ITEM
    item = Schema(
        columns=(*old.columns, ("removed_at", S.DATETIME)),
        keys=old.keys,
        nullable=frozenset({"removed_at"}) if nullable else frozenset(),
        owner_via=old.owner_via,
    )
    spec = M.SPEC
    api = dataclasses.replace(
        spec.profile.api, tables={**spec.profile.api.tables, "OrderItem": item}
    )
    return dataclasses.replace(spec, profile=dataclasses.replace(spec.profile, api=api))


def codes(spec, forms) -> list[str]:
    return [d.code for d in F.check_forms(spec, None, forms)]


# ---- the type checker ---------------------------------------------------------------------------------------


def test_none_types_against_a_nullable_column():
    assert codes(nullable_shop(), [mk(limit(removed_at=None))]) == []


def test_none_is_still_refused_on_a_not_null_column():
    spec = nullable_shop(nullable=False)
    assert codes(spec, [mk(limit(removed_at=None))]) == ["F004"]
    assert codes(M.SPEC, [mk(limit(quantity=None))]) == ["F004"]


def test_a_wrong_typed_literal_on_a_nullable_column_is_still_f004():
    assert codes(nullable_shop(), [mk(limit(removed_at=5))]) == ["F004"]


# ---- the generator ------------------------------------------------------------------------------------------


def test_a_none_where_is_generated_as_is_null_and_validates():
    spec = nullable_shop()
    g = F.generate(spec, [mk(limit(removed_at=None, quantity=1))])
    (inv,) = g.invariants
    assert inv.expr == (
        "count(OrderItem, by='order_id', where=OrderItem.quantity == 1 and is_null(OrderItem.removed_at)) <= 5"
    )
    assert "== None" not in inv.expr
    eff = F.effective_spec(spec, g)
    assert [p for p in validate(eff) if p.code == "S008"] == []
    assert F.self_check(spec, g) == []


def test_a_none_where_alone():
    g = F.generate(nullable_shop(), [mk(limit(removed_at=None))])
    assert (
        g.invariants[0].expr
        == "count(OrderItem, by='order_id', where=is_null(OrderItem.removed_at)) <= 5"
    )


def test_the_scenarios_carry_the_none_locus():
    g = F.generate(nullable_shop(), [mk(limit(removed_at=None))])
    at = next(s for s in g.scenarios if s.name == "form:F9:at_limit")
    assert at.steps[0].expect_db["OrderItem"]["where"] == {"order_id": 10, "removed_at": None}
    assert len(at.given["OrderItem"]) == 5
    assert all(r.get("removed_at") is None for r in at.given["OrderItem"])


# ---- nothing else changes -----------------------------------------------------------------------------------


def _digest(g: F.Generated) -> str:
    return hashlib.sha256(
        json.dumps(F.generated_view(g), sort_keys=True, default=str).encode()
    ).hexdigest()


def test_the_minishop_fixture_generates_byte_identically():
    """Pinned on the library before this fix (the same digest before and after)."""
    trace = Trace.load(FIX / "spec.trace.json")
    fs = F.load_set(trace, F.forms_paths(FIX / "spec.trace.json"))
    assert fs is not None
    assert _digest(F.generate(M.SPEC, fs.generating())) == (
        "c01d6b1e2882efa60a277a4876bc119a7d28d0ab48324706224a647f1c136773"
    )


def test_a_non_none_where_generates_byte_identically():
    trace = Trace.load(FIX / "spec.trace.json")
    fs = F.load_set(trace, F.forms_paths(FIX / "spec.trace.json"))
    assert fs is not None
    f7 = next(r for r in fs.generating() if r.form.id == "F7")
    form = copy.deepcopy(f7.form.form)
    form["where"] = {"quantity": 1, "price": 0.0}
    nf = F.Form("F7", f7.form.rule, f7.form.anchor, "row_limit", form, "approved", "hand", "t",
                F.form_sha("row_limit", form))  # fmt: skip
    g = F.generate(M.SPEC, [nf])
    assert [i.expr for i in g.invariants] == [
        "count(OrderItem, by='order_id', where=OrderItem.price == 0.0 and OrderItem.quantity == 1) <= 5"
    ]
    assert _digest(g) == "06d246f201259475f163be98098180fc990aa9e42021aae995d5c31e4c711722"


# ---- the four Streak forms (F28, F29, F33, F35) -------------------------------------------------------------


@pytest.fixture(scope="module")
def streak_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Streak a0679e9 (``git archive``) with the registered set's forms beside its trace."""
    if not STREAK_TGZ.is_file() or not (STREAK_REPO / ".git").exists():
        pytest.skip("the Streak repository or the registered streak-set.tgz is not available")
    probe = subprocess.run(
        ["git", "-C", str(STREAK_REPO), "cat-file", "-e", f"{STREAK_SHA}^{{commit}}"],
        capture_output=True,
        check=False,
    )  # noqa: S603, S607
    if probe.returncode != 0:
        pytest.skip("Streak a0679e9 is not in the local repository")
    root = tmp_path_factory.mktemp("streak")
    tree, sset = root / "tree", root / "set"
    tree.mkdir()
    sset.mkdir()
    tar = subprocess.run(
        ["git", "-C", str(STREAK_REPO), "archive", STREAK_SHA], capture_output=True, check=True
    )  # noqa: S603, S607
    subprocess.run(["tar", "x", "-C", str(tree)], input=tar.stdout, check=True)  # noqa: S603, S607
    subprocess.run(["tar", "xzf", str(STREAK_TGZ), "-C", str(sset)], check=True)  # noqa: S603, S607
    shutil.copyfile(sset / "streak" / "forms.json", tree / "REQUIREMENTS.forms.json")
    return tree


def _load_streak(tree: Path):
    import importlib.util

    sys.path.insert(0, str(tree))
    sys.modules.pop("spec", None)
    try:
        ms = importlib.util.spec_from_file_location("streak_spec_none_literal", tree / "spec.py")
        assert ms is not None and ms.loader is not None
        mod = importlib.util.module_from_spec(ms)
        ms.loader.exec_module(mod)
    finally:
        sys.path.remove(str(tree))
    return mod.SPEC


NONE_FORMS = ("F28", "F29", "F33", "F35")


def test_streak_forms_check_accepts_the_four_row_limit_forms(streak_tree: Path):
    spec = _load_streak(streak_tree)
    tp = streak_tree / "spec.trace.json"
    fs = F.load_set(Trace.load(tp), F.forms_paths(tp))
    assert fs is not None
    by_id = {r.form.id: r for r in fs.resolved}
    for fid in NONE_FORMS:
        assert by_id[fid].form.form["where"]["deleted_at"] is None
    assert [d for d in F.check_forms(spec, Trace.load(tp), [r.form for r in fs.resolved])] == []
    assert set(NONE_FORMS) <= {r.form.id for r in F.usable(fs.resolved)}


def test_streak_generated_spec_validates_without_s008(streak_tree: Path):
    spec = _load_streak(streak_tree)
    tp = streak_tree / "spec.trace.json"
    fs = F.load_set(Trace.load(tp), F.forms_paths(tp))
    assert fs is not None
    gen = F.generate(spec, F.usable(fs.resolved))
    exprs = {i.name: i.expr for i in gen.invariants if i.name.split(":")[1] in NONE_FORMS}
    assert set(exprs) == {f"form:{f}:invariant" for f in NONE_FORMS}
    for e in exprs.values():
        assert "== None" not in e and "is_null(" in e
    assert exprs["form:F35:invariant"] == (
        "count(Completion, by=('habit_id', 'date'), where=is_null(Completion.deleted_at)) <= 1"
    )
    eff = F.effective_spec(spec, gen)
    base = {(p.code, p.message) for p in validate(spec)}
    assert [p for p in validate(eff) if (p.code, p.message) not in base] == []
    assert [p for p in validate(eff) if p.code == "S008"] == []
    assert F.self_check(spec, gen) == []


def test_streak_cli_forms_check_and_generate(streak_tree: Path):
    env_path = f"{REPO}:{streak_tree}"
    common = ["--trace", "spec.trace.json", "--spec", "spec:SPEC"]
    for sub in ("check", "generate"):
        p = subprocess.run(
            [sys.executable, "-m", "lattice.spec", "forms", sub, *common],
            cwd=streak_tree,
            env={"PYTHONPATH": env_path, "PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )  # noqa: S603
        assert p.returncode == 0, p.stdout + p.stderr
        if sub == "check":
            assert "0 problem(s) in 43 form(s)" in p.stdout
        else:
            assert "is_null(Habit.deleted_at)" in p.stdout
            assert "== None" not in p.stdout
