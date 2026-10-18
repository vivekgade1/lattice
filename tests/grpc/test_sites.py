"""P0-SITES: every place that tells a scenario's ``Step`` from its ``Await`` (51 ``isinstance``
sites in 19 modules at 6a318e5; the pre-registration counted 49 in 15) is fed a scenario with an
``Rpc`` and shown to *handle* it (an arm of its own) or to *skip* it, with no exception and no
read of a ``Step`` attribute. A site that can do neither is a finding; the three in the frozen
``ask.py`` and ``impact_tier2.py`` got a one-line guard (Amendment 1, Q8).

``SITE_IDS`` is the list of sites at 6a318e5 (``dogfood/grpc_probes.py`` checks it against the
baseline tree); each id runs the check named in ``CHECKS``."""

from __future__ import annotations

import contextlib
from dataclasses import replace
from typing import Any

import pytest

from lattice.spec import Await, Rpc, Scenario, Service, Step
from lattice.spec import lint as lint_mod
from lattice.spec.rules import Rule, Trace
from tests.grpc.fixtures.notes import spec as dev

NOTES = "notes.v1.Notes"
RPC = Rpc(NOTES, "GetNote", "ann", {"id": 1}, expect_code="NOT_FOUND")
RPC_OK = Rpc(NOTES, "CreateNote", "ann", {"title": "t"})
GET = Step("GET", "/v1/notes/1", "ann", expect_status=200, expect_body={"id": 1})
POST = Step("POST", "/v1/notes", "ann", json={"title": "t"}, expect_status=201)
BAD = Step("POST", "/v1/notes", "ann", json={"title": ""}, expect_status=422)
WAIT = Await("worker", expect_db={"Note": {"count": 1}})
MIXED_STEPS = (POST, RPC_OK, GET, RPC, WAIT, BAD)
MIXED = replace(
    dev.SPEC,
    scenarios=(Scenario("mixed", steps=MIXED_STEPS), Scenario("plain", steps=(POST, RPC_OK))),
    services=(Service("worker"),),
)

SITE_IDS = [
    "contracts/export.py:186",
    "contracts/live.py:425",
    "contracts/live.py:525",
    "security/plan.py:93",
    "security/plan.py:481",
    "spec/ask.py:318",
    "spec/ask.py:906",
    "spec/ask.py:1001",
    "spec/ask.py:1588",
    "spec/ask.py:1785",
    "spec/ask.py:1815",
    "spec/ask.py:1836",
    "spec/ask.py:1862",
    "spec/authoring.py:264",
    "spec/check.py:1377",
    "spec/check.py:1394",
    "spec/check.py:1441",
    "spec/check.py:1659",
    "spec/check.py:2015",
    "spec/fidelity.py:269",
    "spec/implementation.py:629",
    "spec/implementation.py:1720",
    "spec/impact_tier2.py:287",
    "spec/impact_tier2.py:366",
    "spec/impact_tier2.py:510",
    "spec/impact_tier2.py:761",
    "spec/impact_tier2.py:775",
    "spec/impact_tier3.py:413",
    "spec/impact_tier3.py:422",
    "spec/impact_tier3.py:425",
    "spec/impact_tier3.py:429",
    "spec/impact_tier3.py:435",
    "spec/impact_tier3.py:1118",
    "spec/lint.py:207",
    "spec/lint.py:216",
    "spec/lint.py:230",
    "spec/lint.py:465",
    "spec/lint.py:534",
    "spec/lint.py:593",
    "spec/lint.py:604",
    "spec/lint.py:720",
    "spec/lint.py:799",
    "spec/lint.py:918",
    "spec/lint.py:922",
    "spec/preconditions.py:42",
    "spec/preconditions.py:50",
    "spec/preconditions.py:69",
    "spec/race.py:903",
    "spec/review.py:205",
    "spec/review.py:238",
    "spec/validate.py:343",
]


def trace_for(scenario: str = "mixed") -> Trace:
    rules = [
        Rule("R1", "A user never has more than 3 published notes.", "s", 1),
        Rule("R2", "A background loop archives notes in production.", "s", 2),
        Rule("R3", "A title is 1-80 characters.", "s", 3),
        Rule("R4", "Only members read notes; a non-member gets 404.", "s", 4),
    ]
    links = {r.id: [f"scenario:{scenario}"] for r in rules}
    links["R1"] = ["scenario:plain"]  # a scenario of successes only: L003
    links["R2"] = ["scenario:plain"]  # no Await in it: L008
    return Trace("", rules, links=links)


