"""L3 (spec 2026-09-29 ui-a §5.1 claims 2 and 3) over real HTTP, for an admitted caller."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lattice.check.api_info import ParamSpec
from lattice.tsgen import generate_ts
from tests.tsgen.gate.apps import ADMIN_TOKENS, APP_NAMES, app, serve
from tests.tsgen.gate.corpus import typed_corpus
from tests.tsgen.node import run_node

DRIVER = Path(__file__).resolve().parent / "gate" / "http_driver.ts"


def _run(name: str, tmp: Path, cases: list[dict], token: str | None) -> list[dict]:
    client = tmp / "client"
    client.mkdir()
    for fname, text in generate_ts(app(name).plans).items():
        (client / fname).write_text(text)
    server = tmp / "server"
    server.mkdir()
    with serve(name, server) as base:
        done = run_node([str(DRIVER), client.as_uri(), base, token or ""], cwd=tmp,
                        stdin=json.dumps(cases, allow_nan=False))  # fmt: skip
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout)


def test_every_app_has_an_admin_token() -> None:
    assert set(ADMIN_TOKENS) == set(APP_NAMES)


@pytest.mark.parametrize("name", APP_NAMES)
def test_http_differential(name: str, tmp_path: Path) -> None:
    cases = typed_corpus(app(name))
    results = _run(name, tmp_path, cases, ADMIN_TOKENS[name])
    guarded = {p.name for p in app(name).plans if p.auth != "Public"}
    bad = []
    admitted = 0
    for c, r in zip(cases, results, strict=True):
        err = r.get("error") or {}
        status = 200 if r["ok"] else err.get("status")
        if status not in (401, 403) and (c["endpoint"] in guarded or not guarded):
            admitted += 1  # on an authenticated endpoint, when the app has one
        server_422 = not r["ok"] and status == 422
        if bool(r["local"]) != server_422:
            bad.append((c["name"], "claim 2", r["local"], err))
        elif server_422 and r["local"] != err["detail"]:
            bad.append((c["name"], "claim 2 detail", r["local"], err["detail"]))
        unexpected = not r["ok"] and err.get("kind") == "unexpected"
        if unexpected and (err["reason"] == "decode" or err["status"] < 500):
            bad.append((c["name"], "claim 3", err))
    assert admitted > 0, "the admin token was never admitted"
    assert not bad, bad[:20]


def test_unadmitted_caller_gets_auth_error_first(tmp_path: Path) -> None:
    """Review Focus 1 / spec delta 1: the server authenticates before it validates."""
    name = "realapps"

    def int_query(s: ParamSpec) -> bool:
        return s.source == "query" and s.scalar is not None and s.scalar.value == "Int"

    plan = next(p for p in app(name).plans if p.auth != "Public" and any(map(int_query, p.params)))
    param = next(s.name for s in plan.params if int_query(s))
    cases = [{"name": "bad token, bad param", "endpoint": plan.name,
              "params": {param: {"t": "json", "v": "not-a-number"}}}]  # fmt: skip
    [r] = _run(name, tmp_path, cases, "no-such-token")
    assert r["local"] != []  # the client alone says 422
    assert r["error"]["status"] == 401  # the server answers the auth error first


def test_unroutable_path_values(tmp_path: Path) -> None:
    """Review Focus 2 / R-a: sent, never a 422 from the server, never a crash."""
    name = "realapps"
    plan = next(p for p in app(name).plans if "{" in p.path)
    param = next(s for s in plan.params if s.source == "path")
    results = _run(name, tmp_path, [
        {"name": f"R-a {v!r}", "endpoint": plan.name, "params": {param.name: {"t": "str", "v": v}}}
        for v in ["", ".", "..", "a/b"]
    ], ADMIN_TOKENS[name])  # fmt: skip
    for r in results:
        assert r["ok"] or r["error"]["status"] != 422 or r["local"] != []
