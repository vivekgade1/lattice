"""``gocheck extract`` on ``fixtures/bodybind`` (node GD): request bodies read without a Gin
bind method in the handler, the body fields whose absence the code refuses (workflow opt-in),
and a handler that authenticates itself with no middleware. Skipped when ``go`` is not on
PATH."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lattice.gocheck import build_gocheck

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

FIXTURE = Path(__file__).parent / "fixtures" / "bodybind"


def extract(root: Path, workflow: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"root": str(root)}
    if workflow:
        payload["workflow"] = True
    proc = subprocess.run(
        [os.environ.get("GOEXTRACT_BIN") or str(build_gocheck()), "extract"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def fx() -> dict[str, Any]:
    return extract(FIXTURE)


@pytest.fixture(scope="module")
def wf() -> dict[str, Any]:
    return extract(FIXTURE, workflow=True)


def route(x: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    (r,) = [r for r in x["routes"] if r["method"] == method and r["path_template"] == path]
    return r  # type: ignore[no-any-return]


def body(x: dict[str, Any], method: str, path: str) -> list[tuple[str, bool]]:
    return [(p["name"], p["required"]) for p in route(x, method, path)["params"] if p["source"] == "body"]


# ---- GD-1: a body decoded outside a Gin bind method -----------------------------------------


def test_a_helper_that_unmarshals_the_raw_body_binds_the_callers_struct(fx):
    # readJSON(c, &in): c.GetRawData(), raw = bytes.TrimSpace(raw), json.Unmarshal(raw, v)
    assert body(fx, "POST", "/login") == [("email", False), ("password", False)]
    assert body(fx, "POST", "/apps") == [("name", False)]


def test_a_helper_that_reads_the_body_with_io_readall_binds_a_struct_of_another_package(fx):
    r = route(fx, "POST", "/users")
    assert body(fx, "POST", "/users") == [("email", False), ("nickname", False)]
    assert r["body_models"] == ["UserIn"]


def test_a_json_decoder_over_the_request_body_binds_the_struct(fx):
    assert body(fx, "POST", "/notes") == [("text", False), ("tag", False)]


def test_a_helper_that_hands_its_parameter_to_a_gin_binder_binds_the_callers_struct(fx):
    (name,) = [p for p in route(fx, "PUT", "/apps/{id}")["params"] if p["source"] == "body"]
    assert (name["name"], name["required"], name["max_length"]) == ("name", True, 100)


def test_unmarshal_of_a_constant_is_not_a_request_body(fx):
    assert body(fx, "GET", "/config") == []


def test_the_login_route_names_its_credential_fields(fx):
    # the public POST comparing a stored password column, now with its body fields
    a = fx["auth"]
    assert (a["login_path"], a["username_field"], a["password_field"]) == (
        "/login",
        "email",
        "password",
    )


# ---- GD-1 in the workflow taint -------------------------------------------------------------


def test_a_field_of_a_struct_a_helper_decoded_carries_its_body_key(wf):
    got = {
        (u["route"], u["source"], u["param"], u["table"], u["column"], u["how"])
        for u in wf["param_uses"]
    }
    assert ("POST /login", "body", "email", "users", "email", "compare") in got
    assert ("POST /apps", "body", "name", "apps", "name", "assign") in got
    assert ("POST /notes", "body", "text", "notes", "text", "assign") in got


# ---- GD-2: fields the code refuses when absent (workflow opt-in) ----------------------------


def test_param_required_lists_the_fields_whose_absence_is_refused(wf):
    got = {(r["route"], r["source"], r["param"], r["how"]) for r in wf["param_required"]}
    assert got == {
        ("POST /login", "body", "email", "nil"),
        ("POST /login", "body", "password", "nil"),
        ("POST /apps", "body", "name", "nil"),
        ("POST /users", "body", "email", "empty"),
        ("POST /notes", "body", "text", "length"),
        # appended to a list a later `if len(errs) > 0` refuses
        ("POST /members", "body", "role", "nil"),
        # the refusal's raise is located at its first call only; the second is read by callee
        ("POST /tags", "body", "label", "nil"),
    }
    for r in wf["param_required"]:
        assert r["file"] == "main.go" and r["line"] > 0


def test_a_nested_check_and_a_conjunction_do_not_make_a_field_required(wf):
    got = {(r["route"], r["param"]) for r in wf["param_required"]}
    assert ("POST /members", "note") not in got  # under `if in.Note != nil`
    assert ("POST /tags", "color") not in got  # `in.Color != nil && *in.Color == ""`
    assert ("POST /notes", "tag") not in got


def test_param_required_is_not_in_the_default_output(fx):
    assert "param_required" not in fx
    assert all(not p["required"] for p in route(fx, "POST", "/apps")["params"])


# ---- GD-3: a handler that authenticates itself -------------------------------------------------


def test_a_handler_that_reads_the_authorization_header_and_refuses_401_is_customer(fx):
    r = route(fx, "GET", "/self")
    assert r["auth"] == "Customer"
    assert "authenticate" in r["auth_reason"] and "Authorization" in r["auth_reason"]


def test_routes_with_no_such_check_stay_public(fx):
    for method, path in (("GET", "/ping"), ("GET", "/config"), ("POST", "/login")):
        assert route(fx, method, path)["auth"] == "Public"


def test_middleware_auth_is_unchanged(fx):
    r = route(fx, "POST", "/apps")
    assert r["auth"] == "Customer" and r["auth_reason"].startswith("authenticated by main.Server.authMW")
