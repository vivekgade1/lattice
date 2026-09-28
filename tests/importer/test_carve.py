"""R3's simple carve-out (importer I5): which helpers of a route's slice become typed
natives, and their effect flags -- an effectful helper is never declared pure (plan
review focus 4)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from lattice.importer.carve import (
    SKIP_TODO,
    CarveNative,
    NativeCarveOut,
    carve_profile_source,
    used_natives,
)
from lattice.importer.graph import build_graph

HELPERS = """\
import smtplib
import uuid


def slugify(title: str) -> str:
    return title.strip().lower()


def maybe_id(text: str) -> uuid.UUID | None:
    return None


def notify(email: str) -> None:
    smtplib.SMTP("localhost").sendmail("me", [email], "hi")


def notify_and_count(email: str) -> int:
    notify(email)
    return 1


def shaped(data: dict) -> str:
    return str(data)


def quiet(x: str) -> None:
    return None
"""

ROUTES = """\
from fastapi import APIRouter

from pkg.helpers import maybe_id, notify, notify_and_count, quiet, shaped, slugify

router = APIRouter()


@router.post("/things")
def make_thing(title: str):
    notify(title)
    notify_and_count(title)
    shaped({})
    quiet(title)
    maybe_id(title)
    return {"slug": slugify(title)}


@router.get("/plain")
def plain():
    return {}
"""


def _graph(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "helpers.py").write_text(HELPERS)
    (pkg / "routes.py").write_text(textwrap.dedent(ROUTES))
    return build_graph(tmp_path, "pkg")


def test_simple_helpers_become_typed_natives_with_their_effects(tmp_path):
    graph = _graph(tmp_path)
    natives, why = NativeCarveOut().natives(graph, "pkg.routes:make_thing", known=())
    assert why == ""
    # module-qualified names (see native_name), keyed here by the helper's name
    by = {n.target.partition(":")[2]: n for n in natives}
    assert set(by) == {"slugify", "maybe_id", "notify"}
    assert by["slugify"] == CarveNative(
        "carve_pkg_helpers_slugify", "pkg.helpers:slugify", (("title", "STR"),), "STR"
    )
    assert by["maybe_id"].name.startswith("carve_pkg_helpers_maybe_id_")
    assert by["maybe_id"].result == "UUID" and by["maybe_id"].nullable
    notify = by["notify"]
    assert notify.effect and notify.result is None and "smtplib" in notify.effect_reason


def test_known_natives_are_not_carved_again(tmp_path):
    graph = _graph(tmp_path)
    known = {"pkg.helpers:slugify", "pkg.helpers:maybe_id", "pkg.helpers:notify"}
    natives, why = NativeCarveOut().natives(graph, "pkg.routes:make_thing", known=known)
    assert natives == ()
    # an effectful helper with a result is skipped, never declared pure
    assert "pkg.helpers:notify_and_count: effectful" in why
    assert "pkg.helpers:shaped: parameter 'data' is not a required scalar" in why
    assert "pkg.helpers:quiet: no result and no effect" in why
    assert why.endswith(SKIP_TODO)


def test_a_route_reaching_no_helper_skips_r3(tmp_path):
    natives, why = NativeCarveOut().natives(_graph(tmp_path), "pkg.routes:plain", known=())
    assert natives == () and "reaches no helper" in why


def test_the_profile_gains_the_natives(tmp_path):
    from lattice.importer.llm_lift import load_profile_source

    base = (
        "from lattice.profile import ApiProfile, NativeSig, Profile\n"
        "from lattice.typesys import Scalar\n\nNATIVES = {}\n\n\n"
        "def profile() -> Profile:\n"
        "    api = ApiProfile(tables={}, table_names={}, roles=frozenset())\n"
        "    return Profile(schemas={}, api=api)\n"
    )
    pure = CarveNative("carve_up", "string:capwords", (("s", "STR"),), "STR")
    effect = CarveNative("carve_ping", "builtins:print", (("s", "STR"),), None, effect=True)
    module = load_profile_source(carve_profile_source(base, (pure, effect)))
    api = module.profile().api
    assert api.natives["carve_up"].result.name == "STR" and not api.natives["carve_up"].effect
    assert api.natives["carve_ping"].effect and api.natives["carve_ping"].result is None
    assert module.NATIVES["carve_up"]("a b") == "A B"
    assert carve_profile_source(base, ()) == base


def test_used_natives_reads_native_calls():
    a = CarveNative("carve_a", "m:a", (), "STR")
    b = CarveNative("carve_b", "m:b", (), "STR")
    src = "def f():\n    x = derive(t, s=native.carve_b(col('x')))\n"
    assert used_natives(src, (a, b)) == (b,)


# ---- name collisions (two helpers of the same name in different modules) --------------------

USERS_HELPERS = """\
def validate(text: str) -> str:
    return text.strip()
"""
ITEMS_HELPERS = """\
def validate(text: str) -> str:
    return text.upper()
"""
TWO_ROUTES = """\
from fastapi import APIRouter

from pkg import items, users

router = APIRouter()


@router.post("/users")
def make_user(name: str):
    return {"name": users.validate(name)}


@router.post("/items")
def make_item(title: str):
    return {"title": items.validate(title)}


@router.post("/both")
def make_both(title: str):
    return {"a": users.validate(title), "b": items.validate(title)}
