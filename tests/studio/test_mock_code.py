"""Studio 2 in mock mode: the code lens, explain, request flow and rebuild endpoints of
lattice/studio/API.md (v1.3), served from fixtures. No LLM, no git, no browser."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from lattice.studio.mock_server import (
    FIXTURE_DIR,
    POLLS_REBUILD_QUEUED,
    POLLS_ROUND,
    flow_key,
    make_server,
    pick_explain_fixture,
)

RUNS = "internal/app/runs.go"
CANCEL = "cell:eve|POST /applications/{}/runs/{}/cancel"


@pytest.fixture()
def base() -> Iterator[str]:
    srv = make_server(0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def call(method: str, url: str, body: Any = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def get(url: str) -> tuple[int, Any]:
    return call("GET", url)


def q(**kw: str) -> str:
    return urllib.parse.urlencode(kw)


def model_rule_ids(base: str) -> set[str]:
    return {r["id"] for r in get(base + "/api/model")[1]["rules"]}


def accept_change(base: str) -> str:
    """Accept the seeded change c0 and return its id."""
    status, _ = call("POST", base + "/api/changes/c0/accept", {})
    assert status == 200
    return "c0"


def run_rebuild(base: str, **body: Any) -> dict[str, Any]:
    """Start a rebuild of c0 and poll it to a final status."""
    cid = accept_change(base)
    status, out = call("POST", base + "/api/rebuild", {"changes": [cid], **body})
    assert status == 200, out
    rid = out["id"]
    for _ in range(40):
        status, rb = get(f"{base}/api/rebuild/{rid}")
        assert status == 200
        if rb["status"] not in ("queued", "running"):
            return rb
    raise AssertionError("rebuild never finished")


# ---------------------------------------------------------------- code lens


def test_code_files_shape(base: str) -> None:
    status, files = get(base + "/api/code/files")
    assert status == 200 and files
    ids = model_rule_ids(base)
    paths = {f["path"] for f in files}
    assert {RUNS, "internal/app/handlers.go", "internal/app/jobs.go"} <= paths
    for f in files:
        assert set(f) == {"path", "language", "size", "rules"}
        assert f["language"] == "go" and f["size"] > 0
        assert f["rules"] and set(f["rules"]) <= ids


def test_code_file_shape_and_annotation_lines(base: str) -> None:
    for f in get(base + "/api/code/files")[1]:
        status, c = get(f"{base}/api/code/file?{q(path=f['path'])}")
        assert status == 200
        assert c["path"] == f["path"] and c["language"] == "go"
        assert c["lines"] and all(isinstance(x, str) for x in c["lines"])
        ids = model_rule_ids(base)
        assert c["annotations"]
        for a in c["annotations"]:
            assert 1 <= a["line"] <= len(c["lines"]), (f["path"], a)
            assert a["rules"] and set(a["rules"]) <= ids
            assert a["coverage"] in ("covered", "failing", "untested")
            assert "expect" not in a
            if "finding" in a:
                assert set(a["finding"]) == {"code", "message"}


def test_annotations_sit_on_the_lines_they_name() -> None:
    index = json.loads((FIXTURE_DIR / "code" / "index.json").read_text(encoding="utf-8"))
    for f in index["files"]:
        lines = (FIXTURE_DIR / "code" / f["source"]).read_text(encoding="utf-8").splitlines()
        for a in f["annotations"]:
            assert a["expect"] in lines[a["line"] - 1], (f["path"], a["line"], a["expect"])
        if f.get("rebuilt_source"):
            rebuilt = (FIXTURE_DIR / "code" / f["rebuilt_source"]).read_text().splitlines()
            assert len(rebuilt) == len(lines)
            diff = [i + 1 for i, (x, y) in enumerate(zip(lines, rebuilt, strict=True)) if x != y]
            assert diff == f["rebuilt_lines"]
    for link in index["links"]:
        f = next(f for f in index["files"] if f["path"] == link["path"])
        n = len((FIXTURE_DIR / "code" / f["source"]).read_text().splitlines())
        assert 1 <= link["line_from"] <= link["line_to"] <= n, link


def test_r44_is_on_the_count_check_and_the_insert(base: str) -> None:
    _, c = get(f"{base}/api/code/file?{q(path=RUNS)}")
    r44 = {a["line"] for a in c["annotations"] if "R44" in a["rules"]}
    texts = {c["lines"][n - 1].strip() for n in r44}
    assert any("if n > 0" in t for t in texts)
    assert any("tx.Create(&r)" in t for t in texts)
    r46 = [a for a in c["annotations"] if "R46" in a["rules"]]
    assert any("cancelRun" in c["lines"][a["line"] - 1] for a in r46)
    assert any(a["coverage"] == "failing" and a["finding"] for a in c["annotations"])


def test_code_file_unknown_is_404(base: str) -> None:
    assert get(f"{base}/api/code/file?{q(path='nope.go')}")[0] == 404
    assert get(base + "/api/code/file")[0] == 422


def test_code_links_for_rule_cell_scenario(base: str) -> None:
    status, links = get(f"{base}/api/code/links?{q(target='rule:R44')}")
    assert status == 200 and links
    for link in links:
        assert set(link) == {"path", "line_from", "line_to", "symbol", "why"}
        assert link["line_from"] <= link["line_to"]
    assert any(link["symbol"] == "startRun" for link in links)
    _, cell = get(f"{base}/api/code/links?{q(target=CANCEL)}")
    assert any(link["symbol"] == "cancelRun" for link in cell)
    _, sc = get(f"{base}/api/code/links?{q(target='scenario:a_second_live_run_is_refused')}")
    assert any(link["symbol"] == "startRun" for link in sc)
    _, none = get(f"{base}/api/code/links?{q(target='rule:R1')}")
    assert none == []
    assert get(base + "/api/code/links")[0] == 422


def test_code_links_for_a_code_location_list_the_rules(base: str) -> None:
    _, c = get(f"{base}/api/code/file?{q(path=RUNS)}")
    n = next(i + 1 for i, t in enumerate(c["lines"]) if "if n > 0" in t)
    status, served = get(f"{base}/api/code/links?{q(target=f'code:{RUNS}:{n}')}")
    assert status == 200
    assert [s["rule"] for s in served][0] == "R44"
    assert set(served[0]) == {"rule", "text", "status"}
    assert get(f"{base}/api/code/links?{q(target=f'code:{RUNS}:9999')}")[0] == 404
    assert get(f"{base}/api/code/links?{q(target='code:nope.go:1')}")[0] == 404


# ---------------------------------------------------------------- explain


def test_explain_fixture_choice() -> None:
    assert pick_explain_fixture("Why does this return 409?") == "explain-r44"
    assert pick_explain_fixture("where is R44 enforced") == "explain-r44"
    assert pick_explain_fixture("is there one live run?") == "explain-r44"
    assert pick_explain_fixture("Why does cancel return 409 here?") == "explain-cancel"
    assert pick_explain_fixture("what colour is the sky") == "explain-not-found"


def test_explain_answers_with_code_citations(base: str) -> None:
    status, a = call(
        "POST", base + "/api/explain", {"question": "Why does this 409?", "selected": []}
    )
    assert status == 200
    assert a["kind"] in ("yes", "no", "value", "not_specified")
    assert a["verified"] is None and a["usage"]["tokens"] > 0
    code = [c for c in a["citations"] if c["target"].startswith("code:")]
    assert code
    ids = model_rule_ids(base)
    for c in a["citations"]:
        if c["target"].startswith("rule:"):
            assert c["target"].removeprefix("rule:") in ids
    for c in code:
        m = re.fullmatch(r"code:(.+):(\d+)", c["target"])
        assert m
        _, f = get(f"{base}/api/code/file?{q(path=m.group(1))}")
        assert f["lines"][int(m.group(2)) - 1].strip() == c["quote"]
    _, ns = call("POST", base + "/api/explain", {"question": "something unrelated"})
    assert ns["kind"] == "not_specified" and ns["citations"] == []
    assert call("POST", base + "/api/explain", {"question": " "})[0] == 422


# ---------------------------------------------------------------- request flow


def test_flow_key_spellings() -> None:
    a = flow_key("POST /configs/{}/runs")
    assert a == flow_key("POST /applications/{}/jobs/{}/runs")
    assert a == flow_key("POST /applications/1/jobs/100/runs")
    assert flow_key("POST /runs/{}/cancel") == flow_key("POST /applications/{}/runs/{}/cancel")


@pytest.mark.parametrize(
    "endpoint",
    ["POST /configs/{}/runs", "POST /runs/{}/cancel", "POST /applications/{}/runs/{}/cancel"],
)
def test_flow_shape_and_rules(base: str, endpoint: str) -> None:
    status, f = get(f"{base}/api/flow?{q(endpoint=endpoint, caller='ann')}")
    assert status == 200
    assert f["endpoint"] == endpoint and f["caller"] == "ann"
    ids = model_rule_ids(base)
    stages = f["stages"]
    assert len(stages) >= 5
    for s in stages:
        assert set(s) == {"stage", "symbol", "path", "line", "rules", "reads", "writes"}
        assert s["rules"] and set(s["rules"]) <= ids
        _, c = get(f"{base}/api/code/file?{q(path=s['path'])}")
        assert 1 <= s["line"] <= len(c["lines"])
    d = f["divergence"]
    if d is not None:
        assert {"stage", "expected", "actual"} <= set(d)
        assert d["stage"] in {s["stage"] for s in stages}


def test_flow_divergence_and_errors(base: str) -> None:
    ep = "POST /runs/{}/cancel"
    _, ann = get(f"{base}/api/flow?{q(endpoint=ep, caller='ann')}")
    assert ann["divergence"]["stage"] == "database write"
    _, eve = get(f"{base}/api/flow?{q(endpoint=ep, caller='eve')}")
    assert eve["divergence"]["stage"] == "access" and "c0" in eve["divergence"]["expected"]
    _, start = get(f"{base}/api/flow?{q(endpoint='POST /configs/{}/runs', caller='ann')}")
    assert start["divergence"] is None
    assert get(f"{base}/api/flow?{q(endpoint='GET /nowhere')}")[0] == 404
    assert get(base + "/api/flow")[0] == 422


# ---------------------------------------------------------------- rebuild


def test_model_build_field_only_on_accepted_changes(base: str) -> None:
    m = get(base + "/api/model")[1]
    assert [c["status"] for c in m["changes"]] == ["ready"]
    assert "build" not in m["changes"][0]
    accept_change(base)
    m = get(base + "/api/model")[1]
    assert m["changes"][0]["status"] == "accepted"
    assert m["changes"][0]["build"] == {"state": "not_built"}


def test_rebuild_refusals(base: str) -> None:
    assert call("POST", base + "/api/rebuild", {"changes": ["c0"]})[0] == 409  # still ready
    assert call("POST", base + "/api/rebuild", {"changes": ["c99"]})[0] == 404
    assert call("POST", base + "/api/rebuild", {"changes": []})[0] == 422
    assert call("POST", base + "/api/rebuild", {"changes": ["c0"], "max_rounds": 0})[0] == 422
    assert get(base + "/api/rebuild/b99")[0] == 404
    assert call("DELETE", base + "/api/rebuild/b99")[0] == 404
    accept_change(base)
    status, out = call("POST", base + "/api/rebuild", {"changes": ["c0"]})
    assert status == 200
    # a change that is rebuilding cannot be rebuilt again
    assert call("POST", base + "/api/rebuild", {"changes": ["c0"]})[0] == 409
    assert out["id"] == "b1"


def test_rebuild_progresses_to_passed(base: str) -> None:
    accept_change(base)
    _, out = call("POST", base + "/api/rebuild", {"changes": ["c0"], "model": "sonnet"})
    rid = out["id"]
    seen: list[dict[str, Any]] = []
    for _ in range(POLLS_REBUILD_QUEUED + 2 * POLLS_ROUND + 3):
        _, rb = get(f"{base}/api/rebuild/{rid}")
        seen.append(rb)
        if rb["status"] == "passed":
            break
    assert seen[0]["status"] == "queued" and seen[0]["rounds"] == []
    assert any(s["status"] == "running" for s in seen)
    final = seen[-1]
    assert final["status"] == "passed"
    assert final["model"] == "sonnet" and final["changes"] == ["c0"]
    assert [r["n"] for r in final["rounds"]] == [1, 2]
    r1, r2 = (r["check"] for r in final["rounds"])
    assert r1["verdict"] == "fail" and len(r1["findings"]) == 2
    assert set(r1["counts"]) == {"A", "C", "D", "X"}
    for f in r1["findings"]:
        assert set(f) == {"id", "title", "target"}
    assert r2["verdict"] == "pass" and r2["findings"] == []
    for r in final["rounds"]:
        assert r["agent_summary"] and r["files_changed"] and r["started"]
    assert all(t["done"] for t in final["targets"])
    assert re.fullmatch(r"[0-9a-f]{40}", final["commit"]["sha"])
    # a running poll shows a round in progress (no check yet) and unticked targets
    mid = next(s for s in seen if s["status"] == "running")
    assert mid["rounds"][-1]["check"] is None and not any(t["done"] for t in mid["targets"])
    # another poll after the end changes nothing
    assert get(f"{base}/api/rebuild/{rid}")[1] == final
    assert call("DELETE", f"{base}/api/rebuild/{rid}")[0] == 409


def test_rebuild_passing_updates_model_and_code_lens(base: str) -> None:
    before = get(base + "/api/model")[1]
    assert before["app"]["last_check"]["verdict"] == "fail"
    _, c0 = get(f"{base}/api/code/file?{q(path=RUNS)}")
    assert not any("rebuilt_in" in a for a in c0["annotations"])
    final = run_rebuild(base)
    assert final["status"] == "passed"
    m = get(base + "/api/model")[1]
    assert m["changes"][0]["build"] == {"state": "built", "rebuild_id": final["id"]}
    assert m["changes"][0]["status"] == "accepted"
    lc = m["app"]["last_check"]
    assert lc["verdict"] == "pass" and lc["findings"] == []
    assert set(lc["targets"].values()) == {"pass"}
    assert m["project"]["git_head"] == final["commit"]["sha"][:7]
    _, c1 = get(f"{base}/api/code/file?{q(path=RUNS)}")
    rebuilt = [a for a in c1["annotations"] if a.get("rebuilt_in")]
    assert rebuilt and all(a["rebuilt_in"] == "c0" for a in rebuilt)
    assert "appAdmin" in c1["lines"][rebuilt[0]["line"] - 1]
    assert len(c1["lines"]) == len(c0["lines"])
    assert not any(a["coverage"] == "failing" or "finding" in a for a in c1["annotations"])
    # the flow no longer diverges
    ep = "POST /runs/{}/cancel"
    assert get(f"{base}/api/flow?{q(endpoint=ep, caller='ann')}")[1]["divergence"] is None
    # a built change cannot be rebuilt again
    assert call("POST", base + "/api/rebuild", {"changes": ["c0"]})[0] == 409


def test_rebuild_gives_up_at_max_rounds(base: str) -> None:
    final = run_rebuild(base, max_rounds=1)
    assert final["status"] == "gave_up" and final["max_rounds"] == 1
    assert len(final["rounds"]) == 1 and "commit" not in final
    assert not any(t["done"] for t in final["targets"])
    m = get(base + "/api/model")[1]
    assert m["changes"][0]["build"]["state"] == "gave_up"
    assert m["app"]["last_check"]["verdict"] == "fail"
    # gave up can be rebuilt again
    status, _ = call("POST", base + "/api/rebuild", {"changes": ["c0"]})
    assert status == 200


def test_rebuild_stop_between_rounds(base: str) -> None:
    accept_change(base)
    _, out = call("POST", base + "/api/rebuild", {"changes": ["c0"]})
    rid = out["id"]
    for _ in range(POLLS_REBUILD_QUEUED + POLLS_ROUND + 1):
        _, rb = get(f"{base}/api/rebuild/{rid}")
    assert rb["status"] == "running" and len(rb["rounds"]) == 2  # round 1 done, round 2 live
    status, stopped = call("DELETE", f"{base}/api/rebuild/{rid}")
    assert status == 200 and stopped == {"id": rid, "status": "stopped"}
    _, rb = get(f"{base}/api/rebuild/{rid}")
    assert rb["status"] == "stopped" and [r["n"] for r in rb["rounds"]] == [1]
    assert rb["rounds"][0]["check"]["verdict"] == "fail"
    m = get(base + "/api/model")[1]
    assert m["changes"][0]["build"] == {"state": "not_built", "rebuild_id": rid}
    assert m["app"]["last_check"]["verdict"] == "fail"
    assert call("DELETE", f"{base}/api/rebuild/{rid}")[0] == 409


def test_change_status_enum_is_unchanged(base: str) -> None:
    run_rebuild(base)
    m = get(base + "/api/model")[1]
    allowed = {"drafting", "checking", "ready", "failed", "accepted", "rejected", "cancelled"}
    assert {c["status"] for c in m["changes"]} <= allowed
