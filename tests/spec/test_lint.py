"""lattice.spec.lint: a drafted spec against the owner's rules -- one hand-made spec and
trace per code, a clean spec with no findings, the vacuity stubs run for real (on the
runs app of tests/spec/racy.py), and the CLI."""

from __future__ import annotations

import json
from pathlib import Path

from lattice.spec import Await, Contract, Scenario, Service, Spec, Step, check
from lattice.spec.lint import (
    LintConfig,
    LintFinding,
    VacuityConfig,
    lint,
    main,
    stub_app,
    template_of,
)
from lattice.spec.rules import Question, Trace, extract_rules
from tests.spec import racy

CREATE = Step(
    "POST",
    "/configs/1/runs",
    "ann",
    expect_status=201,
    expect_body={"id": 1, "status": "queued"},
    expect_db={"runs": {"count": 1}},
)
ANON = Scenario(
    "anon_is_refused",
    steps=(
        Step(
            "POST", "/configs/1/runs", None, expect_status=401, expect_db={"runs": {"count": 0}}
        ),  # fmt: skip
        Step("GET", "/runs/1", None, expect_status=401),
    ),
)
QUEUE = Scenario("queue_a_run", steps=(CREATE,))
ONE_LIVE = Scenario(
    "one_live_run",
    steps=(
        CREATE,
        Step(
            "POST", "/configs/1/runs", "ben", expect_status=409, expect_db={"runs": {"count": 1}}
        ),  # fmt: skip
    ),
)
READ = Scenario(
    "read_a_run",
    given={"runs": [{"id": 1, "config_id": 1, "status": "queued"}]},
    steps=(Step("GET", "/runs/1", "ann", expect_body={"id": 1, "status": "queued"}),),
)
CLEAN_MD = """# Runs

Runs are the queued executions of a config.

- A signed-in user can queue a run of a config.
- A config never has more than one live run.
- Reading a run needs a signed-in user.
"""
CLEAN_SPEC = Spec(
    profile=racy.RUNS_PROFILE,
    invariants=(racy.ONE_LIVE,),
    scenarios=(QUEUE, ONE_LIVE, ANON, READ),
    users=racy.USERS,
)
CLEAN_LINKS = {
    "R2": ["scenario:queue_a_run"],
    "R3": ["invariant:one_live_run_per_config", "scenario:one_live_run"],
    "R4": ["scenario:anon_is_refused", "scenario:read_a_run"],
}


def trace(md: str, links=None, *, unchecked=None, context=None, questions=None) -> Trace:
    return Trace(source="REQ.md", rules=extract_rules(md), links=dict(links or {}),
                 unchecked=dict(unchecked or {}), context=list(context or []),
                 questions=list(questions or []))  # fmt: skip


def clean_trace() -> Trace:
    return trace(CLEAN_MD, CLEAN_LINKS, context=["R1"])


def spec(*scenarios: Scenario, **kw) -> Spec:
    return Spec(profile=racy.RUNS_PROFILE, scenarios=scenarios, users=racy.USERS, **kw)


def codes(findings: list[LintFinding], code: str) -> list[LintFinding]:
    return [f for f in findings if f.code == code]


# ---- clean ----------------------------------------------------------------------------------


def test_the_clean_spec_has_no_findings() -> None:
    notes: list[str] = []
    assert lint(CLEAN_SPEC, clean_trace(), notes=notes) == []
    assert any("L009 skipped" in n for n in notes)


def test_the_clean_spec_is_not_vacuous_and_holds_on_the_real_app() -> None:
    vac = VacuityConfig(racy.build_runs_db)
    notes: list[str] = []
    assert lint(CLEAN_SPEC, clean_trace(), vacuity=vac, notes=notes) == []
    assert notes == []
    report = check(CLEAN_SPEC, racy.runs_app(), racy.build_runs_db, layers="D",
                   login=racy.login)  # fmt: skip
    assert report.findings == []


# ---- L001, L002 -----------------------------------------------------------------------------


def test_l001_untraced_rule() -> None:
    t = clean_trace()
    del t.links["R4"]
    found = lint(CLEAN_SPEC, t)
    assert [(f.code, f.rule_id) for f in found] == [("L001", "R4")]
    assert '"unchecked"' in found[0].fix and '"context"' in found[0].fix


