"""``[mcp] allow_writes`` (HD-3, HD-4; pre-registered in
``docs/results/2026-10-16-hardening-findings-prereg.md``, decisions D3 and D4, probe P6).

* ``writes_allowed`` in ``GET /api/mcp`` is true only for a server that is ``connected`` AND named
  by ``allow_writes``: it says what a ``change`` session gets (``external_tools`` already denies
  the writes of every other status).
* Matching stays exact and case-sensitive (fail closed). A name that matches no server the
  discovery listed is reported once per (name, listed set) through ``warn``, with a hint when a
  listed name matches it case-insensitively; never on a failed or empty discovery, nor while a
  server is ``pending``, nor without ``warn``.

No process runs: the discovery is injected.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from lattice.studio.mcp_intake import McpIntake, external_tools

LINEAR = "claude.ai Linear"
LI = "mcp__claude_ai_Linear__"
TOOLS = {
    "read": [LI + "list_issues"],
    "write": [LI + "update_issue"],
    "unknown": [LI + "triage_issue"],
}


def srv(name: str, status: str = "connected") -> dict[str, Any]:
    tools = TOOLS if status == "connected" else {"read": [], "write": [], "unknown": []}
    return {"name": name, "status": status, "source": "claudeai", "tools": tools}


class Fake:
    """An injected discovery; ``d`` is the answer it gives (mutable between calls)."""

    def __init__(self, d: dict[str, Any]) -> None:
        self.d = d
        self.calls = 0

    def __call__(self, *_a: Any, **_kw: Any) -> dict[str, Any]:
        self.calls += 1
        return json.loads(json.dumps(self.d))


def intake(fake: Fake, allow: Sequence[str], lines: list[str] | None, tmp_path: Path) -> McpIntake:
    return McpIntake(
        "claude",
        tmp_path,
        allow_writes=allow,
        discover_fn=fake,
        warn=None if lines is None else lines.append,
    )


def answer(*servers: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"servers": list(servers), "refreshed_at": "2026-10-16T00:00:00Z", **extra}


def line(name: str, hint: str | None = None) -> str:
    tail = "names are case-sensitive" + (f'; did you mean "{hint}"?' if hint else "")
    return (
        f'studio.toml: [mcp] allow_writes names "{name}", which matches no server the '
        f"discovery listed ({tail})"
    )


def test_the_exact_line_with_and_without_a_hint(tmp_path: Path) -> None:
    lines: list[str] = []
    it = intake(Fake(answer(srv(LINEAR))), ["claude.ai linear", "nope"], lines, tmp_path)
    it.view()
    assert lines == [line("claude.ai linear", LINEAR), line("nope")]


def test_the_hint_comes_from_the_prefix_form(tmp_path: Path) -> None:
    lines: list[str] = []
    it = intake(
        Fake(answer(srv(LINEAR))), ["claude_ai_linear", "CLAUDE_AI_LINEAR"], lines, tmp_path
    )
    it.view()
    assert lines == [line("claude_ai_linear", LINEAR), line("CLAUDE_AI_LINEAR", LINEAR)]


def test_the_first_listed_display_name_is_the_hint(tmp_path: Path) -> None:
    lines: list[str] = []
    two = answer(srv("claude.ai LINEAR"), srv(LINEAR))
    intake(Fake(two), ["claude.ai linear"], lines, tmp_path).view()
    assert lines == [line("claude.ai linear", "claude.ai LINEAR")]


def test_a_matching_name_and_its_prefix_form_do_not_warn(tmp_path: Path) -> None:
    lines: list[str] = []
    it = intake(Fake(answer(srv(LINEAR))), [LINEAR, "claude_ai_Linear"], lines, tmp_path)
    it.view()
    assert lines == []


def test_names_are_json_quoted(tmp_path: Path) -> None:
    lines: list[str] = []
    odd = 'a"b\nc é'
    intake(Fake(answer(srv(LINEAR))), [odd], lines, tmp_path).view()
    assert lines == [
        'studio.toml: [mcp] allow_writes names "a\\"b\\nc é", which matches no server the '
        "discovery listed (names are case-sensitive)"
    ]
    assert "\n" not in lines[0]


@pytest.mark.parametrize(
    "d",
    [
        answer(srv(LINEAR), error="boom"),
        answer(error="no init record (the claude process ended or timed out first)"),
        answer(),
        answer(srv(LINEAR), srv("claude.ai Gmail", "pending")),
    ],
    ids=["error-with-servers", "error", "no-servers", "a-pending-server"],
)
def test_no_line_on_error_empty_or_pending(tmp_path: Path, d: dict[str, Any]) -> None:
    lines: list[str] = []
    intake(Fake(d), ["claude.ai linear", "nope"], lines, tmp_path).view()
    assert lines == []


def test_without_warn_nothing_is_reported_and_nothing_breaks(tmp_path: Path) -> None:
    it = intake(Fake(answer(srv(LINEAR))), ["nope"], None, tmp_path)
    assert it.view()["servers"][0]["writes_allowed"] is False


def test_the_same_discovery_three_times_gives_one_line(tmp_path: Path) -> None:
    lines: list[str] = []
    fake = Fake(answer(srv(LINEAR)))
    it = intake(fake, ["nope"], lines, tmp_path)
    it.view()
    it.view(refresh=True)
    it.tools_for("change")
    assert fake.calls == 3 and lines == [line("nope")]


def test_a_changed_listed_set_warns_again_and_a_back_and_forth_does_not(tmp_path: Path) -> None:
    lines: list[str] = []
    fake = Fake(answer(srv(LINEAR)))
    it = intake(fake, ["nope"], lines, tmp_path)
    it.view()
    fake.d = answer(srv("claude.ai Gmail"))
    it.view()
    assert lines == [line("nope"), line("nope")]
    fake.d = answer(srv(LINEAR))
    it.view()  # the first set again: already reported
    assert len(lines) == 2


def test_the_listed_set_is_the_names_not_their_order_or_status(tmp_path: Path) -> None:
    lines: list[str] = []
    fake = Fake(answer(srv(LINEAR), srv("claude.ai Gmail")))
    it = intake(fake, ["nope"], lines, tmp_path)
    it.view()
    fake.d = answer(srv("claude.ai Gmail", "failed"), srv(LINEAR, "needs-auth"))
    it.view()
    assert lines == [line("nope")]


def test_duplicates_warn_once_and_in_order(tmp_path: Path) -> None:
    lines: list[str] = []
    intake(Fake(answer(srv(LINEAR))), ["b", "a", "b", "a", "b"], lines, tmp_path).view()
    assert lines == [line("b"), line("a")]


def test_lattice_is_never_listed_so_it_warns(tmp_path: Path) -> None:
    lines: list[str] = []
    intake(Fake(answer(srv(LINEAR))), ["lattice"], lines, tmp_path).view()
    assert lines == [line("lattice")]


def test_a_listed_but_not_connected_name_does_not_warn(tmp_path: Path) -> None:
    lines: list[str] = []
    for status in ("failed", "needs-auth"):
        intake(Fake(answer(srv(LINEAR, status))), [LINEAR], lines, tmp_path).view()
    assert lines == []


def test_concurrent_discoveries_report_a_name_once(tmp_path: Path) -> None:
    lines: list[str] = []
    it = intake(Fake(answer(srv(LINEAR))), ["nope"], lines, tmp_path)
    ts = [threading.Thread(target=it.view) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert lines == [line("nope")]


def test_matching_stays_exact_so_a_wrong_case_name_opens_nothing(tmp_path: Path) -> None:
    it = intake(Fake(answer(srv(LINEAR))), ["claude.ai linear"], [], tmp_path)
    assert it.view()["servers"][0]["writes_allowed"] is False
    allowed, denied = it.tools_for("change")
    assert TOOLS["write"][0] in denied and TOOLS["write"][0] not in allowed


@pytest.mark.parametrize("listed", [True, False])
@pytest.mark.parametrize("status", ["connected", "pending", "needs-auth", "failed"])
def test_writes_allowed_is_true_only_for_a_connected_listed_server(
    tmp_path: Path, status: str, listed: bool
) -> None:
    it = intake(Fake(answer(srv(LINEAR, status))), [LINEAR] if listed else [], [], tmp_path)
    view = it.view()
    row = view["servers"][0]
    assert row["writes_allowed"] is (status == "connected" and listed)
    # the flag says what a change session gets: its write and unknown tools
    allowed, _ = external_tools(it.discovery()["servers"], "change", it.allow_writes)
    got = TOOLS["write"][0] in allowed
    assert got is row["writes_allowed"]
    assert row["allowed_in"]["change"] == len(allowed)
    # the other keys of the body are untouched
    assert list(view) == ["servers", "refreshed_at", "allowed_in"]
    assert list(row) == ["name", "status", "source", "tools", "allowed_in", "writes_allowed"]
