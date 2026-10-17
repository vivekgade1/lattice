"""P0-SPEC: the facts of the ``Rpc`` step form (Spike I, D3) -- hash and equality, the
validate codes S026-S029 (two crafted specs each), lint L013 (two pairs), the review step line
and the access matrix of a mixed spec -- and that a spec without an ``Rpc`` is unchanged."""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from typing import Any

import pytest

from lattice.spec import Contract, Rpc, Scenario, Spec, Step, validate
from lattice.spec.lint import lint, rpc_conflicts
from lattice.spec.model import RPC_CODES
from lattice.spec.review import _build_access, _step_line
from lattice.spec.rules import Trace
from tests.grpc import oracle
from tests.grpc.fixtures.notes import spec as dev
from tests.grpc.fixtures.notes.harness import PROTOS

NOTES, ACC = "notes.v1.Notes", "notes.v1.Accounts"
T_UNSUPPORTED = str(oracle.PROTOS / "unsupported.proto")


def spec_with(*steps: Any, protos: tuple[str, ...] = PROTOS, name: str = "s", **kw: Any) -> Spec:
    return replace(dev.SPEC, scenarios=(Scenario(name, steps=tuple(steps)),), protos=protos, **kw)


def codes(spec: Spec) -> list[str]:
    return [p.code for p in validate(spec)]


def test_rpc_is_a_frozen_value() -> None:
    a = Rpc(NOTES, "GetNote", "ann", {"id": 1}, expect_code="NOT_FOUND")
    b = Rpc(NOTES, "GetNote", "ann", {"id": 1}, expect_code="NOT_FOUND")
    assert a == b and hash(a) == hash(b) and a != replace(a, expect_code="OK")
    assert hash(a) == hash(replace(a, message={"id": 2}))  # payloads take no part in the hash
    assert a != replace(a, message={"id": 2})  # but they do in equality
    assert a.endpoint == "RPC notes.v1.Notes/GetNote"
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.method = "x"  # type: ignore[misc]
    assert Rpc(NOTES, "ListNotes").expect_code == "OK" and Rpc(NOTES, "ListNotes").user is None
    sc = Scenario("x", steps=[Step("GET", "/a"), a])
    assert isinstance(sc.steps, tuple) and hash(sc) == hash(Scenario("x", steps=sc.steps))


def test_a_spec_without_rpcs_is_unchanged() -> None:
    assert Spec(dev.PROFILE).protos == ()
    assert validate(replace(dev.SPEC, protos=(), scenarios=())) == []
    assert Rpc.__dataclass_fields__.keys() >= {"service", "method", "expect_code"}


def test_the_dev_spec_validates() -> None:
    assert validate(dev.SPEC) == []


@pytest.mark.parametrize(
    ("steps", "protos", "want"),
    [
        ((Rpc("notes.v1.Nope", "GetNote"),), PROTOS, "S026"),
        ((Rpc(NOTES, "NoSuchRpc"),), PROTOS, "S026"),
        ((Rpc(NOTES, "GetNote", expect_code="NOT_THERE"),), PROTOS, "S027"),
        ((Rpc(NOTES, "GetNote", expect_code="not_found"),), PROTOS, "S027"),
        ((Rpc(NOTES, "WatchNotes"),), PROTOS, "S028"),
        ((Rpc("t.v1.OddSvc", "Up"),), (T_UNSUPPORTED,), "S028"),
    ],
)
def test_validate_rpc_codes(steps: tuple[Any, ...], protos: tuple[str, ...], want: str) -> None:
    got = [p for p in validate(spec_with(*steps, protos=protos)) if p.code == want]
    assert len(got) == 1, [p.render() for p in validate(spec_with(*steps, protos=protos))]


def test_validate_s029_contract_and_program_on_an_rpc_endpoint() -> None:
    contract = Contract("RPC", "/notes.v1.Notes/GetNote", auth="Authenticated")
    assert codes(spec_with(Rpc(NOTES, "GetNote"), contracts=(contract,))) == ["S029"]
    program = '@endpoint("RPC", "/notes.v1.Notes/GetNote")\ndef get_note(): ...\n'
    assert codes(spec_with(Rpc(NOTES, "GetNote"), programs={"p": program})) == ["S029"]


def test_validate_rpc_other_checks() -> None:
    assert codes(spec_with(Rpc(NOTES, "GetNote", "nobody"))) == ["S011"]
    assert codes(spec_with(Rpc(NOTES, "GetNote", expect_db={"Nope": {"count": 1}}))) == ["S002"]
    # no protos named: the contract is not checked (S026 only when protos is not empty)
    assert codes(spec_with(Rpc("anything", "Goes"), protos=())) == []
    assert codes(spec_with(Rpc(NOTES, "GetNote"), protos=("tests/missing.proto",))) == ["S026"]


