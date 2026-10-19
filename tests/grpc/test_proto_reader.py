"""P0-PARSE, P0-UNREAD and P0-VAL: the stdlib ``.proto`` reader of ``gocheck extract``
(``goextract_proto.go``) against protocompile (the bridge) and the hand-written expectations
of the torture set T (``tests/grpc/fixtures/protos``) and the notes app N."""

from __future__ import annotations

from typing import Any

from lattice.spec.grpc import Bridge
from tests.grpc import oracle
from tests.grpc.conftest import needs_go
from tests.grpc.test_extract_notes import with_interceptor_codes

pytestmark = needs_go


def _describe(extract: dict[str, Any], root: Any, proto_paths: list[Any]) -> dict[str, Any]:
    files = [root / p["file"] for p in extract["grpc"]["protos"]]
    with Bridge() as bridge:
        return bridge.describe(files, [root, *proto_paths])["grpc"]  # type: ignore[no-any-return]


def test_notes_protos_agree_with_protocompile(n_extract: dict[str, Any]) -> None:
    bridge = _describe(n_extract, oracle.NOTES, [oracle.NOTES / "proto"])
    assert oracle.compare_parse(n_extract["grpc"], bridge) == []


def test_torture_set_agrees_with_protocompile(t_extract: dict[str, Any]) -> None:
    bridge = _describe(t_extract, oracle.PROTOS, [oracle.PROTOS])
    assert oracle.compare_parse(t_extract["grpc"], bridge) == []


def test_torture_set_has_thirty_supported_constructs(t_extract: dict[str, Any]) -> None:
    g = t_extract["grpc"]
    constructs = {
        "scalar types": 15,
        "repeated": any(f["label"] == "repeated" for m in g["messages"] for f in m["fields"]),
        "optional": any(f["label"] == "optional" for m in g["messages"] for f in m["fields"]),
        "map": any(f["label"] == "map" for m in g["messages"] for f in m["fields"]),
        "oneof": any(f.get("oneof") for m in g["messages"] for f in m["fields"]),
        "nested message": any(m["name"].count(".") > 2 for m in g["messages"]),
        "nested enum": any(e["name"].count(".") > 2 for e in g["enums"]),
        "top enum": any(e["name"] == "t.v1.Color" for e in g["enums"]),
        "http get/put/post/delete/patch": {
            r["http"]["method"] for s in g["services"] for r in s["rpcs"] if r.get("http")
        }
        >= {"get", "put", "post", "delete", "patch"},
        "stream flags": any(r["server_streaming"] for s in g["services"] for r in s["rpcs"]),
    }
    assert all(constructs.values()), constructs
    # fifteen scalar fields + nine more kinds above + the rule families of the Rules message
    rules = [
        f
        for m in g["messages"]
        if m["name"] == "t.v1.Rules"
        for f in m["fields"]
        if f.get("bounds")
    ]
    assert len(rules) >= 14


def test_unsupported_constructs_are_unread_with_their_line(t_extract: dict[str, Any]) -> None:
    expected = oracle.load(oracle.PROTOS / "expected_unread.json")
    assert oracle.compare_unread(t_extract, expected) == []
    assert len(expected["unread"]) + len(expected["listed"]) == 10


def test_validation_bounds_are_exact(
    t_extract: dict[str, Any], n_extract: dict[str, Any], truth: dict[str, Any]
) -> None:
    assert (
        oracle.compare_bounds(t_extract, oracle.load(oracle.PROTOS / "expected_bounds.json")) == []
    )
    # includes N's four bounded fields
    assert oracle.compare_extract(n_extract, with_interceptor_codes(truth)) == []


def test_every_proto_construct_outside_the_grammar_is_unread_never_guessed(
    tmp_path: Any, front_end: Any
) -> None:
    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n")
    (tmp_path / "a.proto").write_text(
        'syntax = "proto3";\npackage a;\nmessage M {\n  string s = 1 [deprecated = true];\n'
        "  oneof o { int32 x = 2; }\n}\n"
        "service S {\n  rpc R(M) returns (Nope);\n}\n"
    )
    out = front_end.extract_json(tmp_path, ".")
    (rpc,) = out["grpc"]["services"][0]["rpcs"]
    assert rpc["output"] == "Nope"  # kept as written
    assert [u["what"] for u in out["unread"]] == ["proto_type_unresolved"]
