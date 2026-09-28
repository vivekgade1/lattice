"""R3 emits T2 ``@procedure``s (importer I5b): deterministic carving of helpers already
in the T2 subset, and the plans :class:`ProcedureCarveOut` offers the ladder -- a
procedure plan first, a typed-native plan only for helpers outside the subset."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lattice.check import check_source
from lattice.importer.carve import (
    CarvedProcedure,
    ProcedureCarveOut,
    carve_procedure,
    procedure_program,
    with_procedures,
)
from lattice.importer.graph import build_graph
from lattice.profile import ApiProfile, Profile
from lattice.t2 import T2Fuel, compile_program, t2_load

PROFILE = Profile(schemas={}, api=ApiProfile(tables={}, table_names={}, roles=frozenset()))

HELPERS = """\
import math
import re
from typing import Optional

import httpx
from unidecode import unidecode

from pkg.shapes import clamp

RATE = 3
SEPARATOR = "-"


def score(title: str, n: int) -> float:
    \"\"\"A weighted word score.\"\"\"
    words = [w for w in title.split() if w]
    total: float = 0.0
    for w in words:
        total += len(w) * RATE
    return math.floor(total / max(n, 1) * 100) / 100


def slug(title: str) -> str:
    parts = [p for p in re.split(r"[^a-z0-9]+", title.lower()) if p]
    return SEPARATOR.join(parts)


def initials(title: Optional[str]) -> Optional[str]:
    if title is None:
        return None
    return "".join([w[0] for w in title.split()]).upper()


def banner(title: str) -> str:
    return slug(title).upper() + "!" * clamp(len(title))


def remote_title(url: str) -> str:
    return httpx.get(url).text


def ascii_title(title: str) -> str:
    return unidecode(title).strip()


def untyped(x):
    return x
"""

SHAPES = """\
def clamp(n: int) -> int:
    if n > 3:
        return 3
    return n
"""

ROUTES = """\
from fastapi import APIRouter

from pkg.helpers import ascii_title, banner, initials, remote_title, score, slug, untyped

router = APIRouter()


@router.post("/things")
def make_thing(title: str):
    return {
        "score": score(title, 2),
        "slug": slug(title),
        "initials": initials(title),
        "banner": banner(title),
        "remote": remote_title(title),
        "ascii": ascii_title(title),
        "raw": untyped(title),
    }


@router.get("/pure")
def pure(title: str):
    return {"score": score(title, 1), "x": untyped(title)}


@router.get("/plain")
def plain():
    return {}
"""


@pytest.fixture
def graph(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "helpers.py").write_text(HELPERS)
    (pkg / "shapes.py").write_text(SHAPES)
    (pkg / "routes.py").write_text(textwrap.dedent(ROUTES))
    return build_graph(tmp_path, "pkg")


def _run_procedure(procs: tuple[CarvedProcedure, ...], name: str, *args):
    """Check, compile and run the carved procedures' program; call ``name``."""
    checked = check_source(procedure_program(procs), PROFILE)
    errors = [d.render() for d in checked.diagnostics if d.severity.name == "ERROR"]
    assert checked.program is not None and not errors, errors
    code = compile_program(checked.program)
    assert code is not None
    ns = t2_load(code.source)
    return ns[name](T2Fuel(100_000), *args)


# ---- deterministic carving -------------------------------------------------------------------


def test_a_pure_helper_is_carved_mechanically(graph):
    proc, why, outside = carve_procedure(graph, "pkg.helpers:score")
    assert why == "" and not outside and proc is not None
    assert proc.name == "score" and proc.origin == "pkg.helpers:score"
    assert proc.source.startswith("@procedure\ndef score(title: Str, n: Int) -> Float:")
    assert "total: Float = 0.0" in proc.source  # local annotations are translated too
    assert proc.support == ("import math", "RATE = 3")
    assert proc.calls == ()
    assert proc.lines == len(proc.source.strip().splitlines())
    # the carved procedure computes what the original computes
    namespace: dict = {}
    exec(compile(HELPERS.replace("import httpx\n", "").replace(
        "from unidecode import unidecode\n", "").replace(
        "from pkg.shapes import clamp\n", ""), "helpers", "exec"), namespace)  # fmt: skip
    for title, n in (("alpha beta", 2), ("", 0), ("one  two three", 3)):
        assert _run_procedure((proc,), "score", title, n) == namespace["score"](title, n)