def test_l002_dangling_links() -> None:
    links = dict(CLEAN_LINKS)
    links["R2"] = ["scenario:queue_a_run", "scenario:missing", "invariant:nope",
                   "machine:Run.status", "profile:Run.nope", "bogus:x"]  # fmt: skip
    t = trace(CLEAN_MD, links, context=["R1"], unchecked={"R9": "?"})
    found = codes(lint(CLEAN_SPEC, t), "L002")
    assert {f.element for f in found} == {"scenario:missing", "invariant:nope",
                                          "machine:Run.status", "profile:Run.nope",
                                          "bogus:x", None}  # fmt: skip
    assert any(f.rule_id == "R9" and '"unchecked"' in f.fix for f in found)


def test_l002_accepts_both_table_spellings() -> None:
    links = dict(CLEAN_LINKS)
    links["R2"] = ["scenario:queue_a_run", "profile:Run.status", "profile:runs",
                   "machine:runs.status"]  # fmt: skip
    s = Spec(profile=racy.RUNS_PROFILE, invariants=(racy.ONE_LIVE,), machines=(racy.RUN_MACHINE,),
             scenarios=CLEAN_SPEC.scenarios, users=racy.USERS)  # fmt: skip
    assert codes(lint(s, trace(CLEAN_MD, links, context=["R1"])), "L002") == []


# ---- L003 -----------------------------------------------------------------------------------

L003_MD = "- Every user must be able to read the queue.\n- A config never has two live runs.\n"
GET_ANON = Step("GET", "/runs/1", None, expect_status=401)


def test_l003_linked_only_to_scenarios_with_no_failing_expectation() -> None:
    weak2 = Scenario("reads", steps=(Step("GET", "/runs/1", "ann"),))
    s = spec(weak2, Scenario("x", steps=(CREATE, GET_ANON)))
    found = codes(lint(s, trace(L003_MD, {"R1": ["scenario:reads"], "R2": ["scenario:x"]})),
                  "L003")  # fmt: skip
    assert [f.rule_id for f in found] == ["R1", "R2"]
    assert "only 2xx" in found[0].message
    assert "forbidden case" in found[1].message  # (b): one create, no second attempt


def test_l003_never_rule_satisfied_by_a_refused_write_or_an_invariant() -> None:
    s = spec(Scenario("ok", steps=(Step("GET", "/runs/1", "ann", expect_body={"id": 1}),)),
             ONE_LIVE)  # fmt: skip
    t = trace(L003_MD, {"R1": ["scenario:ok"], "R2": ["scenario:one_live_run"]})
    assert codes(lint(s, t), "L003") == []
    s2 = spec(QUEUE, invariants=(racy.ONE_LIVE,))
    t2 = trace(L003_MD, {"R1": ["scenario:queue_a_run"],
                         "R2": ["invariant:one_live_run_per_config"]})  # fmt: skip
    assert codes(lint(s2, t2), "L003") == []


# ---- L004, L005 -----------------------------------------------------------------------------


def test_l004_refused_write_without_expect_db() -> None:
    sc = Scenario("dup", steps=(CREATE, Step("POST", "/configs/1/runs", "ben", expect_status=409)))
    found = codes(lint(spec(sc), trace("- x\n", {"R1": ["scenario:dup"]})), "L004")
    assert len(found) == 1 and found[0].rule_id == "R1"
    assert "step 2" in found[0].message and "'runs'" in found[0].fix


def test_l005_write_without_effect_unless_a_later_step_reads_it() -> None:
    bare = Step("POST", "/configs/1/runs", "ann", expect_status=201)
    found = codes(lint(spec(Scenario("w", steps=(bare,))), trace("- x\n", {"R1": ["scenario:w"]})),
                  "L005")  # fmt: skip
    assert len(found) == 1 and "step 1" in found[0].message
    read = Step("GET", "/runs/1", "ann", expect_body={"status": "queued"})
    s = spec(Scenario("w", steps=(bare, read)))
    assert codes(lint(s, trace("- x\n", {"R1": ["scenario:w"]})), "L005") == []


# ---- L006 -----------------------------------------------------------------------------------


def test_template_of() -> None:
    assert template_of("/applications/1/jobs/2") == "/applications/{}/jobs/{}"
    assert template_of("/runs/0f8fad5b-d9cb-469f-a165-70867728950e?x=1") == "/runs/{}"


