"""Round 2, D2-D3: rpc flow synthesis (F1 id threading, F2 natural keys by a response field or a
lookup step, F3 least-privilege example values, F4 ownership chains, F6 the binding record and
``lattice.rpc-flows/2``) on a synthetic extract that needs no Go, and verification (D3) on the
notes app."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from lattice.importer import workflows as wf
from tests.grpc import oracle
from tests.grpc.conftest import needs_go


def fld(name: str, typ: str = "string", label: str = "single", **kw: Any) -> dict[str, Any]:
    return {"name": name, "type": typ, "label": label, **kw}


def msg(name: str, *fields: dict[str, Any]) -> dict[str, Any]:
    return {"name": f"d.v1.{name}", "fields": list(fields)}


def rpc(
    name: str,
    inp: str,
    out: str,
    *,
    auth: str = "Member",
    tables: tuple[str, ...] = (),
    creates: tuple[str, ...] = (),
    updates: tuple[str, ...] = (),
    deletes: tuple[str, ...] = (),
    compare: bool = False,
) -> dict[str, Any]:
    writes = (
        [{"table": t, "op": "create"} for t in creates]
        + [{"table": t, "op": "update"} for t in updates]
        + [{"table": t, "op": "delete"} for t in deletes]
    )
    return {
        "name": name,
        "input": f"d.v1.{inp}",
        "output": f"d.v1.{out}",
        "state": "implemented",
        "auth": auth,
        "tables": list(tables),
        "writes": writes,
        "owner_uses": [{"how": "compare"}] if compare else [],
    }


def extract(*, lookup: bool = True) -> dict[str, Any]:
    messages = [
        msg("Queue", fld("id", "int64"), fld("slug"), fld("title")),
        msg("ListQueuesRequest", fld("include_closed", "bool")),
        msg("ListQueuesReply", fld("queues", "d.v1.Queue", "repeated")),
        msg("GetQueueRequest", fld("slug")),
        msg("Ticket", fld("id", "int64"), fld("queue_slug"), fld("state", "d.v1.State")),
        msg(
            "OpenRequest",
            fld("queue_slug"),
            fld("subject"),
            fld("priority", "d.v1.Priority"),
            fld("labels", "string", "repeated"),
            fld("urgent", "bool"),
            fld("page_token"),
            fld("display_name"),
        ),
        msg("Ref", fld("id", "int64")),
        msg("Remark", fld("id", "int64"), fld("ticket_id", "int64")),
        msg("RemarkRequest", fld("ticket_id", "int64"), fld("text"), fld("internal", "bool")),
        msg("Empty"),
        msg("AuditRequest", fld("user_id", "int64")),
        msg("ListRequest", fld("state", "d.v1.State", oneof="f"), fld("queue_slug", oneof="f")),
    ]
    queues = [
        rpc(
            "ListQueues", "ListQueuesRequest", "ListQueuesReply", auth="Public", tables=("queues",)
        ),
        rpc("GetQueue", "GetQueueRequest", "Queue", auth="Public", tables=("queues",)),
    ]
    tickets = [
        rpc("Open", "OpenRequest", "Ticket", tables=("queues", "tickets"), creates=("tickets",)),
        rpc("Get", "Ref", "Ticket", tables=("tickets",), compare=True),
        rpc("Close", "Ref", "Ticket", tables=("tickets",), updates=("tickets",)),
        rpc("Delete", "Ref", "Empty", tables=("tickets",), deletes=("tickets",), compare=True),
        rpc(
            "Remark",
            "RemarkRequest",
            "Remark",
            tables=("tickets", "ticket_notes"),
            creates=("ticket_notes",),
            compare=True,
        ),
        rpc("Audit", "AuditRequest", "Empty", tables=("tickets",)),
        rpc("List", "ListRequest", "Empty", tables=("tickets", "queues")),
    ]
    return {
        "grpc": {
            "messages": messages,
            "enums": [
                {
                    "name": "d.v1.Priority",
                    "values": [
                        {"name": "P_UNSPECIFIED", "number": 0},
                        {"name": "P_LOW", "number": 1},
                        {"name": "P_HIGH", "number": 2},
                    ],
                },  # noqa: E501
                {"name": "d.v1.State", "values": [{"name": "S_UNSPECIFIED", "number": 0}]},
            ],
            "services": [
                {"name": "d.v1.Queues", "rpcs": queues if lookup else queues[1:]},
                {"name": "d.v1.Tickets", "rpcs": tickets},
            ],
        }
    }


@pytest.fixture
def views() -> list[wf.RpcView]:
    return wf.rpc_views(extract())


def by(views: list[wf.RpcView], name: str) -> wf.RpcView:
    return next(v for v in views if v.method == name)


# ---- F1: id threading ------------------------------------------------------------------------


def test_views_read_the_response_shape(views: list[wf.RpcView]) -> None:
    v = by(views, "ListQueues")
    assert v.out_lists == (("queues", ("id", "slug", "title")),) and v.out_paths == ()
    assert ("slug",) in by(views, "GetQueue").out_paths
    assert by(views, "Open").makes_id and by(views, "Remark").makes_id


def test_f1_edges_thread_id_and_singular_table_id(views: list[wf.RpcView]) -> None:
    edges = {(t, p.rsplit("/", 1)[1], c.rsplit("/", 1)[1], f) for t, p, c, f in wf.rpc_edges(views)}
    assert ("tickets", "Open", "Get", "id") in edges
    assert ("tickets", "Open", "Remark", "ticket_id") in edges
    assert ("ticket_notes", "Remark", "Remark", "ticket_id") not in edges  # not its own producer


def test_f1_never_binds_an_id_to_another_tables_field(views: list[wf.RpcView]) -> None:
    """``Audit`` reads tickets but takes ``user_id``: a ticket's id does not feed it."""
    assert all(c.rsplit("/", 1)[1] != "Audit" for _, _, c, _ in wf.rpc_edges(views))
    plans = wf.static_rpc_plans(views)
    assert all(s.key.rsplit("/", 1)[1] != "Audit" for p in plans for s in p.steps)


