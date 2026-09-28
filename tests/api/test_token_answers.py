"""The token answers of ``NativeToken`` (spec 2026-09-27 surface §4.1, auth-policy
fidelity): an imported app answers a token naming no caller exactly as the original --
``invalid`` when the auth native decodes it to null (a bad, expired or claimless
token), ``unknown`` when the decoded value matches no users row. Profile-level, so every
endpoint with auth answers so, including endpoints added later by edits. The generated
FastAPI app is compared with the runtime in test_codegen_differential ("solo_answers").
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lattice.api.ir import lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from lattice.profile import NativeToken, Profile
from tests.api import solo as F

INVALID = (401, "Could not validate credentials")
UNKNOWN = (403, "User not found")


def _client(tmp_path: Path, profile: Profile, source: str = F.PROGRAM) -> TestClient:
    check = check_source(source, profile)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    db = tmp_path / "app.db"
    F.build_db(db)
    return TestClient(build_app(lower_api(check.program), db, natives=F.NATIVES,
                                settings=F.SETTINGS))  # fmt: skip


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with _client(tmp_path, F.PROFILE_ANSWERS) as c:
        yield c


def _get(c: TestClient, path: str, who: str | None) -> tuple[int, object]:
    headers = {} if who is None else {"Authorization": f"Bearer {F.TOKENS[who]}"}
    r = c.get(path, headers=headers)
    return r.status_code, r.json()


def test_the_answers_are_validated() -> None:
    for bad in ((418, "x"), (401, ""), (401,), "401", (True, "x")):
        with pytest.raises(ValueError, match="NativeToken"):
            NativeToken("decode_token", invalid=bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="NativeToken"):
            NativeToken("decode_token", unknown=bad)  # type: ignore[arg-type]
    assert NativeToken("d", invalid=(401, "a"), unknown=(404, "b")).unknown == (404, "b")


def test_an_undecodable_token_gets_the_invalid_answer(client: TestClient) -> None:
    assert _get(client, "/me", "forged") == (401, {"error": "unauthorized", "detail": INVALID[1]})


def test_a_token_naming_no_user_gets_the_unknown_answer(client: TestClient) -> None:
    assert _get(client, "/me", "ghost") == (403, {"error": "forbidden", "detail": UNKNOWN[1]})


def test_no_token_and_a_known_caller_are_unchanged(client: TestClient) -> None:
    assert _get(client, "/me", None)[0] == 401
    assert _get(client, "/me", None)[1] == {"error": "unauthorized"}
    assert _get(client, "/me", "alice")[0] == 200
    assert _get(client, "/me", "carol")[0] == 400  # inactive: after the lookup


def test_every_endpoint_answers_so_including_a_new_one(tmp_path: Path) -> None:
    added = (
        F.PROGRAM
        + """

@endpoint("GET", "/tasks/count", auth=Customer)
def count_tasks(user: CurrentUser) -> One:
    tasks = fetch(Task)
    own = mine(tasks, by=user)
    n = group(own, by=[], count=agg.count())
    return n
"""
    )
    with _client(tmp_path, F.PROFILE_ANSWERS, added) as c:
        for path in ("/tasks", "/tasks/1", "/tasks/count"):
            assert _get(c, path, "forged")[0] == INVALID[0]
            assert _get(c, path, "ghost") == (403, {"error": "forbidden", "detail": UNKNOWN[1]})


def test_without_answers_both_are_the_unauthorized_401(tmp_path: Path) -> None:
    with _client(tmp_path, F.PROFILE) as c:
        unauthorized = (401, {"error": "unauthorized"})
        assert _get(c, "/me", "forged") == _get(c, "/me", "ghost") == unauthorized


def test_the_fastapi_error_body_carries_the_detail(tmp_path: Path) -> None:
    api = replace(F.API_ANSWERS, error_body="fastapi", unauthorized_detail="Not authenticated")
    with _client(tmp_path, Profile(schemas={}, api=api)) as c:
        assert _get(c, "/me", "forged") == (401, {"detail": INVALID[1]})
        assert _get(c, "/me", "ghost") == (403, {"detail": UNKNOWN[1]})
        assert _get(c, "/me", None) == (401, {"detail": "Not authenticated"})
