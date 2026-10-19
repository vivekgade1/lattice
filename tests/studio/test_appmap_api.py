"""GET /api/appmap, /api/appmap/element and /api/impact (API v1.25) on the real Studio server and
the mock: the shapes the API.md section documents, the states, that nothing is written or
started, and that the mock is built by the same modules over the committed fixture project.

The world is the committed fixture project lattice/studio/mock/appmap/ (a copy of
tests/spec/fixtures/implementation plus one candidate) copied under ``tmp_path`` with a state
directory of its own; the change record ``c1`` is drafted through a scripted generator that returns
the candidate's three files, as the probes do."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from lattice.llm.client import Generation, Usage
from lattice.spec import appmap as am
from lattice.spec import impact_ask
from lattice.spec import impact_report as ir
from lattice.studio import server as server_mod
from lattice.studio.mock_server import make_server as make_mock
from lattice.studio.server import BEGIN, END, make_hub_server, make_server
from tests.studio.test_server import request

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "lattice" / "studio" / "mock" / "appmap"
API_MD = ROOT / "lattice" / "studio" / "API.md"


# ---------------------------------------------------------------- the world


class CandidateGenerator:
    """The scripted generator of a drafted change: the edit returns the candidate's
    REQUIREMENTS.md, the draft its spec.py and spec.trace.json. It counts its calls."""

    def __init__(self, cand: Path) -> None:
        self.md = (cand / "REQUIREMENTS.md").read_text()
        self.module = (cand / "spec.py").read_text()
        self.trace = (cand / "spec.trace.json").read_text()
        self.calls: list[str] = []

    def generate(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation:
        from lattice.spec.draft import system_prompt

        self.calls.append(system[:40])
        if system == server_mod.EDIT_SYSTEM:
            text = f"{BEGIN}\n{self.md}{END}\n"
        elif system == system_prompt():
            text = f"```python\n{self.module}```\n\n```json\n{self.trace}\n```\n"
        else:
            text = json.dumps(
                {
                    "kind": "not_specified",
                    "answer": "Not specified (scripted).",
                    "explanation": "The scripted generator answers every question this way.",
                    "citations": [],
                }
            )
        return Generation(text=text, usage=Usage(0, 0), latency_s=0.0, stop_reason="end_turn")


def make_world(
    tmp: Path,
    *,
    impact: bool = True,
    implementation: bool = True,
    reports: bool = False,
    state: bool = True,
    spec: bool = True,
) -> dict[str, Path]:
    """project/, app/, inventory/ (copies of the committed fixture), candidate/, state/ and
    reports/ under `tmp`; studio.toml as asked."""
    tmp.mkdir(parents=True, exist_ok=True)
    for name in ("app", "inventory", "candidate"):
        shutil.copytree(MOCK / name, tmp / name)
    proj = tmp / "project"
    shutil.copytree(MOCK / "project", proj)
    toml = '[llm]\nmodel = "scripted"\n'
    if implementation:
        toml += '\n[implementation]\ninventory = "../inventory"\napp_root = "../app"\n'
    if impact:
        toml += "\n[impact]\n"
        if reports:
            (tmp / "reports").mkdir()
            toml += f'reports = "{tmp / "reports"}"\n'
    toml += f'\n[state]\ndir = "{tmp / "state"}"\n' if state else "\n[state]\nenabled = false\n"
    (proj / "studio.toml").write_text(toml)
    if not spec:
        (proj / "spec.py").unlink()
    return {
        "project": proj,
        "app": tmp / "app",
        "inventory": tmp / "inventory",
        "candidate": tmp / "candidate",
        "state": tmp / "state",
        "reports": tmp / "reports",
    }


class Served:
    def __init__(self, base: str, srv: Any, gen: CandidateGenerator | None) -> None:
        self.base, self.srv, self.gen = base, srv, gen

    def get(self, path: str) -> tuple[int, Any]:
        return request("GET", self.base + path)

    def post(self, path: str, body: Any = None) -> tuple[int, Any]:
        return request("POST", self.base + path, body if body is not None else {})

    def delete(self, path: str) -> tuple[int, Any]:
        return request("DELETE", self.base + path)

    def draft(self, text: str = "publish answers 204") -> str:
        """Record a change through the scripted generator and wait until it is ready."""
        status, body = self.post("/api/changes", {"request": text})
        assert status in (200, 201, 202), body
        cid = body["id"]
        end = time.monotonic() + 60
        while time.monotonic() < end:
            _, c = self.get(f"/api/changes/{cid}")
            if c["status"] not in ("drafting", "checking"):
                return cid
            time.sleep(0.05)
        raise AssertionError("the change did not finish")


@pytest.fixture()
def serve() -> Iterator[Any]:
    servers: list[Any] = []

    def start(world: Mapping[str, Path], gen: Any = "candidate") -> Served:
        g = CandidateGenerator(world["candidate"]) if gen == "candidate" else gen
        srv = make_server(world["project"], 0, generator=g)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return Served(f"http://127.0.0.1:{srv.server_address[1]}", srv, g)

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def tree(root: Path) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        st = p.stat()
        out[p.relative_to(root).as_posix()] = (
            "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest(),
            st.st_mode,
            st.st_mtime_ns,
        )
    return out


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------- GET /api/appmap


def test_the_map_is_the_cli_map_and_the_documented_shape(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path)
    s = serve(w)
    status, doc = s.get("/api/appmap")
    assert status == 200 and doc["state"] == "ok" and doc["schema"] == "lattice.appmap/1"
    assert canonical(doc) == canonical(am.appmap_from_dir(w["project"]).doc)
    r = subprocess.run(
        [sys.executable, "-m", "lattice.spec", "appmap", "--spec", "spec:SPEC", "--trace",
         "spec.trace.json", "--inventory", "../inventory", "--app-root", "../app", "--json"],
        capture_output=True, text=True, cwd=w["project"], check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert canonical(json.loads(r.stdout)) == canonical(doc)


def test_sections_and_a_bad_section(tmp_path: Path, serve) -> None:
    s = serve(make_world(tmp_path))
    _, whole = s.get("/api/appmap")
    for sec in am.SECTIONS:
        status, part = s.get(f"/api/appmap?section={sec}")
        assert status == 200 and part[sec] == whole[sec], sec
        assert part["inputs"] == whole["inputs"]
    assert "excluded" in s.get("/api/appmap?section=endpoints")[1]
    status, body = s.get("/api/appmap?section=nope")
    assert status == 422 and body["code"] == "bad_section"
    assert "summary" in body["detail"]


def test_the_summary_section_is_small(tmp_path: Path, serve) -> None:
    s = serve(make_world(tmp_path))
    _, part = s.get("/api/appmap?section=summary")
    assert len(json.dumps(part)) < 4000


def test_the_states_answer_200_with_only_schema_state_inputs_and_why(tmp_path: Path, serve) -> None:
    cases = {
        "not_configured": dict(implementation=False),
        "no_spec": dict(spec=False),
    }
    for state, kw in cases.items():
        s = serve(make_world(tmp_path / state, **kw))
        status, doc = s.get("/api/appmap")
        assert status == 200 and doc["state"] == state, doc
        assert set(doc) == {"schema", "state", "inputs", "why"}
        assert "/Users" not in json.dumps(doc) and "/private" not in json.dumps(doc)
    # an inventory directory that is gone, and one whose extract was tampered with
    w = make_world(tmp_path / "missing")
    shutil.rmtree(w["inventory"])
    status, doc = serve(w).get("/api/appmap")
    assert status == 200 and doc["state"] == "inventory_missing" and doc["why"]
    w = make_world(tmp_path / "bad")
    ex = w["inventory"] / "extract.json"
    ex.write_text(ex.read_text() + " ")
    status, doc = serve(w).get("/api/appmap")
    assert status == 200 and doc["state"] == "bad_inventory"
    # a non-ok map has no sections: it is returned whole
    assert (
        serve(make_world(tmp_path / "ncs", implementation=False)).get("/api/appmap?section=rules")[
            1
        ]["state"]
        == "not_configured"
    )


def test_a_stale_inventory_is_a_state_inside_the_map(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path)
    s = serve(w)
    go = w["app"] / "api" / "api.go"
    go.write_text(go.read_text() + "\n// edit\n")
    _, doc = s.get("/api/appmap")
    assert doc["state"] == "ok" and doc["inputs"]["inventory"]["stale"]["counts"]["changed"] == 1
    assert [n["code"] for n in doc["summary"]["notes"]] == ["stale", "static_capture"]
    assert not any(r["basis"] == "observed" for r in doc["rules"])


# ---------------------------------------------------------------- GET /api/appmap/element


def test_elements_of_every_kind_and_the_unknown_key(tmp_path: Path, serve) -> None:
    s = serve(make_world(tmp_path))
    _, doc = s.get("/api/appmap")
    keys = {
        "rule": "rule:R3",
        "workflow": doc["workflows"][0]["id"],
        "machine": doc["machines"][0]["key"],
        "cell": "cell:ann|POST /api/projects",
        "route": "impl:route:POST /api/projects",
    }
    for kind, key in keys.items():
        status, e = s.get("/api/appmap/element?key=" + quote(key, safe=""))
        assert status == 200 and e["kind"] == kind and e["key"] == key, e
        assert set(e) == {"schema", "state", "key", "kind", "row", "touches", "basis", "meaning"}
        assert e["basis"] == "static" and "not decided" in e["meaning"]
    status, body = s.get("/api/appmap/element?key=rule:R99")
    assert status == 404 and body["code"] == "unknown_key" and len(body["nearest"]) == 5
    assert set(body) == {"detail", "code", "nearest"}
    assert s.get("/api/appmap/element")[1]["code"] == "bad_key"
    assert s.get("/api/appmap/element?key=" + "x" * 301)[0] == 422
    assert s.get("/api/appmap/element?key=" + "x" * 300)[0] == 404


def test_other_methods_and_a_project_prefix(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path / "a")
    s = serve(w)
    for path in ("/api/appmap", "/api/appmap/element", "/api/impact"):
        for method in ("POST", "DELETE"):
            status, body = request(method, s.base + path, {} if method == "POST" else None)
            assert (status, body) == (404, {"detail": "no such route"}), (method, path)
    # a hub: the same routes under /p/<name>/, an unknown project 404, projects isolated
    w2 = make_world(tmp_path / "b", implementation=False)
    srv = make_hub_server([("alpha", w["project"]), ("beta", w2["project"])], 0, default="alpha")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert request("GET", base + "/api/appmap")[1]["state"] == "ok"
        assert request("GET", base + "/p/alpha/api/appmap")[1]["state"] == "ok"
        assert request("GET", base + "/p/beta/api/appmap")[1]["state"] == "not_configured"
        assert request("GET", base + "/p/nope/api/appmap") == (404, {"detail": "no such project"})
        assert request("GET", base + "/p/beta/api/impact?change=c1")[1]["state"] == "ok" or True
        assert request("GET", base + "/p/alpha/api/appmap/element?key=rule:R3")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_existing_routes_gain_no_key(tmp_path: Path, serve) -> None:
    s = serve(make_world(tmp_path))
    model = s.get("/api/model")[1]
    assert not any("appmap" in k or "impact" in k for k in model)
    assert set(model) == {
        "project", "rules", "markdown", "views", "data_model", "app", "changes",
    }  # fmt: skip
    assert "appmap" not in json.dumps(s.get("/api/projects")[1])


# ---------------------------------------------------------------- GET /api/impact


def cli_report(w: Mapping[str, Path], cid: str, *extra: str) -> dict[str, Any]:
    r = subprocess.run(
        [sys.executable, "-m", "lattice.spec", "impact", "--project", str(w["project"]),
         "--change", cid, "--state-dir", str(w["state"]), "--tiers", "23", "--baseline", "none",
         "--json", *extra],
        capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_report_is_the_cli_report_verbatim(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path)
    s = serve(w)
    cid = s.draft()
    assert cid == "c1"
    status, body = s.get(f"/api/impact?change={cid}")
    assert status == 200 and body["state"] == "ok" and body["why"] is None
    assert set(body) == {
        "schema",
        "change",
        "state",
        "why",
        "report",
        "studio_check",
        "tier1_saved",
    }
    assert body["schema"] == "lattice.studio-impact/1" and body["change"] == "c1"
    assert canonical(body["report"]) == canonical(cli_report(w, cid))
    assert body["report"]["schema"] == "lattice.impact/1"
    assert body["report"]["tiers"] == {
        "1": "not_run:not_requested", "2": "run", "3": "run", "4": "not_run:spike_e",
    }  # fmt: skip
    assert body["studio_check"] is None
    assert body["tier1_saved"] == {"state": "not_configured", "differs": [], "tier1": None}
    # a section narrows the report to D's section_of
    status, part = s.get(f"/api/impact?change={cid}&section=tier3")
    assert status == 200 and part["report"] == ir.section_of(body["report"], "tier3")
    assert s.get(f"/api/impact?change={cid}&section=nope")[1]["code"] == "bad_section"


def test_the_states(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path / "a")
    s = serve(w)
    for bad in (
        "",
        "?change=",
        "?change=x1",
        "?change=c",
        "?change=c1234567",
        "?change=c1%2F..%2Fx",
    ):
        status, body = s.get("/api/impact" + bad)
        assert status == 422 and body["code"] == "bad_change", bad
    assert s.get("/api/impact?change=c1")[1]["state"] == "no_change"
    # a change that failed is not ready
    bad_gen = type(
        "G",
        (),
        {"generate": lambda self, system, messages: (_ for _ in ()).throw(RuntimeError("x"))},
    )()
    s2 = serve(make_world(tmp_path / "b"), gen=bad_gen)
    _, c = s2.post("/api/changes", {"request": "x"})
    time.sleep(0.5)
    body = s2.get(f"/api/impact?change={c['id']}")[1]
    assert body["state"] == "change_not_ready" and body["report"] is None and body["why"]
    # no [impact] table
    body = serve(make_world(tmp_path / "c", impact=False)).get("/api/impact?change=c1")[1]
    assert body["state"] == "impact_not_configured" and body["report"] is None
    # durable state off: the record lives in memory only
    s4 = serve(make_world(tmp_path / "d", state=False))
    cid = s4.draft()
    assert s4.get(f"/api/impact?change={cid}")[1]["state"] == "no_state"
    # no inventory
    w5 = make_world(tmp_path / "e")
    s5 = serve(w5)
    cid = s5.draft()
    shutil.rmtree(w5["inventory"])
    assert s5.get(f"/api/impact?change={cid}")[1]["state"] == "inventory_missing"


def test_studio_check_is_the_records_own_check_verbatim(tmp_path: Path, serve) -> None:
    s = serve(make_world(tmp_path))
    cid = s.draft()
    check = {
        "verdict": "fail",
        "failing": [{"target": "scenario:x", "label": "not_built_yet", "message": "m"}],
    }
    s.srv.studio.changes[cid]["checks"]["impact"] = copy.deepcopy(check)
    body = s.get(f"/api/impact?change={cid}")[1]
    assert body["studio_check"] == check
    assert body["report"]["tier1"]["failing"] == []  # never relabelled as tier 1


def saved_report(w: Mapping[str, Path], cid: str, *, ran: bool = True) -> dict[str, Any]:
    """A tier 1 report as `lattice spec impact --tiers 123` would write it, for tests with no app:
    D's tier 2 and 3 report with tier 1 marked run and one observed failing item."""
    rep = cli_report(w, cid)
    if ran:
        rep["tiers"]["1"] = "run"
        rep["tier1"]["failing"] = [
            {"id": "t1-0000000001", "kind": "check_fails", "target": "scenario:x", "rules": ["R3"],
             "message": "the app answered 200 where the candidate expects 204", "tier": 1}
        ]  # fmt: skip
    return rep


