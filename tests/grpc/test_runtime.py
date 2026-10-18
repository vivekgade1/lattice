"""Arm G on the notes app (a grpc-go server over GORM and pure-Go SQLite; no Docker): the bridge
(PG-BR), reconcile (PG-REC), layers C and D with the eight deliberate faults (PG-D, PG-C), the
message form (PG-J), the gateway beside gRPC (PG-MIX), login by rpc and the access matrix
(PG-AUTH), the recovered flows (PG-WF) and that nothing is left running (PG-LEAK)."""

from __future__ import annotations

import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from lattice.spec import Rpc, Scenario, Spec, Step, check
from lattice.spec.grpc import BridgeError, GrpcTransport, bearer, live_bridges
from lattice.spec.process import ProcessApp, ProcessStartError, live_processes
from lattice.spec.review import _build_access
from tests.grpc import oracle
from tests.grpc.conftest import needs_go
from tests.grpc.fixtures.notes import harness
from tests.grpc.fixtures.notes import spec as dev

pytestmark = needs_go
NOTES, ACC = "notes.v1.Notes", "notes.v1.Accounts"


def run(spec: Spec = dev.SPEC, factory: Any = None, layers: str = "CD", **kw: Any) -> Any:
    return check(
        spec,
        factory or harness.make_app,
        harness.build_db,
        layers=layers,
        runtime=harness.RUNTIME,
        **kw,
    )


@pytest.fixture
def served(tmp_path: Path, notes_binary: Any) -> Any:
    """The notes app on a seeded database, started, and its runtime transport."""
    db = harness.build_db(tmp_path / "n.db")
    app = harness.make_app(db)
    with harness.RUNTIME.open(app) as transport:
        yield app, transport
    harness.RUNTIME.close(app)


# ---- PG-BR ------------------------------------------------------------------------------


def test_bridge_builds_offline_from_the_checked_in_go_sum(tmp_path: Path) -> None:
    assert (harness.HERE.parents[3] / "lattice/grpcbridge/go.sum").is_file()
    src = tmp_path / "bridge"
    shutil.copytree(harness.HERE.parents[3] / "lattice/grpcbridge", src)
    proc = subprocess.run(
        [
            "go",
            "build",
            "-trimpath",
            "-buildvcs=false",
            "-mod=readonly",
            "-o",
            str(tmp_path / "b"),
            ".",
        ],
        cwd=src,
        capture_output=True,
        text=True,
        check=False,
        env={**__import__("os").environ, "GOFLAGS": "-mod=readonly", "GOPROXY": "off"},
    )
    assert proc.returncode == 0, proc.stderr


def test_bridge_refuses_non_loopback_and_streaming(served: Any) -> None:
    app, transport = served
    rpc = transport.rpc()
    rpc.describe()
    for target in ("10.0.0.1:80", "example.com:443", "localhost:80", "[2001:db8::1]:80"):
        with pytest.raises(BridgeError) as err:
            rpc.bridge.request(
                {
                    "op": "call",
                    "target": target,
                    "service": NOTES,
                    "method": "GetNote",
                    "message": {},
                }
            )
        assert err.value.kind == "non_loopback"
        with pytest.raises(BridgeError) as err:
            rpc.bridge.request({"op": "health", "target": target})
        assert err.value.kind == "non_loopback"
    with pytest.raises(BridgeError) as err:
        rpc.call(NOTES, "WatchNotes", {})
    assert err.value.kind == "streaming_not_supported"


def test_bridge_health_and_identical_answers(served: Any) -> None:
    app, transport = served
    rpc = transport.rpc()
    assert rpc.health() is True
    lines = set()
    for _ in range(10):
        rpc.call(NOTES, "ListNotes", {}, bearer("tok-ann"))
        lines.add(rpc.bridge.last_line)
    assert len(lines) == 1


# ---- PG-REC -----------------------------------------------------------------------------


def test_reconcile_answers_every_rpc_the_extract_found(front_end: Any) -> None:
    model = front_end.extract(oracle.FIXTURES, "notes")
    got = front_end.reconcile(oracle.NOTES, model)
    implemented = {
        f"{r.rpc.service}/{r.rpc.method}" for r in model.routes if r.rpc.state == "implemented"
    }
    assert len(implemented) == 10
    assert all(got[k]["answered"] for k in implemented), got
    assert got[f"{NOTES}/ShareNote"] == {"code": "UNIMPLEMENTED", "answered": False}
    assert got["(absent)"]["code"] == "UNIMPLEMENTED"


