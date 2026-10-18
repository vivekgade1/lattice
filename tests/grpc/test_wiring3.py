"""Round 3, D1: registration reachability (R1-R6). The dev fixture ``wiring`` has one service per
way a grpc-go server can be wired plus the negatives (a dead ``Register`` function, one referenced
only from ``_test.go``, one declared and never registered, one referenced but not reachable); its
``truth.json`` was written and hashed before any round-3 extractor code. A few small modules in
``tmp_path`` pin the rules the fixture cannot (R4 with two concrete types, R6 for a variable)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

WIRING = oracle.FIXTURES / "wiring"
TRUTH_SHA = "138cb5e4bb227aa58de1e51d5e825fd6a8ccbb359b5d929e65c13f97b7d2fbb2"

pytestmark = needs_go


@pytest.fixture(scope="module")
def wiring(front_end: Any) -> dict[str, Any]:
    return front_end.extract_json(oracle.FIXTURES, "wiring")  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def truth() -> Any:
    return oracle.load(WIRING / "truth.json")


def rpcs(extract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f"{s['name']}/{r['name']}": r for s in extract["grpc"]["services"] for r in s["rpcs"]}


def test_the_truth_was_hashed_before_the_extractor_changed() -> None:
    assert hashlib.sha256((WIRING / "truth.json").read_bytes()).hexdigest() == TRUTH_SHA


ROUND4_UNREAD = ("Dead", "Gone", "Never")


@pytest.mark.parametrize(
    "service",
    [
        "Alpha",
        "Beta",
        "Gamma",
        "Delta",
        "Epsilon",
        "Zeta",
        "Eta",
        "Dead",
        "Gone",
        "Never",
        "Opaque",
    ],
)
def test_every_state_equals_the_truth(wiring: dict[str, Any], truth: Any, service: str) -> None:
    got = rpcs(wiring)
    want = {k: v["state"] for k, v in truth["rpcs"].items() if f".{service}/" in k}
    assert want, service
    if service in ROUND4_UNREAD:
        # round 4 (D1.3): while a registration call in the module is unresolved (Opaque),
        # a never-registered service reads `unread`, never a confident `not_registered`
        want = {k: "unread" for k in want}
    assert {k: got[k]["state"] for k in want} == want


def test_r1_a_literal_in_a_package_level_slice_is_reached(wiring: dict[str, Any]) -> None:
    reg = {r["service"]: r for r in wiring["grpc"]["registrations"]}
    assert reg["wiring.v1.Alpha"]["file"].endswith("internal/serve/mounts.go")
    assert reg["wiring.v1.Alpha"]["impl"] == "newAlpha(db)"
    # the helper named in the same slice (R1) and the one it calls (R3)
    assert {"wiring.v1.Beta", "wiring.v1.Gamma"} <= set(reg)


def test_r1_a_function_named_in_a_package_level_map_is_reached(wiring: dict[str, Any]) -> None:
    assert rpcs(wiring)["wiring.v1.Delta/Get"]["state"] == "implemented"


def test_r3_a_helper_on_a_registrar_parameter_or_field_counts(wiring: dict[str, Any]) -> None:
    got = rpcs(wiring)
    for k in ("Beta/Get", "Gamma/Get", "Eta/Get", "Zeta/Get"):
        assert got[f"wiring.v1.{k}"]["state"] == "implemented", k


def test_r4_a_constructor_returning_the_interface_is_followed(wiring: dict[str, Any]) -> None:
    h = rpcs(wiring)["wiring.v1.Gamma/Put"]["handler"]
    assert h["func"] == "serve.gammaDesk.Put"


def test_r4_methods_are_the_impls_full_set(wiring: dict[str, Any]) -> None:
    got = rpcs(wiring)
    assert got["wiring.v1.Beta/Get"]["handler"]["func"] == "serve.betaDesk.Get"  # value receiver
    assert got["wiring.v1.Delta/Get"]["handler"]["func"] == "serve.deltaCore.Get"  # promoted
    for k in ("Beta/Put", "Delta/Put"):  # the embedded Unimplemented stub answers
        r = got[f"wiring.v1.{k}"]
        assert r["state"] == "unimplemented" and r["codes"][0]["name"] == "UNIMPLEMENTED"


def test_r5_a_descriptor_held_in_a_package_variable_is_followed(wiring: dict[str, Any]) -> None:
    reg = {r["service"]: r for r in wiring["grpc"]["registrations"]}
    assert reg["wiring.v1.Delta"]["via"] == "RegisterService"
    assert reg["wiring.v1.Delta"]["file"].endswith("internal/serve/extras.go")


def test_r6_dead_and_test_only_registrations_are_unread_beside_an_unresolved_one(
    wiring: dict[str, Any],
) -> None:
    # round 4 changed this on purpose (D1.3, accepted by the owner): the fixture also holds
    # the unresolvable `Opaque` registration, so no service can be stated `not_registered`
    got = rpcs(wiring)
    for k in ROUND4_UNREAD:
        assert {got[f"wiring.v1.{k}/{m}"]["state"] for m in ("Get", "Put")} == {"unread"}


def test_r6_a_referenced_but_unreached_registration_is_unread_not_not_registered(
    wiring: dict[str, Any],
) -> None:
    r = rpcs(wiring)["wiring.v1.Opaque/Get"]
    assert r["state"] == "unread"
    (u,) = r["unread"]
    assert u["what"] == "registration_reachability_unknown"
    assert u["file"].endswith("internal/serve/plugins.go") and u["line"] > 0
    assert "auth" not in r  # an unread rpc is not given an auth


def test_r6_the_excluded_entries_keep_todays_shape(wiring: dict[str, Any]) -> None:
    ex = [x for x in wiring["excluded"] if x["what"] == "rpc_registration"]
    assert sorted(x["path"] for x in ex) == ["wiring.v1.Dead", "wiring.v1.Gone", "wiring.v1.Opaque"]
    assert {(x["kind"], x["method"], x["reason"]) for x in ex} == {
        ("unwired", "RPC", "not reachable from main")
    }


def test_unread_and_unregistered_rpcs_are_not_routes(front_end: Any) -> None:
    model = front_end.extract(oracle.FIXTURES, "wiring")
    keys = {f"{r.rpc.service}/{r.rpc.method}" for r in model.routes if r.rpc}
    assert "wiring.v1.Alpha/Get" in keys and "wiring.v1.Beta/Put" in keys
    assert not {k for k in keys if ".Opaque/" in k or ".Dead/" in k or ".Never/" in k}


def test_the_extract_is_the_same_from_a_copy(front_end: Any, tmp_path: Path) -> None:
    copy = tmp_path / "x" / "y"
    shutil.copytree(oracle.FIXTURES / "wiring", copy / "wiring")
    a = front_end.extract_json(oracle.FIXTURES, "wiring")
    b = front_end.extract_json(copy, "wiring")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---- small modules: the rules the fixture cannot show -------------------------------------


def module(tmp_path: Path, main: str, serve: str) -> Path:
    """The wiring fixture's contract and generated code, with a server written by the test."""
    root = tmp_path / "m"
    shutil.copytree(WIRING / "gen", root / "wiring" / "gen")
    shutil.copytree(WIRING / "proto", root / "wiring" / "proto")
    shutil.copy(WIRING / "go.mod", root / "wiring" / "go.mod")
    (root / "wiring" / "main.go").write_text(main)
    (root / "wiring" / "internal" / "serve").mkdir(parents=True)
    (root / "wiring" / "internal" / "serve" / "serve.go").write_text(serve)
    return root