# ---- F2: natural keys ------------------------------------------------------------------------


def test_f2_natural_fields(views: list[wf.RpcView]) -> None:
    assert wf.natural_fields(by(views, "Open")) == [("queue_slug", "queues", "slug")]
    assert wf.natural_fields(by(views, "Get")) == []  # an id
    assert wf.natural_fields(by(views, "GetQueue")) == []  # no <s>_ prefix
    assert wf.natural_fields(by(views, "List")) == []  # a oneof member is not a key
    assert wf.natural_fields(by(views, "Remark")) == []  # ticket_id is an id, text has no prefix


def plan_of(views: list[wf.RpcView], kind: str, method: str = "Get") -> wf.RpcPlan:
    return next(
        p
        for p in wf.static_rpc_plans(views)
        if p.kind == kind and p.steps[-1].key.endswith("/" + method)
    )


def test_f2b_a_lookup_step_is_inserted_before_the_step_that_needs_it(
    views: list[wf.RpcView],
) -> None:
    p = plan_of(views, "pair")
    assert [s.key.rsplit("/", 1)[1] for s in p.steps] == ["ListQueues", "Open", "Get"]
    look, opener, getter = p.steps
    assert look.role == "lookup" and look.caller is None  # a public rpc: no session
    assert opener.nat == {"queue_slug": (1, "slug")}
    assert getter.binds == {"id": 2}  # renumbered past the inserted step