# ---- the checks -----------------------------------------------------------------------------


def check_export() -> None:
    from lattice.contracts.export import export_spec

    out = export_spec(MIXED)
    assert list(out["endpoints"]) == [
        "GET /v1/notes/{}",
        "POST /v1/notes",
    ]  # the Rpc is no endpoint


def check_select_step() -> None:
    from lattice.contracts.export import endpoint_key
    from lattice.contracts.live import select_step

    sc, i = select_step(MIXED, endpoint_key("GET", "/v1/notes/1"), "token", None)
    assert (sc.name, i) == ("mixed", 2)  # the rpc before it is skipped, not read as a Step


def check_replay_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from lattice.contracts import live
    from lattice.security import target as target_mod

    sent: list[Any] = []

    class Target:
        def request(self, req: Any) -> Any:
            sent.append(req)
            return type("A", (), {"status": 200, "body": {"id": 1}})()

    @contextlib.contextmanager
    def fake(harness: Any, seed: Any, **kw: Any) -> Any:
        yield Target()

    monkeypatch.setattr(target_mod, "start_target_harness", fake)
    out = live._replay_one(object(), MIXED, MIXED.scenarios[0], 2)
    assert out["answered"] == 200 and out["awaits_skipped"] == 1  # the rpc, counted as skipped
    assert [r.path for r in sent] == ["/v1/notes", "/v1/notes/1"]  # only HTTP steps were sent


def check_security_plan() -> None:
    from lattice.security import plan

    assert plan.generate(MIXED) is not None
    kept = plan.scope_endpoints(MIXED, "endpoints", "GET /v1")
    assert [s.name for s in kept.scenarios] == ["mixed"]
    only_rpc = replace(MIXED, scenarios=(Scenario("r", steps=(RPC,)),))
    assert plan.scope_endpoints(only_rpc, "endpoints", "GET /v1").scenarios == ()


def check_ask_check_step() -> None:
    from lattice.spec import ask

    line = "RPC notes.v1.Notes/GetNote as ann -> NOT_FOUND"
    assert ask._check_step(RPC, line, "RPC notes.v1.Notes/GetNote as ann -> NOT_FOUND") is None
    assert ask._check_step(RPC, line, "GET /x") is not None


def check_ask_endpoint_and_digest() -> None:
    from lattice.spec import ask

    assert ask._endpoint(RPC) is None  # not an HTTP endpoint
    digest = ask.build_digest(MIXED, trace_for())
    index = ask.Index(digest)
    cell = next(t for t in digest.elements if t.startswith("cell:") and "RPC" in t)
    assert index.related_scenarios([cell]) == []  # an rpc cell names no HTTP step to match
    assert index.related_scenarios(["cell:ann|GET /v1/notes/{}"]) in ([], ["scenario:mixed"])


def check_ask_step_json_and_claim_key() -> None:
    import json

    from lattice.spec import ask

    d = json.loads(ask._step_json(RPC))
    assert d["rpc"] == "RPC notes.v1.Notes/GetNote" and d["expect_code"] == "NOT_FOUND"
    assert ask._claim_key(RPC)[0] == "rpc" and ask._claim_key(GET)[0] == "GET"


def check_ask_db_only_and_observe(monkeypatch: pytest.MonkeyPatch) -> None:
    from lattice.spec import ask

    finding = type(
        "F",
        (),
        {
            "case": "mixed / step 4",
            "details": {
                "actual": {"status": "OK", "code": "OK", "body": {}},
                "expected": {"status": "NOT_FOUND", "code": "NOT_FOUND", "body": None, "db": None},
                "problems": ["code: expected NOT_FOUND, got OK"],
            },
        },
    )()
    sc = MIXED.scenarios[0]
    assert ask._db_only_failure(sc, finding) is False  # step 4 is an Rpc: not an HTTP db-only miss
    assert "expected NOT_FOUND, got OK" in ask._finding_text(sc, finding)
    calls = []

    def fake_run(spec: Any, scenario: Any, cfg: Any) -> tuple[str, Any]:
        calls.append(scenario)
        f = type(
            "F",
            (),
            {
                "case": "mixed / step 2",
                "details": {
                    "actual": {"status": "OK", "body": {}},
                    "expected": {"status": "OK"},
                    "problems": ["code: expected OK, got NOT_FOUND"],
                },
            },
        )()
        return ("fail", f) if len(calls) == 1 else ("pass", None)

    monkeypatch.setattr(ask, "_run", fake_run)
    out = ask._observe(MIXED, sc, object())  # type: ignore[arg-type]
    assert (
        out["status"] == "error" and "observation:" in out["detail"]
    )  # an Rpc step is not rewritten
    only_http = Scenario("h", steps=(POST, GET))
    assert ask._observe(MIXED, only_http, object())["status"] == "observed"  # type: ignore[arg-type]


