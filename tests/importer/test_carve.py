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
    by = {n.name: n for n in natives}
    assert set(by) == {"carve_slugify", "carve_maybe_id", "carve_notify"}
    assert by["carve_slugify"] == CarveNative(
        "carve_slugify", "pkg.helpers:slugify", (("title", "STR"),), "STR"
    )
    assert by["carve_maybe_id"].result == "UUID" and by["carve_maybe_id"].nullable
    notify = by["carve_notify"]
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
