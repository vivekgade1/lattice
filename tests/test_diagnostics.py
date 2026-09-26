import ast

from lattice.diagnostics import (
    Diagnostic,
    Severity,
    Span,
    has_errors,
    render_all,
    render_for_llm,
    sort_key,
    span_of,
)


def test_render_with_hint():
    d = Diagnostic(
        "L201", Severity.ERROR, Span(3, 5), "'x' is already bound (line 2)", "pick a new name"
    )
    assert d.render() == "3:5: error L201: 'x' is already bound (line 2) (hint: pick a new name)"


def test_render_without_hint():
    d = Diagnostic("L204", Severity.WARNING, Span(1, 1), "'x' is never used")
    assert d.render() == "1:1: warning L204: 'x' is never used"


def test_span_of_is_one_based():
    mod = ast.parse("a = 1\n  \nbb = c(d)\n")
    call = mod.body[1].value
    assert span_of(call) == Span(3, 6)
    assert span_of(mod) == Span(1, 1)


def test_has_errors():
    w = Diagnostic("L204", Severity.WARNING, Span(1, 1), "w")
    e = Diagnostic("L201", Severity.ERROR, Span(1, 1), "e")
    assert not has_errors([w])
    assert has_errors([w, e])


def test_render_all_is_sorted():
    a = Diagnostic("L201", Severity.ERROR, Span(3, 1), "a")
    b = Diagnostic("L101", Severity.ERROR, Span(1, 2), "b")
    assert render_all([a, b]) == "1:2: error L101: b\n3:1: error L201: a"
    assert sort_key(a) == (3, 1, "L201")


def test_render_for_llm_errors_first():
    w = Diagnostic("L204", Severity.WARNING, Span(1, 1), "'x' is never used")
    e = Diagnostic("L201", Severity.ERROR, Span(3, 5), "'x' is already bound", "use a new name")
    out = render_for_llm([w, e]).splitlines()
    assert out[0] == "- L201 at line 3, col 5: 'x' is already bound. Fix: use a new name"
    assert out[1] == "- L204 at line 1, col 1: 'x' is never used"