def test_optional_annotations_and_allowlisted_modules(graph):
    slug, why, _ = carve_procedure(graph, "pkg.helpers:slug")
    assert slug is not None, why
    assert slug.support == ("import re", "SEPARATOR = '-'")
    assert _run_procedure((slug,), "slug", "Hello, World") == "hello-world"
    initials, why, _ = carve_procedure(graph, "pkg.helpers:initials")
    assert initials is not None, why
    assert "def initials(title: Str | None) -> Str | None:" in initials.source
    assert _run_procedure((initials,), "initials", "big red dog") == "BRD"
    assert _run_procedure((initials,), "initials", None) is None


def test_a_helper_calling_package_helpers_carries_them(graph):
    banner, why, _ = carve_procedure(graph, "pkg.helpers:banner")
    assert banner is not None, why
    assert set(banner.calls) == {"slug", "clamp"}
    slug, _, _ = carve_procedure(graph, "pkg.helpers:slug")
    clamp, _, _ = carve_procedure(graph, "pkg.shapes:clamp")
    assert slug is not None and clamp is not None
    assert _run_procedure((banner, slug, clamp), "banner", "Hi there") == "HI-THERE!!!"


def test_helpers_outside_the_subset_are_not_carved(graph):
    proc, why, outside = carve_procedure(graph, "pkg.helpers:ascii_title")
    assert proc is None and outside
    assert "unidecode" in why and "allowlist" in why
    proc, why, outside = carve_procedure(graph, "pkg.helpers:remote_title")
    assert proc is None and outside and "effectful" in why and "httpx" in why
    proc, why, outside = carve_procedure(graph, "pkg.helpers:untyped")
    assert proc is None and not outside  # in reach of T2, but the LLM must type it
    assert "parameter 'x'" in why


def test_with_procedures_adds_the_called_ones_and_their_support(graph):
    quals = ("pkg.helpers:banner", "pkg.helpers:slug", "pkg.shapes:clamp", "pkg.helpers:score")
    procs = [carve_procedure(graph, q)[0] for q in quals]
    endpoint = '@endpoint("GET", "/x", auth=Public)\ndef x(t: Str) -> One:\n    return banner(t)\n'
    out = with_procedures(endpoint, [p for p in procs if p is not None])
    assert "def banner(" in out and "def slug(" in out and "def clamp(" in out
    assert "def score(" not in out and "import math" not in out
    assert out.index("import re") < out.index("def banner(")
    # a procedure the candidate defines itself is left alone
    own = "@procedure\ndef slug(title: Str) -> Str:\n    return title\n\n\n" + endpoint
    assert with_procedures(own, [p for p in procs if p is not None]).count("def slug(") == 1


# ---- the plans ------------------------------------------------------------------------------


def test_the_procedure_plan_comes_first_and_natives_only_for_outside_helpers(graph):
    plans, why = ProcedureCarveOut().plans(graph, "pkg.routes:make_thing", (), PROFILE)
    assert [p.kind for p in plans] == ["procedure", "native"]
    proc, native = plans
    assert {p.name for p in proc.procedures} == {"score", "slug", "initials", "banner", "clamp"}
    assert [t.origin for t in proc.translate] == ["pkg.helpers:untyped"]
    assert [n.target for n in native.natives] == ["pkg.helpers:ascii_title"]
    assert native.natives[0].name.startswith("carve_pkg_helpers_ascii_title_")  # qualified
    assert "unidecode" in native.why and "outside the T2 subset" in native.why
    # an effectful helper with a result is outside the subset and not a simple native
    assert "pkg.helpers:remote_title" in why and "not a simple native" in why


def test_no_native_plan_when_every_helper_fits_the_subset(graph):
    plans, _ = ProcedureCarveOut().plans(graph, "pkg.routes:pure", (), PROFILE)
    assert [p.kind for p in plans] == ["procedure"]
    assert [p.name for p in plans[0].procedures] == ["score"]


def test_a_route_without_helpers_still_gets_a_procedure_plan(graph):
    """The route's own code may hold the logic T0 cannot express."""
    plans, _ = ProcedureCarveOut().plans(graph, "pkg.routes:plain", (), PROFILE)
    assert [p.kind for p in plans] == ["procedure"]
    assert plans[0].procedures == () and plans[0].translate == ()
