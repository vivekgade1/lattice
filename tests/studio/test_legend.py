"""The legend of the linked-question list (lattice/contracts/linked.py ``LEGEND``, API.md v1.20,
F4 of docs/results/2026-10-14-agent-run-3-prereg.md): one source, the last key of every list
body: the CLI's ``--json``, the MCP tool, ``GET /api/linked-questions`` and the mock. Listing
never changes a stored question."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from lattice import workspace as ws
from lattice.contracts import linked
from lattice.mcp.projects import KnownProject, ProjectRouter
from lattice.mcp.tools import PendingBackend
from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.server import StubGenerator, make_hub_server
from tests.studio.test_linked_api import Pair, drift_201, git, serve_bg, stop
from tests.studio.test_server import request

TEXT = {
    "CHANGED*": "CHANGED on an endpoint this consumer imports (the finding has relied: true): the "
    "provider's contract changed there since the pin, but none of the consumer's imported claims "
    "stopped holding (that would be BROKEN)",
    "{}": "a templated path parameter, any id or name: POST /invoices/{}/pay is the pay endpoint "
    "of every invoice",
}


@pytest.fixture()
def pair(tmp_path: Path) -> Pair:
    return Pair(tmp_path / "w")


@pytest.fixture()
def hub(pair: Pair) -> Iterator[str]:
    srv = make_hub_server(
        [("prov", pair.w.provider), ("cons", pair.w.consumer)],
        generator=StubGenerator(),
        workspace=pair.manifest,
    )
    base = serve_bg(srv)
    try:
        yield base
    finally:
        stop(srv)


def question_files(state: Path) -> dict[str, str]:
    root = state / linked.DIR
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_the_legend_is_the_registered_text() -> None:
    assert linked.LEGEND == TEXT
    assert list(linked.LEGEND) == ["CHANGED*", "{}"]


def test_list_body_ends_with_a_copy_of_the_legend() -> None:
    for status in (None, "all", "open", "resolved"):
        body = linked.list_body("c", "missing", None, status)
        assert list(body)[-1] == "legend" and body["legend"] == TEXT
        assert body["legend"] is not linked.LEGEND
        body["legend"]["x"] = "y"
        assert "x" not in linked.LEGEND


def test_cli_mcp_api_and_mock_carry_the_same_legend(pair: Pair, hub: str) -> None:
    git(pair.w.provider, "branch", "pinned")
    pair.w.edit(drift_201())
    git(pair.w.provider, "commit", "-qam", "drift")
    git(pair.w.provider, "branch", "brk")
    rc, out = pair.w.run("notify", "prov", "--candidate", "brk")
    assert rc == 0, out
    before = question_files(pair.state)
    assert before

    router = ProjectRouter(
        [KnownProject("cons", pair.w.consumer.resolve(), "workspace")],
        lambda p: PendingBackend(p.root),
        workspace=ws.load(pair.manifest),
    )
    srv = make_mock()
    mbase = serve_bg(srv)
    try:
        for status in (None, "open", "dismissed"):
            q = f"?status={status}" if status else ""
            args = ["--status", status] if status else []
            _rc, cli = pair.w.run("questions", "list", "cons", *args)
            mcp = router.call(
                "linked_question_list",
                {"consumer": "cons", **({"status": status} if status else {})},
            )
            _s, api = request("GET", f"{hub}/p/cons/api/linked-questions{q}")
            _s, mock = request("GET", f"{mbase}/p/scheduler-web/api/linked-questions{q}")
            for body in (cli, mcp, api, mock):
                assert list(body)[-1] == "legend" and body["legend"] == TEXT
            assert api == cli == mcp
        # the mock's other projects and the no-question case carry it too
        _s, other = request("GET", f"{mbase}/p/lattice-studio/api/linked-questions")
        assert other["questions"] == [] and other["legend"] == TEXT
    finally:
        stop(srv)
    assert question_files(pair.state) == before, "listing changed a stored question"


def test_an_empty_list_has_the_legend_too(pair: Pair, hub: str) -> None:
    _s, api = request("GET", f"{hub}/p/cons/api/linked-questions")
    assert api["questions"] == [] and api["legend"] == TEXT
    _rc, cli = pair.w.run("questions", "list", "cons")
    assert cli == api
