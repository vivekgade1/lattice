"""Round 4, D1: registration through generic helpers, option constructors, init registries and
interface dispatch (D1.1-D1.4), and the module-wide safety rule (D1.3). ``fixtures/wiring4/<case>``
are three small servers; ``truth.json`` (hashed before any round-4 library code) says the state of
every rpc. A confident ``not_registered`` is never said while a registration is unresolved."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

W4 = oracle.FIXTURES / "wiring4"
TRUTH_SHA = "2562a0bef7b5d627383ae7e74a3b0e435087d7bdee70942670b27377a292aa68"
CASES = ["main", "unres", "varlost"]

pytestmark = needs_go


@pytest.fixture(scope="module")
def truth() -> Any:
    return oracle.load(W4 / "truth.json")["cases"]


@pytest.fixture(scope="module")
def ex(front_end: Any) -> dict[str, dict[str, Any]]:
    return {c: front_end.extract_json(W4, c) for c in CASES}


def rpcs(extract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f"{s['name']}/{r['name']}": r for s in extract["grpc"]["services"] for r in s["rpcs"]}


def test_the_truth_was_hashed_before_the_library_changed() -> None:
    assert hashlib.sha256((W4 / "truth.json").read_bytes()).hexdigest() == TRUTH_SHA


@pytest.mark.parametrize("case", CASES)
def test_every_state_equals_the_truth(ex: dict[str, Any], truth: Any, case: str) -> None:
    got = rpcs(ex[case])
    assert {k: got[k]["state"] for k in truth[case]["rpcs"]} == truth[case]["rpcs"]
    assert set(got) == set(truth[case]["rpcs"])


def test_d1_1_a_helper_with_parameter_descriptor_and_impl_registers_each_call_site(
    ex: dict[str, Any],
) -> None:
    regs = {r["service"]: r for r in ex["main"]["grpc"]["registrations"]}
    for s in ("Gen", "Twin"):
        assert regs[f"wiring4.v1.{s}"]["via"].startswith("RegisterService via ")
        assert regs[f"wiring4.v1.{s}"]["file"].endswith("internal/app/app.go")
    assert regs["wiring4.v1.Gen"]["line"] != regs["wiring4.v1.Twin"]["line"]  # two call sites


def test_d1_1_the_descriptor_variable_and_the_impl_expression_are_followed(
    ex: dict[str, Any],
) -> None:
    got = rpcs(ex["main"])
    assert got["wiring4.v1.Gen/Get"]["handler"]["func"] == "genx.service.Get"
    assert got["wiring4.v1.Twin/Get"]["handler"]["func"] == "app.twinDesk.Get"


def test_d1_2_an_options_applying_constructor_is_followed_to_its_concrete_type(
    ex: dict[str, Any],
) -> None:
    put = rpcs(ex["main"])["wiring4.v1.Gen/Put"]
    assert put["state"] == "unimplemented" and put["codes"][0]["name"] == "UNIMPLEMENTED"


def test_d1_4_an_init_registry_of_interface_values_is_followed(ex: dict[str, Any]) -> None:
    regs = {r["service"]: r for r in ex["main"]["grpc"]["registrations"]}
    assert regs["wiring4.v1.Reg"]["file"].endswith("internal/regsvc/regsvc.go")  # func-type adapter
    assert regs["wiring4.v1.RegB"]["file"].endswith("internal/regb/regb.go")  # a struct value
    assert "wiring4.v1.Ghost" not in regs


def test_d1_4_an_implementer_nothing_builds_is_not_reached(ex: dict[str, Any]) -> None:
    got = rpcs(ex["main"])
    assert {got[f"wiring4.v1.Ghost/{m}"]["state"] for m in ("Get", "Put")} == {"not_registered"}
    assert "unread" not in got["wiring4.v1.Ghost/Get"]


def test_d1_3_an_unreferenced_unresolvable_call_is_dead_not_unread(ex: dict[str, Any]) -> None:
    dropped = [x["path"] for x in ex["main"]["excluded"] if x["what"] == "rpc_registration"]
    assert dropped == [
        "wiring4.v1.Ghost"
    ]  # the legacy Mount, with no descriptor, is not even listed
    assert not ex["main"]["unread"]


def test_d1_3_an_unresolved_registration_makes_every_unregistered_service_unread(
    ex: dict[str, Any],
) -> None:
    got = rpcs(ex["unres"])
    for k in ("Mystery", "Orphan"):
        r = got[f"wiring4.v1.{k}/Get"]
        assert r["state"] == "unread"
        (u,) = r["unread"]
        assert u["what"] == "registration_reachability_unknown"
        assert u["file"].endswith("internal/app/app.go") and u["line"] > 0
        assert f"{u['file']}:{u['line']}" in u["reason"]  # the unresolved call is named


@pytest.mark.parametrize("case", ["unres", "varlost"])
def test_d1_3_no_rpc_is_not_registered_beside_an_unresolved_registration(
    ex: dict[str, Any], case: str
) -> None:
    assert "not_registered" not in {r["state"] for r in rpcs(ex[case]).values()}


def test_round3_deviation_8_7_a_variable_read_only_by_unreached_code_is_unread(
    ex: dict[str, Any],
) -> None:
    held = rpcs(ex["varlost"])["wiring4.v1.Held/Get"]
    assert held["state"] == "unread" and held["unread"][0]["file"].endswith("app.go")
    paths = [x["path"] for x in ex["varlost"]["excluded"] if x["what"] == "rpc_registration"]
    assert paths == ["wiring4.v1.Held"]


def test_d1_3_changes_round_3s_wiring_fixture_by_design(front_end: Any) -> None:
    """Round 3's fixture has the referenced-but-unresolvable ``Opaque`` registration: under D1.3 the
    never-registered services beside it are ``unread``, no longer ``not_registered`` (the four
    test_wiring3 tests that say otherwise are the pre-registered consequence of D1.3, Q3)."""
    got = rpcs(front_end.extract_json(oracle.FIXTURES, "wiring"))
    for s in ("Dead", "Gone", "Never"):
        assert {got[f"wiring.v1.{s}/{m}"]["state"] for m in ("Get", "Put")} == {"unread"}
    assert got["wiring.v1.Alpha/Get"]["state"] == "implemented"


# ---- small modules ------------------------------------------------------------------------

MAIN = """package main