# ---- PG-D, PG-C -------------------------------------------------------------------------


def test_the_dev_spec_has_seventeen_scenarios_and_no_finding_on_the_good_app(
    notes_binary: Any,
) -> None:
    assert len(dev.SPEC.scenarios) >= 12
    report = run()
    assert report.findings == [] and len(report.cases) >= 50


@pytest.mark.parametrize("bug", sorted(dev.BUG_SCENARIO))
def test_each_deliberate_fault_breaks_exactly_its_scenario(bug: str, notes_binary: Any) -> None:
    report = run(factory=harness.bugged(bug))
    broken = sorted({f.rule.removeprefix("scenario: ") for f in report.findings if f.layer == "D"})
    assert broken == [dev.BUG_SCENARIO[bug]], [f.details.get("problems") for f in report.findings]


def test_layer_c_catches_the_broken_invariants_after_the_rpc(notes_binary: Any) -> None:
    limit = run(factory=harness.bugged("publish_limit_off"))
    (c,) = [f for f in limit.findings if f.layer == "C"]
    assert (
        c.case == "publish_limit / step 8"
        and c.invariant_violations[0]["invariant"] == "three_published_at_most"
    )
    prio = run(factory=harness.bugged("no_priority_check"))
    (c,) = [f for f in prio.findings if f.layer == "C"]
    assert c.invariant_violations[0]["invariant"] == "priority_in_range"
    assert [f for f in run().findings if f.layer == "C"] == []


def test_a_streaming_scenario_or_layer_a_x_is_refused_not_passed(notes_binary: Any) -> None:
    report = run(layers="ACDX")
    assert report.layers == "CD"
    assert {n.split(":")[0] for n in report.notes} >= {"rpc_layer_not_supported"}
    assert sum("rpc_layer_not_supported" in n for n in report.notes) == 2


def test_code_expectations_are_compared() -> None:
    wrong = replace(
        dev.SPEC,
        scenarios=(
            Scenario(
                "w",
                steps=(
                    Rpc(ACC, "Login", None, {"name": "ann", "password": "bad"}, expect_code="OK"),
                ),
            ),
        ),
    )
    (f,) = run(wrong).findings
    assert f.layer == "D" and f.details["problems"] == [
        "code: expected OK, got UNAUTHENTICATED (bad credentials)"
    ]
    assert f.details["actual"]["status"] == "UNAUTHENTICATED"


# ---- PG-J -------------------------------------------------------------------------------


def test_message_form_int64_enums_and_unpopulated_fields(served: Any) -> None:
    app, transport = served
    rpc = transport.rpc()
    tok = bearer("tok-ann")
    big = rpc.call(NOTES, "CreateNote", {"title": "a", "client_rev": 2**53 - 1}, tok).body["note"]
    over = rpc.call(NOTES, "CreateNote", {"title": "b", "client_rev": 2**53 + 1}, tok).body["note"]
    neg = rpc.call(NOTES, "CreateNote", {"title": "c", "client_rev": -(2**53) - 1}, tok).body[
        "note"
    ]
    assert big["client_rev"] == 2**53 - 1 and isinstance(big["client_rev"], int)
    assert over["client_rev"] == str(2**53 + 1)
    assert neg["client_rev"] == str(-(2**53) - 1)
    assert big["visibility"] == "PRIVATE" and big["body"] == "" and big["priority"] == 0
    assert set(big) == {"id", "title", "body", "state", "priority", "client_rev", "visibility"}
    pub = rpc.call(NOTES, "PublishNote", {"id": big["id"]}, tok).body["note"]
    assert pub["visibility"] == "SHARED"
    empty = rpc.call(NOTES, "ListNotes", {"state": "archived"}, tok).body
    assert empty == {"notes": []}


