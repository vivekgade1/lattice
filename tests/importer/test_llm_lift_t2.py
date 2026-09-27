"""R3 emits T2 ``@procedure``s (importer I5b, spec 1c §7.5) -- with a fake LLM only.

The template's ``read_item`` reaches the loop subtly wrong (see test_llm_lift); R1 and
R2 answer wrong, and R3 is the T2 carve-out: the LLM writes the route as T0 plus
``@procedure``s. The candidate passes the checker and ``verify()`` against the original
like every other; the fallback order is procedure, then a typed native only for logic
outside the T2 subset, then R4. The report records the carve kind and the tier."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lattice.importer.carve import (
    CarvedProcedure,
    CarveNative,
    CarvePlan,
    HelperSource,
    ProcedureCarveOut,
)
from lattice.importer.lift import ImportResult, check_program
from lattice.importer.llm_lift import CloseResult, CorpusLog, LoopConfig, close_gap
from lattice.importer.report import load_report, render_markdown, report_data, write_import
from tests.importer.test_llm_lift import (  # noqa: F401 -- fixtures
    PACKAGE,
    REPO_ROOT,
    FakeLLM,
    _buggy,
    _corpus,
    _emitted,
    answer,
    wrong,
)

HEAD = """@endpoint("GET", "/api/v1/items/{id}", auth=Customer, response="ItemPublic")
def read_item(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    checked = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
"""
READABLE = '''@procedure
def readable(item: Row[Item], caller: Caller) -> Bool:
    """The original's check: superusers read every item, others their own."""
    return caller.is_admin or item.owner_id == caller.id


'''
FORBIDDEN = 'else_=Forbidden("Not enough permissions")'
TAIL = f"""    allowed = require(checked, readable(row(), user), {FORBIDDEN})
    return allowed
"""
PROCEDURE = READABLE + HEAD + TAIL
# subtly wrong: forgets the superuser (admins get 403 on other users' items)
WRONG_PROCEDURE = PROCEDURE.replace("caller.is_admin or ", "")
KEEP = CarvedProcedure(
    "keep",
    "pkg.helpers:keep",
    "@procedure\ndef keep(title: Str) -> Bool:\n    return len(title) >= 0\n",
)
KEEP_CALL = (
    HEAD
    + f"""    allowed = require(checked, keep(col("title")), {FORBIDDEN})
    return allowed
"""
)
NATIVE = CarveNative("carve_title", "string:capwords", (("text", "STR"),), "STR")
NATIVE_CALL = (
    HEAD
    + f"""    allowed = require(
        checked,
        native.carve_title(col("title")) == native.carve_title(col("title")),
        {FORBIDDEN},
    )
    return allowed
"""
)


@pytest.fixture(scope="module")
def buggy() -> ImportResult:
    result = _buggy({"read_item"})
    assert result.result("read_item").status == "lifted-unverified"
    return result


class Plans:
    """A fixed plan list (the template's read_item reaches no helper)."""

    def __init__(self, *plans: CarvePlan, why: str = "") -> None:
        self._plans, self.why, self.calls = plans, why, 0

    def plans(self, graph, route, known, profile):
        self.calls += 1
        return self._plans, self.why


def _run(result: ImportResult, gen: FakeLLM, tmp_path: Path, **kw) -> CloseResult:
    corpus = CorpusLog(tmp_path / "imports.jsonl", repo="template")
    return close_gap(result, gen, config=LoopConfig(method="M1"), corpus=corpus, **kw)


def _five_wrong(buggy: ImportResult) -> list[str]:
    return [wrong(buggy)] * 5  # R1 x3, R2 x2


# ---- R3 emits a verified procedure ------------------------------------------------------------


def test_r3_emits_a_verified_procedure_carve_out(buggy, tmp_path):
    gen = FakeLLM(*_five_wrong(buggy), answer(PROCEDURE))
    close = _run(buggy, gen, tmp_path)  # the default strategy: ProcedureCarveOut
    o = close.outcome("read_item")
    assert (o.status, o.rung, o.carve, o.tier) == ("verified", "R3", "procedure", "T2")
    assert [(a.rung, a.kind) for a in o.attempts][-1] == ("R3", "carve-procedure")
    assert [p.name for p in o.procedures] == ["readable"]
    assert o.procedures[0].lines == 4 and o.procedures[0].origin is None  # LLM-translated
    assert o.natives == () and close.carve_natives == ()
    # the prompt asks for T0 plus procedures within the T2 subset
    prompt = gen.calls[-1][1][0]["content"]
    assert "@procedure" in prompt and "T2 subset" in prompt and "math" in prompt
    # the emitted router program holds the procedure and checks against the profile
    program = close.programs["items"]
    assert "@procedure\ndef readable(" in program
    plans, errors = check_program(program, buggy.lattice_profile)
    assert plans is not None, errors
    assert _emitted(close) == {"read_item", "readable"}
    route = [r for r in _corpus(tmp_path) if r["type"] == "route"][0]
    assert (route["tier"], route["carve"], route["procedures"]) == ("T2", "procedure", 1)


def test_a_subtly_wrong_procedure_is_never_emitted(buggy, tmp_path):
    """The checker accepts the wrong procedure; verify() refuses it (an admin reading
    another user's item: 200 in the original, 403 here). No native plan exists (the
    route has no helper outside the T2 subset), so the route is an island."""
    gen = FakeLLM(*_five_wrong(buggy), answer(WRONG_PROCEDURE))
    close = _run(buggy, gen, tmp_path)
    o = close.outcome("read_item")
    assert (o.status, o.rung, o.carve, o.tier) == ("island", "R4", "none", "island")
    assert o.source is None and o.procedures == ()
    last = o.attempts[-1]
    assert (last.rung, last.kind, last.outcome) == ("R3", "carve-procedure", "mismatch")
    assert "admin" in last.reason
    assert [a.kind for a in o.attempts].count("carve-native") == 0
    assert close.programs == {} and _emitted(close) == set()


# ---- deterministic procedures and the fallback order -------------------------------------------


def test_a_mechanically_carved_procedure_is_added_when_called(buggy, tmp_path):
    strategy = Plans(CarvePlan("procedure", procedures=(KEEP,), translate=(
        HelperSource("pkg.helpers:fmt", "def fmt(x):\n    return x\n", "untyped"),)))  # fmt: skip
    gen = FakeLLM(*_five_wrong(buggy), answer(KEEP_CALL))
    close = _run(buggy, gen, tmp_path, carve=strategy)
    o = close.outcome("read_item")
    assert (o.rung, o.carve, o.tier) == ("R3", "procedure", "T2")
    assert [(p.name, p.origin) for p in o.procedures] == [("keep", "pkg.helpers:keep")]
    assert o.source is not None and o.source.startswith(KEEP.source)
    prompt = gen.calls[-1][1][0]["content"]
    assert KEEP.source.strip() in prompt and "carved mechanically" in prompt
    assert "pkg.helpers:fmt" in prompt and "untyped" in prompt


def test_the_native_fallback_follows_a_failed_procedure(buggy, tmp_path):
    strategy = Plans(
        CarvePlan("procedure"),
        CarvePlan("native", natives=(NATIVE,), why="the logic needs code outside the T2 "
                  "subset (pkg.helpers:title: uses the module 'unidecode')"),
    )  # fmt: skip
    gen = FakeLLM(*_five_wrong(buggy), answer(WRONG_PROCEDURE), answer(NATIVE_CALL))
    close = _run(buggy, gen, tmp_path, carve=strategy)
    o = close.outcome("read_item")
    assert [(a.rung, a.kind, a.outcome) for a in o.attempts[-2:]] == [
        ("R3", "carve-procedure", "mismatch"),
        ("R3", "carve-native", "verified"),
    ]
    assert (o.status, o.rung, o.carve, o.tier) == ("verified", "R3", "native", "T3")
    assert o.natives == (NATIVE,) and close.carve_natives == (NATIVE,)
    assert "carve_title" in close.profile_source
    system, messages = gen.calls[-1]
    assert "native.carve_title(text: Str) -> Str" in messages[0]["content"]
    assert "unidecode" in messages[0]["content"]


def test_a_procedure_plan_that_verifies_needs_no_native(buggy, tmp_path):
    strategy = Plans(CarvePlan("procedure"), CarvePlan("native", natives=(NATIVE,)))
    gen = FakeLLM(*_five_wrong(buggy), answer(PROCEDURE))  # no answer left for a native
    o = _run(buggy, gen, tmp_path, carve=strategy).outcome("read_item")
    assert (o.rung, o.carve) == ("R3", "procedure")
    assert len(gen.calls) == 6


def test_procedure_carving_through_the_default_strategy_on_a_synthetic_graph(tmp_path):
    from lattice.importer.graph import build_graph
    from tests.importer.test_procedure_carve import HELPERS, PROFILE, ROUTES, SHAPES

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for name, text in (("__init__", ""), ("helpers", HELPERS), ("shapes", SHAPES),
                       ("routes", ROUTES)):  # fmt: skip
        (pkg / f"{name}.py").write_text(text)
    plans, _ = ProcedureCarveOut().plans(build_graph(tmp_path, "pkg"), "pkg.routes:pure", (),
                                         PROFILE)  # fmt: skip
    assert [p.kind for p in plans] == ["procedure"]  # no native: nothing outside the subset


# ---- the report -----------------------------------------------------------------------------


def test_the_report_records_tier_and_carve(buggy, tmp_path):
    gen = FakeLLM(*_five_wrong(buggy), answer(PROCEDURE))
    close = _run(buggy, gen, tmp_path)
    data = report_data(close)
    (route,) = data["routes"]
    assert route["tier"] == "T2"
    assert route["carve_kind"] == "procedure"
    assert route["procedure_count"] == 1 and route["procedure_lines"] == 4
    assert route["procedures"] == [{"name": "readable", "lines": 4, "origin": None}]
    tiers = data["coverage"]["tiers"]
    assert tiers == {"T0": 0, "T1": 0, "T2": 1, "T3": 0, "island": 0, "needs-llm": 0}
    assert data["coverage"]["carve_kinds"] == {"procedure": 1, "native": 0}
    md = render_markdown(data)
    assert "tier mix: T0 0, T1 0, T2 1, T3 0" in md
    assert "| T2 | procedure (1, 4 lines) |" in md
    out = write_import(close, tmp_path / "lattice_import", repo_root=REPO_ROOT, package=PACKAGE)
    assert load_report(out)["routes"][0]["tier"] == "T2"
    program = (out / "routes" / "items.lattice.py").read_text()
    assert any(
        isinstance(n, ast.FunctionDef) and n.name == "readable" for n in ast.parse(program).body
    )


def test_an_island_and_a_lifter_route_have_their_tiers(buggy, tmp_path):
    gen = FakeLLM(*_five_wrong(buggy), answer(WRONG_PROCEDURE))
    data = report_data(_run(buggy, gen, tmp_path))
    (route,) = data["routes"]
    assert (route["tier"], route["carve_kind"], route["procedure_count"]) == ("island", "none", 0)
    assert data["coverage"]["tiers"]["island"] == 1


def test_routes_sharing_a_procedure_join_into_one_program(buggy):
    from lattice.importer.llm_lift import _router_source

    other = KEEP_CALL.replace("def read_item", "def read_item_again").replace(
        "/api/v1/items/{id}", "/api/v1/items/{id}/again"
    )
    sources = [KEEP.source + "\n\n" + KEEP_CALL, KEEP.source + "\n\n" + other]
    program = _router_source(sources, "items", ["read_item", "read_item_again"])
    assert program.count("def keep(") == 1
    plans, errors = check_program(program, buggy.lattice_profile)
    assert plans is not None, errors
    assert {p.name for p in plans} == {"read_item", "read_item_again"}
