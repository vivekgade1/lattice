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
    Return,
    T2Module,
    Tick,
    Var,
    While,
)


def test_dump_is_deterministic_and_typed():
    body = (
        While(Compare((">",), (Var("n", INT), Const(0, INT)), BOOL),
              (Tick(), ), ()),
        If(Var("flag", BOOL), (Return(Binary("+", Var("n", INT), Const(1, INT), INT)),), ()),
        Return(Const(0, INT)),
    )
    mod = T2Module((Procedure("f", (("n", INT), ("flag", BOOL)), INT,
                              {"n": INT, "flag": BOOL}, body, False),), (), (), {}, ())
    assert dump(mod) == (
        "procedure f(n: Int, flag: Bool) -> Int\n"
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