def test_l006_anonymous_access_per_endpoint_template() -> None:
    s = spec(
        READ,
        Scenario("login", steps=(Step("POST", "/login", None, expect_status=200),)),
        Scenario("health", steps=(Step("GET", "/status", None, expect_body={"ok": 1}),)),
    )
    found = codes(lint(s, trace("- x\n", {"R1": ["scenario:read_a_run"]})), "L006")
    # /login is public by path, /status by its anonymous 200; GET /runs/{} has no 401 step
    assert [f.element for f in found] == ["endpoint:GET /runs/{}"]
    assert "None" in found[0].fix and "expect_status=401" in found[0].fix
    # a contract's template, and a Public contract makes the endpoint public
    public = spec(*s.scenarios, contracts=(Contract("GET", "/runs/{rid}", "Public"),))
    assert codes(lint(public, trace("- x\n", {"R1": ["scenario:read_a_run"]})), "L006") == []


def test_l006_outsiders_only_when_the_rules_name_them() -> None:
    s = spec(READ, Scenario("anon", steps=(GET_ANON,)))
    plain = trace("- x\n", {"R1": ["scenario:read_a_run", "scenario:anon"]})
    assert codes(lint(s, plain), "L006") == []
    md = "- A user who is not a member of the config gets 404.\n"
    member = trace(md, {"R1": ["scenario:read_a_run", "scenario:anon"]})
    found = codes(lint(s, member), "L006")
    assert len(found) == 1 and "expect_status=404" in found[0].fix
    s2 = spec(
        READ,
        Scenario("anon", steps=(GET_ANON,)),
        Scenario("outsider", steps=(Step("GET", "/runs/1", "ben", expect_status=404),)),
    )
    assert codes(lint(s2, member), "L006") == []
    assert codes(lint(s, member, config=LintConfig(outsiders=False)), "L006") == []


# ---- L007 -----------------------------------------------------------------------------------


def test_l007_one_of_column_sent_without_an_invalid_value() -> None:
    set_status = Step("PATCH", "/runs/1", "ann", json={"status": "cancelled"},
                      expect_body={"status": "cancelled"})  # fmt: skip
    t = trace("- x\n", {"R1": ["scenario:s"]})
    found = codes(lint(spec(Scenario("s", given=READ.given, steps=(set_status,))), t), "L007")
    assert [f.element for f in found] == ["profile:Run.status"]
    bad = Step(
        "PATCH",
        "/runs/1",
        "ann",
        json={"status": "bogus"},
        expect_status=422,
        expect_db={"runs": {"where": {"id": 1}, "equals": {"status": "queued"}}},
    )
    s = spec(Scenario("s", given=READ.given, steps=(set_status, bad)))
    assert codes(lint(s, t), "L007") == []


def test_l007_a_rule_range_without_an_out_of_range_value() -> None:
    md = "- A config name is 1-100 characters.\n"
    ok = Step("POST", "/configs", "ann", json={"name": "n"}, expect_status=201,
              expect_db={"configs": {"count": 3}})  # fmt: skip
    t = trace(md, {"R1": ["scenario:s"]})
    found = codes(lint(spec(Scenario("s", steps=(ok,))), t), "L007")
    assert [f.rule_id for f in found] == ["R1"] and "101 characters" in found[0].fix
    long = Step("POST", "/configs", "ann", json={"name": "x" * 101}, expect_status=422,
                expect_db={"configs": {"count": 2}})  # fmt: skip
    assert codes(lint(spec(Scenario("s", steps=(ok, long))), t), "L007") == []
    # a context or unchecked rule is not asked for
    assert codes(lint(spec(Scenario("s", steps=(ok,))),
                      trace(md, unchecked={"R1": "free text"})), "L007") == []  # fmt: skip


# ---- L008 -----------------------------------------------------------------------------------

BG_MD = "- The worker finishes queued runs in the background.\n"


def test_l008_background_rule_needs_an_await_service_or_unchecked() -> None:
    s = spec(QUEUE, services=(Service("worker"),))
    found = codes(lint(s, trace(BG_MD, {"R1": ["scenario:queue_a_run"]})), "L008")
    assert len(found) == 1 and "Await('worker'" in found[0].fix
    assert codes(lint(s, trace(BG_MD, {"R1": ["service:worker"]})), "L008") == []
    assert codes(lint(s, trace(BG_MD, unchecked={"R1": "timing"})), "L008") == []
    waits = Scenario("w", steps=(CREATE, Await("worker", {"runs": {"count": 1}})))
    s2 = spec(waits, services=(Service("worker"),))
    assert codes(lint(s2, trace(BG_MD, {"R1": ["scenario:w"]})), "L008") == []


# ---- L010 -----------------------------------------------------------------------------------


