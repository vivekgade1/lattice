"""T2b: procedures in the API runtime and the generated app (spec 1c §3-4).

The differential (tests/api/test_codegen_differential.py, program "t2") compares the
two backends case by case; these tests pin what each one must do on its own: the plan
shape of a T2-post read, the fuel budget of a request (ApiProfile.fuel), R620 as a 500
with a stable body and a log entry, the natives procedures need, and that programs
without procedures are unchanged."""

from __future__ import annotations

import dataclasses
import importlib
import logging
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lattice.api.codegen import compile_source_to_fastapi, generate_fastapi
from lattice.api.ir import CallStep, PostStep, ReadStep, T2CheckStep, lower_api
from lattice.api.runtime import build_app, serve_source
from lattice.catalog import DEFAULT_FUEL
from lattice.check import check_source
from lattice.profile import Profile
from tests.api import effects, t2prog
from tests.api.t2_natives import SENT

KIT_PATH = Path(__file__).resolve().parents[2] / "bench" / "api_kit" / "platform_kit.py"
H = {who: {"Authorization": f"Bearer {tok}"} for who, tok in t2prog.TOKENS.items()}


def _plans(source: str = t2prog.PROGRAM, profile: Profile = t2prog.PROFILE):
    result = check_source(source, profile)
    assert result.program is not None, [d.render() for d in result.diagnostics]
    return {p.name: p for p in lower_api(result.program)}


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    t2prog.build_db(path)
    return path


@pytest.fixture
def client(db: Path) -> Iterator[TestClient]:
    _, app = serve_source(t2prog.PROGRAM, t2prog.PROFILE, db, natives=t2prog.NATIVES)
    assert app is not None
    with TestClient(app) as c:
        yield c


# ---- plans ------------------------------------------------------------------------------------


def test_a_t2_post_read_is_one_statement_then_the_procedures():
    plan = _plans()["list_items"]
    kinds = [type(s) for s in plan.steps]
    assert kinds == [ReadStep, PostStep]
    read, post = plan.steps
    assert plan.t2_post_reads == (read.id,) and post.input == read.id
    # the SQL computes the T0 column and the page (with its total); procedures do not
    # appear in it
    assert "LIMIT" in read.sql.text.upper() and "is_stale" not in read.sql.text
    assert [op[0] for op in post.ops] == ["derive", "derive", "derive", "select"]
    assert plan.page is not None and plan.page.total_step == read.id


def test_checks_calls_and_the_compiled_procedures_ride_on_the_plan():
    plans = _plans()
    assert [type(s) for s in plans["get_item"].steps] == [
        ReadStep,
        type(plans["get_item"].steps[1]),
        T2CheckStep,
        T2CheckStep,
        PostStep,
    ]
    call = plans["rescore"].steps[2]
    assert isinstance(call, CallStep) and call.bind == "s" and call.result == ("scalar", "Float")
    code = plans["replay"].t2
    assert code is not None and all(p.t2 is code for p in plans.values())
    assert ("replay_logins", ("events",)) in code.procedures
    assert plans["notify_item"].t2_effects == (("notify", ("to", "title")),)
    assert plans["notify_item"].effects == ()  # the procedure is not an effect native
    assert all(p.fuel == t2prog.FUEL for p in plans.values())


def test_programs_without_procedures_are_unchanged():
    plans = _plans(effects.PROGRAM, effects.PROFILE)
    for plan in plans.values():
        assert plan.t2 is None and plan.t2_caller == () and plan.t2_effects == ()
        assert plan.fuel == DEFAULT_FUEL and not plan.uses_t2
        assert not any(isinstance(s, PostStep | T2CheckStep | CallStep) for s in plan.steps)


# ---- the runtime ---------------------------------------------------------------------------------


def test_r620_is_a_500_with_a_stable_body_and_a_log_entry(client, caplog):
    with caplog.at_level(logging.ERROR, logger="lattice.t2"):
        first = client.get("/spin", headers=H["alice"])
        again = client.get("/spin", headers=H["bob"])
    assert first.status_code == again.status_code == 500
    assert first.json() == again.json()
    body = first.json()
    assert body["error"] == "internal"
    assert body["detail"].endswith(
        "error R620: procedure ran out of fuel: the request's budget of 20000 steps is spent "
        "(every loop iteration, call and comprehension element costs one)"
    )
    logged = [r.getMessage() for r in caplog.records if r.name == "lattice.t2"]
    assert logged == [body["detail"], body["detail"]]


def test_recursion_depth_is_r620(client):
    assert client.get("/depth/150", headers=H["alice"]).json() == {"id": 1, "depth": 150}
    deep = client.get("/depth/100000", headers=H["alice"])
    assert deep.status_code == 500 and "nest deeper than 200" in deep.json()["detail"]