def check_authoring() -> None:
    from lattice.spec.authoring import requirements_markdown

    text = requirements_markdown(MIXED, title="t")
    assert "RPC notes.v1.Notes/GetNote as ann -> NOT_FOUND" in text and "POST /v1/notes" in text


def check_check_helpers() -> None:
    import importlib

    chk = importlib.import_module("lattice.spec.check")  # `lattice.spec.check` is also a function
    assert chk._step_request(RPC)["rpc"] == "notes.v1.Notes/GetNote"
    assert chk._step_endpoint(RPC) == "RPC notes.v1.Notes/GetNote"
    assert chk._step_endpoint(WAIT) == "service worker"
    assert chk.missing_services(MIXED, {}) == ["worker"]  # the Await is read, the Rpc is not


def check_run_scenario_with_rpc_and_await(notes_binary: Any) -> None:
    """Layer D over a scenario mixing Rpc and Await steps on the live notes server: both branches
    of `_run_scenario`, and `await_cap` capping the Await and leaving the Rpc alone."""
    import sys

    from lattice.spec import ServiceCmd, check
    from tests.grpc.fixtures.notes import harness

    steps = (
        Rpc("notes.v1.Accounts", "Register", None, {"name": "cat", "password": "secret1"}),
        Await("worker", expect_db={"User": {"count": 3}}, within_s=30.0),
        Rpc(
            "notes.v1.Accounts",
            "Login",
            None,
            {"name": "cat", "password": "secret1"},
            expect_message={"token": "tok-cat"},
        ),
    )
    spec = replace(dev.SPEC, services=(Service("worker"),), scenarios=(Scenario("m", steps=steps),))
    noop = {"worker": lambda db: ServiceCmd([sys.executable, "-c", "pass"])}
    report = check(
        spec,
        harness.make_app,
        harness.build_db,
        layers="D",
        runtime=harness.RUNTIME,
        services=noop,
        await_cap=1.0,
    )
    assert report.findings == [] and len(report.cases) == 3
    assert any("--await-cap 1" in n and "1 step(s) capped" in n for n in report.notes)


def check_fidelity() -> None:
    from lattice.importer.transport import Answer
    from lattice.spec.fidelity import run_fidelity

    class Transport:
        client = None

        def request(self, method: str, path: str, **kw: Any) -> Answer:
            return Answer(200, {}, {"id": 1})

    class Runtime:
        @contextlib.contextmanager
        def serve(self, app_factory: Any, db: Any) -> Any:
            yield None, Transport()

    sc = Scenario("f", steps=(Step("GET", "/v1/notes/1"), RPC, Step("GET", "/v1/notes/1")))
    out = run_fidelity(
        replace(dev.SPEC, scenarios=(), protos=()),
        [sc],
        lambda db: None,
        lambda p: p.write_bytes(b""),
        lambda c, h: {},
        runtime=Runtime(),
    )
    assert [s.index for s in out.steps] == [
        1,
        3,
    ]  # the rpc (step 2) is not an HTTP answer to compare


def check_implementation_views() -> None:
    from lattice.spec.implementation import SpecView

    sv = SpecView.of(MIXED, None)
    assert sv.scenarios["mixed"] == [
        "POST /v1/notes",
        "RPC /notes.v1.Notes/CreateNote",
        "GET /v1/notes/{}",
        "RPC /notes.v1.Notes/GetNote",
        "POST /v1/notes",
    ]