def test_expect_message_is_a_subset_of_the_canonical_form(notes_binary: Any) -> None:
    ok = replace(
        dev.SPEC,
        scenarios=(
            Scenario(
                "j",
                steps=(
                    Rpc(
                        NOTES,
                        "CreateNote",
                        "ann",
                        {"title": "t", "client_rev": 2**53 + 1},
                        expect_message={
                            "note": {"client_rev": str(2**53 + 1), "visibility": "PRIVATE"}
                        },
                    ),
                    Rpc(
                        NOTES,
                        "GetNote",
                        "ann",
                        {"id": 1},
                        expect_message={"note": {"title": "t", "body": ""}},
                    ),
                ),
            ),
        ),
    )
    assert run(ok).findings == []
    bad = replace(
        ok,
        scenarios=(
            Scenario(
                "j",
                steps=(
                    Rpc(
                        NOTES,
                        "CreateNote",
                        "ann",
                        {"title": "t"},
                        expect_message={"note": {"title": "other"}},
                    ),
                ),
            ),
        ),
    )
    (f,) = run(bad).findings
    assert f.details["problems"][0].startswith("message: expected")


# ---- PG-MIX -----------------------------------------------------------------------------


def test_gateway_http_and_rpc_agree_on_one_process(notes_binary: Any, tmp_path: Path) -> None:
    db = harness.build_db(tmp_path / "m.db")
    app = harness.make_mixed_app(db)
    try:
        with harness.RUNTIME.open(app) as transport:
            assert app.port != app.grpc_port
            rpc = transport.rpc()
            token = harness.RUNTIME.auth.token(transport, "ann")
            headers = harness.RUNTIME.auth.headers(token)
            made = transport.request(
                "POST",
                "/v1/notes",
                json={"title": "via http", "priority": 2, "client_rev": 7},
                headers=headers,
            )
            assert made.status == 200
            over_http = transport.request("GET", "/v1/notes/1", headers=headers).body["note"]
            over_rpc = rpc.call(NOTES, "GetNote", {"id": 1}, bearer(token)).body["note"]
            norm = {k: (int(v) if k in ("id", "client_rev") else v) for k, v in over_http.items()}
            assert norm == over_rpc  # the gateway writes int64 as strings; the same note otherwise
            assert made.body["note"]["title"] == "via http" == over_rpc["title"]
            assert transport.request("GET", "/v1/notes/1").status == 401
    finally:
        harness.RUNTIME.close(app)


def test_one_scenario_mixes_http_and_rpc_steps(notes_binary: Any) -> None:
    mixed = Scenario(
        "both",
        steps=(
            Step(
                "POST",
                "/v1/notes",
                "ann",
                json={"title": "h"},
                expect_status=200,
                expect_body={"note": {"title": "h", "state": "draft"}},
            ),
            Rpc(NOTES, "GetNote", "ann", {"id": 1}, expect_message={"note": {"title": "h"}}),
            Rpc(NOTES, "PublishNote", "ann", {"id": 1}),
            Step("GET", "/v1/notes/1", "ann", expect_body={"note": {"state": "published"}}),
            Step("GET", "/v1/notes/1", None, expect_status=401),
        ),
    )
    report = run(replace(dev.SPEC, scenarios=(mixed,)), factory=harness.make_mixed_app)
    assert report.findings == [], [f.details for f in report.findings]


# ---- PG-AUTH ----------------------------------------------------------------------------


def test_login_by_rpc_and_the_access_matrix_equals_what_was_observed(notes_binary: Any) -> None:
    report = run(layers="D")
    assert report.findings == []
    observed: dict[tuple[str, str], set[str]] = {}
    for case in report.cases.values():
        r = case["request"]
        observed.setdefault((r["user"] or "no token", case["endpoint"]), set()).add(case["code"])
    cells = 0
    for group in _build_access(dev.SPEC):
        for row in group.rows:
            for col in group.columns:
                stated = group.cells[row, col]
                if stated == "·":
                    assert (row, col) not in observed
                    continue
                cells += 1
                assert set(stated.split("/")) == observed[row, col], (row, col, stated)
    assert cells >= 15
    assert {"ann", "ben"} == {u for u, _ in observed if u != "no token"}


def test_extracted_auth_matches_what_a_caller_without_a_token_sees(
    served: Any, n_extract: dict[str, Any]
) -> None:
    """The extractor's Public / Member against the server: a Member rpc refuses a call with no
    token (UNAUTHENTICATED, before its handler); a Public one never does."""
    app, transport = served
    rpc = transport.rpc()
    seen = {}
    for svc in n_extract["grpc"]["services"]:
        for r in svc["rpcs"]:
            if r.get("state") not in ("implemented", "unimplemented"):
                continue
            code = rpc.call(svc["name"], r["name"], {}).code
            seen[f"{svc['name']}/{r['name']}"] = (r["auth"], code)
    assert len(seen) == 11
    wrong = {
        k: v
        for k, v in seen.items()
        if (v[0] == "Member") != (v[1] == "UNAUTHENTICATED") or v[0] not in ("Public", "Member")
    }
    assert wrong == {}