import "example.com/w4main/internal/serve"

func main() { serve.Build() }
"""
HEAD = """package serve

import (
	"context"

	"google.golang.org/grpc"

	w4 "example.com/w4main/gen/wiring4/v1"
)

type one struct{ w4.UnimplementedGenServer }

func (one) Get(context.Context, *w4.Req) (*w4.Rep, error) { return &w4.Rep{}, nil }

func put[T any](r grpc.ServiceRegistrar, d *grpc.ServiceDesc, impl T) { r.RegisterService(d, impl) }
"""


def module(tmp_path: Path, serve: str, extra: dict[str, str] | None = None) -> Path:
    root = tmp_path / "m"
    shutil.copytree(W4 / "main" / "gen", root / "gen")
    shutil.copytree(W4 / "main" / "proto", root / "proto")
    shutil.copy(W4 / "main" / "go.mod", root / "go.mod")
    (root / "main.go").write_text(MAIN)
    (root / "internal" / "serve").mkdir(parents=True)
    (root / "internal" / "serve" / "serve.go").write_text(serve)
    for rel, text in (extra or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def gen_states(front_end: Any, root: Path) -> dict[str, str]:
    got = rpcs(front_end.extract_json(root.parent, root.name))
    return {k.split("/")[1]: v["state"] for k, v in got.items() if ".Gen/" in k}


def test_d1_1_an_explicit_instantiation_of_the_helper_is_a_call_site(
    front_end: Any, tmp_path: Path
) -> None:
    serve = (
        HEAD
        + """
