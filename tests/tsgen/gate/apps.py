"""The proof apps (spec 2026-09-29 ui-a §5.2): every API program in the codegen differential
registry, plus the FastAPI template, plus the gate's own programs (GATE_PROGRAMS)."""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from bench.template_profile import template_plans
from bench.template_suite import API, TEMPLATE_SUITE
from lattice.api.codegen import compile_source_to_fastapi
from lattice.api.ir import EndpointPlan, lower_api
from lattice.tsgen import compile_source_to_ts
from tests.api.test_codegen_differential import KIT_PATH, PROGRAMS, setup_of


@dataclasses.dataclass(frozen=True)
class Request:
    """A request from an existing suite: method, concrete path, query dict, JSON body."""

    method: str
    path: str
    query: dict[str, Any]
    json: Any
    form: dict[str, str] | None = None


@dataclasses.dataclass(frozen=True)
class App:
    name: str
    plans: tuple[EndpointPlan, ...]
    requests: tuple[Request, ...]


# Programs only the client gate runs (on a registry program's profile and database): bounds
# no registry program has. `bounds`: Int and Float bounds beyond 2^53, int and float, exact
# (final review #1), and a Page whose limit is a parameter within +-(2^53 - 1) and whose offset
# is a literal (both "number" mode, #14).
BOUNDS_PROGRAM = """# lattice: v1
@endpoint("GET", "/bounded", auth=Public)
def bounded(
    n: Int(ge=-9223372036854775808, le=999999999999999999) = 0,
    m: Int(ge=-9223372036854775807, le=9223372036854775806) = 0,
    big: Int(ge=9007199254740993) = 9007199254740993,
    x: Float(ge=0, le=999999999999999999) = 0.0,
    y: Float(ge=10000000000000002.0, le=1e300) = 1e17,
    limit: Int(ge=1, le=100) = 5,
) -> Page:
    products = fetch(Products)
    small = where(products, (col("id") <= n) & (col("id") >= m) & (col("id") < big))
    found = where(small, (col("price") >= x) & (col("price") < y))
    slim = select(found, cols=["id", "price"])
    ordered = sort(slim, by=["id"])
    result = page(ordered, limit=limit, offset=0)
    return result


@endpoint("POST", "/bounded", auth=Public)
def bounded_body(
    n: Int(ge=-9223372036854775808, le=999999999999999999),
    x: Float(ge=0, le=999999999999999999),
) -> List:
    products = fetch(Products)
    found = where(products, (col("id") <= n) & (col("price") <= x))
    slim = select(found, cols=["id", "price"])
    ordered = sort(slim, by=["id"])
    return limit(ordered, n=10)
"""
_BIG = 10**18
GATE_PROGRAMS: dict[str, tuple[str, str, tuple[Request, ...]]] = {
    "bounds": (BOUNDS_PROGRAM, "numeric", (
        Request("GET", "/bounded", {}, None),
        Request("GET", "/bounded", {"n": str(_BIG - 1), "x": str(_BIG - 1)}, None),
        Request("GET", "/bounded", {"n": str(_BIG), "m": str(-(2**63))}, None),
        Request("GET", "/bounded", {"big": str(2**53), "y": "10000000000000001"}, None),
        Request("POST", "/bounded", {}, {"n": _BIG - 1, "x": _BIG - 1}),
        Request("POST", "/bounded", {}, {"n": _BIG, "x": float(_BIG)}),
    )),
}  # fmt: skip


def source_and_setup(name: str) -> tuple[str, Any]:
    """A registry or gate program's source and its setup (profile, database)."""
    if name in GATE_PROGRAMS:
        source, setup_name, _reqs = GATE_PROGRAMS[name]
        return source, setup_of(setup_name)
    return PROGRAMS[name][0], setup_of(name)


@functools.cache
def app(name: str) -> App:
    if name == "template":
        # the suite's paths are relative to the API prefix; the template's plans carry it
        reqs = tuple(
            Request(c.method, API + c.path, dict(c.params or {}), c.json, c.form)
            for c in TEMPLATE_SUITE
        )
        return App(name, tuple(template_plans()), reqs)
    source, setup = source_and_setup(name)
    result, files = compile_source_to_ts(source, setup.profile)
    assert files is not None, result.diagnostics
    plans = tuple(lower_api(result.program))
    if name in GATE_PROGRAMS:
        return App(name, plans, GATE_PROGRAMS[name][2])
    reqs = tuple(Request(c.method, c.path, dict(c.query), c.json) for c in PROGRAMS[name][1].cases)
    return App(name, plans, reqs)


APP_NAMES = sorted(PROGRAMS) + ["template"] + sorted(GATE_PROGRAMS)


# ---- L3: the generated FastAPI app over real HTTP -------------------------------------------

ROOT = Path(__file__).resolve().parents[3]


