"""The workflow layer (``lattice.importer.workflows`` / ``workflow_run``): the dependency
graph and the flows of the fixture app, the value candidates and the repair rules, the live
executor against an in-process stand-in for the fixture app (no Docker, no Go build), the
machine drafts and the static command. The fixture is
``tests/gocheck/fixtures/goextract_workflow``; tests skip when ``go`` is not on PATH."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from lattice.importer import workflow_run as R
from lattice.importer import workflows as W

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")

FIXTURE = Path(__file__).parents[1] / "gocheck" / "fixtures" / "goextract_workflow"
USERS = ["ann", "ben"]


@pytest.fixture(scope="module")
def x() -> dict[str, Any]:
    from lattice.importer.frontends.go_gin import GoGinFrontEnd

    return GoGinFrontEnd().extract_json(FIXTURE, workflow=True)


@pytest.fixture(scope="module")
def plan(x: dict[str, Any]) -> W.Plan:
    return W.plan(x, USERS)


def skeletons(plan: W.Plan, kind: str | None = None) -> list[str]:
    return [
        " > ".join(f.skeleton(plan.view))
        for f in plan.flows
        if kind is None or f.kind == kind or kind in f.merged_kinds
    ]


# ---- D2: the dependency graph -------------------------------------------------------------


def test_edges_are_key_comparisons_of_a_table_some_route_creates(plan):
    got = {(e.table, e.producer, e.consumer, e.param, e.source) for e in plan.edges}
    assert got == {
        ("jobs", "POST /api/jobs", "POST /api/jobs/{id}/activate", "id", "path"),
        ("jobs", "POST /api/jobs", "POST /api/jobs/{id}/runs", "id", "path"),
        ("projects", "POST /api/projects", "GET /api/projects/{id}", "id", "path"),
        ("projects", "POST /api/projects", "POST /api/projects/{id}/archive", "id", "path"),
        ("projects", "POST /api/projects", "POST /api/projects/{id}/publish", "id", "path"),
        ("projects", "POST /api/projects", "POST /api/tickets", "project_id", "body"),
        ("tickets", "POST /api/tickets", "DELETE /api/tickets/{id}", "id", "path"),
        ("tickets", "POST /api/tickets", "GET /api/tickets/{id}", "id", "path"),
    }


def test_an_edge_knows_where_the_producers_key_comes_from(plan):
    by_table = {e.table: e for e in plan.edges}
    assert by_table["projects"].key_from == "id"  # a body field: a fresh id is generated
    assert by_table["tickets"].key_from == "server"
    assert by_table["jobs"].key_from == "server"


def test_an_edge_lists_its_evidence(plan):
    e = next(e for e in plan.edges if e.consumer == "GET /api/projects/{id}")
    assert {x["what"] for x in e.evidence} == {"param_use", "create"}
    assert all(":" in x["at"] for x in e.evidence)


def test_a_comparison_with_no_creator_is_unread_not_an_edge(x):
    y = json.loads(json.dumps(x))
    y["creates"] = [c for c in y["creates"] if c["table"] != "jobs"]
    view = W.AppView(y)
    edges = W.build_edges(view)
    assert not [e for e in edges if e.table == "jobs"]
    assert any("no producer" in u["reason"] for u in view.unread)


def test_a_route_that_creates_the_table_does_not_depend_on_it_through_the_body(x):
    view = W.AppView(x)
    # POST /api/projects compares its own body id to projects.id (an idempotency check)
    assert not [e for e in W.build_edges(view) if e.consumer == "POST /api/projects"]


# ---- D3: the flows ------------------------------------------------------------------------


def test_pair_flows_carry_the_setup_chain_of_the_producer(plan):
    pairs = skeletons(plan, "pair")
    assert "POST /api/projects > POST /api/tickets > DELETE /api/tickets/{}" in pairs
    assert "POST /api/jobs > POST /api/jobs/{}/runs" in pairs
    f = next(f for f in plan.flows if f.skeleton(plan.view)[-1] == "DELETE /api/tickets/{}")
    assert f.focus_from == 1  # the ticket producer, after its project


def test_pairs_with_one_skeleton_are_merged_and_list_every_edge(plan):
    f = next(
        f
        for f in plan.flows
        if f.kind == "pair" and f.skeleton(plan.view) == ["POST /api/projects", "POST /api/tickets"]
    )
    assert f.edge_refs and len(set(f.edge_refs)) == len(f.edge_refs)


def test_a_flow_is_never_listed_twice(plan):
    ids = [f.ident(plan.view) for f in plan.flows]
    assert len(ids) == len(set(ids))


def test_owner_guard_calls_the_consumer_as_the_second_user(plan):
    guards = [f for f in plan.flows if f.kind == "owner_guard"]
    assert guards
    for f in guards:
        assert f.callers()[-1] == "ben" and set(f.callers()[:-1]) == {"ann"}


def test_lifecycle_is_the_write_steps_in_phase_order(plan):
    got = {tuple(f.skeleton(plan.view)) for f in plan.flows if f.kind == "lifecycle"}
    assert got == {
        (
            "POST /api/projects",
            "POST /api/tickets",
            "POST /api/projects/{}/publish",
            "POST /api/projects/{}/archive",
            "DELETE /api/tickets/{}",
        ),
        ("POST /api/jobs", "POST /api/jobs/{}/activate", "POST /api/jobs/{}/runs"),
    }


def test_after_delete_reads_the_row_after_it_is_deleted(plan):
    got = {tuple(f.skeleton(plan.view)) for f in plan.flows if f.kind == "after_delete"}
    assert (
        "POST /api/projects",
        "POST /api/tickets",
        "DELETE /api/tickets/{}",
        "GET /api/tickets/{}",
    ) in got


def test_effect_flows_are_a_write_then_a_read_of_what_it_touched(plan):
    for f in plan.flows:
        if f.kind == "effect":
            sk = f.skeleton(plan.view)
            assert sk[-1].startswith("GET ")
            write = f.steps[-2].route
            assert plan.view.by_key[write].method != "GET"


def test_the_conditional_route_is_never_used(plan):
    assert all("/test/clock" not in s for sk in skeletons(plan) for s in sk.split(" > "))
    assert [r.key for r in plan.view.excluded_conditional] == ["PUT /test/clock"]


def test_flows_are_sorted_by_kind_priority_then_skeleton(plan):
    prio = {k: i for i, k in enumerate(W.KIND_PRIORITY)}
    keys = [(prio[f.kind], " > ".join(f.skeleton(plan.view))) for f in plan.flows]
    assert keys == sorted(keys)


def test_flows_beyond_the_caps_are_listed_not_dropped_silently(x):
    p = W.plan(x, USERS, max_flows=3)
    assert len(p.flows) == 3
    assert p.capped and all(c["reason"] == "more than 3 flows" for c in p.capped)
    q = W.plan(x, USERS, max_steps=1)
    assert q.flows == [] and q.capped


def test_flow_ids_hash_kind_skeleton_and_callers(plan):
    f = plan.flows[0]
    raw = "|".join(
        (f.kind, " > ".join(f.skeleton(plan.view)), ",".join(c or "-" for c in f.callers()))
    )
    assert f.ident(plan.view) == "wf-" + hashlib.sha256(raw.encode()).hexdigest()[:10]


def test_names_are_built_from_verbs_and_nouns(plan):
    f = next(f for f in plan.flows if f.skeleton(plan.view)[-1] == "POST /api/projects/{}/publish")
    assert W.flow_name(plan.view, f).endswith("publish projects")


# ---- D6: machines -------------------------------------------------------------------------


def test_machine_drafts_read_states_initial_and_from_states(plan):
    by = {(m["model"], m["column"]): m for m in plan.machines}
    assert set(by) == {("Project", "status"), ("Job", "status")}  # Run.state has one value
    p = by[("Project", "status")]
    assert p["states"] == ["archived", "draft", "published"]
    assert p["initial"] == ["draft"]
    assert [(e["from"], e["to"]) for e in p["edges"]] == [
        ("draft", "archived"),
        ("draft", "published"),
        ("published", "archived"),
    ]
    assert p["static"] and not p["unread"]
    j = by[("Job", "status")]
    assert [(e["from"], e["to"]) for e in j["edges"]] == [("queued", "active")]
    assert j["initial"] == ["queued"]


def test_an_unknown_from_state_makes_no_static_map(x):
    y = json.loads(json.dumps(x))
    for f in y["status_facts"]:
        if f["model"] == "Job" and f["kind"] == "update":
            f["from"], f["from_at"] = ["*"], []
    drafts = {(m["model"], m["column"]): m for m in W.machine_drafts(W.AppView(y))}
    j = drafts[("Job", "status")]
    assert not j["static"] and j["edges"] == [] and j["unread"]


def test_a_machine_flow_leaves_a_row_then_calls_the_route_that_moves_it(plan):
    sk = skeletons(plan, "machine")
    assert "POST /api/jobs > POST /api/jobs/{}/activate" in sk


# ---- D4: values ---------------------------------------------------------------------------


def p(name: str, typ: str | None, **kw: Any) -> dict[str, Any]:
    return {"name": name, "type": typ, "required": True, "choices": [], **kw}


def cands(param: dict[str, Any], ctx: W.ValueCtx | None = None, lits: tuple[str, ...] = ()):
    return W.field_candidates(param, lits, ctx or W.ValueCtx("abc123"))


def test_choices_come_first_then_literals():
    got = cands(p("period", "Str", choices=["a", "b"]), lits=("c",))
    assert [v for v, _ in got[:3]] == ["a", "b", "c"]
    assert [s for _, s in got[:3]] == ["choices", "choices", "literal"]


def test_an_int_takes_the_smallest_value_the_bounds_allow():
    assert cands(p("n", "Int"))[0] == (1, "default")
    assert cands(p("n", "Int", ge=5))[0] == (5, "default")
    assert cands(p("n", "Int", gt=9))[0] == (10, "default")
    assert cands(p("n", "Int", ge=5, le=3))[0] == (3, "default")


def test_a_bool_tries_false_then_true():
    assert [v for v, _ in cands(p("b", "Bool"))] == [False, True]


def test_a_string_is_a_slug_cut_or_padded_to_its_bounds():
    ctx = W.ValueCtx("abc123")
    assert cands(p("name", "Str"), ctx)[0] == ("wfabc123-name", "default")
    assert cands(p("name", "Str", max_length=6), ctx)[0][0] == "wfabc1"
    assert cands(p("n", "Str", min_length=14), ctx)[0][0] == "wfabc123-nxxxxx"[:14].ljust(14, "x")


def test_the_probe_list_follows_the_defaults():
    got = [v for v, _ in cands(p("x", "Str"))]
    assert got[1:] == [
        "{uuid}",
        "wf{n}@example.test",
        "UTC",
        "2026-01-01",
        "1.0.0",
        "https://example.test/",
    ]


def test_a_bound_value_is_a_same_name_field_of_an_earlier_request_or_answer():
    ctx = W.ValueCtx("t")
    ctx.requests.append({"name": "sent"})
    ctx.responses.append({"name": "answered", "nested": {"name": "deep"}})
    got = [v for v, s in cands(p("name", "Str"), ctx) if s == "bound"]
    assert got == ["sent", "answered", "deep"]


def test_a_date_field_is_bound_to_the_first_plain_date_of_the_answers():
    ctx = W.ValueCtx("t")
    ctx.responses.append({"created_at": "2026-03-04T12:00:00Z", "start_date": "2026-03-04"})
    got = cands(p("date", "DateTime"), ctx)
    assert got[0] == ("2026-03-04", "bound")


def test_a_key_field_is_always_a_fresh_id_and_is_never_bound():
    ctx = W.ValueCtx("t")
    ctx.requests.append({"id": "earlier"})
    got = W.field_candidates(p("id", "Str"), (), ctx, key=True)
    assert got == [("{uuid}", "generated")]
    assert ctx.pick("id", 0, "{uuid}") == "00000000-0000-4000-8000-000000000001"
    assert ctx.pick("id", 0, "{uuid}") == "00000000-0000-4000-8000-000000000001"  # picked once
    assert ctx.pick("other", 0, "{uuid}") == "00000000-0000-4000-8000-000000000002"


def test_a_422_names_the_field_whose_value_is_a_sent_key():
    body = {"error": {"code": "validation_failed", "field": "subject", "message": "x"}}
    assert W.error_field(body, {"subject", "project_id"}) == "subject"
    assert W.error_field({"error": "nope"}, {"subject"}) is None


def test_refusal_codes_and_ids_are_read_from_the_answers():
    assert W.refusal_code({"error": {"code": "not_found"}}) == "not_found"
    assert W.refusal_code({"code": "x"}) == "x"
    assert W.refusal_code("html") is None
    assert W.extract_id({"id": 7}) == 7
    assert W.extract_id({"device": {"id": 3}}) == 3
    assert W.extract_id({"a": {"id": 1}, "b": {"id": 2}}) is None
    assert W.list_id({"meta": 1, "items": [{"id": "z"}, {"id": "y"}]}) == "z"
    assert W.list_id({"habits": [{"id": "h"}], "nudges": [{"id": "n"}]}) == "h"  # document order


# ---- the executor against an in-process stand-in ------------------------------------------


@dataclass
class FakeAnswer:
    status: int
    body: Any = None


@dataclass
class FakeDb:
    tables: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {t: [] for t in ("users", "projects", "tickets", "jobs", "runs")}
    )
    next_id: dict[str, int] = field(default_factory=dict)

    def add(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        if "id" not in row:
            self.next_id[table] = self.next_id.get(table, 0) + 1
            row = {"id": self.next_id[table], **row}
        self.tables[table].append(row)
        return row


class FakeApp:
    """The fixture app's routes, in memory (stricter than its code where a test needs it)."""

    def __init__(
        self,
        db: FakeDb,
        strict_subject: bool = False,
        forbid: str | None = None,
        odd: dict[str, int] | None = None,
    ) -> None:
        self.db, self.strict_subject, self.forbid = db, strict_subject, forbid
        self.odd = odd or {}  # statuses the code gives no evidence for
        self.calls: list[tuple[str, str]] = []

    def handle(self, method: str, path: str, body: Any, who: str | None) -> FakeAnswer:
        self.calls.append((method, path))
        uid = {"ann": 1, "ben": 2}.get(who or "")
        if not path.startswith("/api/login") and uid is None:
            return FakeAnswer(401, {"error": {"code": "unauthorized"}})
        if self.forbid and who == self.forbid:
            return FakeAnswer(403, {"error": {"code": "forbidden"}})
        parts = path.strip("/").split("/")
        db = self.db
        if (method, parts[:2]) == ("POST", ["api", "projects"]) and len(parts) == 2:
            if body.get("kind") not in ("public", "private"):
                return FakeAnswer(422, {"error": {"code": "invalid", "field": "kind"}})
            row = db.add("projects", {**body, "owner_id": uid, "status": "draft"})
            return FakeAnswer(201, row)
        if parts[:2] == ["api", "projects"] and len(parts) == 3 and method == "GET":
            row = next((r for r in db.tables["projects"] if r["id"] == parts[2]), None)
            if row is None or row["owner_id"] != uid:
                return FakeAnswer(self.odd.get("refusal", 404), {"error": {"code": "not_found"}})
            return FakeAnswer(200, row)
        if (method, parts) == ("POST", ["api", "tickets"]):
            if self.strict_subject and "@" not in str(body.get("subject")):
                return FakeAnswer(422, {"error": {"code": "invalid", "field": "subject"}})
            proj = next(
                (r for r in db.tables["projects"] if r["id"] == body.get("project_id")), None
            )
            if proj is None or proj["owner_id"] != uid:
                return FakeAnswer(404, {"error": {"code": "not_found"}})
            db.add("tickets", {"project_id": proj["id"], "subject": body["subject"]})
            return FakeAnswer(201, {"ok": True})
        if (method, parts) == ("GET", ["api", "tickets"]):
            return FakeAnswer(200, {"items": list(db.tables["tickets"])})
        if method == "DELETE" and parts[:2] == ["api", "tickets"]:
            rows = [r for r in db.tables["tickets"] if str(r["id"]) == parts[2]]
            if not rows:
                return FakeAnswer(404, {"error": {"code": "not_found"}})
            db.tables["tickets"].remove(rows[0])
            return FakeAnswer(204)
        if (method, parts) == ("POST", ["api", "jobs"]):
            return FakeAnswer(
                self.odd.get("jobs", 201),
                db.add("jobs", {"owner_id": uid, "name": body["name"], "status": "queued"}),
            )
        if parts[:2] == ["api", "jobs"] and len(parts) == 4 and method == "POST":
            job = next((r for r in db.tables["jobs"] if str(r["id"]) == parts[2]), None)
            if job is None or job["owner_id"] != uid:
                return FakeAnswer(404, {"error": {"code": "not_found"}})
            if parts[3] == "activate":
                if job["status"] != "queued":
                    return FakeAnswer(409, {"error": {"code": "not_queued"}})
                job["status"] = "active"
                return FakeAnswer(204)
            if parts[3] == "runs":
                if job["status"] != "active":
                    return FakeAnswer(409, {"error": {"code": "job_not_active"}})
                return FakeAnswer(201, db.add("runs", {"job_id": job["id"], "state": "running"}))
        return FakeAnswer(404, {"error": {"code": "no_route"}})


