"""Round 4, D2: interceptors as method values and through option fields (D2.1, D2.2), allowlists of
generated and health constants (D2.3) and builder chains with exact or prefix entries (D2.4).
``fixtures/authwire4/<case>`` are small servers; ``truth.json`` (hashed before any round-4 library
code) says each rpc's auth and the kinds the interceptors classify as. ``Public`` still needs a
literal allowlist; a prefix counts only where the module tests the field with ``strings.HasPrefix``."""

from __future__ import annotations

# ruff: noqa: E501 -- docstrings and data lines
import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

AW4 = oracle.FIXTURES / "authwire4"
TRUTH_SHA = "04ae77250787a7f6c8dda45ee43689950ebde42d1c4ec752f3896649610be575"
CASES = ["mval", "optfield", "consts", "builder", "prefix", "exactprefix", "unresolved"]

pytestmark = needs_go


@pytest.fixture(scope="module")
def truth() -> Any:
    return oracle.load(AW4 / "truth.json")


@pytest.fixture(scope="module")
def ex(front_end: Any) -> dict[str, dict[str, Any]]:
    return {c: front_end.extract_json(AW4, c) for c in CASES}


def auth_of(extract: dict[str, Any]) -> dict[str, str]:
    out = {}
    for s in extract["grpc"]["services"]:
        for r in s["rpcs"]:
            key = (
                r["name"]
                if len(extract["grpc"]["services"]) == 1
                else f"{s['name'][-4:]}/{r['name']}"
            )
            out[key] = r["auth"]
    return out


def want_of(case: dict[str, Any]) -> dict[str, str]:
    return dict(case["auth"])


def test_the_truth_was_hashed_before_the_library_changed() -> None:
    assert hashlib.sha256((AW4 / "truth.json").read_bytes()).hexdigest() == TRUTH_SHA


def test_the_truth_names_every_case(truth: Any) -> None:
    assert sorted(truth["cases"]) == sorted(CASES)


@pytest.mark.parametrize("case", CASES)
def test_the_auth_of_each_rpc_equals_the_truth(ex: dict[str, Any], truth: Any, case: str) -> None:
    assert auth_of(ex[case]) == want_of(truth["cases"][case])


@pytest.mark.parametrize("case", CASES)
def test_the_interceptor_kinds_equal_the_truth(ex: dict[str, Any], truth: Any, case: str) -> None:
    kinds = [i["kind"] for i in ex[case]["grpc"]["interceptors"]]
    assert kinds == truth["cases"][case]["kinds"]


def test_d2_1_a_method_value_is_the_methods_interceptor(ex: dict[str, Any]) -> None:
    (ic,) = ex["mval"]["grpc"]["interceptors"]
    assert ic["func"] == "main.keeper.Unary" and ic["public"] == ["/aw4.v1.Desk/Get"]


def test_d2_2_options_reach_the_chain_through_a_config_field(ex: dict[str, Any]) -> None:
    other, auth = ex["optfield"]["grpc"]["interceptors"]
    assert other["kind"] == "other" and other["chained"] is True
    assert auth["func"] == "main.authUnary" and auth["chained"] is True


def test_d2_3_generated_and_health_constants_are_literal_entries(ex: dict[str, Any]) -> None:
    (ic,) = ex["consts"]["grpc"]["interceptors"]
    assert ic["public"] == [
        "/aw4.v1.Desk/Get",
        "/grpc.health.v1.Health/Check",
        "/grpc.health.v1.Health/Watch",
    ]


def test_d2_4_a_builder_chain_adds_exact_entries(ex: dict[str, Any]) -> None:
    (ic,) = ex["builder"]["grpc"]["interceptors"]
    assert ic["public"] == ["/aw4.v1.Desk/Get", "/aw4.v1.Desk/List"] and "public_prefix" not in ic


def test_d2_4_a_field_tested_with_has_prefix_holds_prefixes(ex: dict[str, Any]) -> None:
    (ic,) = ex["prefix"]["grpc"]["interceptors"]
    assert ic["public"] == ["/aw4.v1.Shut/Get"]
    assert ic["public_prefix"] == ["/aw4.v1.Open/"]  # "/"+name+"/", evaluated from the link's body


def test_d2_4_a_prefix_looking_entry_compared_with_equality_is_exact(ex: dict[str, Any]) -> None:
    (ic,) = ex["exactprefix"]["grpc"]["interceptors"]
    assert ic["public"] == ["/aw4.v1.Desk/"] and "public_prefix" not in ic


def test_d2_4_an_unresolvable_builder_argument_leaves_the_gate_unread(ex: dict[str, Any]) -> None:
    (ic,) = ex["unresolved"]["grpc"]["interceptors"]
    assert ic["kind"] == "auth_not_literal" and "public" not in ic
    assert {r["auth"] for s in ex["unresolved"]["grpc"]["services"] for r in s["rpcs"]} == {
        "unclear"
    }


def test_no_rpc_behind_an_unread_gate_is_public(ex: dict[str, Any]) -> None:
    for case, e in ex.items():
        if any(
            i["kind"] in ("auth_unknown", "auth_not_literal") for i in e["grpc"]["interceptors"]
        ):
            assert "Public" not in auth_of(e).values(), case


# ---- small modules ------------------------------------------------------------------------


def variant(tmp_path: Path, case: str, edits: dict[str, tuple[str, str]]) -> Path:
    root = tmp_path / "m"
    shutil.copytree(AW4 / case, root / case)
    for rel, (old, new) in edits.items():
        p = root / case / rel
        assert old in p.read_text(), (rel, old)
        p.write_text(p.read_text().replace(old, new))
    return root


def test_d2_2_a_slice_literal_in_the_config_is_not_guessed(front_end: Any, tmp_path: Path) -> None:
    root = variant(
        tmp_path,
        "optfield",
        {
            "main.go": (
                "\tcfg := &config{}\n\tfor _, o := range []option{withUnary(logUnary, authUnary)} {\n\t\to(cfg)\n\t}\n",
                "\tcfg := &config{unary: []grpc.UnaryServerInterceptor{logUnary, authUnary}}\n",
            )
        },
    )
    ex = front_end.extract_json(root, "optfield")
    assert [i["kind"] for i in ex["grpc"]["interceptors"]] == ["auth_not_literal"]
    assert {r["auth"] for s in ex["grpc"]["services"] for r in s["rpcs"]} == {"unclear"}


def test_d2_4_a_second_policy_value_does_not_leak_into_the_gates_allowlist(
    front_end: Any, tmp_path: Path
) -> None:
    """Entries come from the chain that reaches the gate's field, not from every policy built."""
    root = variant(
        tmp_path,
        "builder",
        {
            "main.go": (
                "\tg := newGate(",
                '\t_ = newPolicy("/aw4.v1.Desk/Put")\n\tg := newGate(',
            )
        },
    )
    ex = front_end.extract_json(root, "builder")
    assert ex["grpc"]["interceptors"][0]["public"] == ["/aw4.v1.Desk/Get", "/aw4.v1.Desk/List"]


def test_d2_3_the_health_methods_are_not_rpcs_of_the_module(ex: dict[str, Any]) -> None:
    assert auth_of(ex["consts"]) == {"Get": "Public", "Put": "Member"}