def _admin_tokens() -> dict[str, str]:
    """A token of a user every endpoint of the app admits (the admin role is admitted by every
    endpoint, codegen.py `authenticate`), per app, from the app's seed data."""
    from bench.api_datasets import TOKENS as DATASET_TOKENS
    from bench.template_app.seed import TEMPLATE_USERS
    from bench.template_profile import create_access_token
    from tests.api import effects, hardening, loops, realapps, rolesets, solo, t2prog

    modules = {
        **dict.fromkeys(("realapps", "partb", "gaps", "mine", "round2", "n1b"), realapps.TOKENS),
        **dict.fromkeys(("t2", "t2go"), t2prog.TOKENS),
        "loops": loops.TOKENS,
        "effects": effects.TOKENS,
        "hardening": hardening.TOKENS,
    }
    out = {name: modules.get(name, DATASET_TOKENS)["admin"] for name in PROGRAMS}
    out |= {name: DATASET_TOKENS["admin"] for name in GATE_PROGRAMS}
    # solo*: NoRoles (no admin role exists); alice is an active user every
    # Customer endpoint admits
    for name in ("solo", "solo_answers", "solo_runtime"):
        out[name] = solo.TOKENS["alice"]
    out["template"] = create_access_token(TEMPLATE_USERS["admin"][2])  # the seeded superuser
    # rolesets: exact role sets (auth=Roles(...)) admit the admin only where listed, so
    # no one caller passes every endpoint: the admin, with ADMIN_REFUSED declared
    out["rolesets"] = rolesets.TOKENS["ada"]
    return out


# endpoints that refuse the gate's caller by design (auth=Roles(...) without the admin);
# the gate still decodes their 403 answers
ADMIN_REFUSED: dict[str, list[str]] = {"rolesets": ["buyers_only", "buyers_or_vendors"]}


ADMIN_TOKENS: dict[str, str] = _admin_tokens()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


RESET_PATH = "/__lattice_gate_reset__"

# The server the gate runs: the generated app, plus one test-only route in front of it
# that restores the pristine seeded database (the generated app opens a fresh sqlite
# connection per request -- platform_kit.get_db via codegen `connect()` -- and closes it
# before answering, so replacing the file between requests is safe).
_GATE_SERVER = f"""import os
import shutil

from app import app as generated


async def app(scope, receive, send):
    if scope["type"] == "http" and scope["path"] == {RESET_PATH!r}:
        shutil.copyfile("pristine.db", "app.db.tmp")
        os.replace("app.db.tmp", "app.db")
        for extra in ("app.db-journal", "app.db-wal", "app.db-shm"):
            if os.path.exists(extra):
                os.remove(extra)
        await send({{"type": "http.response.start", "status": 204, "headers": []}})
        await send({{"type": "http.response.body", "body": b""}})
        return
    await generated(scope, receive, send)
"""


@contextlib.contextmanager
def serve(name: str, workdir: Path) -> Iterator[str]:
    """The generated FastAPI app of `name`, on a fresh seeded app.db, over real HTTP
    (uvicorn on an ephemeral port); yields its base URL.

    A POST to RESET_PATH restores app.db from the pristine seed (`pristine.db`) and answers
    204; the L3 driver sends it before each endpoint's cases, so every endpoint's answers
    are decoded against the seed data rather than whatever earlier writes left."""
    if name == "template":
        from bench.template_app.seed import build_template_db
        from bench.template_profile import template_profile, template_program

        _res, code = compile_source_to_fastapi(template_program(), template_profile(),
                                               natives_module="bench.template_profile")  # fmt: skip
        build_template_db(workdir / "pristine.db")
    else:
        source, setup = source_and_setup(name)
        _res, code = compile_source_to_fastapi(source, setup.profile)
        if setup.natives_file is not None:  # imported by the generated app as `natives`
            shutil.copyfile(setup.natives_file, workdir / "natives.py")
        setup.build(workdir / "pristine.db")
    assert code is not None, name
    shutil.copyfile(workdir / "pristine.db", workdir / "app.db")
    (workdir / "app.py").write_text(code)
    (workdir / "gate_server.py").write_text(_GATE_SERVER)
    shutil.copyfile(KIT_PATH, workdir / "platform_kit.py")
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": f"{workdir}{os.pathsep}{ROOT}"}
    command = [sys.executable, "-m", "uvicorn", "gate_server:app", "--port", str(port),
               "--log-level", "warning"]  # fmt: skip
    with (workdir / "server.log").open("w") as log:
        proc = subprocess.Popen(command, cwd=workdir, env=env, stdout=log,
                                stderr=subprocess.STDOUT)  # fmt: skip
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    log_text = (workdir / "server.log").read_text()
                    raise RuntimeError(f"{name}: the app exited\n{log_text}")
                with (
                    contextlib.suppress(OSError),
                    socket.create_connection(("127.0.0.1", port), 0.2),
                ):
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError(f"{name}: the app did not start")
            yield f"http://127.0.0.1:{port}"
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