MAIN = """package main

import "example.com/wiringapp/internal/serve"

func main() { serve.Build() }
"""
HEAD = """package serve

import (
	"context"

	"google.golang.org/grpc"

	wv1 "example.com/wiringapp/gen/wiring/v1"
)

type one struct{ wv1.UnimplementedAlphaServer }
type two struct{ wv1.UnimplementedAlphaServer }

func (one) Get(context.Context, *wv1.Req) (*wv1.Rep, error) { return &wv1.Rep{}, nil }
func (two) Get(context.Context, *wv1.Req) (*wv1.Rep, error) { return &wv1.Rep{}, nil }
"""


def states(front_end: Any, root: Path, service: str = "Alpha") -> dict[str, str]:
    got = rpcs(front_end.extract_json(root, "wiring"))
    return {k.split("/")[1]: v["state"] for k, v in got.items() if f".{service}/" in k}


def test_r4_a_constructor_with_two_concrete_types_is_unread(front_end: Any, tmp_path: Path) -> None:
    serve = (
        HEAD
        + """
func pick(n int) wv1.AlphaServer {
	if n > 0 {
		return one{}
	}
	return two{}
}

func Build() *grpc.Server {
	s := grpc.NewServer()
	wv1.RegisterAlphaServer(s, pick(1))
	return s
}
"""
    )
    got = states(front_end, module(tmp_path, MAIN, serve))
    assert got == {"Get": "unread", "Put": "unread"}  # not guessed: one type or none