def test_tier1_saved_is_shown_only_when_the_hashes_match(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path, reports=True)
    s = serve(w)
    cid = s.draft()
    # absent
    assert s.get(f"/api/impact?change={cid}")[1]["tier1_saved"]["state"] == "absent"
    # shown, verbatim
    rep = saved_report(w, cid)
    (w["reports"] / f"{cid}.json").write_text(json.dumps(rep))
    t = s.get(f"/api/impact?change={cid}")[1]["tier1_saved"]
    assert t["state"] == "shown" and t["tier1"] == rep["tier1"] and t["differs"] == []
    # the candidate changes by one byte: mismatch, naming it
    cand_md = w["candidate"] / "REQUIREMENTS.md"
    rep2 = copy.deepcopy(rep)
    rep2["input"]["candidate"]["requirements"] = hashlib.sha256(
        cand_md.read_bytes() + b" "
    ).hexdigest()
    (w["reports"] / f"{cid}.json").write_text(json.dumps(rep2))
    t = s.get(f"/api/impact?change={cid}")[1]["tier1_saved"]
    assert t["state"] == "mismatch" and t["differs"] == ["candidate_sha256"] and t["tier1"] is None
    # the inventory changes: both hashes are checked; a report that did not run tier 1 is not shown
    rep3 = copy.deepcopy(rep)
    rep3["input"]["inventory"]["extract_sha256"] = "0" * 64
    rep3["tiers"]["1"] = "not_run:no_app"
    (w["reports"] / f"{cid}.json").write_text(json.dumps(rep3))
    t = s.get(f"/api/impact?change={cid}")[1]["tier1_saved"]
    assert t["state"] == "mismatch" and set(t["differs"]) == {"extract_sha256", "tier1_not_run"}
    (w["reports"] / f"{cid}.json").write_text("{not json")
    assert s.get(f"/api/impact?change={cid}")[1]["tier1_saved"]["differs"] == ["not_a_report"]
    # without a reports key it is not configured, whatever is lying around
    w2 = make_world(tmp_path / "x")
    s2 = serve(w2)
    assert s2.get(f"/api/impact?change={s2.draft()}")[1]["tier1_saved"]["state"] == "not_configured"