def test_l010_questions() -> None:
    qs = [Question("Q1", "Is a cancel final?", "", ["R2"]),
          Question("Q2", "Retry?", "no", ["R99"]),
          Question("Q3", "Answered?", "", ["R2"], answer="yes")]  # fmt: skip
    t = clean_trace()
    t.questions = qs
    found = codes(lint(CLEAN_SPEC, t), "L010")
    assert [(f.element, "default" in f.message) for f in found] == [
        ("question:Q1", True), ("question:Q2", False)]  # fmt: skip


# ---- L009: the stubs, run for real -----------------------------------------------------------


def test_stub_apps_answer_everything() -> None:
    from fastapi.testclient import TestClient

    for status in (200, 500):
        with TestClient(stub_app(status)) as c:
            for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                assert c.request(method, "/a/1/b").status_code == status
            assert c.get("/").status_code == status


def test_l009_vacuous_scenarios_against_the_stubs() -> None:
    vacuous = Scenario(
        "only_200s",
        steps=(
            Step("POST", "/configs/1/runs", "ann"),
            Step("GET", "/runs/1", "ann", expect_body={}),
        ),
    )
    empty = Scenario("no_steps")
    already = Scenario("await_holds_at_seed", steps=(
        Step("GET", "/runs/1", "ann"),
        Await("worker", {"configs": {"count": 2}}, within_s=30)))  # fmt: skip
    s = Spec(profile=racy.RUNS_PROFILE, machines=(racy.RUN_MACHINE,), invariants=(racy.ONE_LIVE,),
             scenarios=(*racy.RUNS_SCENARIOS, vacuous, empty, already), users=racy.USERS,
             services=(Service("worker"),))  # fmt: skip
    links = {f"R{i}": [f"scenario:{sc.name}"] for i, sc in enumerate(s.scenarios, 1)}
    md = "".join(f"- rule {i}\n" for i in range(1, len(s.scenarios) + 1))
    notes: list[str] = []
    found = lint(s, trace(md, links), vacuity=VacuityConfig(racy.build_runs_db), notes=notes)
    l009 = codes(found, "L009")
    assert notes == []
    assert {f.element for f in l009} == {"scenario:only_200s", "scenario:no_steps",
                                         "scenario:await_holds_at_seed"}  # fmt: skip
    by = {f.element: f for f in l009}
    assert "all-200" in by["scenario:only_200s"].message
    assert "all-500" not in by["scenario:only_200s"].message
    assert "all-500" in by["scenario:no_steps"].message
    assert by["scenario:only_200s"].rule_id == "R4"


def test_l009_is_skipped_with_a_note_when_the_check_cannot_run() -> None:
    def broken(path: Path) -> Path:
        raise RuntimeError("no db")

    notes: list[str] = []
    found = lint(CLEAN_SPEC, clean_trace(), vacuity=VacuityConfig(broken), notes=notes)
    assert codes(found, "L009") == [] and any("L009 skipped" in n for n in notes)


# ---- findings, CLI --------------------------------------------------------------------------


def test_findings_are_json_serialisable() -> None:
    t = clean_trace()
    del t.links["R2"]
    found = lint(CLEAN_SPEC, t)
    assert json.loads(json.dumps([f.to_dict() for f in found]))[0]["code"] == "L001"


SPEC_MODULE = """
from tests.spec.test_lint import CLEAN_SPEC as SPEC
"""


def test_cli(tmp_path: Path, capsys) -> None:
    mod = tmp_path / "drafted_spec.py"
    mod.write_text(SPEC_MODULE)
    clean_trace().save(tmp_path / "drafted_spec.trace.json")
    assert main(["lint", "--spec", str(mod)]) == 0
    out = capsys.readouterr().out
    assert "0 finding(s)" in out and "L009 skipped" in out

    t = clean_trace()
    del t.links["R4"]
    t.save(tmp_path / "gappy.trace.json")
    args = ["--spec", "tests.spec.test_lint:CLEAN_SPEC", "--trace", str(tmp_path /
            "gappy.trace.json"), "--db", "tests.spec.racy:build_runs_db"]  # fmt: skip
    assert main(args) == 1
    out = capsys.readouterr().out
    assert "L001 untraced rule (1)" in out and "fix:" in out
    assert main([*args, "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [f["code"] for f in data["findings"]] == ["L001"] and data["notes"] == []
    assert main(["--spec", "nope.mod:SPEC", "--trace", "x.json"]) == 2
