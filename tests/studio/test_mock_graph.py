"""The business-logic graph of lattice/studio/API.md (v1.4): GET /api/graph and the
``id``/``next`` fields of GET /api/flow stages, served from the mock fixtures."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from lattice.studio.mock_server import FIXTURE_DIR, make_server

EDGE_KINDS = {"enforces", "reads", "writes", "traces", "implements", "applies_to", "transitions"}
NODE_KINDS = {"rule", "endpoint", "table", "service", "scenario", "code", "invariant", "machine"}
TRUST = {"checked", "unchecked", "context", "untraced", "probe_gap", "not_built"}
CANCEL = "endpoint:POST /applications/{}/runs/{}/cancel"


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


def get(url: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def graph(base: str, **kw: Any) -> dict[str, Any]:
    status, g = get(f"{base}/api/graph?{urllib.parse.urlencode(kw)}")
    assert status == 200, g
    return g


def ids(g: dict[str, Any]) -> set[str]:
    return {n["id"] for n in g["nodes"]}


def test_shapes(base: str) -> None:
    g = graph(base)
    assert g["truncated"] is False and g["focus"] is None
    for n in g["nodes"]:
        assert {"id", "kind", "label", "target", "trust", "failing", "rules"} <= set(n)
        assert n["kind"] in NODE_KINDS and n["trust"] in TRUST
        assert isinstance(n["failing"], int) and n["failing"] >= 0
        assert n["target"] and n["id"] == n["target"]
        assert n["target"].split(":", 1)[0] in {
            "rule",
            "endpoint",
            "profile",
            "service",
            "scenario",
            "code",
            "invariant",
            "machine",
        }
    assert len(ids(g)) == len(g["nodes"])
    assert len({e["id"] for e in g["edges"]}) == len(g["edges"])
    for e in g["edges"]:
        assert e["kind"] in EDGE_KINDS
    c = g["counts"]
    assert sum(c["nodes"].values()) == len(g["nodes"])
    assert sum(c["edges"].values()) == len(g["edges"])
    assert c["total"] == {"nodes": len(g["nodes"]), "edges": len(g["edges"])}
    assert set(c["nodes"]) == NODE_KINDS
    assert set(c["edges"]) == EDGE_KINDS


def test_edges_resolve_and_rules_exist(base: str) -> None:
    g = graph(base)
    have = ids(g)
    for e in g["edges"]:
        assert e["source"] in have and e["target"] in have
    rule_ids = {r["id"] for r in get(base + "/api/model")[1]["rules"]}
    for n in g["nodes"]:
        assert set(n["rules"]) <= rule_ids
        if n["kind"] == "rule":
            assert n["rules"] == [n["target"].removeprefix("rule:")]
    assert {n["label"] for n in g["nodes"] if n["kind"] == "rule"} == rule_ids


def test_known_edges(base: str) -> None:
    g = graph(base)
    got = {(e["source"], e["kind"], e["target"]) for e in g["edges"]}
    assert ("rule:R44", "traces", "invariant:one_live_run_per_config") in got
    assert ("rule:R47", "transitions", "machine:JobRun.status") in got
    assert ("rule:R53", "applies_to", "service:worker") in got
    assert ("rule:R46", "enforces", CANCEL) in got
    assert ("rule:R4", "enforces", CANCEL) in got
    assert (CANCEL, "writes", "profile:JobRun") in got
    assert ("scenario:cancel_running_run", "traces", CANCEL) in got
    code = next(
        n["target"] for n in g["nodes"] if n["kind"] == "code" and n["label"] == "CancelRun"
    )
    assert (code, "implements", "rule:R46") in got
    assert (code, "writes", "profile:JobRun") in got
    # every code node resolves in the code lens
    for n in g["nodes"]:
        if n["kind"] == "code":
            assert (
                get(f"{base}/api/code/links?{urllib.parse.urlencode({'target': n['target']})}")[0]
                == 200
            )


def test_trust_and_failing(base: str) -> None:
    g = graph(base)
    by = {n["id"]: n for n in g["nodes"]}
    assert by["scenario:cancel_running_run"]["failing"] == 1
    assert by["invariant:finished_at_exactly_when_finished"]["failing"] == 1
    assert by["scenario:cancel_finished_is_refused"]["failing"] == 0
    assert by[CANCEL]["failing"] >= 1
    assert by["rule:R46"]["failing"] >= 1
    statuses = {r["id"]: r["status"] for r in get(base + "/api/model")[1]["rules"]}
    for rid, st in statuses.items():
        assert by[f"rule:{rid}"]["trust"] == st
    worst = {n["id"]: n["trust"] for n in g["nodes"] if n["kind"] == "rule"}
    assert by["profile:User"]["trust"] == "probe_gap" and worst["rule:R7"] == "probe_gap"


def test_not_built_after_accept_and_failing_clears(base: str) -> None:
    req = urllib.request.Request(base + "/api/changes/c0/accept", data=b"{}", method="POST")
    urllib.request.urlopen(req, timeout=10).read()
    g = graph(base)
    by = {n["id"]: n for n in g["nodes"]}
    assert by["rule:R46"]["trust"] == "not_built" and by["rule:R4"]["trust"] == "not_built"
    assert by[CANCEL]["trust"] == "not_built"
    assert g["change"] is None and g["diff"] == {}


def test_focus_depth_kinds(base: str) -> None:
    g1 = graph(base, focus="rule:R44")
    assert g1["focus"] == "rule:R44" and g1["depth"] == 1
    assert "rule:R44" in ids(g1) and "invariant:one_live_run_per_config" in ids(g1)
    assert len(g1["nodes"]) < graph(base)["counts"]["total"]["nodes"]
    for e in g1["edges"]:
        assert "rule:R44" in (e["source"], e["target"]) or {
            e["source"],
            e["target"],
        } <= ids(g1)
    g0 = graph(base, focus="rule:R44", depth=0)
    assert ids(g0) == {"rule:R44"} and g0["edges"] == []
    g2 = graph(base, focus="rule:R44", depth=2)
    assert ids(g1) < ids(g2)
    only = graph(base, focus="rule:R44", kinds="scenario")
    assert {n["kind"] for n in only["nodes"]} == {"scenario", "rule"}
    assert all(n["kind"] == "scenario" or n["id"] == "rule:R44" for n in only["nodes"])
    # a cell target resolves to its endpoint; so does a short route spelling
    cell = graph(base, focus="cell:eve|POST /applications/{}/runs/{}/cancel")
    assert cell["focus"] == CANCEL
    short = graph(base, focus="endpoint:POST /runs/{}/cancel")
    assert short["focus"] == CANCEL
    assert graph(base, focus="profile:JobRun.status")["focus"] == "profile:JobRun"


def test_limit_truncates_nearest_first(base: str) -> None:
    g = graph(base, focus=CANCEL, depth=2, limit=10)
    assert g["truncated"] is True and len(g["nodes"]) == 10
    assert CANCEL in ids(g)
    have = ids(g)
    assert all(e["source"] in have and e["target"] in have for e in g["edges"])


@pytest.mark.parametrize(
    "q",
    ["kinds=rule,banana", "kinds=", "depth=-1", "depth=7", "depth=x", "limit=0", "limit=abc"],
)
def test_bad_params_422(base: str, q: str) -> None:
    assert get(f"{base}/api/graph?{q}")[0] == 422


def test_unknown_focus_404(base: str) -> None:
    assert get(f"{base}/api/graph?focus=rule:R999")[0] == 404
    assert get(f"{base}/api/graph?focus=nonsense")[0] == 404


def test_deterministic(base: str) -> None:
    a = get(base + "/api/graph")[1]
    b = get(base + "/api/graph")[1]
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)
    f1 = get(f"{base}/api/graph?focus=rule:R46&depth=2")[1]
    f2 = get(f"{base}/api/graph?focus=rule:R46&depth=2")[1]
    assert f1 == f2


def test_machines_match_model(base: str) -> None:
    g = graph(base)
    model = get(base + "/api/model")[1]
    want = model["views"]["section6"]["machines"]
    assert [m["target"] for m in g["machines"]] == [m[0] for m in want]
    for m, (_target, name, text) in zip(g["machines"], want, strict=True):
        assert m["name"] == name
        states = {s["id"] for s in m["states"]}
        assert sum(1 for s in m["states"] if s.get("initial")) == 1
        initial = next(s["id"] for s in m["states"] if s.get("initial"))
        assert text.endswith(f"(initial: {initial})")
        for mv in m["moves"]:
            assert mv["from"] in states and mv["to"] in states
            assert f"{mv['from']} ->" in text and mv["to"] in text
            assert set(mv.get("rules", [])) <= {r["id"] for r in model["rules"]}
        sources = {mv["from"] for mv in m["moves"]}
        for s in m["states"]:
            assert bool(s.get("final")) == (s["id"] not in sources)
    jr = g["machines"][0]
    assert {(m["from"], m["to"]) for m in jr["moves"]} == {
        ("queued", "running"),
        ("queued", "cancelled"),
        ("running", "succeeded"),
        ("running", "failed"),
        ("running", "cancelled"),
    }
    assert jr["moves"][1]["rules"] == ["R47"]
    # machines ignore focus and kinds
    assert graph(base, focus="rule:R44", kinds="rule")["machines"] == g["machines"]


def test_diff_for_the_open_change(base: str) -> None:
    g = graph(base)
    assert g["change"] == "c0"
    d = g["diff"]
    fx = json.loads((FIXTURE_DIR / "change-c1.json").read_text())
    assert d[CANCEL] == "modified"
    for t in fx["targets"]["modified"]:
        assert d[t] == "modified"
    for t in fx["targets"]["added"]:
        assert d[t] == "added"
    for r in fx["rules"]["added"]:
        assert d[f"rule:{r}"] == "added"
    assert set(d.values()) <= {"modified", "added", "removed", "impact"}
    assert "impact" in d.values()
    # the focused view carries the same diff
    assert graph(base, focus="rule:R46")["diff"] == d


def test_flow_stage_ids_and_next(base: str) -> None:
    for ep in ("POST /configs/{}/runs", "POST /runs/{}/cancel"):
        status, f = get(f"{base}/api/flow?{urllib.parse.urlencode({'endpoint': ep})}")
        assert status == 200
        sids = [s["id"] for s in f["stages"]]
        assert len(set(sids)) == len(sids) and all(sids)
        for i, s in enumerate(f["stages"]):
            assert s["next"] == ([sids[i + 1]] if i + 1 < len(sids) else [])
    assert "database-write" in [s["id"] for s in f["stages"]]


RUNS = "endpoint:POST /applications/{}/jobs/{}/runs"


def test_inferred_edges_have_a_path_of_direct_edges(base: str) -> None:
    g = graph(base)
    direct = {(e["source"], e["target"]) for e in g["edges"] if not e.get("inferred")}
    undirected = direct | {(b, a) for a, b in direct}
    ids_ = [e["id"] for e in g["edges"]]
    assert len(ids_) == len(set(ids_))
    inferred = [e for e in g["edges"] if e.get("inferred")]
    assert inferred, "derived edges are expected in the mock graph"
    for e in g["edges"]:
        assert e.get("inferred", False) in (True, False)
        if not e.get("inferred"):
            assert "via" not in e
            continue
        assert e["kind"] in {"enforces", "applies_to"}
        assert e["source"].startswith("rule:")
        assert e["target"].split(":", 1)[0] in {"endpoint", "profile"}
        hops = [e["source"], *e["via"], e["target"]]
        assert len(e["via"]) >= 1
        for a, b in zip(hops, hops[1:], strict=False):
            assert (a, b) in undirected, (e["id"], a, b)
    # a direct edge is never shadowed by a derived one with the same ends and kind
    keys = [(e["source"], e["kind"], e["target"]) for e in g["edges"]]
    assert len(keys) == len(set(keys))


def test_rule_reaches_what_it_governs_within_one_step(base: str) -> None:
    g1 = graph(base, focus="rule:R44", depth=1)
    have = ids(g1)
    assert RUNS in have and "profile:JobRun" in have
    assert "scenario:a_second_live_run_is_refused" in have
    assert any(n["kind"] == "code" for n in g1["nodes"])
    got = {(e["source"], e["kind"], e["target"]): e for e in g1["edges"]}
    assert got[("rule:R44", "enforces", RUNS)]["inferred"] is True
    assert got[("rule:R44", "applies_to", "profile:JobRun")]["inferred"] is True
    # the direct edges stay authoritative: no inferred flag on them
    full = {(e["source"], e["kind"], e["target"]): e for e in graph(base)["edges"]}
    assert "inferred" not in full[("rule:R46", "enforces", CANCEL)]
    assert "inferred" not in full[("rule:R44", "traces", "invariant:one_live_run_per_config")]


def test_inferred_edges_invent_no_rules_and_are_deterministic(base: str) -> None:
    rule_ids = {f"rule:{r['id']}" for r in get(base + "/api/model")[1]["rules"]}
    a = graph(base)
    for e in a["edges"]:
        for end in (e["source"], e["target"], *e.get("via", [])):
            if end.startswith("rule:"):
                assert end in rule_ids
    b = graph(base)
    assert [e for e in a["edges"] if e.get("inferred")] == [
        e for e in b["edges"] if e.get("inferred")
    ]
    # an endpoint and a table count the rules that govern them, derived ones included
    by = {n["id"]: n for n in a["nodes"]}
    assert "R44" in by[RUNS]["governed_by"] and "R44" in by["profile:JobRun"]["governed_by"]
    for n in a["nodes"]:
        if n["kind"] in ("endpoint", "table"):
            assert set(n["rules"]) <= set(n["governed_by"]) <= {r[5:] for r in rule_ids}
        else:
            assert "governed_by" not in n