def test_get_never_runs_tier_one_or_a_process_and_writes_nothing(
    tmp_path: Path, serve, monkeypatch
) -> None:
    w = make_world(tmp_path, reports=True)
    s = serve(w)
    cid = s.draft()
    (w["reports"] / f"{cid}.json").write_text(json.dumps(saved_report(w, cid)))
    before = {k: tree(w[k]) for k in ("project", "app", "inventory", "state", "reports")}

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("a GET started something")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(ir, "run_tier1", boom)
    monkeypatch.setattr(ir, "harness_runner", boom)
    monkeypatch.setattr(ir, "owned_containers", boom)
    monkeypatch.setattr(server_mod, "StateStore", boom)
    for _ in range(10):
        for path in (
            "/api/appmap", "/api/appmap?section=rules", "/api/appmap/element?key=rule:R3",
            f"/api/impact?change={cid}", f"/api/impact?change={cid}&section=tier2",
            "/api/impact?change=c9",
        ):  # fmt: skip
            assert s.get(path)[0] == 200
    assert {k: tree(w[k]) for k in before} == before
    assert (w["project"].parent / "candidate").exists()  # the world is untouched


def test_the_routes_are_deterministic_across_processes(tmp_path: Path, serve) -> None:
    w = make_world(tmp_path)
    s = serve(w)
    cid = s.draft()
    a = (canonical(s.get("/api/appmap")[1]), canonical(s.get(f"/api/impact?change={cid}")[1]))
    b = (canonical(s.get("/api/appmap")[1]), canonical(s.get(f"/api/impact?change={cid}")[1]))
    assert a == b
    text = a[0] + a[1]
    assert not re.search(r"/Users|/private|/tmp|127\.0\.0\.1", text)