class FakeTransport:
    def __init__(self, app: FakeApp) -> None:
        self.app = app
        self.client = self
        self.who: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        **_,
    ):
        auth = (headers or {}).get("Authorization", "")
        self.who = auth.removeprefix("Bearer tok-") if auth else None
        return self.app.handle(method, path, json, self.who)

    def __enter__(self) -> FakeTransport:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeAuth:
    def token(self, transport: Any, handle: str, login: Any) -> str:
        return f"tok-{handle}"

    def present(self, transport: Any, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


class FakeRuntime:
    def __init__(self) -> None:
        self.auth = FakeAuth()

    def open(self, app: FakeApp) -> FakeTransport:
        return FakeTransport(app)

    def close(self, app: Any) -> None:
        return None


class FakeProvider:
    dialect = "fake"
    url = None

    def __init__(self) -> None:
        self.dbs: list[FakeDb] = []

    def fresh(self) -> FakeDb:
        self.dbs.append(FakeDb())
        return self.dbs[-1]

    def release(self, ref: Any) -> None:
        return None

    def read(self, ref: FakeDb, table: str, order_by: str | None) -> list[dict[str, Any]]:
        return list(ref.tables.get(table, []))

    def snapshot(self, ref: FakeDb, tables: Any, keys: Any, dest: Path) -> None:
        R.write_sqlite(dest, {t: ref.tables.get(t, []) for t in tables})


def executor(plan: W.Plan, tmp_path: Path, **app_kw: Any) -> tuple[R.Executor, FakeProvider]:
    provider = FakeProvider()
    h = R.Harness(
        lambda ref: FakeApp(ref, **app_kw), lambda ref: None, None, {}, FakeRuntime(), "harness.py"
    )
    pl = W.Planner(plan.view, plan.edges, USERS)
    return R.Executor(plan.view, h, provider, h.runtime, USERS, tmp_path, pl, []), provider


def flow_of(plan: W.Plan, *last: str, kind: str | None = None) -> W.Flow:
    for f in plan.flows:
        if f.skeleton(plan.view)[-len(last) :] == list(last) and (kind is None or f.kind == kind):
            return f
    raise AssertionError(f"no flow ends {last}")


def test_a_pair_flow_runs_threads_a_fresh_client_id_and_passes(plan, tmp_path):
    ex, _ = executor(plan, tmp_path)
    f = flow_of(plan, "POST /api/projects", "GET /api/projects/{}", kind="pair")
    out = ex.run(f)
    assert out.ok and [r.status for r in out.runs] == [201, 200]
    first, second = out.runs
    assert second.path == f"/api/projects/{first.body['id']}"
    assert first.body["id"] == "00000000-0000-4000-8000-000000000001"
    assert first.sources["kind"] == "literal"  # `public` from the code's comparison
    assert second.bound[0]["id_source"] == "client"


def test_the_snapshots_follow_every_request(plan, tmp_path):
    ex, _ = executor(plan, tmp_path)
    out = ex.run(flow_of(plan, "POST /api/projects", "GET /api/projects/{}", kind="pair"))
    assert len(out.snapshots) == 3 and len(out.requests) == 2  # before, and after each request
    assert all(p.is_file() for p in out.snapshots)


def test_a_server_id_the_answer_does_not_hold_comes_from_a_list(plan, tmp_path):
    ex, _ = executor(plan, tmp_path)
    f = flow_of(plan, "DELETE /api/tickets/{}", kind="pair")
    out = ex.run(f)
    assert out.ok, out.reason
    listed = [r for r in out.runs if r.inserted == "id_source"]
    assert len(listed) == 1 and listed[0].route.key == "GET /api/tickets"
    assert out.runs[-1].bound[0]["id_source"] == "list"
    assert out.runs[-1].path == "/api/tickets/1"
    assert R.final_id(plan.view, f, out) != f.ident(plan.view)  # the skeleton gained a step


def test_a_422_that_names_a_field_moves_that_field_to_its_next_candidate(plan, tmp_path):
    ex, _ = executor(plan, tmp_path, strict_subject=True)
    out = ex.run(flow_of(plan, "POST /api/projects", "POST /api/tickets", kind="pair"))
    assert out.ok, out.reason
    ticket = out.runs[-1]
    assert ticket.attempts == 3  # the slug, then the first probe (a uuid), then an email
    assert ticket.sources["subject"] == "probe" and "@" in ticket.body["subject"]


def test_a_conflict_inserts_the_action_route_on_the_table_first(plan, tmp_path):
    ex, _ = executor(plan, tmp_path)
    out = ex.run(flow_of(plan, "POST /api/jobs", "POST /api/jobs/{}/runs", kind="pair"))
    assert out.ok, out.reason
    assert [r.route.key for r in out.runs] == [
        "POST /api/jobs",
        "POST /api/jobs/{id}/activate",
        "POST /api/jobs/{id}/runs",
    ]
    assert out.runs[1].inserted == "precondition" and out.runs[2].attempts == 2


def test_a_403_retries_with_the_next_declared_user(plan, tmp_path):
    ex, _ = executor(plan, tmp_path, forbid="ann")
    out = ex.run(flow_of(plan, "POST /api/projects", "GET /api/projects/{}", kind="pair"))
    assert out.ok
    assert {r.caller for r in out.runs} == {"ben"}


def test_the_owner_guard_flow_passes_on_the_refusal(plan, tmp_path):
    ex, _ = executor(plan, tmp_path)
    f = flow_of(plan, "POST /api/projects", "GET /api/projects/{}", kind="owner_guard")
    out = ex.run(f)
    assert out.ok and out.runs[-1].status == 404 and out.runs[-1].expected_refusal
    assert out.runs[-1].refusal == {"status": 404, "code": "not_found"}


def test_a_step_that_fails_stops_the_flow_with_its_status_and_code(plan, tmp_path):
    ex, _ = executor(plan, tmp_path)
    f = flow_of(plan, "POST /api/jobs", "POST /api/jobs/{}/runs", kind="pair")
    ex.planner = W.Planner(plan.view, [], USERS)  # no action route to insert: the 409 stays
    out = ex.run(f)
    assert not out.ok and out.failed_at == 1
    assert out.runs[1].refusal == {"status": 409, "code": "job_not_active"}
    status, reason = R.Pipeline.__new__(R.Pipeline)._status(out, None, f)
    assert status == "failed" and "409" in reason and "job_not_active" in reason


def test_a_guard_flow_is_the_prefix_up_to_the_refusal(plan, tmp_path):
    ex, _ = executor(plan, tmp_path)
    f = flow_of(plan, "POST /api/jobs", "POST /api/jobs/{}/runs", kind="pair")
    ex.planner = W.Planner(plan.view, [], USERS)
    out = ex.run(f)
    pipe = R.Pipeline.__new__(R.Pipeline)
    g = pipe._guard_flow(plan, f, out)
    assert g is not None and g.kind == "guard"
    assert g.skeleton(plan.view) == f.skeleton(plan.view)
    out2 = ex.run(g)
    assert out2.ok and out2.runs[-1].status == 409


def test_an_attempt_budget_ends_a_step_that_cannot_be_satisfied(plan, tmp_path):
    ex, _ = executor(plan, tmp_path)
    ex.planner = W.Planner(plan.view, [], USERS)
    out = ex.run(flow_of(plan, "POST /api/jobs", "POST /api/jobs/{}/runs", kind="pair"))
    assert out.attempts <= W.ATTEMPTS_PER_FLOW


def test_no_declared_user_leaves_a_member_flow_unrun(plan, tmp_path):
    ex, provider = executor(plan, tmp_path)
    ex.users = []
    out = ex.run(flow_of(plan, "POST /api/projects", "GET /api/projects/{}", kind="pair"))
    assert not out.ok and out.failed_at is None and "no declared user" in out.reason
    assert provider.dbs == []


def test_a_2xx_not_in_the_code_evidence_fails_the_step(plan, tmp_path):
    ex, _ = executor(plan, tmp_path, odd={"jobs": 202})
    out = ex.run(flow_of(plan, "POST /api/jobs", "POST /api/jobs/{}/runs", kind="pair"))
    assert not out.ok and out.failed_at == 0
    assert "not in the code evidence" in out.reason and out.runs[0].status == 202


def test_a_refusal_not_in_the_code_evidence_fails_the_guard_flow(plan, tmp_path):
    ex, _ = executor(plan, tmp_path, odd={"refusal": 418})
    f = flow_of(plan, "POST /api/projects", "GET /api/projects/{}", kind="owner_guard")
    out = ex.run(f)
    assert not out.ok and out.runs[-1].status == 418
    assert "not in the code evidence" in out.reason


def test_a_scenario_is_frozen_from_the_requests_that_were_made(plan, tmp_path):
    ex, _ = executor(plan, tmp_path)
    f = flow_of(plan, "POST /api/projects", "GET /api/projects/{}", kind="pair")
    sc = R.scenario_of("wf-x", ex.run(f))
    assert [(s.method, s.expect_status, s.user) for s in sc.steps] == [
        ("POST", 201, "ann"),
        ("GET", 200, "ann"),
    ]
    assert sc.steps[1].path.startswith("/api/projects/00000000-0000-4000-8000-")


# ---- snapshots and machines ---------------------------------------------------------------


def test_scan_snapshots_counts_moves_and_creations(tmp_path):
    shots = []
    for i, rows in enumerate(
        ([], [{"id": 1, "s": "a"}], [{"id": 1, "s": "b"}], [{"id": 1, "s": "b"}])
    ):
        shots.append(tmp_path / f"{i}.db")
        R.write_sqlite(shots[-1], {"t": rows})
    moves, created = R.scan_snapshots([shots], "t", "id", "s")
    assert moves == {("a", "b"): 1} and created == {"a": 1}


def test_write_sqlite_stores_dates_decimals_and_bytes_as_text(tmp_path):
    import datetime as dt
    import decimal
    import sqlite3

    dest = tmp_path / "s.db"
    R.write_sqlite(
        dest,
        {
            "t": [
                {
                    "d": dt.datetime(2026, 3, 4, 12, 0),
                    "n": decimal.Decimal("1.5"),
                    "b": b"\x01",
                    "f": True,
                }
            ]
        },
    )
    row = sqlite3.connect(dest).execute("select d, n, b, f from t").fetchone()
    assert row == ("2026-03-04 12:00:00", "1.5", "01", 1)


def test_with_one_of_marks_the_column_an_enum(plan):
    from lattice.importer.frontends.go_gin import model_from_extract
    from lattice.importer.lift import load_profile
    from lattice.importer.profile_gen import generate_profile

    model = model_from_extract(plan.view.x)
    module = load_profile(generate_profile(model), "lattice_wf_test_profile")
    prof = R.with_one_of(module.profile(), "Job", "status", ["queued", "active"])
    assert dict(prof.api.tables["Job"].one_of)["status"] == ("queued", "active")


# ---- the harness --------------------------------------------------------------------------


def test_a_studio_toml_app_table_names_the_harness(tmp_path):
    (tmp_path / "studio.toml").write_text(
        '[app]\napp = "h:make"\ndb = "h:build"\nlogin = "h:login"\nruntime = "h:RT"\n'
        'services = "h:SERVICES"\n'
    )
    (tmp_path / "h.py").write_text(
        "def make(db): return 1\ndef build(db): return 2\ndef login(c, h): return 'x'\n"
        "RT = 'rt'\nSERVICES = {'w': lambda db: None}\n"
    )
    h = R.find_harness(tmp_path)
    assert h is not None and h.source == "studio.toml" and h.runtime == "rt"
    assert h.make_app(0) == 1 and h.build_db(0) == 2 and list(h.services) == ["w"]


def test_a_root_harness_py_is_the_fallback(tmp_path):
    (tmp_path / "harness.py").write_text("def make_app(db): return 1\ndef build_db(db): return 2\n")
    h = R.find_harness(tmp_path)
    assert h is not None and h.source == "harness.py" and h.login is None and h.services == {}


def test_no_harness_is_none(tmp_path):
    assert R.find_harness(tmp_path) is None
    (tmp_path / "harness.py").write_text("def make_app(db): return 1\n")  # no build_db
    assert R.find_harness(tmp_path) is None


# ---- the command (static) -----------------------------------------------------------------


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_a_static_run_writes_workflows_json_and_nothing_into_the_target(tmp_path):
    app = tmp_path / "app"
    shutil.copytree(FIXTURE, app)
    before = tree(app)
    out = tmp_path / "out"
    rc = R.main([str(app), "--user", "ann", "--user", "ben", "--static", "--out", str(out)])
    assert rc == 0 and tree(app) == before
    assert [p.name for p in out.iterdir()] == ["workflows.json"]
    doc = json.loads((out / "workflows.json").read_text())
    assert doc["schema"] == W.SCHEMA and doc["run"]["mode"] == "static"
    assert doc["summary"]["failed"] == 0 and doc["summary"]["verified"] == 0
    assert doc["summary"]["flows"] == len(doc["flows"]) > 0
    assert all(f["status"] == "unverified" and f["source"] == "static" for f in doc["flows"])
    assert not any(k in json.dumps(doc) for k in ("/Users", "/private", str(tmp_path)))


def test_a_static_run_is_byte_identical_from_another_path(tmp_path):
    outs = []
    for i in range(2):
        app = tmp_path / f"copy{i}" / "deeper" / "app"
        shutil.copytree(FIXTURE, app)
        out = tmp_path / f"out{i}"
        assert (
            R.main([str(app), "--user", "ann", "--user", "ben", "--static", "--out", str(out)]) == 0
        )
        outs.append((out / "workflows.json").read_bytes())
    assert outs[0] == outs[1]


def test_out_under_the_target_is_refused(tmp_path, capsys):
    app = tmp_path / "app"
    shutil.copytree(FIXTURE, app)
    rc = R.main([str(app), "--static", "--out", str(app / "out")])
    assert rc == 2 and "under the target" in capsys.readouterr().err
    assert not (app / "out").exists()


def test_the_output_is_sorted_and_has_the_contract_keys(tmp_path):
    out = tmp_path / "o"
    assert R.main([str(FIXTURE), "--user", "ann", "--static", "--out", str(out)]) == 0
    doc = json.loads((out / "workflows.json").read_text())
    assert list(doc) == sorted(doc)
    assert {"app", "run", "edges", "machines", "flows", "unread", "capped", "summary"} <= set(doc)
    edge = doc["edges"][0]
    assert {
        "id",
        "table",
        "key",
        "producer",
        "consumer",
        "param",
        "param_source",
        "id_source",
        "alternatives",
        "evidence",
    } <= set(edge)
    step = doc["flows"][0]["steps"][0]
    assert {
        "i",
        "kind",
        "method",
        "route",
        "path",
        "caller",
        "role",
        "json",
        "expect_status",
        "expect_from",
        "code_statuses",
        "bindings",
        "value_sources",
        "inserted",
        "attempts",
        "refusal",
        "evidence",
    } <= set(step)
    assert doc["edges"] == sorted(
        doc["edges"], key=lambda e: (e["table"], e["producer"], e["consumer"], e["param"])
    )
    assert any(u["what"] == "route" and "TEST_CLOCK" in u["reason"] for u in doc["unread"])


def test_the_importer_cli_dispatches_workflows(tmp_path):
    from lattice.importer.cli import main

    out = tmp_path / "o"
    assert main(["workflows", str(FIXTURE), "--static", "--out", str(out)]) == 0
    assert (out / "workflows.json").is_file()


def test_default_extraction_is_unchanged_by_the_shim_flag():
    from lattice.importer.frontends.go_gin import GoGinFrontEnd

    fe = GoGinFrontEnd()
    plain = fe.extract_json(FIXTURE)
    assert "param_uses" not in plain
    wf = fe.extract_json(FIXTURE, workflow=True)
    assert {k: v for k, v in wf.items() if k in plain} == plain


# ---- sweeps and machine confirmation ------------------------------------------------------


def _states(tmp_path: Path, name: str, seq: list[list[tuple[int, str]]]) -> list[Path]:
    """SQLite snapshots of ``jobs`` (id, status) with the other tables empty."""
    paths = []
    for i, rows in enumerate(seq):
        path = tmp_path / f"{name}-{i}.db"
        data: dict[str, list[dict[str, Any]]] = {
            t: [] for t in ("users", "projects", "tickets", "runs")
        }
        data["jobs"] = [{"id": k, "owner_id": 1, "name": "j", "status": s} for k, s in rows]
        R.write_sqlite(path, data)
        paths.append(path)
    return paths


def _outcome(snaps: list[Path]) -> R.FlowOutcome:
    out = R.FlowOutcome(ok=True)
    out.snapshots = snaps
    out.requests = [{"method": "POST", "path": "/x", "status": 200}] * (len(snaps) - 1)
    return out


def _pipeline(plan: W.Plan) -> R.Pipeline:
    pipe = R.Pipeline.__new__(R.Pipeline)
    pipe.wall = {}
    return pipe


def test_a_move_the_static_map_names_confirms_the_machine(plan, tmp_path):
    snaps = _states(tmp_path, "ok", [[], [(1, "queued")], [(1, "active")]])
    flow = flow_of(plan, "POST /api/jobs", "POST /api/jobs/{}/activate", kind="pair")
    (m,) = [
        m
        for m in _pipeline(plan)._machines(plan, [(flow, _outcome(snaps))], [], None, None)
        if m["model"] == "Job"
    ]
    assert m["status"] == "confirmed"
    assert [(e["from"], e["to"], e["observed"], e["source"]) for e in m["edges"]] == [
        ("queued", "active", 1, "both")
    ]
    assert m["observed_initial"] == ["queued"]


def test_a_move_outside_the_static_map_refutes_it(plan, tmp_path):
    snaps = _states(tmp_path, "bad", [[], [(1, "queued")], [(1, "active")], [(1, "queued")]])
    flow = flow_of(plan, "POST /api/jobs", "POST /api/jobs/{}/activate", kind="pair")
    ms = _pipeline(plan)._machines(plan, [(flow, _outcome(snaps))], [], None, None)
    (m,) = [m for m in ms if m["model"] == "Job"]
    assert m["status"] == "refuted"
    assert ("active", "queued") in {(e["from"], e["to"]) for e in m["edges"]}
    assert {e["source"] for e in m["edges"] if (e["from"], e["to"]) == ("active", "queued")} == {
        "observed"
    }


def test_a_machine_nothing_moved_stays_static(plan, tmp_path):
    snaps = _states(tmp_path, "still", [[], [(1, "queued")]])
    flow = flow_of(plan, "POST /api/jobs", "POST /api/jobs/{}/activate", kind="pair")
    ms = _pipeline(plan)._machines(plan, [(flow, _outcome(snaps))], [], None, None)
    assert {m["model"]: m["status"] for m in ms}["Job"] == "static"


def test_static_machines_are_static_with_the_map_unobserved(plan):
    ms = _pipeline(plan)._machines(plan, None, [], None, None)
    assert {m["status"] for m in ms} == {"static"}
    assert all(e["observed"] == 0 and e["source"] == "static" for m in ms for e in m["edges"])


def test_a_service_sweep_sees_the_column_move(plan, tmp_path):
    import sqlite3
    import sys

    db = tmp_path / "svc.db"
    conn = sqlite3.connect(db)
    conn.execute("create table jobs (id integer primary key, owner_id int, name text, status text)")
    conn.execute("insert into jobs values (1, 1, 'j', 'queued')")
    conn.commit()
    conn.close()
    script = (
        "import sqlite3, sys, time\n"
        "time.sleep(0.6)\n"
        "c = sqlite3.connect(sys.argv[1])\n"
        "c.execute(\"update jobs set status = 'active'\")\n"
        "c.commit()\n"
        "time.sleep(30)\n"
    )
    from lattice.spec.services import ServiceCmd

    class Provider(R.SqliteProvider):
        def __init__(self) -> None:
            self.dialect = "sqlite"

        def snapshot(self, ref, tables, keys, dest):  # noqa: ANN001
            import shutil as sh

            sh.copyfile(ref, dest)

    harness = R.Harness(
        lambda ref: None,
        lambda ref: None,
        None,
        {"worker": lambda ref: ServiceCmd([sys.executable, "-c", script, str(ref)])},
        FakeRuntime(),
        "harness.py",
    )
    ex = R.Executor(
        plan.view,
        harness,
        Provider(),
        harness.runtime,
        USERS,
        tmp_path,
        W.Planner(plan.view, plan.edges, USERS),
        [],
    )
    out = R.FlowOutcome(ok=True)
    out.ref = db
    machine = next(m for m in plan.machines if m["model"] == "Job")
    got = ex._sweep("worker", machine, out)
    assert got["moved"] and got["rows"] == [{"key": 1, "to": "active"}]
    assert len(out.snapshots) >= 2 and out.requests[0] == {
        "method": "SERVICE",
        "path": "worker",
        "status": 0,
    }
    moves, created = R.scan_snapshots([[*out.snapshots]], "jobs", "id", "status")
    assert moves == {("queued", "active"): 1} and created == {}


def test_a_flow_that_raises_is_that_flows_failure_not_the_runs(plan):
    out = R.FlowOutcome(reason="run error: ConnectionError: boom")
    status, reason = _pipeline(plan)._status(out, None, None)
    assert status == "failed" and "boom" in reason


def test_an_app_that_cannot_start_stops_the_run(plan):
    from lattice.spec.process import ProcessStartError

    assert R._cannot_start(ProcessStartError("exited"))
    assert R._cannot_start(FileNotFoundError("bin/server"))
    assert not R._cannot_start(RuntimeError("one flow"))


def test_an_unverified_flow_shows_no_value_taken_from_an_answer(plan):
    rec = R.StepRun(
        W.StepSpec("POST /api/tickets", "ann"), plan.view.by_key["POST /api/tickets"], "ann"
    )
    rec.sources = {"subject": "bound", "project_id": "bound", "other": "default"}
    body = {"subject": "sent-before", "project_id": "from-an-answer", "other": "x"}
    masked = R._mask_body(rec, body, ["sent-before"])
    assert masked == {"subject": "sent-before", "project_id": None, "other": "x"}
