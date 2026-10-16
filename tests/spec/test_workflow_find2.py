"""workflow_find, round 2 (docs/results/2026-10-16-implementation-diff-round2-prereg.md, D2-D7):
each rule of the ranking on the unit fixture (tests/spec/fixtures/implementation/) or, where a rule
needs exact scores, on hand-built candidate documents. Reading writes nothing."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib.util
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.inventory import capture
from lattice.spec import implementation as impl
from lattice.spec.implementation import ImplContext, find_candidates, load_inventory
from lattice.spec.model import Step

FIX = Path(__file__).resolve().parent / "fixtures" / "implementation"
EXTRACT = json.loads((FIX / "extract.json").read_text())
WF = json.loads((FIX / "workflows.json").read_text())


def _fixture_spec():
    sp = importlib.util.spec_from_file_location("find2_fixture_spec", FIX / "fixture_spec.py")
    mod = importlib.util.module_from_spec(sp)
    sys.modules["find2_fixture_spec"] = mod
    sp.loader.exec_module(mod)
    return mod


FS = _fixture_spec()
PID = FS.PID


def make_ctx(tmp_path: Path, extract: Any = EXTRACT, spec: Any = None) -> ImplContext:
    app = tmp_path / "app"
    if not app.exists():
        shutil.copytree(FIX / "app", app)
    wf = tmp_path / "wf.json"
    wf.write_text(json.dumps(WF))
    capture(app, tmp_path / "inv", extract=extract, workflows=wf)
    return ImplContext.of(spec or FS.SPEC, FS.trace(), load_inventory(tmp_path / "inv", app))


@pytest.fixture
def ctx(tmp_path: Path) -> ImplContext:
    return make_ctx(tmp_path)


def extract_with(**edits: Any) -> dict[str, Any]:
    """The fixture extract with a handler renamed and a table added (the fixture's handlers are
    closures, which carry no name to match)."""
    ex = copy.deepcopy(EXTRACT)
    for r in ex["routes"]:
        if (r["method"], r["path"]) == ("DELETE", "/api/tickets/:id"):
            r["handler"]["func"] = "tickets.handler.eraseEntry"
        if (r["method"], r["path"]) == ("GET", "/api/stats"):
            r["tables"] = [*r["tables"], "Widget"]
    return ex


def top(out: dict[str, Any]) -> str | None:
    return out["candidates"][0]["target"] if out["candidates"] else None


def tool_def(tb: Any) -> dict[str, Any]:
    return next(d for d in tb.list_tools() if d["name"] == "workflow_find")


# ---- synthetic candidate documents -----------------------------------------------------------


def doc(
    fields: dict[str, set[str]],
    *,
    method: str | None = "GET",
    last: set[str] | None = None,
    tie: str = "",
    spec_only: bool = False,
    kind: str = "route",
    key: str = "x",
) -> dict[str, Any]:
    return {
        "kind": kind,
        "status": "spec_only" if spec_only else "implemented_unverified",
        "spec_only": spec_only,
        "fields": {k: frozenset(v) for k, v in fields.items()},
        "method": method if kind == "route" else None,
        "last": frozenset(fields.get("path", set()) if last is None else last)
        if kind == "route"
        else None,
        "tie": tie,
        "keys": [key],
        "evidence": [],
        "flows": [] if kind == "route" else None,
        "rules": [] if spec_only else None,
    }


def synth(monkeypatch: pytest.MonkeyPatch, docs: dict[str, dict[str, Any]]) -> None:
    monkeypatch.setattr(impl, "_find_docs", lambda _ctx: docs)


def filler_docs(n: int) -> dict[str, dict[str, Any]]:
    """n unrelated routes: they set N, and a term in none of them weighs ln(1 + N)."""
    return {
        f"impl:route:GET /f{i}": doc({"path": {f"pad{i}"}}, key=f"GET /f{i}", method="GET")
        for i in range(n)
    }


# ---- D2: the candidates -----------------------------------------------------------------------


def test_candidates_are_endpoints_and_machines_only(ctx: ImplContext) -> None:
    docs = impl._find_docs(ctx)
    assert {d["kind"] for d in docs.values()} == {"route", "machine"}
    statuses = {e["key"]: e["status"] for e in ctx.report["endpoints"]}
    for d in docs.values():
        if d["kind"] == "route":
            assert statuses[d["keys"][0]] in (
                "implemented",
                "implemented_not_required",
                "spec_only",
            )
    assert "impl:route:PUT /test/clock" not in docs  # test_only
    assert len([t for t in docs if t.startswith("impl:machine:")]) == len(ctx.report["machines"])
    # a flow or a rule is never a candidate, however well the question names it
    for q in ("publish projects archive tickets", "R4 export of tickets"):
        out = find_candidates(ctx, q, 10)
        assert {c["kind"] for c in out["candidates"]} <= {"route", "machine"}
    assert top(find_candidates(ctx, "set the clock in tests")) != "impl:route:PUT /test/clock"


def test_a_spec_only_endpoint_is_a_candidate_with_its_rules(ctx: ImplContext) -> None:
    out = find_candidates(ctx, "restore a project")
    c = out["candidates"][0]
    assert c["target"] == "impl:route:POST /api/projects/{}/restore"
    assert c["status"] == "spec_only" and out["hint"] == "spec_only"
    assert c["rules"] == ["R5"] and c["evidence"] == []
    implemented = find_candidates(ctx, "publish a project")["candidates"][0]
    assert "rules" not in implemented and implemented["evidence"]  # rules ride on spec-only only


# ---- D3: the fields ---------------------------------------------------------------------------


def test_each_field_of_an_implemented_route(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, extract_with())
    docs = impl._find_docs(ctx)
    d = docs["impl:route:DELETE /api/tickets/{}"]["fields"]
    assert d["path"] == {"ticket"} and {"V:DELETE", "entri"} <= d["handler"]
    assert "handler" not in d["path"] | d["handler"]  # `handler` and `func` are noise
    p = docs["impl:route:POST /api/projects"]["fields"]
    assert {"kind", "name"} <= p["field"] and "conflict" in p["code"] and "project" in p["table"]
    assert "widget" in docs["impl:route:GET /api/stats"]["fields"]["table"]
    j = docs["impl:route:POST /api/jobs"]["fields"]
    assert {"activat", "run"} <= j["flow"]
    assert not [w for d in docs.values() for w in d["fields"].get("flow", ()) if w.startswith("V:")]
    # a path parameter is not a field; `v1`, `api` and `{}` are not path terms
    t = docs["impl:route:GET /api/tickets/{}"]["fields"]
    assert t["field"] == frozenset() and "api" not in t["path"]


@pytest.mark.parametrize(
    ("query", "target", "field"),
    [
        ("tickets", "impl:route:GET /api/tickets", "path"),
        ("entry", "impl:route:DELETE /api/tickets/{}", "handler"),
        ("subject", "impl:route:POST /api/tickets", "field"),
        ("conflict", "impl:route:POST /api/projects", "code"),
        ("widget", "impl:route:GET /api/stats", "table"),
    ],
)
def test_a_term_is_credited_to_its_field(
    tmp_path: Path, query: str, target: str, field: str
) -> None:
    out = find_candidates(make_ctx(tmp_path, extract_with()), query, 10)
    got = next(c for c in out["candidates"] if c["target"] == target)
    assert set(got["fields"].values()) == {field}


def test_a_term_counts_once_at_its_highest_field_weight(tmp_path: Path) -> None:
    out = find_candidates(make_ctx(tmp_path, extract_with()), "project", 10)
    by = {c["target"]: c for c in out["candidates"]}
    assert by["impl:route:POST /api/projects"]["fields"] == {"project": "path"}  # path and table
    assert by["impl:route:POST /api/tickets"]["fields"] == {"project": "field"}  # field, table


def test_a_flow_field_never_matches_a_verb(ctx: ImplContext) -> None:
    out = find_candidates(ctx, "create", 10)
    assert all(f != "flow" for c in out["candidates"] for f in c["fields"].values())


def test_a_spec_only_endpoint_has_the_keys_of_its_steps(tmp_path: Path) -> None:
    steps = (
        Step("POST", f"/api/projects/{PID}/restore", "ann", json={"reason": "oops"}),
        Step("POST", f"/api/projects/{PID}/restore", "ann", params={"dryrun": "1"}),
    )
    spec = dataclasses.replace(
        FS.SPEC,
        scenarios=(
            *FS.SPEC.scenarios,
            dataclasses.replace(FS.SPEC.scenarios[0], name="r", steps=steps),
        ),
    )
    ctx = make_ctx(tmp_path, spec=spec)
    f = impl._find_docs(ctx)["impl:route:POST /api/projects/{}/restore"]["fields"]
    assert {"reason", "dryrun"} <= f["field"] and "handler" not in f
    assert top(find_candidates(ctx, "a dryrun")) == "impl:route:POST /api/projects/{}/restore"


def test_a_machine_has_a_name_and_states(ctx: ImplContext) -> None:
    f = impl._find_docs(ctx)["impl:machine:jobs.status"]["fields"]
    assert f["table"] == {"job", "status"} and f["state"] == {"activ", "queu"}
    out = find_candidates(ctx, "queued")
    assert top(out) == "impl:machine:jobs.status" and out["hint"] == "exists"
    assert out["candidates"][0]["fields"] == {"queu": "state"}


# ---- D4: the query terms ----------------------------------------------------------------------


def test_filler_words_are_not_query_terms(ctx: ImplContext) -> None:
    a = find_candidates(ctx, "show the endpoint for stats")
    b = find_candidates(ctx, "show stats")
    assert a["candidates"][0]["matched_terms"] == b["candidates"][0]["matched_terms"]
    assert a["candidates"][0]["score"] == b["candidates"][0]["score"]
    only = find_candidates(ctx, "the screen page endpoint of the api")
    assert only["hint"] == "no_match" and only["candidates"] == []


@pytest.mark.parametrize(
    ("question", "klass", "target"),
    [
        ("remove a ticket", "V:DELETE", "impl:route:DELETE /api/tickets/{}"),
        ("create a job", "V:POST", "impl:route:POST /api/jobs"),
        ("see the stats", "V:GET", "impl:route:GET /api/stats"),
        ("new ticket", "V:POST", "impl:route:POST /api/tickets"),
    ],
)
def test_verbs_become_their_method_class(
    ctx: ImplContext, question: str, klass: str, target: str
) -> None:
    out = find_candidates(ctx, question)
    assert top(out) == target and klass in out["candidates"][0]["matched_terms"]


def test_update_is_patch_or_put_and_rename_is_patch_only() -> None:
    from lattice.spec.ask import _stem

    lex = {w: c for w, c in impl._find_lexicon()[0].items()}

    def of(word: str) -> str:
        return lex[_stem(word)]

    assert of("update") == of("edit") == of("modify") == "V:PATCH/PUT"
    assert of("rename") == "V:PATCH" and of("replace") == "V:PUT"
    assert {of(w) for w in ("remove", "delete", "unregister", "erase")} == {"V:DELETE"}
    assert {of(w) for w in ("view", "show", "see", "read", "fetch", "browse", "list")} == {"V:GET"}
    assert {of(w) for w in ("add", "create", "new", "register", "submit", "post")} == {"V:POST"}


def test_a_verb_class_counts_once_at_the_method_weight(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, extract_with())
    docs = impl._find_docs(ctx)
    n = len(docs)
    df = sum(1 for d in docs.values() if "V:DELETE" in frozenset().union(*d["fields"].values()))
    one = find_candidates(ctx, "delete", 10)["candidates"][0]
    assert one["target"] == "impl:route:DELETE /api/tickets/{}"
    # carried by the handler, still 0.5; no query term names the last segment: x 0.8
    assert one["score"] == round(0.8 * 0.5 * math.log(1 + n / df), 4)
    assert one["fields"] == {"V:DELETE": "handler"}
    many = find_candidates(ctx, "delete remove erase unregister", 10)["candidates"][0]
    assert many["score"] == one["score"] and many["matched_terms"] == ["V:DELETE"]


def test_a_verb_matches_a_method_alone(ctx: ImplContext) -> None:
    out = find_candidates(ctx, "create", 10)
    got = {c["target"]: c for c in out["candidates"]}
    assert got["impl:route:POST /api/login"]["fields"] == {"V:POST": "method"}
    assert "impl:route:GET /api/stats" not in got
    assert all(c["target"].split()[0] == "impl:route:POST" for c in out["candidates"])


def test_two_adjacent_words_join_into_a_path_term(ctx: ImplContext) -> None:
    out = find_candidates(ctx, "log in")
    assert top(out) == "impl:route:POST /api/login" and out["candidates"][0]["matched_terms"] == [
        "login"
    ]
    assert out["hint"] == "exists"
    # no path or handler term "logout": nothing joins, and "log"/"out" match nothing
    assert find_candidates(ctx, "log out")["hint"] == "no_match"


# ---- D5: score and order -----------------------------------------------------------------------


def test_the_resource_penalty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_ctx(tmp_path)
    q = "archive project"
    pen = {c["target"]: c["score"] for c in find_candidates(ctx, q, 10)["candidates"]}
    monkeypatch.setattr(impl, "FIND_PENALTY", 1.0)
    raw = {c["target"]: c["score"] for c in find_candidates(ctx, q, 10)["candidates"]}
    # the last static segment names a query term: no penalty; otherwise S x 0.8
    assert (
        pen["impl:route:POST /api/projects/{}/archive"]
        == raw["impl:route:POST /api/projects/{}/archive"]
    )
    assert pen["impl:route:POST /api/projects"] == raw["impl:route:POST /api/projects"]
    other = "impl:route:POST /api/projects/{}/publish"
    assert abs(pen[other] - 0.8 * raw[other]) < 1e-3 and pen[other] < raw[other]


def test_an_empty_last_segment_counts_as_unmatched(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = {
        "impl:route:GET /v1/me": doc({"path": {"v"}}, key="GET /v1/me", last=set()),
        "impl:route:GET /v1/x": doc({"path": {"v"}}, key="GET /v1/x", last={"acct"}),
    }
    docs["impl:route:GET /v1/me"]["fields"] = {"path": frozenset({"acct"})}
    docs["impl:route:GET /v1/x"]["fields"] = {"path": frozenset({"acct"})}
    synth(monkeypatch, {**docs, **filler_docs(3)})
    out = find_candidates(None, "acct", 10)
    s = {c["target"]: c["score"] for c in out["candidates"]}
    assert s["impl:route:GET /v1/me"] == round(0.8 * s["impl:route:GET /v1/x"], 4)


def test_a_machine_is_exempt_from_the_penalty(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = {
        "impl:machine:a.b": doc({"table": {"acct"}}, kind="machine", key="impl:machine:a.b"),
        "impl:route:GET /r": doc({"path": {"acct"}}, key="GET /r", last={"other"}),
        **filler_docs(2),
    }
    synth(monkeypatch, docs)
    s = {c["target"]: c["score"] for c in find_candidates(None, "acct", 10)["candidates"]}
    # the machine's table weighs 0.4 and is not scaled; the route's path weighs 1.0, then x 0.8
    idf = math.log(1 + 4 / 2)
    assert s["impl:machine:a.b"] == round(0.4 * idf, 4)
    assert s["impl:route:GET /r"] == round(1.0 * idf * 0.8, 4)


def test_an_exact_mention_ranks_first_then_score(ctx: ImplContext) -> None:
    out = find_candidates(ctx, "Is there anything for tickets, or GET /api/stats?")
    assert out["candidates"][0]["target"] == "impl:route:GET /api/stats"
    assert out["candidates"][0]["exact"] is True and out["hint"] == "exists"
    assert [c["exact"] for c in out["candidates"][1:]] == [False] * (len(out["candidates"]) - 1)
    scores = [c["score"] for c in out["candidates"][1:]]
    assert scores == sorted(scores, reverse=True)


def test_an_exact_spec_only_mention_is_spec_only(ctx: ImplContext) -> None:
    out = find_candidates(ctx, "Does POST /api/projects/{id}/restore exist?")
    assert out["hint"] == "spec_only" and out["candidates"][0]["exact"] is True


def test_equal_scores_are_ordered_by_the_rule_texts_then_the_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = {
        "impl:route:GET /a": doc({"path": {"foo"}}, key="GET /a", tie="beta gamma"),
        "impl:route:GET /z": doc({"path": {"foo"}}, key="GET /z", tie="foo story"),
        "impl:route:GET /m": doc({"path": {"foo"}}, key="GET /m", tie="beta gamma"),
        **filler_docs(3),
    }
    synth(monkeypatch, docs)
    out = find_candidates(None, "foo", 10)
    assert [c["target"] for c in out["candidates"]] == [
        "impl:route:GET /z",  # its rule text names the term
        "impl:route:GET /a",  # then the target, ascending
        "impl:route:GET /m",
    ]
    assert len({c["score"] for c in out["candidates"]}) == 1  # T orders equal S only
    assert out["hint"] == "several"  # and never changes the hint


def test_the_rule_text_never_outweighs_the_score(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = {
        "impl:route:GET /lo": doc({"path": {"foo"}}, key="GET /lo", tie="foo " * 40),
        "impl:route:GET /hi": doc({"path": {"foo", "bar"}}, key="GET /hi", tie=""),
        **filler_docs(3),
    }
    synth(monkeypatch, docs)
    out = find_candidates(None, "foo bar", 10)
    assert top(out) == "impl:route:GET /hi"


# ---- D6: hints ----------------------------------------------------------------------------------


def test_no_match_when_the_question_has_no_term(ctx: ImplContext) -> None:
    for q in ("", "?", "the", "can a user use the app"):
        out = find_candidates(ctx, q)
        assert out["hint"] == "no_match" and out["candidates"] == [] and out["coverage"] == 0.0


def test_no_match_when_the_top_score_is_zero(ctx: ImplContext) -> None:
    out = find_candidates(ctx, "quantum entanglement of zebras")
    assert out["hint"] == "no_match" and out["candidates"] == []


def test_no_match_without_an_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = {
        "impl:route:POST /a": doc(
            {"path": {"alpha"}, "field": {"zeta"}}, key="POST /a", method="POST"
        ),
        **filler_docs(3),
    }
    synth(monkeypatch, docs)
    # coverage is 1.0 and the score is positive, but the term is found in a field only
    out = find_candidates(None, "zeta", 10)
    assert out["candidates"][0]["fields"] == {"zeta": "field"} and out["coverage"] == 1.0
    assert out["hint"] == "no_match"
    assert find_candidates(None, "alpha", 10)["hint"] == "exists"  # a path term anchors
    assert find_candidates(None, "create", 10)["hint"] == "exists"  # a method does too


def test_a_machine_name_anchors(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = {
        "impl:machine:jobs.status": doc(
            {"table": {"job"}, "state": {"queu"}}, kind="machine", key="impl:machine:jobs.status"
        ),
        **filler_docs(3),
    }
    synth(monkeypatch, docs)
    assert find_candidates(None, "job", 10)["hint"] == "exists"  # the name
    assert find_candidates(None, "queu", 10)["hint"] == "exists"  # a state


def test_no_match_under_the_coverage_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = {"impl:route:GET /a": doc({"path": {"foo"}}, key="GET /a"), **filler_docs(3)}
    synth(monkeypatch, docs)
    # N = 4: idf(foo) = ln 5; a term in no candidate weighs ln(1 + N) = ln 5
    half = find_candidates(None, "foo bar", 10)
    assert half["coverage"] == 0.5 and half["hint"] == "exists"
    third = find_candidates(None, "foo bar baz", 10)
    assert third["coverage"] == pytest.approx(1 / 3, abs=1e-4) and third["hint"] == "no_match"
    assert third["candidates"]  # the candidates stay listed, the hint says why not to trust them


def test_a_linked_rule_text_gives_half_credit(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = {
        "impl:route:GET /a": doc({"path": {"foo"}}, key="GET /a", tie="the bar story"),
        **filler_docs(3),
    }
    synth(monkeypatch, docs)
    # "bar" is in the top's rule text only: 1 + 0.5 over 1 + 1 (equal idf here) = 0.75
    out = find_candidates(None, "foo bar", 10)
    assert 0.55 < out["coverage"] < 0.95 and out["hint"] == "exists"
    assert out["candidates"][0]["matched_terms"] == ["foo"]  # the text is not a field


def test_several_at_ninety_percent_of_the_top(monkeypatch: pytest.MonkeyPatch) -> None:
    def docs(second: float) -> dict[str, dict[str, Any]]:
        # one term weighs ln(1 + N/df); the field weight scales the second candidate's score
        a = doc({"path": {"foo"}}, key="GET /a")
        b = doc({"field": {"foo"}, "path": {"qux"}}, key="GET /b", last={"foo"})
        b["fields"] = {"path": frozenset({"foo"})}
        return {"impl:route:GET /a": a, "impl:route:GET /b": b, **filler_docs(4)}

    synth(monkeypatch, docs(1.0))
    assert find_candidates(None, "foo", 10)["hint"] == "several"  # equal scores
    d = docs(0.0)
    d["impl:route:GET /b"]["fields"] = {"field": frozenset({"foo"})}
    d["impl:route:GET /b"]["last"] = frozenset({"foo"})
    synth(monkeypatch, d)
    out = find_candidates(None, "foo", 10)
    s = [c["score"] for c in out["candidates"]]
    assert s[1] / s[0] == pytest.approx(0.6, abs=1e-3)  # 0.6 of the top: not several
    assert out["hint"] == "exists"
    d["impl:route:GET /b"]["fields"] = {"handler": frozenset({"foo"})}
    d["impl:route:GET /a"]["fields"] = {"path": frozenset({"foo"})}
    synth(monkeypatch, d)
    assert find_candidates(None, "foo", 10)["hint"] == "several"  # handler weighs 1.0 as path does


def test_the_boundary_of_several_is_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    a = doc({"path": {"foo", "bar"}}, key="GET /a")
    b = doc({"path": {"foo"}}, key="GET /b")
    synth(monkeypatch, {"impl:route:GET /a": a, "impl:route:GET /b": b, **filler_docs(2)})
    out = find_candidates(None, "foo bar", 10)
    s = [c["score"] for c in out["candidates"]]
    assert s[1] < 0.9 * s[0] and out["hint"] == "exists"
    monkeypatch.setattr(impl, "FIND_TIE", 0.6)  # 0.4 or more of the top is now close enough
    assert find_candidates(None, "foo bar", 10)["hint"] == "several"


def test_exists_and_spec_only_follow_the_top(ctx: ImplContext) -> None:
    assert find_candidates(ctx, "publish a project")["hint"] == "exists"
    assert find_candidates(ctx, "restore a project")["hint"] == "spec_only"
    assert find_candidates(ctx, "export tickets")["hint"] == "spec_only"
    assert find_candidates(ctx, "ticket")["hint"] == "several"


# ---- D7: output, compatibility ---------------------------------------------------------------


def test_the_output_keeps_the_round_one_keys_and_adds_fields_flows_rules_coverage(
    ctx: ImplContext,
) -> None:
    out = find_candidates(ctx, "publish a project")
    assert set(out) == {"question", "hint", "candidates", "coverage", "note"}
    assert out["question"] == "publish a project" and 0.0 <= out["coverage"] <= 1.0
    c = out["candidates"][0]
    assert {"target", "kind", "status", "score", "matched_terms", "exact", "evidence"} <= set(c)
    assert set(c) == {
        *("target", "kind", "status", "score", "matched_terms", "exact", "evidence"),
        *("fields", "flows"),
    }
    assert sorted(c["fields"]) == c["matched_terms"] and len(c["flows"]) <= 5
    assert set(c["flows"][0]) == {"id", "name", "status"}
    assert "lexical match, not understanding" in out["note"]
    machine = find_candidates(ctx, "queued")["candidates"][0]
    assert "flows" not in machine and "rules" not in machine


def test_the_limit_bounds_the_candidates(ctx: ImplContext) -> None:
    assert len(find_candidates(ctx, "ticket", 2)["candidates"]) == 2
    assert len(find_candidates(ctx, "ticket", 10)["candidates"]) > 2


def test_find_is_deterministic_and_writes_nothing(ctx: ImplContext, tmp_path: Path) -> None:
    def tree() -> dict[str, str]:
        return {
            p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(tmp_path.rglob("*"))
            if p.is_file()
        }

    before = tree()
    first = [find_candidates(ctx, q, 5) for q in ("ticket", "log in", "remove a ticket", "zzz")]
    for _ in range(5):
        assert [
            find_candidates(ctx, q, 5) for q in ("ticket", "log in", "remove a ticket", "zzz")
        ] == first
    assert tree() == before


def test_the_tool_is_unchanged_and_calls_find(tmp_path: Path) -> None:
    from lattice.mcp.tools import Toolbox
    from tests.mcp.test_implementation_tools import FakeBackend, make_project

    proj, app, inv = make_project(tmp_path)
    tb = Toolbox(FakeBackend(proj))
    # sha256 of the workflow_find definition (name, description, schema, annotations) at bb3ff0c
    digest = hashlib.sha256(json.dumps(tool_def(tb), sort_keys=True).encode()).hexdigest()
    assert digest == "5ab7a143f6c122623e4855aed4d0449cd035ffaa9e3a57fa774c3b6f04b0c8e2"
    out = tb.call("workflow_find", {"question": "publish a project", "limit": 3})
    assert out["hint"] == "exists" and len(out["candidates"]) <= 3 and "coverage" in out
