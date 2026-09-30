"""The proof apps (spec 2026-09-29 ui-a §5.2): every API program in the codegen differential
registry, plus the FastAPI template."""

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


@functools.cache
def app(name: str) -> App:
    if name == "template":
        # the suite's paths are relative to the API prefix; the template's plans carry it
        reqs = tuple(
            Request(c.method, API + c.path, dict(c.params or {}), c.json, c.form)
            for c in TEMPLATE_SUITE
        )
        return App(name, tuple(template_plans()), reqs)
    source, task = PROGRAMS[name]
    result, files = compile_source_to_ts(source, setup_of(name).profile)
    assert files is not None, result.diagnostics
    plans = tuple(lower_api(result.program))
    reqs = tuple(Request(c.method, c.path, dict(c.query), c.json) for c in task.cases)
    return App(name, plans, reqs)


APP_NAMES = sorted(PROGRAMS) + ["template"]


# ---- L3: the generated FastAPI app over real HTTP -------------------------------------------

ROOT = Path(__file__).resolve().parents[3]


def _admin_tokens() -> dict[str, str]:
    """A token of a user every endpoint of the app admits (the admin role is admitted by every
    endpoint, codegen.py `authenticate`), per app, from the app's seed data."""
    from bench.api_datasets import TOKENS as DATASET_TOKENS
    from bench.template_app.seed import TEMPLATE_USERS
    from bench.template_profile import create_access_token
    from tests.api import effects, hardening, loops, realapps, solo, t2prog

    modules = {
        **dict.fromkeys(("realapps", "partb", "gaps", "mine", "round2", "n1b"), realapps.TOKENS),
        **dict.fromkeys(("t2", "t2go"), t2prog.TOKENS),
        "loops": loops.TOKENS,
        "effects": effects.TOKENS,
        "hardening": hardening.TOKENS,
    }
    out = {name: modules.get(name, DATASET_TOKENS)["admin"] for name in PROGRAMS}
    # solo*: NoRoles (no admin role exists); alice is an active user every
    # Customer endpoint admits
    for name in ("solo", "solo_answers", "solo_runtime"):
        out[name] = solo.TOKENS["alice"]
    out["template"] = create_access_token(TEMPLATE_USERS["admin"][2])  # the seeded superuser
    return out


ADMIN_TOKENS: dict[str, str] = _admin_tokens()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def serve(name: str, workdir: Path) -> Iterator[str]:
    """The generated FastAPI app of `name`, on a fresh seeded app.db, over real HTTP
    (uvicorn on an ephemeral port); yields its base URL."""
    if name == "template":
        from bench.template_app.seed import build_template_db
        from bench.template_profile import template_profile, template_program

        _res, code = compile_source_to_fastapi(template_program(), template_profile(),
                                               natives_module="bench.template_profile")  # fmt: skip
        build_template_db(workdir / "app.db")
    else:
        source, _task = PROGRAMS[name]
        setup = setup_of(name)
        _res, code = compile_source_to_fastapi(source, setup.profile)
        if setup.natives_file is not None:  # imported by the generated app as `natives`
            shutil.copyfile(setup.natives_file, workdir / "natives.py")
        setup.build(workdir / "app.db")
    assert code is not None, name
    (workdir / "app.py").write_text(code)
    shutil.copyfile(KIT_PATH, workdir / "platform_kit.py")
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": f"{workdir}{os.pathsep}{ROOT}"}
    log = (workdir / "server.log").open("w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--port", str(port), "--log-level", "warning"],
        cwd=workdir, env=env, stdout=log, stderr=subprocess.STDOUT,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                log_text = (workdir / "server.log").read_text()
                raise RuntimeError(f"{name}: the app exited\n{log_text}")
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.2):
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
        log.close()