def check_tiers_2_and_3(tmp_path: Any) -> None:
    from tests.spec import test_impact_report as t

    edit = (
        "from lattice.spec import Rpc as _Rpc, Scenario as _Sc\n"
        "from dataclasses import replace as _r\n"
        "_first = SPEC.scenarios[0].steps[0]\n"
        "SPEC = _r(SPEC, scenarios=SPEC.scenarios + (\n"
        "    _Sc('rpc_mix', steps=(_first, _Rpc('p.v1.S', 'M', None, {}), _first)),))\n"
    )
    base, cand = t._side(edit), t._side(edit + t.STATUS_EDIT)
    inp = t.ir.build_inputs(project=None, base_side=base, cand_side=cand)
    inp.inventory = t._gin_inventory(tmp_path)
    rep = t.ir.build_report(inp, tiers="23", baseline="none")
    assert rep["tiers"]["3"] == "run"
    # the rpc step ended the prefix: no tier-2 claim is made against it
    assert all(
        d["against"]["step"] != 2
        for d in rep["tier2"]["determined"]
        if d.get("scenario") == "rpc_mix"
    )
    from lattice.spec import impact_tier3

    assert impact_tier3.has_await(inp.cand.spec) is False  # an Rpc is not an Await


def check_tier2_replay(tmp_path: Any) -> None:
    from tests.spec import minishop
    from tests.spec import test_impact_report as t

    base = replace(
        minishop.SPEC,
        scenarios=(
            *minishop.SPEC.scenarios[:1],
            Scenario("rpc_mix", steps=(minishop.SPEC.scenarios[0].steps[0], RPC)),
        ),
    )
    prog = base.programs["orders"].replace(
        'else_=Conflict("Order cannot be cancelled")', 'else_=NotFound("gone")'
    )
    cand = replace(base, programs={"orders": prog})
    root = tmp_path / "shop"
    root.mkdir()
    app_dir, package = minishop.write_app(root, "ok")
    env = {
        "make_db": lambda variant, path: minishop.build_db(path),
        "app_factory": minishop.app_factory(package),
        "login": minishop.login,
    }
    rep = t.t2.runtime_replay(base, cand, t._passed_all(base), env, t.t2.SpecLines(""))
    assert rep["status"] == "run"  # the Rpc scenario's step 1 may replay; the Rpc stops it


def check_lint_helpers() -> None:
    lint = lint_mod
    assert lint._is_write(RPC) is False and lint._is_write(POST) is True
    assert lint._failing(RPC) is True and lint._failing(RPC_OK) is False
    assert lint._failing(replace(RPC_OK, expect_db={"Note": {"count": 1}})) is True
    assert lint._failing(replace(RPC_OK, expect_message={"note": {}})) is True
    assert lint._has_failing(Scenario("s", steps=(RPC_OK,))) is False
    # a refused write expecting 4xx, an rpc, then a failing check after the second write
    sc = Scenario("s", steps=(POST, RPC_OK, POST, RPC))
    assert lint._sets_up_forbidden(sc) is True
    assert lint._sets_up_forbidden(Scenario("t", steps=(RPC_OK, RPC_OK))) is False


def check_lint_all_codes() -> None:
    findings = lint_mod.lint(MIXED, trace_for())
    assert {f.code for f in findings} >= {"L003", "L008"}  # it ran to the end over the Rpc
    notes: list[str] = []
    from tests.grpc.fixtures.notes.harness import build_db

    lint_mod.lint(
        MIXED, trace_for(), vacuity=lint_mod.VacuityConfig(lambda p: build_db(p)), notes=notes
    )
    assert any("L009 skipped for the scenarios with Rpc steps" in n for n in notes)


def check_lint_setup_steps() -> None:
    sc = Scenario("s", steps=(GET, RPC_OK, Step("GET", "/v1/notes/2", "ann")))
    got = lint_mod._setup_steps(replace(MIXED, scenarios=(sc,)))
    assert [(i, s.path) for _, i, s, _ in got] == [(1, "/v1/notes/1")]  # an rpc may write: stop


