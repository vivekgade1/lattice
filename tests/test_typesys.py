import pytest

from lattice.typesys import UNKNOWN, Scalar, Schema, UnknownT, unify

S = Scalar


def test_unify_rules():
    assert unify(S.INT, S.INT) is S.INT
    assert unify(S.INT, S.FLOAT) is S.FLOAT
    assert unify(S.STR, S.INT) is None
    assert unify(S.BOOL, S.BOOL) is S.BOOL


def test_schema_ops():
    sch = Schema.from_pairs([("a", S.INT), ("b", S.STR)])
    assert sch.names() == ("a", "b")
    assert sch.get("b") is S.STR and sch.get("zz") is None
    assert "a" in sch and "zz" not in sch
    assert sch.extend([("c", S.BOOL)]).names() == ("a", "b", "c")
    assert sch.project(["b", "a"]).names() == ("b", "a")
    assert sch.renamed({"a": "x"}).names() == ("x", "b")
    assert sch.render() == "{a: Int, b: Str}"


def test_schema_rejects_duplicates():
    with pytest.raises(ValueError):
        Schema.from_pairs([("a", S.INT), ("a", S.STR)])


def test_unknown_is_singleton():
    assert UnknownT() is UNKNOWN
