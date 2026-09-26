import ast

from lattice.check.context import CheckContext
from lattice.diagnostics import Severity, Span
from lattice.typesys import UNKNOWN


def test_error_from_node(profile):
    ctx = CheckContext(profile)
    node = ast.parse("a = 1\nbb = 2\n").body[1]
    ctx.error("L201", node, "msg", "hint")
    (d,) = ctx.diagnostics
    assert (d.code, d.severity, d.span, d.message, d.hint) == (
        "L201",
        Severity.ERROR,
        Span(2, 1),
        "msg",
        "hint",
    )
    assert ctx.error_count() == 1


def test_warn_from_span(profile):
    ctx = CheckContext(profile)
    ctx.warn("L204", Span(4, 7), "unused")
    (d,) = ctx.diagnostics
    assert d.severity is Severity.WARNING and d.span == Span(4, 7) and d.hint is None
    assert ctx.error_count() == 0


def test_set_type(profile):
    ctx = CheckContext(profile)
    node = ast.Name("x")
    assert ctx.set_type(node, UNKNOWN) is UNKNOWN
    assert ctx.types[id(node)] is UNKNOWN


MINIMAL = (
    "# lattice: v1\n"
    "from lattice.ops import load, write\n"
    "from lattice.schemas import Orders\n"
    'x = load("orders.csv", schema=Orders)\n'
    'write(x, "out.parquet")\n'
)


def test_check_source_minimal_ok(profile):
    from lattice.check import check_source

    r = check_source(MINIMAL, profile)
    assert r.diagnostics == ()
    assert r.ok
    assert r.program is not None and r.program.source == MINIMAL


def test_check_source_syntax_error(profile):
    from lattice.check import check_source

    r = check_source("# lattice: v1\nx = (\n", profile)
    assert not r.ok
    assert [d.code for d in r.diagnostics] == ["L002"]


def test_check_source_missing_header_not_ok(profile):
    from lattice.check import check_source

    r = check_source(MINIMAL.split("\n", 1)[1], profile)
    assert not r.ok
    assert [d.code for d in r.diagnostics] == ["L001"]
