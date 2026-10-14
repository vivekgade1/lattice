"""``GET /api/contracts/impact`` (API v1.18) on the real Studio server and the mock: the advisory,
read-only preview of a candidate provider version, equal to ``lattice contracts impact PROVIDER
--candidate X --json`` (A1), over the synthetic two-provider workspace of
tests/studio/contract_world.py built under ``tmp_path``. No app is started, no LLM is used."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from lattice import workspace as ws
from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.server import StubGenerator, make_server
from tests.contracts.helpers import World
from tests.studio.contract_world import make_world, types, without_elapsed
from tests.studio.test_server import make_project, request

TOP = {
    "format", "workspace", "provider", "candidate", "current", "advisory", "static_only",
    "baseline", "checked", "consumers", "counts", "problems", "exit_code", "elapsed_s",
}  # fmt: skip
ROW = {
    "consumer", "provider", "contract", "label", "error", "imports", "pinned", "version_changed",
    "spec_hash_changed", "spec_ref_changed", "current_label", "findings", "gaps", "counts", "live",
}  # fmt: skip
URL = "/api/contracts/impact"


def serve_bg(srv: Any) -> str:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def stop(srv: Any) -> None:
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def world(tmp_path: Path) -> World:
    return make_world(tmp_path / "w")


@pytest.fixture()
def served(world: World, tmp_path: Path) -> Iterator[str]:
    solo = make_project(tmp_path / "solo", repo=False)
    srv = make_server(solo, generator=StubGenerator(), workspace=world.root / ws.MANIFEST)
    base = serve_bg(srv)
    try:
        yield base
    finally:
        stop(srv)


def get(base: str, provider: str | None, candidate: str | None, prefix: str = "") -> Any:
    qs = "&".join(
        f"{k}={quote(v)}"
        for k, v in (("provider", provider), ("candidate", candidate))
        if v is not None
    )
    status, body = request("GET", f"{base}{prefix}{URL}?{qs}")
    assert status == 200, body
    return body


def files(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------- A1: the real server vs the CLI


def test_the_answer_is_the_cli_json_key_for_key(served: str, world: World) -> None:
    for cand in ("HEAD", str(world.provider)):
        out = get(served, "prov", cand)
        rc, cli = world.run("impact", "prov", "--candidate", cand)
        assert set(out) == TOP and out["checked"] == 1 and out["exit_code"] == rc
        assert without_elapsed(out) == without_elapsed(cli)
    assert [set(r) for r in out["consumers"]] == [ROW]
    assert [r["label"] for r in out["consumers"]] == ["contradiction"]
    assert out["candidate"]["kind"] == "path" and out["advisory"] is True


def test_it_answers_under_a_project_prefix_and_writes_nothing(served: str, world: World) -> None:
    before = files(world.root)
    first = get(served, "prov", "HEAD")
    again = get(served, "prov", "HEAD", prefix="/p/solo")
    assert without_elapsed(first) == without_elapsed(again)
    status, body = request("POST", served + URL, {})
    assert status == 404 and body["detail"] == "no such route"
    assert files(world.root) == before


def test_the_last_repeated_value_wins(served: str) -> None:
    status, out = request("GET", f"{served}{URL}?provider=ghost&provider=prov&candidate=HEAD")
    assert status == 200 and out["provider"] == "prov" and out["checked"] == 1


def test_a_relative_directory_is_relative_to_the_manifest(served: str, world: World) -> None:
    out = get(served, "prov", "prov")
    assert out["candidate"]["kind"] == "path"
    assert out["candidate"]["resolved"] == str(world.provider.resolve())


def test_missing_arguments_unknown_provider_and_bad_candidate(served: str) -> None:
    for prov, cand in ((None, "HEAD"), ("prov", None), ("", "HEAD"), ("prov", "")):
        out = get(served, prov, cand)
        assert set(out) == TOP and out["workspace"] == "w" and out["exit_code"] == 2
        assert out["consumers"] == [] and out["checked"] == 0
        assert [(p["code"], p["severity"]) for p in out["problems"]] == [
            ("missing_argument", "error")
        ]
    ghost = get(served, "ghost", "HEAD")
    assert ghost["exit_code"] == 2 and ghost["problems"][-1]["code"] == "unknown_project"
    bad = get(served, "prov", "no-such-ref")
    assert bad["exit_code"] == 2 and bad["problems"][-1]["code"] == "unknown_candidate"
    assert bad["consumers"] == [] and "no-such-ref" in bad["problems"][-1]["message"]


def test_a_provider_nobody_points_to_has_no_consumers(served: str) -> None:
    out = get(served, "cons", "no-such-ref")
    assert out["exit_code"] == 0 and out["checked"] == 0 and out["consumers"] == []
    assert out["problems"][-1]["code"] == "no_consumers"
    assert out["candidate"]["resolved"] is None


def test_the_manifest_is_read_on_every_request(served: str, world: World) -> None:
    assert get(served, "prov", "HEAD")["checked"] == 1
    (world.root / ws.MANIFEST).write_text("[[[")
    out = get(served, "prov", "HEAD")
    assert set(out) == TOP and out["workspace"] is None and out["exit_code"] == 2
    assert [(p["code"], p["severity"]) for p in out["problems"]] == [("unreadable", "error")]


def test_no_workspace_answers_every_key_with_exit_2(tmp_path: Path) -> None:
    srv = make_server(make_project(tmp_path / "solo", repo=False), generator=StubGenerator())
    base = serve_bg(srv)
    try:
        out = get(base, "prov", "HEAD")
    finally:
        stop(srv)
    assert set(out) == TOP and out["workspace"] is None and out["exit_code"] == 2
    assert out["problems"] == [] and out["consumers"] == [] and out["checked"] == 0


# ---------------------------------------------------------------- the mock


def test_the_mock_has_the_real_shape(served: str) -> None:
    real = get(served, "prov", "HEAD")
    srv = make_mock()
    mbase = serve_bg(srv)
    try:
        mock = get(mbase, "scheduler", "release-2")
        assert get(mbase, "scheduler", "release-2", prefix="/p/lattice-studio") == mock
        assert request("POST", mbase + URL, {})[0] == 404
        studio = get(mbase, "studio", "HEAD")
        other = get(mbase, "scheduler-web", "HEAD")
        missing = get(mbase, "scheduler", "")
        ghost = get(mbase, "ghost", "HEAD")
        srv.workspace_enabled = False
        none = get(mbase, "scheduler", "HEAD")
    finally:
        stop(srv)
    assert set(mock) == TOP == set(real) and types(mock) == types(real)
    assert mock["checked"] == len(mock["consumers"]) == 1 and mock["exit_code"] == 1
    row = mock["consumers"][0]
    assert set(row) == ROW and types(row) == types(real["consumers"][0])
    assert (row["consumer"], row["provider"], row["label"]) == (
        "scheduler-web", "scheduler", "contradiction",
    )  # fmt: skip
    assert [f["class"] for f in row["findings"]] == ["BROKEN", "CHANGED"]
    assert set(mock["candidate"]) == set(real["candidate"])
    assert mock["candidate"]["input"] == "release-2" and mock["candidate"]["kind"] == "ref"
    assert set(mock["candidate"]["version"]) == {"spec_hash", "git_ref", "dirty"}
    assert mock["counts"] == {"contradiction": 1, "regression": 0, "unaffected": 0, "error": 0}
    assert studio["consumers"][0]["live"] == {"status": "not_run", "reason": "static-only"}
    assert studio["consumers"][0]["label"] == "unaffected" and studio["exit_code"] == 0
    assert other["problems"][-1]["code"] == "no_consumers" and other["exit_code"] == 0
    assert missing["problems"][-1]["code"] == "missing_argument" and missing["exit_code"] == 2
    assert ghost["problems"][-1]["code"] == "unknown_project" and ghost["exit_code"] == 2
    assert set(none) == TOP and none["workspace"] is None and none["exit_code"] == 2
