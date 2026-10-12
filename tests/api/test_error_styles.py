"""Error conventions of non-FastAPI apps (phase 2 plan, W1-D): ``ApiProfile.error_body``
``"drf"`` / ``"json_key(<key>)"`` and ``ApiProfile.validation_status``.

* the profile accepts exactly the documented values, and refuses any other with an
  exact fix hint (at construction, and as checker diagnostic L836 for a profile that
  reaches the checker unvalidated);
* the Lattice runtime answers each style (the generated FastAPI app is compared with it
  in test_codegen_differential: errors_drf, errors_json_key, errors_400);
* the defaults are today's: "lattice" bodies, 422 validation;
* Go is gated (L892, ``Target.error_styles``), with a decision-log row (the TS client's
  gate, L893, stays in the checker; its generator was removed).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lattice.api.ir import lower_api
from lattice.api.runtime import build_app, error_body
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.profile import ApiProfile, Profile, error_style_problem
from lattice.targets import TARGETS, target_named
from tests.api import errstyles as F

ROOT = Path(__file__).resolve().parents[2]
RULES_DOC = (ROOT / "docs" / "lattice-language-rules.md").read_text()


# ---- the profile -------------------------------------------------------------------------


def test_the_defaults_are_todays_behaviour() -> None:
    api = F.BASE
    assert api.error_body == "lattice" and api.validation_status == 422


@pytest.mark.parametrize(
    "value", ["lattice", "fastapi", "drf", "json_key(message)", "json_key(error_code)"]
)
def test_the_profile_accepts_every_error_body(value: str) -> None:
    assert dataclasses.replace(F.BASE, error_body=value).error_body == value


@pytest.mark.parametrize("status", [400, 422])
def test_the_profile_accepts_400_and_422_validation(status: int) -> None:
    assert dataclasses.replace(F.BASE, validation_status=status).validation_status == status


@pytest.mark.parametrize(
    ("changes", "hint"),
    [
        ({"error_body": "json"}, 'error_body="json_key(error)"'),
        ({"error_body": "dfr"}, 'error_body="drf"'),
        ({"error_body": "FastAPI"}, 'error_body="fastapi"'),
        ({"error_body": "json_key()"}, 'error_body="json_key(error)"'),
        ({"error_body": "json_key(error message)"}, 'error_body="json_key(error_message)"'),
        ({"error_body": "json_key(message"}, 'error_body="json_key(message)"'),
        ({"error_body": "json_key(1st)"}, 'error_body="json_key(_1st)"'),
        ({"error_body": 3}, 'error_body="lattice"'),
        ({"validation_status": 404}, "validation_status=400"),
        ({"validation_status": 200}, "validation_status=422"),
        ({"validation_status": True}, "validation_status=422"),
        ({"validation_status": "400"}, "validation_status=400"),
    ],
)
def test_the_profile_refuses_other_values_with_an_exact_hint(changes, hint) -> None:
    (name,) = changes
    with pytest.raises(ValueError, match=name) as err:
        dataclasses.replace(F.BASE, **changes)
    assert hint in str(err.value)


def test_the_problem_is_shared_by_the_profile_and_the_checker() -> None:
    assert error_style_problem("drf", 400) is None
    assert error_style_problem("lattice", 422) is None
    message, hint = error_style_problem("drf", 404)  # type: ignore[misc]
    assert "validation_status" in message and "404" in message
    assert hint == (
        "write validation_status=400 (Django REST framework, Flask) or validation_status=422 "
        "(FastAPI, the default)"
    )


def _unvalidated(**changes: object) -> Profile:
    """A profile carrying a value construction would refuse (as a profile built some
    other way, e.g. unpickled, may)."""
    api = dataclasses.replace(F.BASE)
    for name, value in changes.items():
        object.__setattr__(api, name, value)
    return Profile(schemas={}, api=api)


@pytest.mark.parametrize(
    ("changes", "hint"),
    [
        ({"error_body": "json_key(a b)"}, 'error_body="json_key(a_b)"'),
        ({"validation_status": 418}, "validation_status=400"),
    ],
)
def test_the_checker_reports_an_invalid_error_style(changes, hint) -> None:
    result = check_source(F.PROGRAM, _unvalidated(**changes))
    assert result.program is None
    (diag,) = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    assert diag.code == "L836"
    assert diag.hint is not None and hint in diag.hint


@pytest.mark.parametrize("pid", sorted(F.PROFILES))
def test_the_checker_accepts_the_fixture_on_every_profile(pid: str) -> None:
    result = check_source(F.PROGRAM, F.PROFILES[pid])
    assert result.program is not None, [d.render() for d in result.diagnostics]
    plans = lower_api(result.program)
    api = F.PROFILES[pid].api
    assert {(p.error_body, p.validation_status) for p in plans} == {
        (api.error_body, api.validation_status)  # type: ignore[union-attr]
    }


# ---- the runtime ---------------------------------------------------------------------------


def _client(profile: Profile, tmp_path: Path) -> Iterator[TestClient]:
    db = tmp_path / f"{id(profile)}.db"
    F.build_db(db)
    result = check_source(F.PROGRAM, profile)
    assert result.program is not None
    app = build_app(lower_api(result.program), db)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def clients(tmp_path: Path) -> Iterator[dict[str, TestClient]]:
    profiles = {**F.PROFILES, "default": Profile(schemas={}, api=F.BASE)}
    gens = {name: _client(p, tmp_path) for name, p in profiles.items()}
    out = {name: next(g) for name, g in gens.items()}
    yield out
    for g in gens.values():
        next(g, None)


def _auth(who: str | None) -> dict[str, str]:
    return {} if who is None else {"Authorization": f"Bearer {F.TOKENS[who]}"}


# (name, method, path, who, json, query)
REQUESTS = {
    "no token": ("GET", "/notes/1", None, None, None),
    "unknown token": ("GET", "/notes/1", "nobody", None, None),
    "forbidden": ("DELETE", "/notes/1", "ann", None, None),
    "declared 404": ("GET", "/notes/2", "ann", None, None),
    "unknown route": ("GET", "/no-such-route", "ann", None, None),
    "wrong method": ("PUT", "/notes", "ann", None, None),
    "declared 400": ("POST", "/notes", "ann", {"title": "nope"}, None),
    "conflict": ("POST", "/notes", "ann", {"title": "bob's"}, None),
    "missing field": ("POST", "/notes", "ann", {}, None),
    "two fields": ("POST", "/notes", "ann", {"title": "", "rank": 99}, None),
    "list body": ("POST", "/notes", "ann", [1, 2], None),
    "query": ("GET", "/notes", "ann", None, {"limit": "0", "offset": "-1"}),
    "path": ("GET", "/notes/abc", "ann", None, None),
    "ok": ("GET", "/notes/1", "ann", None, None),
}
VALIDATION = ("missing field", "two fields", "list body", "query", "path")


def _send(client: TestClient, name: str):
    method, path, who, body, query = REQUESTS[name]
    headers = _auth(who) if who != "nobody" else {"Authorization": "Bearer tok-nobody"}
    return client.request(method, path, json=body, params=query, headers=headers)


def _drf(detail: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for entry in detail:
        loc = entry["loc"]
        out.setdefault(loc[-1] if len(loc) > 1 else "non_field_errors", []).append(entry["msg"])
    return out


@pytest.mark.parametrize("name", sorted(REQUESTS))
def test_each_style_answers_the_default_answer_reshaped(clients, name: str) -> None:
    """Every style answers the same status (the validation status aside) and the same
    detail, in its own shape."""
    base = _send(clients["default"], name)
    want = base.json()
    status = base.status_code
    for pid, profile in F.PROFILES.items():
        got = _send(clients[pid], name)
        api = profile.api
        assert api is not None
        if status < 400:
            assert (got.status_code, got.json()) == (status, want), pid
            continue
        detail = want.get("detail")
        if status == 401 and api.unauthorized_detail is not None:
            detail = api.unauthorized_detail
        expected = error_body(api.error_body, status, want["error"], detail)
        if status == 422:
            assert got.status_code == api.validation_status == 400, pid
        else:
            assert got.status_code == status, pid
        assert got.json() == expected, pid


def test_drf_bodies(clients) -> None:
    c = clients["drf"]
    assert _send(c, "no token").json() == {"detail": F.UNAUTHORIZED}
    assert _send(c, "forbidden").json() == {"detail": "Forbidden"}
    assert _send(c, "declared 404").json() == {"detail": "Note not found"}
    assert _send(c, "unknown route").json() == {"detail": "Not Found"}
    assert _send(c, "wrong method").json() == {"detail": "Method Not Allowed"}
    assert _send(c, "declared 400").json() == {"detail": "title refused"}
    assert _send(c, "conflict").json() == {"detail": "Title taken"}
    assert _send(c, "missing field").json() == {"title": ["field required"]}
    assert _send(c, "list body").json() == {"non_field_errors": ["the body must be a JSON object"]}
    two = _send(c, "two fields")
    assert two.status_code == 400 and list(two.json()) == ["title", "rank"]
    base = _send(clients["default"], "two fields").json()["detail"]
    assert two.json() == _drf(base)
    assert list(_send(c, "query").json()) == ["limit", "offset"]
    assert list(_send(c, "path").json()) == ["note_id"]


def test_json_key_bodies(clients) -> None:
    c = clients["json_key"]
    assert _send(c, "no token").json() == {"message": "Unauthorized"}
    assert _send(c, "forbidden").json() == {"message": "Forbidden"}
    assert _send(c, "declared 404").json() == {"message": "Note not found"}
    assert _send(c, "unknown route").json() == {"message": "Not Found"}
    assert _send(c, "declared 400").json() == {"message": "title refused"}
    missing = _send(c, "missing field")
    assert missing.status_code == 400
    assert missing.json() == {"message": [{"loc": ["body", "title"], "msg": "field required"}]}


def test_lattice_bodies_with_400_validation(clients) -> None:
    c = clients["lattice_400"]
    missing = _send(c, "missing field")
    assert missing.status_code == 400
    assert missing.json() == {
        "error": "validation",
        "detail": [{"loc": ["body", "title"], "msg": "field required"}],
    }
    assert _send(c, "declared 400").json() == {"error": "bad_request", "detail": "title refused"}
    assert _send(c, "no token").json() == {"error": "unauthorized"}


def test_the_default_answers_are_unchanged(clients) -> None:
    c = clients["default"]
    missing = _send(c, "missing field")
    assert missing.status_code == 422
    assert missing.json() == {
        "error": "validation",
        "detail": [{"loc": ["body", "title"], "msg": "field required"}],
    }
    assert _send(c, "declared 404").json() == {"error": "not_found", "detail": "Note not found"}


def test_a_failed_validation_writes_nothing(clients) -> None:
    for pid in F.PROFILES:
        c = clients[pid]
        assert _send(c, "two fields").status_code == 400
        assert c.get("/notes", headers=_auth("ann")).json()["items"] == [
            {"id": 1, "title": "ann's", "rank": 1},
            {"id": 3, "title": "ann's too", "rank": 3},
        ]


# ---- the targets ---------------------------------------------------------------------------


def test_python_carries_every_style() -> None:
    assert TARGETS["python"].error_styles


@pytest.mark.parametrize("pid", sorted(F.PROFILES))
def test_go_is_gated(pid: str) -> None:
    assert not target_named("go").error_styles
    result = check_source(F.PROGRAM, F.PROFILES[pid], target="go")
    assert result.program is None
    errors = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    assert {d.code for d in errors} == {"L892"} and len(errors) == 1
    assert "not available on target 'go' yet" in errors[0].message
    assert errors[0].hint == (
        'use error_body="lattice" or "fastapi" with validation_status=422, or compile '
        "for target 'python'"
    )


def test_go_accepts_the_default_styles() -> None:
    for api in (F.BASE, dataclasses.replace(F.BASE, error_body="fastapi")):
        assert check_source(F.PROGRAM, Profile(schemas={}, api=api), target="go").ok


def test_the_gates_have_a_decision_log_row() -> None:
    row = next(line for line in RULES_DOC.splitlines() if "Target.error_styles" in line)
    assert "L892" in row and "L893" in row and "validation_status" in row


def test_the_importers_declared_deviations_follow_the_convention() -> None:
    """The importer's policy deviations (lattice.importer.policy) predict the answer in
    the profile's convention: a refused page bound / null is the validation status."""
    from lattice.importer.policy import NotNull, PageBound, deviations_for
    from lattice.importer.verify import Case

    cases = [
        Case("r.huge", "GET", "/x", route="r", params={"limit": 1000}),
        Case("r.null", "POST", "/x", route="r", json={"title": None}),
    ]
    policies = [PageBound("limit", 1, 50), NotNull("title")]
    out = deviations_for(cases, policies, F.DRF)
    assert (out["r.huge"].status, out["r.huge"].body) == (400, {"limit": ["must be <= 50"]})
    assert (out["r.null"].status, out["r.null"].body) == (400, {"title": ["must not be null"]})
    out = deviations_for(cases, policies, F.BASE)
    assert out["r.huge"].status == 422
    assert out["r.huge"].body == {
        "error": "validation",
        "detail": [{"loc": ["query", "limit"], "msg": "must be <= 50"}],
    }


def test_the_profile_api_is_an_api_profile() -> None:
    assert all(isinstance(p.api, ApiProfile) for p in F.PROFILES.values())