# ---- PG-WF ------------------------------------------------------------------------------


def test_flows_are_recovered_replayed_and_none_verified_fails(
    front_end: Any, notes_binary: Any
) -> None:
    from lattice.spec.grpc import run_rpc_flows

    extract = front_end.extract_json(oracle.FIXTURES, "notes")
    flows = run_rpc_flows(
        extract, dev.SPEC, harness.make_app, harness.build_db, runtime=harness.RUNTIME
    )
    kinds = {f["kind"] for f in flows}
    assert kinds >= {"pair", "lifecycle", "owner_guard", "after_delete", "account"}
    verified = [f for f in flows if f["status"] == "verified"]
    assert len(verified) >= 3
    life = max((f for f in verified if f["kind"] == "lifecycle"), key=lambda f: len(f["steps"]))
    assert [s["rpc"].rsplit("/", 1)[-1] for s in life["steps"]][0] == "CreateNote"
    guard = next(
        f
        for f in verified
        if f["kind"] == "owner_guard" and f["steps"][1]["rpc"].endswith("GetNote")
    )
    assert [s["code"] for s in guard["steps"]] == ["OK", "NOT_FOUND"] and guard["steps"][1][
        "user"
    ] == "ben"
    after = [f for f in verified if f["kind"] == "after_delete"]
    assert {f["steps"][-1]["rpc"].rsplit("/", 1)[-1] for f in after} == {"GetNote", "ListNotes"}
    failed_account = [f for f in flows if f["kind"] == "account" and f["status"] != "verified"]
    assert failed_account == [], failed_account
    # independent replay of every verified flow as layer D: none fails
    from lattice.spec.grpc import _scenario_of

    scenarios = tuple(_scenario_of(f["id"], f["steps"]) for f in verified)
    assert run(replace(dev.SPEC, scenarios=scenarios), layers="D").findings == []


# ---- PG-LEAK ----------------------------------------------------------------------------


def nothing_left() -> None:
    assert live_processes() == [] and live_bridges() == []


def test_nothing_is_left_after_a_check(notes_binary: Any) -> None:
    run(layers="D")
    nothing_left()


def test_nothing_is_left_after_a_failed_start(notes_binary: Any, tmp_path: Path) -> None:
    # the server exits at once
    bad = harness.bugged("x")

    def exits(db: Path) -> ProcessApp:
        app = bad(db)
        app.env = {**app.env, "STARTUP_FAIL": "1"}
        return app

    with pytest.raises(ProcessStartError, match="exited with code 3 before answering grpc.health"):
        run(replace(dev.SPEC, scenarios=dev.SPEC.scenarios[:1]), factory=exits)
    nothing_left()
    # the server runs and is never SERVING
    quiet = ProcessApp(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        db_path=tmp_path / "x.db",
        ready="grpc",
        startup_timeout=1.5,
    )
    with pytest.raises(
        ProcessStartError, match="did not answer grpc.health.v1.Health/Check SERVING"
    ):
        quiet.start()
    nothing_left()


def test_nothing_is_left_after_a_check_that_raises_mid_scenario(
    notes_binary: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    real = GrpcTransport.call

    def boom(self: Any, *a: Any, **kw: Any) -> Any:
        calls.append(1)
        if len(calls) == 3:
            raise KeyboardInterrupt("stop")
        return real(self, *a, **kw)

    monkeypatch.setattr(GrpcTransport, "call", boom)
    with pytest.raises(KeyboardInterrupt):
        run(layers="D")
    nothing_left()


# ---- PG-X -------------------------------------------------------------------------------


def test_a_call_costs_milliseconds_and_the_dev_spec_seconds(served: Any, notes_binary: Any) -> None:
    app, transport = served
    rpc = transport.rpc()
    rpc.call(NOTES, "ListNotes", {}, bearer("tok-ann"))
    times = []
    for _ in range(50):
        t0 = time.perf_counter()
        rpc.call(NOTES, "ListNotes", {}, bearer("tok-ann"))
        times.append(time.perf_counter() - t0)
    assert statistics.median(times) < 0.005
    t0 = time.perf_counter()
    run()
    assert time.perf_counter() - t0 < 30