def test_l013_conflicting_rpc_expectations() -> None:
    a = Scenario("a", steps=(Rpc(NOTES, "GetNote", "ann", {"id": 1}, expect_code="NOT_FOUND"),))
    b = Scenario("b", steps=(Rpc(NOTES, "GetNote", "ann", {"id": 1}, expect_code="OK"),))
    found = rpc_conflicts(replace(dev.SPEC, scenarios=(a, b)))
    assert [(c.scenario_a, c.scenario_b, c.code_a, c.code_b) for c in found] == [
        ("a", "b", "NOT_FOUND", "OK")
    ]
    # an access answer against OK conflicts whatever the messages
    c = Scenario(
        "c", steps=(Rpc(NOTES, "ListNotes", None, {"state": "x"}, expect_code="UNAUTHENTICATED"),)
    )
    d = Scenario("d", steps=(Rpc(NOTES, "ListNotes", None, {}, expect_code="OK"),))
    assert [
        (x.scenario_a, x.scenario_b) for x in rpc_conflicts(replace(dev.SPEC, scenarios=(c, d)))
    ] == [("c", "d")]
    findings = lint(replace(dev.SPEC, scenarios=(a, b)), Trace(source="", rules=[]))
    assert [f.code for f in findings if f.code == "L013"] == ["L013"]


def test_l013_stays_quiet_where_it_should() -> None:
    other = Scenario("b", steps=(Rpc(NOTES, "GetNote", "ann", {"id": 2}, expect_code="OK"),))
    one = Scenario("a", steps=(Rpc(NOTES, "GetNote", "ann", {"id": 1}, expect_code="NOT_FOUND"),))
    assert rpc_conflicts(replace(dev.SPEC, scenarios=(one, other))) == []  # another message
    after_ok = Scenario(
        "c",
        steps=(
            Rpc(NOTES, "CreateNote", "ann", {"title": "x"}),
            Rpc(NOTES, "GetNote", "ann", {"id": 1}, expect_code="OK"),
        ),
    )
    assert rpc_conflicts(replace(dev.SPEC, scenarios=(one, after_ok))) == []  # an OK rpc ran first
    given_a = replace(one, given={"User": [{"id": 9}]})
    same_call = Scenario("d", steps=(Rpc(NOTES, "GetNote", "ann", {"id": 1}, expect_code="OK"),))
    assert rpc_conflicts(replace(dev.SPEC, scenarios=(given_a, same_call))) == []  # another setup


def test_review_step_line_and_access_matrix() -> None:
    step = Rpc(NOTES, "GetNote", "ann", {"id": 1}, expect_code="NOT_FOUND")
    assert _step_line(step) == "RPC notes.v1.Notes/GetNote as ann -> NOT_FOUND"
    mixed = spec_with(
        Step("GET", "/v1/notes/1", "ann"),
        step,
        Rpc(NOTES, "GetNote", None, {"id": 1}, expect_code="UNAUTHENTICATED"),
        Rpc(NOTES, "GetNote", "ann", {"id": 2}),
        Rpc(ACC, "Login", None, {"name": "ann", "password": "x"}),
    )
    groups = {g.prefix: g for g in _build_access(mixed)}
    assert list(groups) == ["v1", NOTES, ACC]  # HTTP groups, then one per service, first seen first
    g = groups[NOTES]
    assert g.columns == ("RPC notes.v1.Notes/GetNote",)
    assert g.rows == ("ann", "ben", "no token")
    col = g.columns[0]
    assert g.cells["ann", col] == "OK/NOT_FOUND"  # codes in google.rpc order, joined by "/"
    assert g.cells["no token", col] == "UNAUTHENTICATED" and g.cells["ben", col] == "·"
    assert groups["v1"].cells["ann", "GET /v1/notes/{}"] == "200"


def test_the_access_matrix_of_an_http_spec_has_no_rpc_group() -> None:
    http = replace(
        dev.SPEC, scenarios=(Scenario("h", steps=(Step("GET", "/a/1", "ann"),)),), protos=()
    )
    assert [g.prefix for g in _build_access(http)] == ["a"]


def test_rpc_codes_are_google_rpc_codes() -> None:
    assert RPC_CODES[0] == "OK" and RPC_CODES[16] == "UNAUTHENTICATED" and len(RPC_CODES) == 17
