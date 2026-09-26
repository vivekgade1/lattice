from lattice.catalog import (
    FN_ALIASES,
    FN_SIGS,
    IMPORTABLE,
    OP_ALIASES,
    OP_SHAPES,
    TABLE_OPS,
    TERMINAL_OPS,
)


def test_op_aliases_point_to_ops():
    assert all(v in TABLE_OPS for v in OP_ALIASES.values())


def test_fn_aliases_point_to_fns():
    assert all(v in FN_SIGS for v in FN_ALIASES.values())


def test_importable_contains_step():
    assert "step" in IMPORTABLE
    assert TABLE_OPS <= IMPORTABLE


def test_shapes_consistent():
    assert TERMINAL_OPS <= TABLE_OPS
    for shape in OP_SHAPES.values():
        assert shape.tables <= len(shape.positional)
        assert not (shape.required_kw & shape.optional_kw)
    assert OP_SHAPES["where"].positional == ("table", "predicate")
