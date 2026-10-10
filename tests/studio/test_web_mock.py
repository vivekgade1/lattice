"""The Studio web UI in mock mode: the mock server answers every route of
lattice/studio/API.md, serves the static app, and its fixtures match the contract's
shapes. No LLM, no git, no browser."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice.studio import mock_server
from lattice.studio.mock_server import FIXTURE_DIR, WEB_DIR, make_server, pick_ask_fixture

# ---------------------------------------------------------------- server fixture


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


def get(url: str) -> tuple[int, str, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def post(url: str, body: Any = None, raw: bytes | None = None) -> tuple[int, Any]:
    data = raw if raw is not None else json.dumps(body or {}).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def get_json(url: str) -> tuple[int, Any]:
    status, _, body = get(url)
    return status, json.loads(body)


# ---------------------------------------------------------------- shape checks
# A small hand-written check of the shapes in API.md: required keys and value kinds.

STATUSES = {"checked", "unchecked", "context", "untraced", "probe_gap"}
CHANGE_STATUSES = {"drafting", "checking", "ready", "failed", "accepted", "rejected", "cancelled"}
VERIFY_STATUSES = {"agrees", "disagrees", "error", "observed", "inconclusive"}
TARGET_RE = re.compile(
    r"^(scenario|invariant|service|question):.+$"
    r"|^machine:[A-Za-z_]\w*\.\w+$"
    r"|^profile:[A-Za-z_]\w*(\.\w+)?$"
    r"|^rule:R\d+$"
    r"|^cell:[^|]+\|(GET|POST|PUT|PATCH|DELETE) /\S*$"
)


def need(obj: dict[str, Any], keys: dict[str, Any], where: str) -> None:
    assert isinstance(obj, dict), f"{where}: not an object"
    for k, kinds in keys.items():
        assert k in obj, f"{where}: missing {k!r}"
        assert isinstance(obj[k], kinds), f"{where}.{k}: {type(obj[k]).__name__}, want {kinds}"


def check_target(t: str, where: str) -> None:
    assert TARGET_RE.match(t), f"{where}: {t!r} is not a contract target"


def check_model(m: dict[str, Any]) -> None:
    need(
        m,
        {
            "project": dict,
            "rules": list,
            "markdown": str,
            "views": dict,
            "data_model": dict,
            "app": dict,
            "changes": list,
        },
        "model",
    )
    need(m["project"], {"name": str, "root": str, "git_head": str}, "project")
    ids = set()
    for r in m["rules"]:
        need(
            r,
            {
                "id": str,
                "text": str,
                "section": str,
                "line": int,
                "status": str,
                "links": list,
                "questions": list,
            },
            f"rule {r.get('id')}",
        )
        assert r["status"] in STATUSES, r["status"]
        assert "unchecked_reason" in r
        if r["status"] == "unchecked":
            assert r["unchecked_reason"], f"{r['id']}: unchecked with no reason"
        for t in r["links"]:
            check_target(t, r["id"])
        ids.add(r["id"])
    lines = m["markdown"].splitlines()
    for r in m["rules"]:
        assert 1 <= r["line"] <= len(lines), f"{r['id']}: line {r['line']} outside the markdown"
    v = m["views"]
    for k in (
        "counts",
        "questions",
        "rule_rows",
        "access_groups",
        "section6",
        "probe_gaps",
        "unchecked_trace",
        "unchecked_notes",
        "lint_gaps",
        "back_translation",
    ):
        assert k in v, f"views: missing {k}"
    for g in v["access_groups"]:
        need(g, {"prefix": str, "rows": list, "columns": list, "cells": dict}, "access_group")
        for key in g["cells"]:
            row, col = key.split("|", 1)
            assert row in g["rows"] and col in g["columns"], key
    s6 = v["section6"]
    for k in ("invariants", "machines", "services", "scenarios"):
        assert isinstance(s6[k], list)
    for item in s6["invariants"] + s6["machines"] + s6["services"]:
        assert len(item) == 3
        check_target(item[0], "section6")
    for sc in s6["scenarios"]:
        need(sc, {"target": str, "name": str, "steps": list}, "scenario")
    for t in m["data_model"]["tables"]:
        need(t, {"name": str, "sql": str, "columns": list}, "table")
        for c in t["columns"]:
            need(
                c,
                {"name": str, "type": str, "nullable": bool, "unique": bool, "private": bool},
                f"column {t['name']}",
            )
            assert "one_of" in c and "bounds" in c
    app = m["app"]
    need(app, {"configured": bool}, "app")
    lc = app["last_check"]
    if lc is not None:
        need(
            lc,
            {"at": str, "verdict": str, "counts": dict, "targets": dict, "findings": list},
            "last_check",
        )
        assert lc["verdict"] in ("pass", "fail")
        assert set(lc["counts"]) == {"A", "B", "C", "D", "X"}
        for t, res in lc["targets"].items():
            check_target(t, "last_check.targets")
            assert res in ("pass", "fail")
        for f in lc["findings"]:
            need(f, {"layer": str, "target": str, "message": str}, "finding")
    for c in m["changes"]:
        need(c, {"id": str, "request": str, "status": str}, "changes[]")
        assert c["status"] in CHANGE_STATUSES


def check_answer(a: dict[str, Any]) -> None:
    need(
        a,
        {
            "kind": str,
            "answer": str,
            "explanation": str,
            "citations": list,
            "citations_checked": bool,
            "rejected_citations": list,
            "usage": dict,
        },
        "answer",
    )
    assert a["kind"] in ("yes", "no", "value", "not_specified")
    assert "verified" in a
    for c in a["citations"]:
        need(c, {"target": str, "quote": str}, "citation")
        assert "step" in c
        check_target(c["target"], "citation")
    v = a["verified"]
    if v is not None:
        need(v, {"status": str, "detail": str, "scenario": str}, "verified")
        assert v["status"] in VERIFY_STATUSES
        if "claim_step" in v and v["claim_step"] is not None:
            assert isinstance(v["claim_step"], int) and v["claim_step"] >= 1
    need(a["usage"], {"seconds": (int, float), "tokens": int}, "usage")
    if "digest_elements" in a["usage"]:
        assert isinstance(a["usage"]["digest_elements"], int)


def check_change(c: dict[str, Any]) -> None:
    need(
        c,
        {
            "id": str,
            "request": str,
            "status": str,
            "markdown_diff": str,
            "rules": dict,
            "targets": dict,
            "view_diff": dict,
            "checks": dict,
            "questions": list,
        },
        "change",
    )
    assert "error" in c
    assert c["status"] in CHANGE_STATUSES
    assert "parent" in c and (c["parent"] is None or isinstance(c["parent"], str))
    assert "superseded_by" in c and (c["superseded_by"] is None or isinstance(c["superseded_by"], str))
    need(c["rules"], {"added": list, "changed": list, "removed": list}, "rules")
    for ch in c["rules"]["changed"]:
        need(ch, {"old": str, "new": str}, "rules.changed")
    need(c["targets"], {"added": list, "removed": list, "modified": list}, "targets")
    vd = c["view_diff"]
    need(vd, {"cells": list, "invariants": dict, "machines": dict, "scenarios": dict}, "view_diff")
    for cell in vd["cells"]:
        need(cell, {"row": str, "col": str, "old": str, "new": str}, "view_diff.cells")
    need(vd["invariants"], {"added": list, "removed": list}, "view_diff.invariants")
    need(vd["machines"], {"changed": list}, "view_diff.machines")
    need(vd["scenarios"], {"added": list, "removed": list, "modified": list}, "view_diff.scenarios")
    ch = c["checks"]
    need(ch, {"valid": bool, "problems": list, "lint": list, "conflicts": list}, "checks")
    for li in ch["lint"]:
        need(li, {"code": str, "message": str, "fix": str}, "lint")
    for k in ch["conflicts"]:
        need(k, {"a": str, "b": str, "message": str, "question": str}, "conflict")
    imp = ch["impact"]
    if imp is not None:
        need(imp, {"verdict": str, "failing": list}, "impact")
        for f in imp["failing"]:
            need(f, {"target": str, "label": str, "message": str}, "impact.failing")
            assert f["label"] in ("not_built_yet", "regression", "contradiction")
    for q in c["questions"]:
        need(q, {"id": str, "text": str, "default": str}, "question")


# ---------------------------------------------------------------- fixtures on disk


def load(name: str) -> Any:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def test_model_fixture_matches_contract() -> None:
    check_model(load("model"))


@pytest.mark.parametrize(
    "name", ["ask-yes", "ask-not-specified", "ask-verified-disagrees", "ask-inconclusive"]
)
def test_ask_fixtures_match_contract(name: str) -> None:
    check_answer(load(name))


def test_ask_fixtures_cover_the_three_cases() -> None:
    yes, ns, dis = load("ask-yes"), load("ask-not-specified"), load("ask-verified-disagrees")
    model = load("model")
    assert yes["kind"] == "yes"
    cells = [c["target"] for c in yes["citations"] if c["target"].startswith("cell:")]
    assert cells, "the yes answer cites an access cell"
    row, col = cells[0].removeprefix("cell:").split("|", 1)
    assert any(f"{row}|{col}" in g["cells"] for g in model["views"]["access_groups"])
    assert ns["kind"] == "not_specified"
    assert dis["verified"]["status"] == "disagrees"


def test_ask_fixtures_cover_observed_and_inconclusive() -> None:
    ns = load("ask-not-specified")
    inc = load("ask-inconclusive")
    assert ns["verified"]["status"] == "observed"
    assert inc["verified"]["status"] == "inconclusive"
    assert inc["verified"]["claim_step"] == 3


def test_citations_point_at_real_things() -> None:
    model = load("model")
    known = {f"rule:{r['id']}" for r in model["rules"]}
    s6 = model["views"]["section6"]
    known |= {x[0] for x in s6["invariants"] + s6["machines"] + s6["services"]}
    known |= {s["target"] for s in s6["scenarios"]}
    for g in model["views"]["access_groups"]:
        known |= {f"cell:{k}" for k in g["cells"]}
    for name in ("ask-yes", "ask-not-specified", "ask-verified-disagrees", "ask-inconclusive"):
        for c in load(name)["citations"]:
            assert c["target"] in known, f"{name}: {c['target']}"


def test_change_fixture_matches_contract() -> None:
    c = load("change-c1")
    check_change(c)
    assert c["status"] == "ready"
    assert c["checks"]["conflicts"], "c1 carries a conflict"
    cell = c["view_diff"]["cells"][0]
    assert (cell["row"], cell["col"], cell["new"]) == (
        "eve",
        "POST /applications/{}/runs/{}/cancel",
        "403",
    )
    assert cell["old"].startswith("200")
    labels = {f["label"] for f in c["checks"]["impact"]["failing"]}
    assert labels == {"not_built_yet", "regression", "contradiction"}
    model = load("model")
    for k in ("modified", "removed"):
        for t in c["targets"][k]:
            assert any(s["target"] == t for s in model["views"]["section6"]["scenarios"]), t


def test_followup_fixture_matches_contract() -> None:
    c = load("change-followup")
    check_change(c)
    assert c["status"] == "ready"
    assert not c["checks"]["conflicts"], "the follow-up resolves c1's conflict"
    assert not c["questions"], "the follow-up resolves c1's question"
    model = load("model")
    for k in ("modified", "removed"):
        for t in c["targets"][k]:
            assert any(s["target"] == t for s in model["views"]["section6"]["scenarios"]), t


def test_pick_ask_fixture_by_keyword() -> None:
    assert pick_ask_fixture("Can eve cancel a run?") == "ask-yes"
    assert pick_ask_fixture("Are failed runs retried?") == "ask-not-specified"
    assert pick_ask_fixture("What does an inactive user get?") == "ask-verified-disagrees"
    assert pick_ask_fixture("Can two ticks run concurrently for the same config?") == "ask-inconclusive"
    assert pick_ask_fixture("anything else") == "ask-not-specified"


# ---------------------------------------------------------------- static app


def test_index_and_assets_are_served(base: str) -> None:
    status, ctype, body = get(base + "/")
    assert status == 200 and ctype.startswith("text/html")
    html = body.decode()
    assert 'src="js/app.js"' in html and 'href="css/app.css"' in html
    assets = sorted(
        p.relative_to(WEB_DIR).as_posix() for p in WEB_DIR.rglob("*") if p.suffix in (".js", ".css")
    )
    assert "js/app.js" in assets and "css/app.css" in assets
    for rel in assets:
        status, ctype, body = get(f"{base}/{rel}")
        assert status == 200, rel
        assert body
        want = "text/javascript" if rel.endswith(".js") else "text/css"
        assert ctype.startswith(want), (rel, ctype)


def test_every_js_import_resolves() -> None:
    for js in WEB_DIR.rglob("*.js"):
        for spec in re.findall(r'^import .*? from "([^"]+)";', js.read_text(), re.M):
            assert (js.parent / spec).resolve().is_file(), f"{js.name} imports {spec}"


def test_static_paths_cannot_escape_web_dir(base: str) -> None:
    for path in ("/../mock_server.py", "/%2e%2e/mock_server.py", "/js/../../API.md"):
        status, _, _ = get(base + path)
        assert status == 404, path
    assert get(base + "/no-such-file.js")[0] == 404


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_js_syntax_with_node() -> None:
    for js in sorted(WEB_DIR.rglob("*.js")):
        r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
        assert r.returncode == 0, f"{js}: {r.stderr}"


# ---------------------------------------------------------------- API routes


def find_change(m: dict[str, Any], cid: str) -> dict[str, Any]:
    out = next((c for c in m["changes"] if c["id"] == cid), None)
    assert out is not None, f"no change {cid} in model.changes"
    return out


def test_get_model(base: str) -> None:
    status, m = get_json(base + "/api/model")
    assert status == 200
    check_model(m)
    # The mock seeds one pre-existing change so a fresh page load has something to show.
    assert len(m["changes"]) == 1
    c0 = find_change(m, "c0")
    assert c0["status"] == "ready"
    assert c0["parent"] is None and c0["superseded_by"] is None


def test_post_ask(base: str) -> None:
    status, a = post(
        base + "/api/ask",
        {
            "question": "Can eve cancel a run?",
            "selected": ["cell:eve|POST /applications/{}/runs/{}/cancel"],
            "verify": False,
        },
    )
    assert status == 200
    check_answer(a)
    assert a["kind"] == "yes"

    status, a = post(
        base + "/api/ask", {"question": "Are failed runs retried?", "selected": [], "verify": False}
    )
    assert status == 200 and a["kind"] == "not_specified"

    status, a = post(
        base + "/api/ask",
        {"question": "Can an inactive user log in?", "selected": [], "verify": True},
    )
    assert status == 200 and a["verified"]["status"] == "disagrees"
    status, a = post(
        base + "/api/ask",
        {"question": "Can an inactive user log in?", "selected": [], "verify": False},
    )
    assert a["verified"] is None, "no verification unless asked"


def test_post_ask_rejects_bad_input(base: str) -> None:
    assert post(base + "/api/ask", raw=b"not json")[0] == 400
    assert post(base + "/api/ask", {"question": "  "})[0] == 422


def test_change_lifecycle_accept(base: str) -> None:
    status, out = post(
        base + "/api/changes", {"request": "only app admins may cancel runs", "selected": []}
    )
    assert status == 200
    cid = out["id"]
    seen = []
    for _ in range(mock_server.POLLS_DRAFTING + mock_server.POLLS_CHECKING + 2):
        status, c = get_json(f"{base}/api/changes/{cid}")
        assert status == 200
        check_change(c)
        seen.append(c["status"])
        if c["status"] == "ready":
            break
    assert seen[0] == "drafting" and "checking" in seen and seen[-1] == "ready"
    assert c["request"] == "only app admins may cancel runs"
    assert c["view_diff"]["cells"]
    assert c["parent"] is None and c["superseded_by"] is None

    _, m = get_json(base + "/api/model")
    assert find_change(m, cid) == {
        "id": cid,
        "request": c["request"],
        "status": "ready",
        "parent": None,
        "superseded_by": None,
    }

    status, acc = post(f"{base}/api/changes/{cid}/accept")
    assert status == 200
    assert re.fullmatch(r"[0-9a-f]{40}", acc["commit"]) and acc["message"]
    _, m = get_json(base + "/api/model")
    assert m["project"]["git_head"] == acc["commit"][:7]
    assert find_change(m, cid)["status"] == "accepted"


def test_change_reject(base: str) -> None:
    _, out = post(base + "/api/changes", {"request": "x", "selected": []})
    status, r = post(f"{base}/api/changes/{out['id']}/reject")
    assert status == 200 and r == {"ok": True}
    _, m = get_json(base + "/api/model")
    assert find_change(m, out["id"])["status"] == "rejected"


def test_change_cancel(base: str) -> None:
    _, out = post(base + "/api/changes", {"request": "y", "selected": []})
    cid = out["id"]
    status, c = get_json(f"{base}/api/changes/{cid}")
    assert status == 200 and c["status"] == "drafting"

    req = urllib.request.Request(f"{base}/api/changes/{cid}", method="DELETE")
    with urllib.request.urlopen(req, timeout=10) as r:
        status, body = r.status, json.loads(r.read())
    assert status == 200 and body == {"id": cid, "status": "cancelled"}

    status, c = get_json(f"{base}/api/changes/{cid}")
    assert status == 200
    check_change(c)
    assert c["status"] == "cancelled"
    _, m = get_json(base + "/api/model")
    assert find_change(m, cid)["status"] == "cancelled"


def test_cancel_a_ready_change_is_409(base: str) -> None:
    req = urllib.request.Request(f"{base}/api/changes/c0", method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=10)
        raised = False
    except urllib.error.HTTPError as e:
        raised = True
        assert e.code == 409
    assert raised


def test_cancel_unknown_change_is_404(base: str) -> None:
    req = urllib.request.Request(f"{base}/api/changes/c99", method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=10)
        raised = False
    except urllib.error.HTTPError as e:
        raised = True
        assert e.code == 404
    assert raised


def test_followup_supersedes_its_parent(base: str) -> None:
    # c0 is the pre-seeded ready change; answer its conflict as a follow-up.
    status, out = post(
        base + "/api/changes",
        {"request": "Q13 answered: no, executors may never cancel.", "parent": "c0", "selected": []},
    )
    assert status == 200
    cid = out["id"]
    for _ in range(mock_server.POLLS_DRAFTING + mock_server.POLLS_CHECKING + 2):
        status, c = get_json(f"{base}/api/changes/{cid}")
        assert status == 200
        check_change(c)
        if c["status"] == "ready":
            break
    assert c["status"] == "ready"
    assert c["parent"] == "c0"
    assert not c["checks"]["conflicts"], "the follow-up resolves the parent's conflict"

    status, parent = get_json(f"{base}/api/changes/c0")
    assert status == 200
    check_change(parent)
    assert parent["status"] == "rejected"
    assert parent["superseded_by"] == cid

    _, m = get_json(base + "/api/model")
    assert find_change(m, "c0")["status"] == "rejected"
    assert find_change(m, "c0")["superseded_by"] == cid
    assert find_change(m, cid)["status"] == "ready"


def test_unknown_change_is_404(base: str) -> None:
    assert get(base + "/api/changes/c99")[0] == 404
    assert post(base + "/api/changes/c99/accept")[0] == 404
    assert post(base + "/api/changes", {"request": ""})[0] == 422
    assert get(base + "/api/nope")[0] == 404


def test_post_check_moves_last_check(base: str) -> None:
    _, before = get_json(base + "/api/model")
    status, out = post(base + "/api/check")
    assert status == 200 and out == {"started": True}
    at = before["app"]["last_check"]["at"]
    for _ in range(mock_server.POLLS_CHECK + 1):
        _, m = get_json(base + "/api/model")
        if m["app"]["last_check"]["at"] != at:
            break
    assert m["app"]["last_check"]["at"] != at
    check_model(m)


def test_mock_reads_fixtures_from_the_package() -> None:
    assert FIXTURE_DIR == Path(mock_server.__file__).parent / "mock"
    assert (WEB_DIR / "index.html").is_file()