def check_preconditions() -> None:
    from lattice.spec import preconditions as pre

    assert pre.removes_rows(RPC) is False  # it expects a refusal
    assert pre.removes_rows(RPC_OK) is True  # an rpc that succeeds may write
    assert pre.creates_rows(Rpc(NOTES, "ListNotes", "ann", expect_code="NOT_FOUND")) is True
    assert pre.creates_rows(Rpc(NOTES, "ListNotes", None, expect_code="NOT_FOUND")) is False
    sc = Scenario(
        "s",
        steps=(
            Step("GET", "/a", expect_db={"Note": {"count": 1}}),
            RPC_OK,
            Step("GET", "/a", expect_db={"Note": {"count": 0}}),
        ),
    )
    assert pre.contradictions(sc) == []  # the rpc may have removed the row
    assert (
        pre.unestablished(sc, 3, {"Note": {"count": 1}}, ["db Note: expected 1 rows, found 0"])
        == []
    )


def check_race() -> None:
    from lattice.spec import race
    from lattice.spec.report import Report

    report = Report(layers="X", unchecked=[])
    out = race._before_await(Scenario("s", steps=(POST, RPC_OK, GET)), report)
    assert out.steps == (POST,) and "rpc_layer_not_supported:X" in report.notes[0]


def check_review() -> None:
    from lattice.spec.review import _build_access, _step_line

    assert _step_line(RPC).startswith("RPC notes.v1.Notes/GetNote as ann")
    assert {g.prefix for g in _build_access(MIXED)} == {"v1", NOTES}


def check_validate() -> None:
    from lattice.spec import validate

    assert [p.code for p in validate(MIXED)] == []  # step 3 of `mixed` (the Rpc) is validated


CHECKS: dict[str, Any] = {}
for _ids, _fn in (
    (["contracts/export.py:186"], check_export),
    (["contracts/live.py:425"], check_select_step),
    (["contracts/live.py:525"], check_replay_one),
    (["security/plan.py:93", "security/plan.py:481"], check_security_plan),
    (["spec/ask.py:318"], check_ask_check_step),
    (["spec/ask.py:906", "spec/ask.py:1001"], check_ask_endpoint_and_digest),
    (["spec/ask.py:1588", "spec/ask.py:1862"], check_ask_step_json_and_claim_key),
    (["spec/ask.py:1785", "spec/ask.py:1815", "spec/ask.py:1836"], check_ask_db_only_and_observe),
    (["spec/authoring.py:264"], check_authoring),
    (["spec/check.py:1377", "spec/check.py:1394", "spec/check.py:1441"], check_check_helpers),
    (["spec/check.py:1659", "spec/check.py:2015"], check_run_scenario_with_rpc_and_await),
    (["spec/fidelity.py:269"], check_fidelity),
    (["spec/implementation.py:629", "spec/implementation.py:1720"], check_implementation_views),
    (
        [
            "spec/impact_tier2.py:287",
            "spec/impact_tier2.py:366",
            "spec/impact_tier2.py:510",
            "spec/impact_tier3.py:413",
            "spec/impact_tier3.py:422",
            "spec/impact_tier3.py:425",
            "spec/impact_tier3.py:429",
            "spec/impact_tier3.py:435",
            "spec/impact_tier3.py:1118",
        ],
        check_tiers_2_and_3,
    ),
    (["spec/impact_tier2.py:761", "spec/impact_tier2.py:775"], check_tier2_replay),
    (["spec/lint.py:207", "spec/lint.py:216", "spec/lint.py:230"], check_lint_helpers),
    (
        [
            "spec/lint.py:465",
            "spec/lint.py:534",
            "spec/lint.py:593",
            "spec/lint.py:604",
            "spec/lint.py:720",
            "spec/lint.py:918",
            "spec/lint.py:922",
        ],
        check_lint_all_codes,
    ),
    (["spec/lint.py:799"], check_lint_setup_steps),
    (
        ["spec/preconditions.py:42", "spec/preconditions.py:50", "spec/preconditions.py:69"],
        check_preconditions,
    ),
    (["spec/race.py:903"], check_race),
    (["spec/review.py:205", "spec/review.py:238"], check_review),
    (["spec/validate.py:343"], check_validate),
):
    for _id in _ids:
        CHECKS[_id] = _fn


def test_every_site_has_a_check() -> None:
    assert sorted(CHECKS) == sorted(SITE_IDS) and len(SITE_IDS) == 51


@pytest.mark.parametrize("site", SITE_IDS)
def test_site(site: str, request: pytest.FixtureRequest) -> None:
    fn = CHECKS[site]
    names = fn.__code__.co_varnames[: fn.__code__.co_argcount]
    kwargs = {n: request.getfixturevalue(n) for n in names}
    fn(**kwargs)
