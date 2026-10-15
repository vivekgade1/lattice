"""``--studio-url`` with several projects (lattice/mcp/server.py, plumbing D2 as amended): a
non-first project the Studio serves, found by path, gets its READ tools from ``<base>/p/<name>``;
change_propose, check_run and spec_ask stay local. A fake Studio on loopback logs every request.
Real ``python -m lattice.mcp serve`` processes; no LLM, no API key."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import lattice
from lattice.mcp.server import StudioReads, studio_base, studio_listing, studio_name_of
from lattice.mcp.tools import ToolError
from tests.mcp.plumbing_support import (
    INIT,
    FakeStudio,
    clean_env,
    rpc,
    run_server,
    tool_call,
    tool_result,
)

CODE = Path(lattice.__file__).resolve().parents[1]
NOT_A_PROJECT = "is not a Lattice project yet"


@pytest.fixture
def fx(tmp_path: Path):
    """Three bare directories (a, b, c: no spec) and a fake Studio serving a and b."""
    dirs = {n: tmp_path / n for n in "abc"}
    for d in dirs.values():
        d.mkdir()
    studio = FakeStudio(dirs["a"], dirs["b"]).start()
    yield dirs, studio, tmp_path
    studio.stop()


def serve(
    fx_: tuple[Any, FakeStudio, Path], projects: list[Path | str], url: str, calls: list[Any]
) -> list[str]:
    _dirs, _studio, tmp = fx_
    argv: list[str] = []
    for p in projects:
        argv += ["--project", str(p)]
    argv += ["--studio-url", url]
    reqs = [rpc(1, "initialize", INIT)]
    reqs += [tool_call(10 + i, name, **args) for i, (name, args) in enumerate(calls)]
    lines, err = run_server(argv, reqs, cwd=tmp, env=clean_env(CODE))
    assert len(lines) == len(reqs), (lines, err)
    return lines[1:]


def rule_text(line: str) -> str:
    err, data = tool_result(line)
    assert not err, data
    return data["rules"][0]["text"]


def test_u1_the_listing_is_asked_once_per_other_project_and_by_path(fx) -> None:
    dirs, studio, _ = fx
    out = serve(
        fx,
        [dirs["a"], dirs["b"], dirs["c"]],
        studio.url + "/p/alpha",
        [("model_get", {"project": p}) for p in ("a", "b", "c", "b")],
    )
    assert rule_text(out[0]) == "ALPHA via studio"
    assert rule_text(out[1]) == "BETA via studio"
    err, text = tool_result(out[2])
    assert err and NOT_A_PROJECT in text and str(dirs["c"].resolve()) in text
    assert rule_text(out[3]) == "BETA via studio"
    assert len(studio.requests("GET", "/api/projects")) == 2, studio.log
    assert not [r for r in studio.log if "/p/c" in r[1]], "no request names c"
    assert studio.requests("GET", "/p/beta/api/model"), "b's reads go to /p/beta"


def test_u2_the_bare_base_url_works_too(fx) -> None:
    dirs, studio, _ = fx
    out = serve(
        fx,
        [dirs["a"], dirs["b"], dirs["c"]],
        studio.url,
        [("model_get", {"project": p}) for p in ("a", "b", "c")],
    )
    assert rule_text(out[0]) == "ALPHA via studio"
    assert rule_text(out[1]) == "BETA via studio"
    assert NOT_A_PROJECT in tool_result(out[2])[1]
    assert studio.requests("GET", "/api/model") and studio.requests("GET", "/p/beta/api/model")


def test_u3_a_studio_without_the_listing_falls_back_to_local(fx) -> None:
    dirs, studio, _ = fx
    studio.listing_status = 404
    out = serve(
        fx, [dirs["a"], dirs["b"]], studio.url + "/p/alpha",
        [("model_get", {"project": "a"}), ("model_get", {"project": "b"})],
    )  # fmt: skip
    assert rule_text(out[0]) == "ALPHA via studio"
    err, text = tool_result(out[1])
    assert err and NOT_A_PROJECT in text
    assert not studio.requests(None, "/p/beta/")


def test_u4_a_failed_listing_is_an_error_and_is_not_remembered(fx) -> None:
    dirs, studio, _ = fx
    studio.listing_status = 500
    out = serve(
        fx, [dirs["a"], dirs["b"]], studio.url + "/p/alpha",
        [("model_get", {"project": "b"}), ("model_get", {"project": "b"})],
    )  # fmt: skip
    for line in out:
        err, text = tool_result(line)
        assert err and "Studio answered 500" in text and text.endswith("(project: b)"), text
    assert len(studio.requests("GET", "/api/projects")) == 2


def test_u5_one_project_never_asks_for_the_listing(fx) -> None:
    dirs, studio, _ = fx
    out = serve(fx, [dirs["a"]], studio.url + "/p/alpha", [("model_get", {})])
    assert rule_text(out[0]) == "ALPHA via studio"
    assert not studio.requests(None, "/api/projects")


def test_u6_a_symlinked_project_path_still_matches(fx) -> None:
    dirs, studio, tmp = fx
    link = tmp / "b-link"
    link.symlink_to(dirs["b"], target_is_directory=True)
    out = serve(
        fx, [dirs["a"], link], studio.url + "/p/alpha", [("model_get", {"project": "b-link"})]
    )
    assert rule_text(out[0]) == "BETA via studio"


def test_the_other_projects_writes_are_not_routed_to_the_studio(fx) -> None:
    """Amendment 1: change_propose, check_run and spec_ask of a served non-first project answer
    as without the Studio (here: the local `is not a Lattice project yet`), and no POST of
    theirs reaches the Studio."""
    dirs, studio, _ = fx
    out = serve(
        fx, [dirs["a"], dirs["b"]], studio.url + "/p/alpha",
        [
            ("change_propose", {"project": "b", "request": "make notes public"}),
            ("check_run", {"project": "b"}),
            ("spec_ask", {"project": "b", "question": "who owns notes?"}),
            ("model_get", {"project": "b"}),
        ],
    )  # fmt: skip
    for line in out[:3]:
        err, text = tool_result(line)
        assert err and NOT_A_PROJECT in text and text.endswith("(project: b)"), text
    assert rule_text(out[3]) == "BETA via studio"
    assert not studio.requests("POST"), studio.log


def test_reads_of_changes_and_the_listing_of_changes_come_from_the_studio(fx) -> None:
    dirs, studio, _ = fx
    studio.changes["c7"] = {"id": "c7", "status": "ready", "request": "r"}
    out = serve(
        fx, [dirs["a"], dirs["b"]], studio.url + "/p/alpha",
        [
            ("change_get", {"project": "b", "id": "c7"}),
            ("change_list", {"project": "b"}),
            ("spec_lint", {"project": "b"}),
            ("project_status", {"project": "b"}),
        ],
    )  # fmt: skip
    err, got = tool_result(out[0])
    assert not err and got["status"] == "ready"
    assert tool_result(out[1])[1]["count"] == 0
    assert tool_result(out[2])[1]["count"] == 0
    assert tool_result(out[3])[1]["backend"] == "http"
    assert studio.requests("GET", "/p/beta/api/changes/c7")


def test_studio_base_strips_one_trailing_name() -> None:
    assert studio_base("http://h:1/p/alpha") == "http://h:1"
    assert studio_base("http://h:1/p/alpha/") == "http://h:1"
    assert studio_base("http://h:1/") == "http://h:1"
    assert studio_base("http://h:1/p/a%20b") == "http://h:1"
    assert studio_base("http://h:1/x/p/alpha") == "http://h:1/x"


def test_name_matching_is_by_path_and_skips_malformed_rows(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    rows = [
        "junk", {"name": 1, "root": str(real)}, {"name": "x"},
        {"name": "hub-name", "root": str(link)}, {"name": "later", "root": str(real)},
    ]  # fmt: skip
    assert studio_name_of(rows, real) == "hub-name"
    assert studio_name_of(rows, tmp_path) is None
    assert studio_name_of([], real) is None


def test_listing_errors_are_tool_errors() -> None:
    with pytest.raises(ToolError, match="cannot reach Studio at http://127.0.0.1:9"):
        studio_listing("http://127.0.0.1:9")


class Rec:
    """A backend double that records the calls it gets."""

    root = Path("/x")
    configured = True

    def __init__(self, tag: str) -> None:
        self.tag, self.calls = tag, []

    def model(self):
        self.calls.append("model")
        return {"tag": self.tag}

    def lint(self):
        self.calls.append("lint")
        return []

    def ask(self, body):
        self.calls.append("ask")
        return {"tag": self.tag}

    def new_change(self, body):
        self.calls.append("new_change")
        return {"id": "mine"}

    def get_change(self, cid):
        self.calls.append(f"get_change:{cid}")
        return {"tag": self.tag, "id": cid}

    def check(self, layers, timeout):
        self.calls.append("check")
        return {"tag": self.tag}


def test_studio_reads_splits_reads_from_writes_and_keeps_its_own_change_local() -> None:
    http, local = Rec("http"), Rec("local")
    box = StudioReads(http, local)
    assert box.model() == {"tag": "http"} and box.lint() == []
    assert box.ask({}) == {"tag": "local"} and box.check(None, 1.0) == {"tag": "local"}
    assert box.new_change({}) == {"id": "mine"}
    assert box.get_change("mine")["tag"] == "local", "the change this process made is local"
    assert box.get_change("theirs")["tag"] == "http"
    assert http.calls == ["model", "lint", "get_change:theirs"]
    assert box.kind == "http" and box.configured is local.configured


def test_a_single_project_server_still_answers_the_studio_alone(fx) -> None:
    """The one-project server's tools/list is the same with and without the extra code path."""
    dirs, studio, tmp = fx
    lines, _ = run_server(
        ["--project", str(dirs["a"]), "--studio-url", studio.url + "/p/alpha"],
        [rpc(1, "initialize", INIT), rpc(2, "tools/list")],
        cwd=tmp, env=clean_env(CODE),
    )  # fmt: skip
    names = [t["name"] for t in json.loads(lines[1])["result"]["tools"]]
    assert "project_list" in names and "workspace_impact" not in names
    assert os.path.isdir(dirs["a"])
