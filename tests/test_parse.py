from lattice.diagnostics import span_of
from lattice.parse import HEADER, parse

BODY = "x = load('a.csv')\ny = where(x, col('a') > 1)\n"


def test_valid_header():
    r = parse(HEADER + "\n" + BODY)
    assert r.diagnostics == ()
    assert r.module is not None


def test_missing_header():
    r = parse(BODY)
    assert [d.code for d in r.diagnostics] == ["L001"]
    assert r.diagnostics[0].span.line == 1 and r.diagnostics[0].span.col == 1
    assert r.module is not None


def test_wrong_version():
    r = parse("# lattice: v2\n" + BODY)
    assert [d.code for d in r.diagnostics] == ["L001"]


def test_header_trailing_whitespace_ok():
    assert parse(HEADER + "   \n" + BODY).diagnostics == ()


def test_syntax_error():
    r = parse(HEADER + "\nx = load(\n\ny = 1 +\n")
    assert r.module is None
    codes = [d.code for d in r.diagnostics]
    assert codes == ["L002"]
    assert r.diagnostics[0].span.line >= 2


def test_syntax_error_line():
    r = parse(HEADER + "\nx = 1\ny = = 2\n")
    assert r.module is None
    assert r.diagnostics[0].code == "L002"
    assert r.diagnostics[0].span.line == 3


def test_crlf_and_bom():
    lf = HEADER + "\n" + BODY
    crlf = "﻿" + lf.replace("\n", "\r\n")
    r1, r2 = parse(lf), parse(crlf)
    assert r2.diagnostics == ()
    assert r2.source == lf
    assert span_of(r2.module.body[1]) == span_of(r1.module.body[1])
    assert span_of(r2.module.body[1].value) == span_of(r1.module.body[1].value)


def test_bare_cr_newlines():
    r = parse((HEADER + "\n" + BODY).replace("\n", "\r"))
    assert r.diagnostics == ()


def test_v1_1_header_is_accepted():
    """`# lattice: v1.1` is accepted as well as `v1` (both run under the v1.1 rules)."""
    r = parse("# lattice: v1.1\n" + BODY)
    assert r.diagnostics == ()
    assert r.module is not None
    assert parse("# lattice: v1.1  \n" + BODY).diagnostics == ()


def test_other_versions_are_refused():
    for first in ("# lattice: v1.2", "# lattice: v1.10", "# lattice: v1.1.0", "# lattice:v1.1"):
        assert [d.code for d in parse(first + "\n" + BODY).diagnostics] == ["L001"], first


def test_a_v1_1_program_checks_like_its_v1_twin_on_every_target():
    from lattice.check import check_source
    from tests.api import edges

    v11 = edges.PROGRAM.replace(HEADER, "# lattice: v1.1", 1)
    assert v11 != edges.PROGRAM
    for target in ("python", "go"):
        a = check_source(edges.PROGRAM, edges.PROFILE, target=target)
        b = check_source(v11, edges.PROFILE, target=target)
        assert a.ok and b.ok, target
        assert [d.code for d in b.diagnostics] == [d.code for d in a.diagnostics]


def test_join_sources_keeps_a_v1_1_header():
    from lattice.emit.thin import join_sources

    assert join_sources([HEADER + "\nx = 1\n", HEADER + "\ny = 2\n"]).startswith(HEADER + "\n")
    joined = join_sources([HEADER + "\nx = 1\n", "# lattice: v1.1\ny = 2\n"])
    assert joined == "# lattice: v1.1\nx = 1\n\n\ny = 2\n"
    assert parse(joined).diagnostics == ()
