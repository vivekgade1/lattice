"""P0-EXT, P0-MODEL, P0-EMIT and the HTTP side of P0-BC on the notes app N: the extract against
``truth.json`` (written by hand before the extractor, hash frozen), the ``RepoModel``, the
candidate spec, and the promise that an HTTP-only module's extract keeps its bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from lattice.importer.frontends import select
from lattice.spec import validate
from tests.grpc import oracle
from tests.grpc.conftest import needs_go
from tests.grpc.fixtures.notes import spec as dev

pytestmark = needs_go


def test_extract_matches_the_truth(n_extract: dict[str, Any], truth: dict[str, Any]) -> None:
    assert oracle.compare_extract(n_extract, truth) == []


def test_truth_counts_follow_the_fixed_shape(truth: dict[str, Any]) -> None:
    c = truth["counts"]
    assert (c["unary"], c["implemented"], c["unimplemented"], c["streaming"]) == (11, 10, 1, 1)
    assert c["model_endpoints"] == 11 and c["routes_if_bindings_were_routes"] == 13


def test_extract_is_deterministic(front_end: Any, n_extract: dict[str, Any]) -> None:
    again = front_end.extract_json(oracle.FIXTURES, "notes")
    assert json.dumps(again, sort_keys=True) == json.dumps(n_extract, sort_keys=True)
    text = json.dumps(n_extract)
    assert "/home/" not in text and "/tmp/" not in text


def test_model_lists_rpcs_as_routes_and_bindings_on_the_rpc(front_end: Any) -> None:
    model = front_end.extract(oracle.FIXTURES, "notes")
    rpcs = [r for r in model.routes if r.transport == "grpc"]
    assert len(model.routes) == len(rpcs) == 11  # not 13: the two gateway bindings are no routes
    assert all(r.method == "RPC" and r.path.startswith("/notes.v1.") for r in rpcs)
    by = {r.path: r for r in rpcs}
    assert by["/notes.v1.Notes/CreateNote"].http_bindings == (("POST", "/v1/notes"),)
    assert by["/notes.v1.Notes/GetNote"].http_bindings == (("GET", "/v1/notes/{id}"),)
    assert sum(len(r.http_bindings) for r in rpcs) == 2
    assert by["/notes.v1.Notes/ShareNote"].rpc.state == "unimplemented"
    assert [e.path for e in model.excluded_routes] == ["/notes.v1.Notes/WatchNotes"]
    cr = by["/notes.v1.Notes/CreateNote"]
    assert {(x.code, x.kind) for x in cr.raises} == {
        ("INVALID_ARGUMENT", "grpc_status"),
        ("INTERNAL", "grpc_status"),
    }


def test_http_routes_serialise_without_the_grpc_fields(front_end: Any) -> None:
    from lattice.importer.frontends.go_gin import GoGinFrontEnd

    model = GoGinFrontEnd().extract(oracle.GIN_CLIENT_ONLY.parent, "gin_client_only")
    (route,) = model.routes
    d = model.to_dict()["routes"][0]
    assert route.transport == "http" and route.rpc is None
    assert not {"transport", "rpc", "http_bindings"} & set(d)
    assert "code" not in json.dumps(d["raises"])


def test_select_keeps_gin_for_a_client_only_module_and_finds_grpc_go(front_end: Any) -> None:
    assert select(None, oracle.FIXTURES, "gin_client_only").name == "gin"
    assert select(None, oracle.FIXTURES, "notes").name == "grpc-go"
    assert select("grpc-go").name == "grpc-go"


def test_an_http_only_extract_has_no_grpc_key() -> None:
    out = json.loads(
        __import__("subprocess")
        .run(
            [
                str(__import__("lattice.gocheck", fromlist=["build_gocheck"]).build_gocheck()),
                "extract",
            ],
            input=json.dumps({"root": str(oracle.GIN_CLIENT_ONLY)}),
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout
    )
    assert "grpc" not in out and out["routes"]


def test_candidate_spec_has_an_rpc_per_implemented_rpc(front_end: Any) -> None:
    from lattice.importer import emit_spec

    model = front_end.extract(oracle.FIXTURES, "notes")
    spec = emit_spec.build_spec(model, dev.PROFILE, users=dev.USERS)
    assert validate(spec, oracle.NOTES) == []
    steps = [(s.service, s.method) for sc in spec.scenarios for s in sc.steps]
    implemented = {
        (r.rpc.service, r.rpc.method) for r in model.routes if r.rpc.state == "implemented"
    }
    assert set(steps) == implemented and len(steps) == 10
    assert ("notes.v1.Notes", "ShareNote") not in steps
    assert not any(m == "WatchNotes" for _, m in steps)
    assert spec.contracts == ()  # an rpc has no contract: S029


def test_candidate_spec_module_round_trips(front_end: Any, tmp_path: Path) -> None:
    from lattice.importer import emit_spec

    model = front_end.extract(oracle.FIXTURES, "notes")
    spec = emit_spec.build_spec(model, dev.PROFILE, users=dev.USERS)
    text = emit_spec.spec_source(spec, package="notes")
    assert "Rpc(" in text and "PROTOS" in text
    assert emit_spec.spec_source(spec, package="notes") == text
    plain = emit_spec.spec_source(
        emit_spec.build_spec(front_end.extract(oracle.FIXTURES, "gin_client_only"), dev.PROFILE),
        package="x",
    )
    assert "Rpc" not in plain and "SCENARIOS" not in plain and "PROTOS" not in plain


def test_truth_file_is_the_frozen_one(truth: dict[str, Any]) -> None:
    """The truth was hashed before the extractor existed (freeze-truth.sha256 of the build)."""
    raw = (oracle.NOTES / "truth.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "debbd0b4e61297ca94ab1cbd0d7835efc99d67c4b75d6012243ddeb07b22f490"
    )