def test_r4_a_constructor_with_one_concrete_type_is_followed(
    front_end: Any, tmp_path: Path
) -> None:
    serve = (
        HEAD
        + """
func pick(n int) wv1.AlphaServer {
	if n > 0 {
		return one{}
	}
	return one{}
}

func Build() *grpc.Server {
	s := grpc.NewServer()
	wv1.RegisterAlphaServer(s, pick(1))
	return s
}
"""
    )
    assert states(front_end, module(tmp_path, MAIN, serve)) == {
        "Get": "implemented",
        "Put": "unimplemented",
    }


def test_r1_a_variable_nobody_reads_is_not_reached(front_end: Any, tmp_path: Path) -> None:
    serve = (
        HEAD
        + """
var orphan = func(s grpc.ServiceRegistrar) { wv1.RegisterAlphaServer(s, one{}) }

func Build() *grpc.Server { return grpc.NewServer() }
"""
    )
    got = states(front_end, module(tmp_path, MAIN, serve))
    assert got == {"Get": "not_registered", "Put": "not_registered"}


def test_r1_a_variable_a_wired_function_reads_is_reached(front_end: Any, tmp_path: Path) -> None:
    serve = (
        HEAD
        + """
var live = func(s grpc.ServiceRegistrar) { wv1.RegisterAlphaServer(s, one{}) }

func Build() *grpc.Server {
	s := grpc.NewServer()
	live(s)
	return s
}
"""
    )
    got = states(front_end, module(tmp_path, MAIN, serve))
    assert got == {"Get": "implemented", "Put": "unimplemented"}


def test_a_field_named_like_a_variable_is_no_read_of_it(front_end: Any, tmp_path: Path) -> None:
    """``s.live`` is a field, not the package variable ``live``: reading it wires nothing."""
    serve = (
        HEAD
        + """
var live = func(s grpc.ServiceRegistrar) { wv1.RegisterAlphaServer(s, one{}) }

type box struct{ live int }

func Build() *grpc.Server {
	b := box{live: 1}
	_ = b.live
	return grpc.NewServer()
}
"""
    )
    got = states(front_end, module(tmp_path, MAIN, serve))
    assert got == {"Get": "not_registered", "Put": "not_registered"}


def test_r6_a_method_used_through_an_interface_is_unread(front_end: Any, tmp_path: Path) -> None:
    serve = (
        HEAD
        + """
type installer interface{ Install(grpc.ServiceRegistrar) }
type a struct{}
type b struct{}

func (a) Install(s grpc.ServiceRegistrar) { wv1.RegisterAlphaServer(s, one{}) }
func (b) Install(s grpc.ServiceRegistrar) {}

var all []installer

func Build() *grpc.Server {
	s := grpc.NewServer()
	for _, i := range all {
		i.Install(s)
	}
	return s
}
"""
    )
    got = states(front_end, module(tmp_path, MAIN, serve))
    assert got == {"Get": "unread", "Put": "unread"}


def test_without_a_main_every_function_counts_as_wired(front_end: Any, tmp_path: Path) -> None:
    serve = (
        HEAD
        + """
func Build(s grpc.ServiceRegistrar) { wv1.RegisterAlphaServer(s, one{}) }
"""
    )
    root = module(tmp_path, MAIN, serve)
    (root / "wiring" / "main.go").unlink()
    assert states(front_end, root) == {"Get": "implemented", "Put": "unimplemented"}


def test_http_reachability_is_untouched_by_the_grpc_walk(front_end: Any) -> None:
    """D1.0: ``g.wired`` is not widened -- the HTTP fixtures' extracts are compared byte for byte
    by the P3-BC probe; here the cheap half: a Gin-only module has no ``grpc`` key."""
    from lattice.importer.frontends.go_gin import GoGinFrontEnd  # noqa: F401

    data = front_end.extract_json(oracle.FIXTURES, "gin_client_only")
    assert "grpc" not in data or not data["grpc"]["registrations"]


def test_d1_0_the_gateway_walk_is_not_widened(wiring: dict[str, Any]) -> None:
    """The gateway call sits in ``mountWorkshop``, which the gRPC walk reaches (it is named in a
    package-level slice) but the HTTP walk does not: the gateway list (HTTP reachability) stays
    empty while the registration beside it is found."""
    assert wiring["grpc"]["gateway"] == []
    assert any(r["service"] == "wiring.v1.Beta" for r in wiring["grpc"]["registrations"])