def test_f2a_an_earlier_step_that_answers_the_column_is_the_source(views: list[wf.RpcView]) -> None:
    steps = [
        wf.RpcPlanStep("d.v1.Queues/GetQueue", None, {}),
        wf.RpcPlanStep("d.v1.Tickets/Open", "A", {}),
        wf.RpcPlanStep("d.v1.Tickets/Get", "A", {"id": 2}),
    ]
    out = wf.expand_natural(wf.RpcPlan("pair", steps, ""), views)
    assert [s.role for s in out.steps] == ["step", "step", "step"]  # no lookup inserted
    assert out.steps[1].nat == {"queue_slug": (1, "slug")} and out.steps[2].binds == {"id": 2}


def test_f2c_no_source_makes_the_plan_unbound() -> None:
    views = wf.rpc_views(extract(lookup=False))
    plans = [p for p in wf.static_rpc_plans(views) if p.unbound]
    assert plans and all(p.unbound == "no_source:queue_slug" for p in plans)
    assert all(p.id.startswith("rf-") for p in plans)
    # every plan that opens a ticket needs the key; none was run with a made-up slug
    assert all(p.steps[0].key.endswith("/Open") for p in plans if p.kind == "pair")


def test_f2_expansion_is_idempotent(views: list[wf.RpcView]) -> None:
    p = plan_of(views, "pair")
    again = wf.expand_natural(p, views)
    assert [(s.key, s.binds, s.nat, s.role) for s in again.steps] == [
        (s.key, s.binds, s.nat, s.role) for s in p.steps
    ]


def test_f2_the_lookup_is_the_first_candidate_in_sorted_key_order() -> None:
    ex = extract()
    twin = json.loads(json.dumps(ex["grpc"]["services"][0]["rpcs"][0]))
    twin["name"] = "AllQueues"
    ex["grpc"]["services"].insert(0, {"name": "a.v1.Early", "rpcs": [twin]})
    look = wf._lookup_for(wf.rpc_views(ex), "queues", "slug")
    assert look is not None and look.key == "a.v1.Early/AllQueues"


def test_f2_the_value_is_the_smallest_column_value_whatever_the_order() -> None:
    body = {"queues": [{"slug": "legacy"}, {"slug": "billing"}, {"slug": "zeta"}]}
    assert wf.natural_value(body, "slug", lists_first=True) == "billing"
    assert wf.natural_value({"slug": "s", "x": {"slug": "n"}}, "slug") == "s"
    assert wf.natural_value({"x": {"slug": "n"}}, "slug") == "n"
    assert wf.natural_value({"queues": []}, "slug", lists_first=True) is None
    assert wf.natural_value(None, "slug") is None


def test_f2_a_lookup_that_is_not_ok_is_no_source(views: list[wf.RpcView]) -> None:
    steps = [
        wf.RpcPlanStep("d.v1.Queues/GetQueue", None, {}, "refuse"),
        wf.RpcPlanStep("d.v1.Tickets/Open", "A", {}),
    ]
    out = wf.expand_natural(wf.RpcPlan("pair", steps, ""), views)
    # the refused step gives nothing: a lookup is inserted before the step that needs the key
    assert [s.role for s in out.steps] == ["step", "lookup", "step"]


# ---- F3: least-privilege example values --------------------------------------------------------


def test_f3_example_values() -> None:
    enums = wf.rpc_enums(extract())
    assert wf.example_value(fld("urgent", "bool")) is False
    assert wf.example_value(fld("p", "d.v1.Priority"), enums) == "P_LOW"  # not number 0
    assert wf.example_value(fld("p", "d.v1.State"), enums) is None  # only its zero value
    assert wf.example_value(fld("p", "d.v1.Priority")) is None  # no enum table: left out
    assert wf.example_value(fld("subject")) == "wf-subject"
    assert wf.example_value(fld("n", "int32", bounds={"ge": 3})) == 3
    assert wf.example_value(fld("labels", "string", "repeated")) is None
    assert wf.example_value(fld("m", "map", "map")) is None
    assert wf.example_value(fld("x", "string", bounds={"choices": ["a", "b"]})) == "a"