func Build() *grpc.Server {
	s := grpc.NewServer()
	put[w4.GenServer](s, &w4.Gen_ServiceDesc, one{})
	return s
}
"""
    )
    assert gen_states(front_end, module(tmp_path, serve)) == {
        "Get": "implemented",
        "Put": "unimplemented",
    }


def test_d1_1_a_helper_reached_only_as_a_value_is_unresolved_not_not_registered(
    front_end: Any, tmp_path: Path
) -> None:
    serve = (
        HEAD
        + """
var mounts = []func(grpc.ServiceRegistrar, *grpc.ServiceDesc, w4.GenServer){put[w4.GenServer]}

func Build() *grpc.Server {
	s := grpc.NewServer()
	for _, m := range mounts {
		m(s, &w4.Gen_ServiceDesc, one{})
	}
	return s
}
"""
    )
    assert gen_states(front_end, module(tmp_path, serve)) == {"Get": "unread", "Put": "unread"}


def test_d1_4_a_blank_imported_package_init_is_wired(front_end: Any, tmp_path: Path) -> None:
    plug = """package plug

import (
	"context"

	"google.golang.org/grpc"

	w4 "example.com/w4main/gen/wiring4/v1"
	"example.com/w4main/internal/registry"
)

type one struct{ w4.UnimplementedGenServer }

func (one) Get(context.Context, *w4.Req) (*w4.Rep, error) { return &w4.Rep{}, nil }

func init() { registry.Offer(func(r grpc.ServiceRegistrar) { w4.RegisterGenServer(r, one{}) }) }
"""
    reg = """package registry

import "google.golang.org/grpc"

var offered []func(grpc.ServiceRegistrar)

func Offer(f func(grpc.ServiceRegistrar)) { offered = append(offered, f) }

func All(r grpc.ServiceRegistrar) {
	for _, f := range offered {
		f(r)
	}
}
"""
    serve = """package serve

import (
	"google.golang.org/grpc"

	_ "example.com/w4main/internal/plug"
	"example.com/w4main/internal/registry"
)

func Build() *grpc.Server {
	s := grpc.NewServer()
	registry.All(s)
	return s
}
"""
    root = module(
        tmp_path,
        serve,
        {"internal/plug/plug.go": plug, "internal/registry/registry.go": reg},
    )
    assert gen_states(front_end, root)["Get"] == "implemented"


def test_d1_4_a_method_called_on_an_interface_reaches_only_built_implementers(
    front_end: Any, tmp_path: Path
) -> None:
    serve = """package serve

import (
	"google.golang.org/grpc"

	w4 "example.com/w4main/gen/wiring4/v1"
)

type installer interface{ Install(grpc.ServiceRegistrar) }

type live struct{}
type idle struct{}

func (live) Install(r grpc.ServiceRegistrar) { w4.RegisterGenServer(r, genDesk{}) }
func (idle) Install(r grpc.ServiceRegistrar) { w4.RegisterTwinServer(r, twinDesk{}) }

func Build() *grpc.Server {
	s := grpc.NewServer()
	for _, i := range []installer{live{}} {
		i.Install(s)
	}
	return s
}
"""
    desks = (
        HEAD.split("type one")[0]
        + """
type genDesk struct{ w4.UnimplementedGenServer }
type twinDesk struct{ w4.UnimplementedTwinServer }
"""
    )
    root = module(tmp_path, serve, {"internal/serve/desks.go": desks})
    got = rpcs(json.loads(json.dumps(front_end.extract_json(root.parent, root.name))))
    assert got["wiring4.v1.Gen/Get"]["state"] == "unimplemented"  # registered, the stub answers
    assert got["wiring4.v1.Twin/Get"]["state"] == "not_registered"  # idle is built nowhere


def test_the_extract_is_the_same_from_a_copy(front_end: Any, tmp_path: Path) -> None:
    copy = tmp_path / "x" / "y"
    shutil.copytree(W4, copy / "wiring4")
    a = front_end.extract_json(W4, "main")
    b = front_end.extract_json(copy / "wiring4", "main")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