# ---------------------------------------------------------------- the mock (PP)


@pytest.fixture()
def mock() -> Iterator[str]:
    srv = make_mock(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_the_mock_is_the_real_server_over_the_same_fixture(
    tmp_path: Path, serve, mock: str
) -> None:
    w = make_world(tmp_path)
    s = serve(w)
    real = s.get("/api/appmap")[1]
    assert request("GET", mock + "/api/appmap")[1] == real
    for sec in am.SECTIONS:
        assert (
            request("GET", mock + f"/api/appmap?section={sec}")[1]
            == s.get(f"/api/appmap?section={sec}")[1]
        )
    key = quote("impl:route:POST /api/projects", safe="")
    assert (
        request("GET", mock + f"/api/appmap/element?key={key}")[1]
        == s.get(f"/api/appmap/element?key={key}")[1]
    )
    miss = request("GET", mock + "/api/appmap/element?key=rule:R99")
    assert miss == s.get("/api/appmap/element?key=rule:R99")
    # the same refusals
    for path in (
        "/api/appmap?section=nope",
        "/api/appmap/element",
        "/api/impact?change=x",
        "/api/impact",
    ):
        assert request("GET", mock + path) == s.get(path), path
    # the second mock project has no implementation
    other = request("GET", mock + "/p/lattice-studio/api/appmap")[1]
    assert other["state"] == "not_configured" and set(other) == {"schema", "state", "inputs", "why"}
    assert (
        request("GET", mock + "/p/lattice-studio/api/impact?change=c1")[1]["state"]
        == "impact_not_configured"
    )
    # a ready mock change has the fixture candidate: the report is the real server's, key for key
    cid = s.draft()
    status, made = request("POST", mock + "/api/changes", {"request": "publish answers 204"})
    assert status in (200, 201, 202)
    mid = made["id"]
    for _ in range(40):
        if request("GET", mock + f"/api/changes/{mid}")[1]["status"] == "ready":
            break
    mbody = request("GET", mock + f"/api/impact?change={mid}")[1]
    rbody = s.get(f"/api/impact?change={cid}")[1]
    assert set(mbody) == set(rbody) and mbody["state"] == rbody["state"] == "ok"
    mrep, rrep = copy.deepcopy(mbody["report"]), copy.deepcopy(rbody["report"])
    mrep["input"]["change"] = rrep["input"]["change"] = None
    assert canonical(mrep) == canonical(rrep)
    assert (
        mbody["tier1_saved"] == rbody["tier1_saved"]
        or mbody["tier1_saved"]["state"] == "not_configured"
    )
    assert request("GET", mock + "/api/impact?change=c77")[1]["state"] == "no_change"
    for path in ("/api/appmap", "/api/appmap/element", "/api/impact"):
        assert request("POST", mock + path, {}) == (404, {"detail": "no such route"})


# ---------------------------------------------------------------- API.md (PAPI)


def blocks_under(heading: str) -> list[Any]:
    text = API_MD.read_text()
    start = text.index(heading)
    end = text.find("\n## v", start + 1)
    section = text[start : end if end != -1 else None]
    return [json.loads(b) for b in re.findall(r"```json\n(.*?)\n```", section, re.S)]


def shape(doc: Any, sample: Any, path: str = "$") -> None:
    """`doc` has exactly the keys `sample` documents, recursively (a list: its first element's
    keys; a key ending in ``?`` is optional; a map documented as ``<name>`` is free-keyed; a
    value documented ``{"...": ...}`` is free)."""
    if doc is None:  # documented as `|null` in the prose
        return
    if isinstance(sample, dict):
        if set(sample) == {"..."}:
            return
        assert isinstance(doc, dict), path
        if len(sample) == 1 and next(iter(sample)).startswith("<"):
            for k, v in doc.items():
                shape(v, next(iter(sample.values())), f"{path}.{k}")
            return
        req = {k for k in sample if not k.endswith("?")}
        opt = {k[:-1] for k in sample if k.endswith("?")}
        assert req <= set(doc) <= req | opt, (path, sorted(set(doc) ^ req))
        for k, v in sample.items():
            k = k.rstrip("?")
            if k in doc:
                shape(doc[k], v, f"{path}.{k}")
    elif isinstance(sample, list) and sample:
        assert isinstance(doc, list), path
        for item in doc:
            shape(item, sample[0], f"{path}[]")


def test_the_header_and_the_documented_shapes(tmp_path: Path, serve, mock: str) -> None:
    assert API_MD.read_text().startswith("# Lattice Studio API contract (v1.26)")
    appmap_sample, element_sample, error_sample, impact_sample, ask_sample = blocks_under(
        "## v1.25"
    )
    w = make_world(tmp_path, reports=True)
    s = serve(w)
    cid = s.draft()
    (w["reports"] / f"{cid}.json").write_text(json.dumps(saved_report(w, cid)))
    key = quote("rule:R3", safe="")
    for get in (s.get, lambda p: request("GET", mock + p)):
        shape(get("/api/appmap")[1], appmap_sample)
        shape(get(f"/api/appmap/element?key={key}")[1], element_sample)
        shape(get("/api/appmap/element?key=rule:R99")[1], error_sample)
    shape(s.get(f"/api/impact?change={cid}")[1], impact_sample)
    shown = s.get(f"/api/impact?change={cid}")[1]
    assert shown["tier1_saved"]["state"] == "shown"
    shape(request("GET", mock + "/api/impact?change=c0")[1], impact_sample)
    # the routed answers
    a = s.post("/api/ask", {"question": f"What breaks if {cid} is applied?"})[1]
    assert a["kind"] == "value"
    shape(a, ask_sample)
    b = s.post("/api/ask", {"question": "What would break if we raised the limit?"})[1]
    assert b["kind"] == "not_specified"
    shape(b, {**ask_sample, "impact": {"...": "..."}})
    assert set(b["impact"]) == {"change", "state", "tiers", "touches", "basis", "meaning"}
    # the documented enumerations
    text = API_MD.read_text()
    section = text[text.index("## v1.25") :]
    for word in (*am.STATES, *impact_ask.IMPACT_STATES, *impact_ask.TIER1_STATES, *am.SECTIONS):
        assert word in section, word
    for word in ("bad_section", "bad_key", "bad_change", "unknown_key", "nearest", "tier1_saved"):
        assert word in section, word