def test_f3_a_writing_rpcs_message_has_no_privileged_value(views: list[wf.RpcView]) -> None:
    enums = wf.rpc_enums(extract())
    m = wf.rpc_message(by(views, "Open"), {"queue_slug": "billing"}, enums)
    assert m == {
        "queue_slug": "billing",
        "subject": "wf-subject",
        "priority": "P_LOW",
        "urgent": False,
        "page_token": "wf-page_token",
        "display_name": "wf-display_name",
    }
    r = wf.rpc_message(by(views, "Remark"), {"ticket_id": 1}, enums)
    assert r["internal"] is False and r["ticket_id"] == 1


# ---- F4: ownership chains ------------------------------------------------------------------------


def test_f4_another_user_attaching_a_child_row_is_refused(views: list[wf.RpcView]) -> None:
    chains = [p for p in wf.static_rpc_plans(views) if p.kind == "owner_chain"]
    assert [p.steps[-1].key.rsplit("/", 1)[1] for p in chains] == ["Remark"]
    open_, remark = (s for s in chains[0].steps if s.role == "step")
    assert (open_.caller, remark.caller, remark.intent) == ("A", "B", "refuse")
    assert remark.binds and list(remark.binds) == ["ticket_id"]


def test_f4_not_for_a_bare_id_or_a_consumer_that_creates_nothing(views: list[wf.RpcView]) -> None:
    kinds = {(p.kind, p.steps[-1].key.rsplit("/", 1)[1]) for p in wf.static_rpc_plans(views)}
    assert ("owner_chain", "Get") not in kinds and ("owner_chain", "Close") not in kinds
    assert ("owner_guard", "Get") in kinds  # the round-1 guard stays


def test_plan_ids_are_unique_stable_and_see_the_binding(views: list[wf.RpcView]) -> None:
    ids = [p.id for p in wf.static_rpc_plans(views)]
    assert len(ids) == len(set(ids)) and ids == [p.id for p in wf.static_rpc_plans(views)]
    p = plan_of(views, "pair")
    q = wf.RpcPlan(p.kind, [replace(s, nat={}) for s in p.steps], p.why)
    assert p.id != q.id


# ---- D3: verification and the schema ------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "intent", "code", "ok"),
    [
        ("pair", "ok", "OK", True),
        ("pair", "ok", "NOT_FOUND", False),
        ("pair", "ok", "INTERNAL", False),
        ("pair", "refuse", "UNAUTHENTICATED", True),
        ("pair", "refuse", "INVALID_ARGUMENT", True),
        ("pair", "refuse", "OK", False),
        ("pair", "refuse", "INTERNAL", False),
        ("pair", "refuse", "UNKNOWN", False),
        ("after_delete", "refuse", "NOT_FOUND", True),
        ("owner_guard", "refuse", "NOT_FOUND", True),
        ("owner_guard", "refuse", "PERMISSION_DENIED", True),
        ("owner_guard", "refuse", "UNAUTHENTICATED", False),
        ("owner_guard", "refuse", "INVALID_ARGUMENT", False),
        ("owner_chain", "refuse", "NOT_FOUND", True),
        ("owner_chain", "refuse", "PERMISSION_DENIED", True),
        ("owner_chain", "refuse", "UNAUTHENTICATED", False),
        ("owner_chain", "refuse", "FAILED_PRECONDITION", False),
        ("owner_chain", "refuse", "DATA_LOSS", False),
        ("owner_guard", "ok", "OK", True),
    ],
)
def test_d3_a_step_is_ok_only_as_its_intent_says(
    kind: str, intent: str, code: str, ok: bool
) -> None:
    assert wf.rpc_step_ok(kind, intent, code) is ok


def test_f6_the_schema_is_two_and_the_summary_counts_unbound() -> None:
    assert wf.RPC_FLOWS_SCHEMA == "lattice.rpc-flows/2"
    doc = json.loads(
        wf.rpc_flows_json([{"status": "verified"}, {"status": "unbound"}, {"status": "failed"}])
    )
    assert doc["schema"] == "lattice.rpc-flows/2"
    assert doc["summary"]["unbound"] == 1 and doc["summary"]["verified"] == 1