def test_the_budget_is_the_profiles_fuel(tmp_path: Path):
    """The same request runs out of fuel under a small budget and not under a larger one
    (the default is ApiProfile.fuel = 1,000,000)."""
    src = t2prog.PROGRAM
    small = dataclasses.replace(t2prog.PROFILE, api=dataclasses.replace(t2prog.API, fuel=100))
    big = dataclasses.replace(t2prog.PROFILE, api=dataclasses.replace(t2prog.API, fuel=10**6))
    statuses = {}
    for label, profile in (("small", small), ("big", big)):
        db = tmp_path / f"{label}.db"
        t2prog.build_db(db)
        _, app = serve_source(src, profile, db, natives=t2prog.NATIVES)
        assert app is not None
        with TestClient(app) as c:
            statuses[label] = c.get("/depth/150", headers=H["alice"]).status_code
    assert statuses == {"small": 500, "big": 200}
    assert dataclasses.replace(t2prog.API, fuel=DEFAULT_FUEL).fuel == 1_000_000


def test_a_rolled_back_request_writes_nothing_and_sends_nothing(client, db: Path):
    """A 500 from a procedure rolls the transaction back like any other failure."""
    import sqlite3

    before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM login_events").fetchone()
    assert client.get("/spin", headers=H["admin"]).status_code == 500
    after = sqlite3.connect(db).execute("SELECT COUNT(*) FROM login_events").fetchone()
    assert before == after


def test_an_effectful_procedure_is_dispatched_after_commit(client):
    SENT.clear()
    resp = client.post("/items/1/notify", headers=H["alice"])
    assert resp.status_code == 200
    assert SENT == [("send_digest", "alice@example.com", "ALPHA TASK")]
    # a failing effect never fails the request
    resp = client.post("/items/4/notify", headers=H["alice"])
    assert resp.status_code == 200 and len(SENT) == 1


def test_whole_value_results(client, db: Path):
    import sqlite3

    lines = client.get("/digest", headers=H["bob"]).json()
    assert lines == [{"id": 3, "text": "* gamma ray @2000-01-07", "slug": "gamma-ray"}]
    written = client.post("/events/expand", headers=H["admin"], json={"n": 2, "kind": "ok"})
    assert written.json() == [
        {"id": 11, "user_id": 1, "minute": 100, "kind": "ok"},
        {"id": 12, "user_id": 1, "minute": 107, "kind": "ok"},
    ]
    rows = sqlite3.connect(db).execute("SELECT COUNT(*) FROM login_events").fetchone()
    assert rows == (12,)
    assert client.put("/items/3/rescore", headers=H["bob"], json={}).json() == {
        "id": 3,
        "score": 4.87,
    }


def test_natives_procedures_call_need_implementations(db: Path):
    plans = list(_plans().values())
    with pytest.raises(ValueError, match="slugify"):
        build_app(plans, db, natives={"send_digest": t2prog.NATIVES["send_digest"]})


# ---- the generated app ---------------------------------------------------------------------------


@pytest.fixture
def generated(tmp_path: Path, db: Path, monkeypatch) -> Iterator[object]:
    result, code = compile_source_to_fastapi(t2prog.PROGRAM, t2prog.PROFILE)
    assert code is not None
    gen_dir = tmp_path / "gen"
    gen_dir.mkdir()
    shutil.copyfile(KIT_PATH, gen_dir / "platform_kit.py")
    shutil.copyfile(t2prog.NATIVES_FILE, gen_dir / "natives.py")
    (gen_dir / "t2_app.py").write_text(code)
    shutil.copyfile(db, gen_dir / "app.db")
    monkeypatch.chdir(gen_dir)
    monkeypatch.syspath_prepend(str(gen_dir))
    for name in ("platform_kit", "natives", "t2_app"):
        sys.modules.pop(name, None)
    try:
        yield importlib.import_module("t2_app")
    finally:
        for name in ("platform_kit", "natives", "t2_app"):
            sys.modules.pop(name, None)


def test_the_generated_app_contains_the_instrumented_procedures(generated):
    source = "".join(generated.T2_SOURCE)
    assert "def replay_logins(_lt_fuel, events):" in source
    assert "_lt_fuel.enter()" in source and "_lt_fuel.tick()" in source
    assert "@procedure" not in source
    assert callable(generated.T2["replay_logins"])
    assert generated.T2_FUEL == t2prog.FUEL
    assert "procedure:notify" in generated.EFFECTS


def test_the_generated_app_answers_r620_and_logs_it(generated, caplog):
    with TestClient(generated.app) as c, caplog.at_level(logging.ERROR, logger="lattice.t2"):
        resp = c.get("/spin", headers=H["alice"])
    assert resp.status_code == 500
    assert "error R620: procedure ran out of fuel" in resp.json()["detail"]
    assert [r.getMessage() for r in caplog.records if r.name == "lattice.t2"] == [
        resp.json()["detail"]
    ]


def test_the_generated_t2_app_is_ruff_formatted(tmp_path: Path):
    ruff = shutil.which("ruff", path=str(Path(sys.executable).parent)) or shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff is not installed")
    plans = list(_plans().values())
    path = tmp_path / "app.py"
    path.write_text(generate_fastapi(plans, t2prog.PROGRAM))
    done = subprocess.run(
        [ruff, "format", "--check", "--isolated", "--line-length", "100", str(path)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_an_app_without_procedures_has_no_t2_code():
    _, code = compile_source_to_fastapi(effects.PROGRAM, effects.PROFILE)
    assert code is not None
    assert "T2_SOURCE" not in code and "class T2Fuel" not in code