"""


def _twin_graph(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "users.py").write_text(USERS_HELPERS)
    (pkg / "items.py").write_text(ITEMS_HELPERS)
    (pkg / "routes.py").write_text(TWO_ROUTES)
    return build_graph(tmp_path, "pkg")


def test_same_named_helpers_in_different_modules_get_distinct_natives(tmp_path):
    graph = _twin_graph(tmp_path)
    (u,), _ = NativeCarveOut().natives(graph, "pkg.routes:make_user", known=())
    (i,), _ = NativeCarveOut().natives(graph, "pkg.routes:make_item", known=())
    assert (u.target, i.target) == ("pkg.users:validate", "pkg.items:validate")
    assert u.name != i.name
    assert u.name.isidentifier() and i.name.isidentifier()
    assert "users" in u.name and "items" in i.name  # module-qualified
    both, _ = NativeCarveOut().natives(graph, "pkg.routes:make_both", known=())
    assert len({n.name for n in both}) == 2


def test_native_names_are_deterministic_and_unique_per_target():
    from lattice.importer.carve import native_name

    assert native_name("carve_", "pkg.helpers:slugify") == "carve_pkg_helpers_slugify"
    assert native_name("carve_", "pkg.helpers:slugify") == native_name(
        "carve_", "pkg.helpers:slugify"
    )
    # sanitising alone would map these two to the same identifier
    a = native_name("carve_", "a.b:c_d")
    b = native_name("carve_", "a.b.c:d")
    c = native_name("carve_", "a.b_c:d")
    assert len({a, b, c}) == 3 and all(n.isidentifier() for n in (a, b, c))


def test_same_named_helpers_are_not_carved_as_one_procedure(tmp_path):
    from lattice.importer.carve import ProcedureCarveOut
    from lattice.profile import ApiProfile, Profile

    profile = Profile(schemas={}, api=ApiProfile(tables={}, table_names={}, roles=frozenset()))
    graph = _twin_graph(tmp_path)
    plans, _ = ProcedureCarveOut().plans(graph, "pkg.routes:make_both", (), profile)
    procedure = plans[0]
    assert procedure.kind == "procedure"
    # two `validate` procedures cannot share one program: neither is carved
    # mechanically, both are left to the translation (whose names the LLM chooses)
    assert [p.name for p in procedure.procedures] == []
    assert {h.origin for h in procedure.translate} == {"pkg.users:validate", "pkg.items:validate"}
    assert all("validate" in h.why and "same name" in h.why for h in procedure.translate)


BASE_PROFILE = (
    "from lattice.profile import ApiProfile, NativeSig, Profile\n"
    "from lattice.typesys import Scalar, Schema\n\nNATIVES = {}\n\n\n"
    "def profile() -> Profile:\n"
    "    users = Schema.from_pairs(\n"
    "        [('id', Scalar.INT), ('role', Scalar.STR), ('token', Scalar.STR)],\n"
    "        keys=[('id',)], private=['token'],\n"
    "    )\n"
    "    api = ApiProfile(\n"
    "        tables={'Users': users}, table_names={'Users': 'users'},\n"
    "        roles=frozenset({'Public', 'Customer', 'Admin'}),\n"
    "    )\n"
    "    return Profile(schemas={}, api=api)\n"
)


def _outcome(key: str, router: str, native: CarveNative, native_arg: str):
    from types import SimpleNamespace

    from lattice.importer.llm_lift import RouteOutcome

    source = (
        "# lattice: v1\n\n\n"
        f'@endpoint("GET", "/{key}", auth=Customer)\n'
        f"def {key}(user: CurrentUser) -> One:\n"
        f'    shown = derive(user, s=native.{native.name}({native_arg}))\n'
        '    out = select(shown, cols=["id", "s"])\n'
        "    return out\n"
    )
    route = SimpleNamespace(
        module=f"pkg.{router}", response_class=None, status_code=None, params=[]
    )
    result = SimpleNamespace(router=router, route=route)
    return RouteOutcome(key, result, "verified", "R3", source=source, natives=(native,))


def test_join_demotes_a_route_whose_native_name_is_taken_by_another_target():
    """Two routes carving `carve_validate` over different helpers: the first wins the
    joined profile, the second -- whose verified implementation is not the one the
    profile would ship -- becomes an island, never runs the other helper."""
    from lattice.importer.llm_lift import _join

    users = CarveNative("carve_validate", "pkg.users:validate", (("text", "STR"),), "STR")
    items = CarveNative("carve_validate", "pkg.items:validate", (("text", "STR"),), "STR")
    first = _outcome("read_a", "users", users, '"x"')
    second = _outcome("read_b", "items", items, '"x"')
    programs, profile_source, kept = _join(
        [first, second], BASE_PROFILE, {"read_a": 0, "read_b": 1}
    )
    assert kept == (users,)
    assert (first.status, second.status) == ("verified", "island")
    assert "carve_validate" in second.reason and "pkg.items:validate" in second.reason
    assert set(programs) == {"users"}
    assert "pkg.users" in profile_source and "pkg.items" not in profile_source


def test_join_keeps_one_native_shared_by_two_routes():
    from lattice.importer.llm_lift import _join

    n = CarveNative("carve_validate", "pkg.users:validate", (("text", "STR"),), "STR")
    a = _outcome("read_a", "users", n, '"x"')
    b = _outcome("read_b", "items", n, '"y"')
    programs, _, kept = _join([a, b], BASE_PROFILE, {"read_a": 0, "read_b": 1})
    assert kept == (n,) and (a.status, b.status) == ("verified", "verified")
    assert set(programs) == {"users", "items"}