# ---- the notes app (Go): D3 end to end ----------------------------------------------------


@pytest.fixture(scope="module")
def notes_flows(n_extract: Any) -> list[dict[str, Any]]:
    from lattice.spec.grpc import run_rpc_flows
    from tests.grpc.fixtures.notes import harness
    from tests.grpc.fixtures.notes import spec as dev

    return run_rpc_flows(
        n_extract, dev.SPEC, harness.make_app, harness.build_db, runtime=harness.RUNTIME
    )


@needs_go
def test_notes_flows_verify_and_every_step_records_its_binding(
    notes_flows: list[dict[str, Any]],
) -> None:
    verified = [f for f in notes_flows if f["status"] == "verified"]
    assert len(verified) >= 3 and all(
        f["status"] in ("verified", "unverified", "failed", "unbound") for f in notes_flows
    )
    assert not [
        f for f in notes_flows if f["status"] == "unbound"
    ]  # the notes app has no natural key
    for f in verified:
        for n, s in enumerate(f["steps"], 1):
            assert s["role"] == "step" and isinstance(s["bound"], dict)
            for field, b in s["bound"].items():
                assert 1 <= b["step"] < n and b["from_field"].endswith("id")
                assert field in s["message"]
    pair = next(f for f in verified if f["kind"] == "pair")
    assert pair["steps"][1]["bound"] and pair["steps"][0]["bound"] == {}


@needs_go
def test_d3_every_verified_flow_replays_as_layer_d(notes_flows: list[dict[str, Any]]) -> None:
    from lattice.spec import check
    from lattice.spec.grpc import _scenario_of
    from tests.grpc.fixtures.notes import harness
    from tests.grpc.fixtures.notes import spec as dev

    verified = [f for f in notes_flows if f["status"] == "verified"]
    spec = replace(dev.SPEC, scenarios=tuple(_scenario_of(f["id"], f["steps"]) for f in verified))
    assert (
        check(
            spec, harness.make_app, harness.build_db, layers="D", runtime=harness.RUNTIME
        ).findings
        == []
    )


@needs_go
def test_d3_a_flow_the_replay_disagrees_with_is_not_verified(
    n_extract: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    check_mod = importlib.import_module("lattice.spec.check")
    from lattice.spec.grpc import run_rpc_flows
    from tests.grpc.fixtures.notes import harness
    from tests.grpc.fixtures.notes import spec as dev

    real = check_mod.check

    def disagree(spec: Any, *a: Any, **kw: Any) -> Any:
        report = real(spec, *a, **kw)
        report.findings.append(
            type("F", (), {"details": {"problems": ["forced"]}, "rule": "scenario: x"})()
        )
        return report

    monkeypatch.setattr(check_mod, "check", disagree)
    flows = run_rpc_flows(
        n_extract, dev.SPEC, harness.make_app, harness.build_db, runtime=harness.RUNTIME
    )
    assert flows and not [f for f in flows if f["status"] == "verified"]
    assert {f["status"] for f in flows} <= {"unverified", "failed"}
    assert any("the replay disagreed" in str(f["reason"]) for f in flows)


@needs_go
def test_no_verified_guard_flow_rests_on_a_session_refusal(
    notes_flows: list[dict[str, Any]],
) -> None:
    for f in notes_flows:
        if f["kind"] in wf.GUARD_KINDS and f["status"] == "verified":
            assert f["steps"][-1]["code"] in wf.GUARD_REFUSALS


def test_the_notes_extract_has_no_natural_key(n_extract: Any) -> None:
    assert all(not wf.natural_fields(v) for v in wf.rpc_views(n_extract))
    assert oracle.NOTES.is_dir()
