"""Review findings pinned on both backends (the runtime and the generated app): the
caller's row decoded from stored spellings, insert_many with owner=user, and
size-aware fuel (R620) / the value size cap (R622). Fixture: tests/api/hardening.py."""

from __future__ import annotations

import importlib
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lattice.api.codegen import compile_source_to_fastapi
from lattice.api.runtime import serve_source
from tests.api import hardening as H

KIT_PATH = Path(__file__).resolve().parents[2] / "bench" / "api_kit" / "platform_kit.py"
AUTH = {who: {"Authorization": f"Bearer {tok}"} for who, tok in H.TOKENS.items()}


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    H.build_db(path)
    return path


def _runtime(db: Path) -> Iterator[TestClient]:
    _, app = serve_source(H.PROGRAM, H.PROFILE, db)
    assert app is not None
    with TestClient(app) as c:
        yield c


def _generated(tmp_path: Path, db: Path, monkeypatch) -> Iterator[TestClient]:
    result, code = compile_source_to_fastapi(H.PROGRAM, H.PROFILE)
    assert code is not None, [d.render() for d in result.diagnostics]
    gen = tmp_path / "gen"
    gen.mkdir()
    shutil.copyfile(KIT_PATH, gen / "platform_kit.py")
    (gen / "natives.py").write_text("NATIVES = {}\n")
    (gen / "hardening_app.py").write_text(code)
    shutil.copyfile(db, gen / "app.db")
    monkeypatch.chdir(gen)
    monkeypatch.syspath_prepend(str(gen))
    names = ("platform_kit", "natives", "hardening_app")
    for name in names:
        sys.modules.pop(name, None)
    try:
        module = importlib.import_module("hardening_app")
        with TestClient(module.app) as c:
            yield c
    finally:
        for name in names:
            sys.modules.pop(name, None)


@pytest.fixture(params=["runtime", "generated"])
def client(request, tmp_path: Path, db: Path, monkeypatch) -> Iterator[TestClient]:
    if request.param == "runtime":
        yield from _runtime(db)
    else:
        yield from _generated(tmp_path, db, monkeypatch)


# ---- the caller's stored spellings ---------------------------------------------------------


def test_user_column_in_sql_is_the_decoded_value(client):
    """where(fetch(Offers), col("tier") == user.plan): Offers spells tiers "pro"."""
    assert client.get("/offers", headers=AUTH["alice"]).json() == [
        {"id": 2, "title": "Gold"},
        {"id": 3, "title": "Platinum"},
    ]
    assert client.get("/offers", headers=AUTH["bob"]).json() == [{"id": 1, "title": "Basic"}]


def test_derive_and_procedures_see_the_decoded_caller(client):
    resp = client.get("/me/plan", headers=AUTH["alice"])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": 2, "role": "Customer", "plan": "pro", "p": "pro", "q": "pro"}


def test_a_role_column_with_stored_spellings(client):
    """The role check compares the decoded role: "ADMIN" is the Admin role."""
    resp = client.get("/admin/ping", headers=AUTH["admin"])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": 1, "role": "Admin", "plan": "pro"}
    assert client.get("/admin/ping", headers=AUTH["alice"]).status_code == 403
    # a Customer endpoint: "CUSTOMER" is the Customer role
    assert client.get("/me/plan", headers=AUTH["bob"]).status_code == 200


# ---- insert_many owner=user ---------------------------------------------------------------


def test_insert_many_records_the_caller_as_owner(client, db: Path):
    resp = client.post("/notes", headers=AUTH["bob"], json={"title": "t", "n": 2})
    assert resp.status_code in (200, 201), resp.text
    assert [r["owner_id"] for r in resp.json()] == [3, 3]


# ---- size-aware fuel (R620) and the value size cap (R622) -----------------------------------


def test_padding_within_the_cap(client):
    resp = client.get("/pad", headers=AUTH["alice"], params={"s": "ab", "n": 5})
    assert resp.json() == {"id": 2, "padded": "ab..."}


@pytest.mark.parametrize("n", [200_000, 10**11])
def test_padding_beyond_the_cap_is_r622(client, n):
    resp = client.get("/pad", headers=AUTH["alice"], params={"s": "ab", "n": n})
    assert resp.status_code == 500
    assert "error R622" in resp.json()["detail"]
    assert f"largest allowed is {H.MAX_VALUE_SIZE}" in resp.json()["detail"]


def test_repeated_growth_runs_out_of_fuel(client):
    ok = client.get("/grow", headers=AUTH["alice"], params={"n": 90_000, "k": 2})
    assert ok.json() == {"id": 2, "total": 180_000, "digits": 1}
    # 20 values of 90,000 characters: 20 x 5,625 units > the budget of 50,000
    spent = client.get("/grow", headers=AUTH["alice"], params={"n": 90_000, "k": 20})
    assert spent.status_code == 500 and "error R620" in spent.json()["detail"]


def test_a_huge_shift_is_r622_at_once(client):
    resp = client.get("/shift", headers=AUTH["alice"], params={"n": 10**9})
    assert resp.status_code == 500 and "error R622" in resp.json()["detail"]
