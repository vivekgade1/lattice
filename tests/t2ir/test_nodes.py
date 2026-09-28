import dataclasses
import inspect
import re

from lattice.check.t2.types import BOOL, INT
from lattice.t2ir import nodes as t2ir_nodes
from lattice.t2ir.dump import dump
from lattice.t2ir.nodes import (
    Binary,
    Compare,
    Const,
    If,
    Procedure,
    Record,
    Return,
    T2Module,
    Tick,
    Var,
    While,
)


def _make_module(*, recursive: bool = False, locals_: dict | None = None) -> T2Module:
    body = (
        While(Compare((">",), (Var("n", INT), Const(0, INT)), BOOL),
              (Tick(), ), ()),
        If(Var("flag", BOOL), (Return(Binary("+", Var("n", INT), Const(1, INT), INT)),), ()),
        Return(Const(0, INT)),
    )
    locals_ = {"n": INT, "flag": BOOL} if locals_ is None else locals_
    return T2Module((Procedure("f", (("n", INT), ("flag", BOOL)), INT,
                               locals_, body, recursive),), (), (), {}, ())


def test_dump_is_deterministic_and_typed():
    assert dump(_make_module()) == (
        "procedure f(n: Int, flag: Bool) -> Int\n"
        "  recursive: False\n"
        "  locals: flag: Bool, n: Int\n"
        "  While\n"
        "    test: Compare(>) : Bool\n"
        "      Var n : Int\n"
        "      Const 0 : Int\n"
        "    body:\n"
        "      Tick\n"
        "  If\n"
        "    test: Var flag : Bool\n"
        "    body:\n"
        "      Return\n"
        "        Binary(+) : Int\n"
        "          Var n : Int\n"
        "          Const 1 : Int\n"
        "  Return\n"
        "    Const 0 : Int\n"
    )


def test_dump_differs_when_only_recursive_differs():
    assert dump(_make_module(recursive=False)) != dump(_make_module(recursive=True))


def test_dump_differs_when_only_locals_differs():
    base = dump(_make_module(locals_={"n": INT, "flag": BOOL}))
    extra_local = dump(_make_module(locals_={"n": INT, "flag": BOOL, "tmp": INT}))
    different_type = dump(_make_module(locals_={"n": BOOL, "flag": BOOL}))
    assert base != extra_local
    assert base != different_type


def test_dump_is_stable_regardless_of_imports_and_locals_insertion_order():
    forward = _make_module(locals_={"flag": BOOL, "n": INT})
    backward = _make_module(locals_={"n": INT, "flag": BOOL})
    assert dump(forward) == dump(backward)


def test_dump_differs_when_only_record_defaults_differs():
    rec_no_defaults = Record("R", (("x", INT),), {})
    rec_with_default = Record("R", (("x", INT),), {"x": 0})
    mod_no_defaults = T2Module((), (rec_no_defaults,), (), {}, ())
    mod_with_default = T2Module((), (rec_with_default,), (), {}, ())
    assert dump(mod_no_defaults) != dump(mod_with_default)


def test_no_node_field_holds_a_python_ast_object():
    """Every node in `lattice.t2ir.nodes` is language-neutral: no field's declared type
    may mention Python's `ast` module (e.g. `ast.AST`, `ast.expr`)."""
    node_classes = [
        obj for obj in vars(t2ir_nodes).values()
        if inspect.isclass(obj) and dataclasses.is_dataclass(obj)
    ]
    assert len(node_classes) >= 40  # sanity: we actually found the node set
    for cls in node_classes:
        for field in dataclasses.fields(cls):
            assert not re.search(r"\bast\b", field.type), (
                f"{cls.__name__}.{field.name}: {field.type!r} mentions the ast module"
            )
