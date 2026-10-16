"""lattice.spec.ask with an inventory (Spike C, D9): the implemented-app digest section, the
code: and impl: citations, the implementation label and its deterministic downgrades, verify by
replaying a cited flow (no model call), the CLI and Studio. Without an inventory nothing of this
exists: the prompt, digest and answer are what they were (goldens measured on the base)."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.inventory import capture
from lattice.llm.client import ScriptedGenerator
from lattice.spec import ask as ask_mod
from lattice.spec.ask import (
    IMPL_BUDGET,
    IMPL_CORE_BUDGET,
    SYSTEM,
    SYSTEM_IMPL,
    Answer,
    approx_tokens,
    ask,
    build_digest,
    check_citation,
    core_text,
    impl_core,
    retrieve,
)
from lattice.spec.implementation import load_inventory
from lattice.spec.rules import Trace, extract_rules
from tests.spec import racy

FIX = Path(__file__).resolve().parent / "fixtures" / "implementation"
EXTRACT = json.loads((FIX / "extract.json").read_text())
WF = json.loads((FIX / "workflows.json").read_text())


def _fixture_spec():
    sp = importlib.util.spec_from_file_location("ask_impl_fixture_spec", FIX / "fixture_spec.py")
    mod = importlib.util.module_from_spec(sp)
    sys.modules["ask_impl_fixture_spec"] = mod
    sp.loader.exec_module(mod)
    return mod


FS = _fixture_spec()
SPEC, TRACE = FS.SPEC, FS.trace()


def observed(doc: dict[str, Any], machines: str = "confirmed") -> dict[str, Any]:
    d = copy.deepcopy(doc)
    d["run"]["mode"] = "observed"
    for f in d["flows"]:
        f["status"] = "verified"
    for m in d["machines"]:
        m["status"] = machines
    return d


@pytest.fixture
def app(tmp_path: Path) -> Path:
    dest = tmp_path / "app"
    shutil.copytree(FIX / "app", dest)
    return dest


def make_inv(app: Path, tmp_path: Path, workflows: Any = None, name: str = "inv"):
    wf = None
    if workflows is not None:
        wf = tmp_path / f"{name}.json"
        wf.write_text(json.dumps(workflows))
    capture(app, tmp_path / name, extract=EXTRACT, workflows=wf)
    return load_inventory(tmp_path / name, app)


@pytest.fixture
def inv(app: Path, tmp_path: Path):
    return make_inv(app, tmp_path, observed(WF))


def reply(
    kind: str = "yes", impl: str | None = None, cites: list[dict] | None = None, **kw: Any
) -> str:
    body = {"kind": kind, "answer": "a", "explanation": "e", "citations": cites or []}
    if impl is not None:
        body["implementation"] = impl
    body.update(kw)
    return json.dumps(body)


def cite(target: str, quote: str, step: int | None = None) -> dict[str, Any]:
    return {"target": target, "step": step, "quote": quote}


def line_of(app: Path, rel: str, needle: str) -> int:
    for i, ln in enumerate((app / rel).read_text().split("\n"), 1):
        if needle in ln:
            return i
    raise AssertionError(needle)


def flow_target(inv, routes: list[str], kind: str = "pair") -> str:
    f = next(f for f in inv.workflows["flows"] if f["skeleton"] == routes and f["kind"] == kind)
    return f"impl:flow:{f['id']}"


def run(
    inv, text: str | list[str], question: str = "Can a project be published?", **kw: Any
) -> Answer:
    g = ScriptedGenerator([text] if isinstance(text, str) else text)
    return ask(SPEC, question, trace=TRACE, generator=g, model="stub", inventory=inv, **kw)


# ---- without an inventory: nothing changes ------------------------------------------------

BASE_TRACE = Trace(
    "REQ.md",
    extract_rules("# Runs\n\n- A config has at most one live run.\n- Runs are fast.\n"),
    links={"R1": ["invariant:one_live_run_per_config"]},
    unchecked={"R2": "no timing in the spec"},
)
# sha256 of (digest text, system prompt, context) measured on the base (454a8e6 = c0ff1e4 for ask)
GOLD = {
    "notrace": (
        "47afcc655adae8e8f7477088c0df2901d3b594857959ed26281220387f3b5350",
        "e792ce2016fe303c47b83b499be01c555e43e2a24d71ad9228dc1d67e6887ff4",
        "bb6b6e859c24dc147f131ddb47bb35f22e8eba43dec868fb52a9dca2022a9ac6",
    ),
    "trace": (
        "f44876f9ab650f93557d8a7d10861c7328752d9c9a39df3b2445c3df7b9b6ac7",
        "f934e20661a9dfd75a08d38ba84db618e72b9166c6aac9c4b79c4964f3b0dc66",
        "bb6b6e859c24dc147f131ddb47bb35f22e8eba43dec868fb52a9dca2022a9ac6",
    ),
}


@pytest.mark.parametrize(("name", "trace"), [("notrace", None), ("trace", BASE_TRACE)])
def test_without_an_inventory_digest_prompt_and_context_are_the_base_bytes(name, trace) -> None:
    d = build_digest(racy.RUNS_SPEC, trace)
    r = retrieve(d, "Can ben cancel ann's run?", [])
    got = tuple(hashlib.sha256(x.encode()).hexdigest() for x in (d.text, r.system, r.context))
    assert got == GOLD[name]
    assert d.impl is None and SYSTEM_IMPL not in r.system and "Implemented app" not in r.system
    assert r.system == SYSTEM + "\n" + core_text(d)


def test_without_an_inventory_the_answer_dict_has_exactly_the_base_keys() -> None:
    g = ScriptedGenerator(
        [
            reply(
                "yes",
                "implemented_verified",
                [cite("rule:R1", "A config has at most one live run")],
            )
        ]
    )
    a = ask(racy.RUNS_SPEC, "q?", trace=BASE_TRACE, generator=g, model="m")
    assert set(a.to_dict()) == {"kind", "answer", "explanation", "citations", "citations_checked",
                                "rejected_citations", "verified", "usage"}  # fmt: skip
    assert a.implementation is None and a.implementation_basis is None
    assert "implementation:" not in a.render()


def test_without_an_inventory_a_code_citation_is_rejected_as_before() -> None:
    d = build_digest(racy.RUNS_SPEC)
    why = check_citation(d, cite("code:app/routes.py:3", "def create_run"))
    assert why == "no element 'code:app/routes.py:3' in the spec"


# ---- the digest section -------------------------------------------------------------------


def test_the_digest_gains_an_implemented_app_section(inv) -> None:
    d = build_digest(SPEC, TRACE, inv)
    assert d.impl is not None
    kinds = {e.kind for e in d.elements.values() if e.kind.startswith("impl_")}
    assert kinds == {"impl_route", "impl_machine", "impl_rule", "impl_flow"}
    assert "## Implemented app (extracted from the code; static unless marked observed)" in d.text
    line = d.elements["impl:route:POST /api/projects"].content
    assert line.startswith("implemented_verified; auth Customer; registered code:api/api.go:18")
    assert "handler code:api/api.go" in line and "2xx 201" in line and "required by " in line
    assert "refuses 401 unauthorized" in line and "flows " in line
    spec_only = d.elements["impl:route:POST /api/projects/{}/restore"].content
    assert spec_only.startswith("spec_only:")
    assert d.elements["impl:rule:R4"].content.startswith(
        "required_not_implemented; missing GET /api/tickets/export"
    )
    assert d.elements["impl:rule:R6"].content == "no_element"
    mach = d.elements["impl:machine:projects.status"].content
    assert (
        "states archived, draft, published" in mach
        and "status confirmed" in mach
        and "rules R3" in mach
    )


def test_the_core_holds_the_section_flow_heads_only_and_the_system_gets_one_paragraph(inv) -> None:
    d = build_digest(SPEC, TRACE, inv)
    r = retrieve(d, "Can a project be published?")
    assert r.system == SYSTEM + "\n" + SYSTEM_IMPL + "\n" + core_text(d)
    core = "\n".join(impl_core(d))
    fid = flow_target(inv, ["POST /api/projects", "POST /api/projects/{}/publish"])
    assert (
        f"[{fid}] create projects > publish projects (inferred name); kind pair; status verified"
        in core
    )
    assert "as ann ->" not in core  # steps are retrieved per question
    assert fid in r.context and "POST /api/projects/{}/publish as ann -> " in r.context
    assert fid in r.sent


def test_the_core_section_is_within_budget_and_drops_refusal_codes_first(inv) -> None:
    d = build_digest(SPEC, TRACE, inv)
    assert approx_tokens("\n".join(impl_core(d))) <= IMPL_CORE_BUDGET
    assert any("refuses" in e.content for e in d.elements.values() if e.kind == "impl_route")
    ex = copy.deepcopy(EXTRACT)
    for r in ex["routes"]:  # a route with an enormous refusal list
        r["raises"] = [
            {"code": f"code_{i}_" + "x" * 60, "status": 400 + i % 90, "file": "a.go", "line": 1}
            for i in range(400)
        ]
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        shutil.copytree(FIX / "app", t / "app")
        capture(t / "app", t / "inv", extract=ex)
        big = build_digest(SPEC, TRACE, load_inventory(t / "inv", t / "app"))
    assert not any("refuses" in e.content for e in big.elements.values() if e.kind == "impl_route")


def test_flows_for_a_question_are_chosen_by_words_rules_and_selection_within_budget(inv) -> None:
    d = build_digest(SPEC, TRACE, inv)
    r = retrieve(d, "Can a job be activated?")
    jobs = flow_target(inv, ["POST /api/jobs", "POST /api/jobs/{}/activate"])
    assert jobs in r.context
    sel = retrieve(d, "anything", ["rule:R5"])  # R5's route POST /api/projects pulls its flows in
    assert "Implemented flows relevant to the question" in sel.context
    assert sum(1 for t in d.elements if t.startswith("impl:flow:") and t in sel.sent) >= 1
    body = r.context.split("## Implemented flows relevant to the question", 1)[1]
    body = body.split("The user selected", 1)[0]
    assert approx_tokens(body) <= IMPL_BUDGET + 100


# ---- citations ----------------------------------------------------------------------------


def test_impl_targets_are_checked_like_other_elements(inv) -> None:
    d = build_digest(SPEC, TRACE, inv)
    ok = lambda t, q: check_citation(d, cite(t, q))  # noqa: E731
    assert ok("impl:route:POST /api/login", "registered code:api/api.go:16") is None
    assert "not in the element's text" in ok(
        "impl:route:POST /api/login", "registered code:api/api.go:99"
    )
    assert "no element" in ok("impl:route:GET /api/nothing", "anything at all")
    assert ok("impl:rule:R5", "required_not_implemented") is None
    assert ok("impl:machine:projects.status", "draft -> published") is None
    assert check_citation(d, {"target": "impl:route:POST /api/login", "step": 1, "quote": "x"}) == (
        "a step number is only for a scenario target"
    )


def test_a_flows_quoted_status_must_equal_its_status() -> None:
    # the static run: the flows are unverified; "verified" is a substring of "unverified"
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        shutil.copytree(FIX / "app", t / "app")
        stat = make_inv(t / "app", t, WF, "static")
        d = build_digest(SPEC, TRACE, stat)
        fid = flow_target(stat, ["POST /api/projects", "POST /api/projects/{}/publish"])
        assert check_citation(d, cite(fid, "status unverified")) is None
        assert "the flow's status is unverified" in check_citation(d, cite(fid, "status verified"))
        assert "the flow's status is unverified" in check_citation(d, cite(fid, "verified"))
        assert check_citation(d, cite(fid, "create projects > publish projects")) is None


def test_code_citations_go_through_the_checker(app: Path, inv) -> None:
    d = build_digest(SPEC, TRACE, inv)
    n = line_of(app, "api/api.go", 'r.POST("/api/login"')
    assert check_citation(d, cite(f"code:api/api.go:{n}", 'r.POST("/api/login"')) is None
    assert "not in api/api.go" in check_citation(
        d, cite(f"code:api/api.go:{n + 1}", 'r.POST("/api/login"')
    )
    assert check_citation(d, cite("code:../x.go:1", "package x // abc")) is not None
    assert (
        check_citation(d, {"target": f"code:api/api.go:{n}", "step": 3, "quote": "x"}) is not None
    )


def test_a_rejected_code_citation_triggers_the_repair_round(app: Path, inv) -> None:
    n = line_of(app, "api/api.go", 'r.POST("/api/login"')
    bad = reply(
        "yes", "implemented_unverified", [cite(f"code:api/api.go:{n + 2}", 'r.POST("/api/login"')]
    )
    good = reply(
        "yes", "implemented_unverified", [cite(f"code:api/api.go:{n}", 'r.POST("/api/login"')]
    )
    g = ScriptedGenerator([bad, good])
    a = ask(SPEC, "Can a user sign in?", trace=TRACE, generator=g, model="m", inventory=inv)
    assert (
        len(g.calls) == 2 and "failed the mechanical citation check" in g.calls[1][1][-1]["content"]
    )
    assert a.implementation == "implemented_unverified" and not a.rejected_citations


# ---- the label and its downgrades: ten scripted replies -----------------------------------


def test_ten_scripted_replies_one_per_downgrade_rule_and_citation_kind(
    app: Path, inv, tmp_path: Path
) -> None:
    pub = flow_target(inv, ["POST /api/projects", "POST /api/projects/{}/publish"])
    reg = line_of(app, "api/api.go", 'auth.POST("/projects/:id/publish"')
    reg_cite = cite(f"code:api/api.go:{reg}", 'auth.POST("/projects/:id/publish"')
    plain = line_of(app, "api/api.go", "type loginReq struct")
    plain_cite = cite(f"code:api/api.go:{plain}", "type loginReq struct")
    static = make_inv(app, tmp_path, WF, "static")
    spub = flow_target(static, ["POST /api/projects", "POST /api/projects/{}/publish"])
    stale = make_inv(app, tmp_path, observed(WF), "fresh")
    (app / "main.go").write_text((app / "main.go").read_text() + "\n// edited\n")
    stale = load_inventory(tmp_path / "fresh", app)  # now stale
    assert stale.stale is not None

    def v(label: str, *cites: dict[str, Any], kind: str = "yes") -> str:
        return reply(kind, label, list(cites))

    login_reg = cite("impl:route:POST /api/jobs", "registered code:api/api.go")
    proj_reg = cite("impl:route:POST /api/projects", "registered code:api/api.go")
    cases = [
        # (inventory, reply, expected label, expected basis)
        # 1 a valid verified flow and an evidence code location
        (inv, v("implemented_verified", cite(pub, "status verified"), reg_cite),
         "implemented_verified", "observed"),
        # 2 no evidence code: downgraded
        (inv, v("implemented_verified", cite(pub, "status verified")),
         "implemented_unverified", "static"),
        # 3 the code line is valid but is no evidence location
        (inv, v("implemented_verified", cite(pub, "status verified"), plain_cite),
         "implemented_unverified", "static"),
        # 4 the cited flow is not verified
        (static, v("implemented_verified", cite(spub, "status unverified"), reg_cite),
         "implemented_unverified", "static"),
        # 5 a stale inventory
        (stale, v("implemented_verified", cite(pub, "status unverified"), reg_cite),
         "implemented_unverified", "static"),
        # 6 no impl: or code: citation
        (inv, v("implemented_unverified", cite("rule:R2", "A user creates a project")),
         "unsettled", None),
        # 7 R5's restore route is missing
        (inv, v("required_not_implemented", cite("rule:R5", "A project can be restored"),
                kind="no"), "required_not_implemented", "inferred"),
        # 8 R2's endpoints are all present
        (inv, v("required_not_implemented", cite("rule:R2", "A user creates a project")),
         "unsettled", None),
        # 9 two distinct elements
        (inv, v("ambiguous", login_reg, proj_reg), "ambiguous", "static"),
        # 10 only one
        (inv, v("ambiguous", login_reg), "unsettled", None),
    ]  # fmt: skip
    for i, (iv, text, label, basis) in enumerate(cases, 1):
        a = ask(
            SPEC, "q?", trace=TRACE, generator=ScriptedGenerator([text]), model="m", inventory=iv
        )
        assert (a.implementation, a.implementation_basis) == (label, basis), (i, a.render())
    # not_specified_not_implemented is accepted (basis inferred); an invalid label is unsettled
    a = run(inv, reply("not_specified", "not_specified_not_implemented"))
    assert (a.kind, a.implementation, a.implementation_basis) == (
        "not_specified",
        "not_specified_not_implemented",
        "inferred",
    )
    a = run(inv, reply("not_specified", "maybe"))
    assert a.implementation == "unsettled" and a.implementation_basis is None
    a = run(inv, reply("not_specified"))  # no label: no key
    assert a.implementation is None and "implementation" not in a.to_dict()
    a = run(
        inv,
        reply("not_specified", "not_specified_not_implemented", implementation_basis="observed"),
    )
    assert a.implementation_basis == "inferred"  # the model's own basis is never trusted


def test_a_rule_status_citation_can_settle_a_verified_label(app: Path, inv) -> None:
    reg = line_of(app, "api/api.go", 'auth.POST("/projects"')
    a = run(
        inv,
        reply(
            "yes",
            "implemented_verified",
            [
                cite("impl:rule:R2", "implemented_verified"),
                cite(f"code:api/api.go:{reg}", 'auth.POST("/projects"'),
            ],
        ),
    )
    assert a.implementation == "implemented_verified"


def test_kind_is_unchanged_and_the_old_downgrade_still_holds(inv) -> None:
    a = run(
        inv, [reply("yes", "implemented_verified", []), reply("yes", "implemented_verified", [])]
    )
    assert a.kind == "not_specified" and "not grounded" in a.explanation
    assert a.implementation == "unsettled"
    assert ask_mod.KINDS == ("yes", "no", "value", "not_specified")


def test_the_answer_carries_the_label_in_to_dict_and_render(inv) -> None:
    a = run(inv, reply("not_specified", "not_specified_not_implemented"))
    d = a.to_dict()
    assert (
        d["implementation"] == "not_specified_not_implemented"
        and d["implementation_basis"] == "inferred"
    )
    assert "implementation: not_specified_not_implemented (inferred)" in a.render()


# ---- verify: replay a cited flow ----------------------------------------------------------


class Fail:
    layer = "D"
    case = "ask_verify / step 2"
    details = {
        "expected": {"status": 204},
        "actual": {"status": 404, "body": {"code": "not_found"}},
        "problems": ["status: expected 204, got 404"],
    }


def verify_cfg() -> ask_mod.VerifyConfig:
    return ask_mod.VerifyConfig(app=lambda *a: None, db=lambda *a: None)


def test_a_cited_flow_is_replayed_with_no_model_call(inv, monkeypatch: pytest.MonkeyPatch) -> None:
    pub = flow_target(inv, ["POST /api/projects", "POST /api/projects/{}/publish"])
    seen: list[Any] = []

    def fake_run(spec, scenario, cfg):
        seen.append(scenario)
        return "pass", None

    monkeypatch.setattr(ask_mod, "_run", fake_run)
    g = ScriptedGenerator(
        [reply("yes", "implemented_unverified", [cite(pub, "create projects > publish projects")])]
    )
    a = ask(
        SPEC,
        "Can a project be published?",
        trace=TRACE,
        generator=g,
        model="m",
        inventory=inv,
        verify=verify_cfg(),
    )
    assert len(g.calls) == 1  # the ask itself; verify wrote no scenario
    assert (
        a.verified["status"] == "agrees"
        and a.verified["source"] == f"flow:{pub.removeprefix('impl:flow:')}"
    )
    assert [s.expect_status for s in seen[0].steps][:1] == [201]
    assert seen[0].steps[0].path == "/api/projects" and seen[0].steps[0].user == "ann"
    assert a.usage["rounds"] == 1


def test_a_replay_that_answers_otherwise_disagrees_with_expected_vs_actual(
    inv, monkeypatch
) -> None:
    pub = flow_target(inv, ["POST /api/projects", "POST /api/projects/{}/publish"])
    monkeypatch.setattr(ask_mod, "_run", lambda spec, sc, cfg: ("fail", Fail()))
    g = ScriptedGenerator([reply("yes", "implemented_unverified", [cite(pub, "create projects")])])
    a = ask(SPEC, "q?", trace=TRACE, generator=g, model="m", inventory=inv, verify=verify_cfg())
    assert a.verified["status"] == "disagrees" and "expected 204, got 404" in a.verified["detail"]
    assert len(g.calls) == 1


def test_a_replay_that_cannot_run_is_an_error(inv, monkeypatch) -> None:
    pub = flow_target(inv, ["POST /api/projects", "POST /api/projects/{}/publish"])
    monkeypatch.setattr(
        ask_mod, "_run", lambda spec, sc, cfg: ("error", "the check could not run: x")
    )
    g = ScriptedGenerator([reply("yes", "implemented_unverified", [cite(pub, "create projects")])])
    a = ask(SPEC, "q?", trace=TRACE, generator=g, model="m", inventory=inv, verify=verify_cfg())
    assert a.verified["status"] == "error" and a.verified["source"].startswith("flow:")


def test_without_a_cited_flow_verify_is_the_existing_one(inv, monkeypatch) -> None:
    """No impl:flow citation: the model writes the scenario, as before."""
    steps = {
        "steps": [{"method": "POST", "path": "/api/login", "user": None, "expect_status": 200}]
    }
    g = ScriptedGenerator(
        [
            reply(
                "yes",
                "implemented_unverified",
                [cite("impl:route:POST /api/login", "registered code:api/api.go:16")],
            ),
            json.dumps(steps),
        ]
    )
    monkeypatch.setattr(ask_mod, "_run", lambda spec, sc, cfg: ("pass", None))
    a = ask(SPEC, "q?", trace=TRACE, generator=g, model="m", inventory=inv, verify=verify_cfg())
    assert len(g.calls) == 2 and a.verified["status"] == "agrees" and "source" not in a.verified


# ---- the need-more round, the CLI, Studio -------------------------------------------------


def test_the_one_need_more_round_may_name_impl_targets(inv) -> None:
    jobs = flow_target(inv, ["POST /api/jobs", "POST /api/jobs/{}/activate"])
    pub = flow_target(inv, ["POST /api/projects", "POST /api/projects/{}/publish"])
    g = ScriptedGenerator(
        [
            json.dumps({"need": [jobs, "impl:flow:wf-nope"]}),
            reply("yes", "implemented_unverified", [cite(jobs, "activate")]),
        ]
    )
    a = ask(SPEC, "Can a project be published?", trace=TRACE, generator=g, model="m", inventory=inv)
    more = g.calls[1][1][-1]["content"]
    assert "POST /api/jobs/{}/activate as ann -> " in more and "impl:flow:wf-nope" in more
    assert a.implementation == "implemented_unverified"
    assert pub in ask_mod.build_digest(SPEC, TRACE, inv).elements


def test_the_ask_cli_takes_inventory_and_app_root(
    app: Path, inv, tmp_path: Path, capsys, monkeypatch
) -> None:
    mod = tmp_path / "m"
    mod.mkdir()
    shutil.copy(FIX / "fixture_spec.py", mod / "ask_cli_spec.py")
    (tmp_path / "t.json").write_text(TRACE.to_json())
    monkeypatch.syspath_prepend(str(mod))
    g = ScriptedGenerator([reply("not_specified", "not_specified_not_implemented")])
    code = ask_mod.main(
        [
            "--spec", "ask_cli_spec:SPEC", "--trace", str(tmp_path / "t.json"),
            "--inventory", str(inv.directory), "--app-root", str(app),
            "--json", "Can tickets be exported?",
        ],
        generator=g,
    )  # fmt: skip
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["implementation"] == "not_specified_not_implemented"
    assert "SYSTEM_IMPL"[:0] == "" and "Implemented app" in g.calls[0][0]
    # without --inventory: no key, the base prompt
    g2 = ScriptedGenerator([reply("not_specified")])
    assert (
        ask_mod.main(
            ["--spec", "ask_cli_spec:SPEC", "--trace", str(tmp_path / "t.json"), "--json", "q?"],
            generator=g2,
        )
        == 0
    )
    assert "implementation" not in json.loads(capsys.readouterr().out)
    assert "Implemented app" not in g2.calls[0][0]
    # a missing inventory is exit 2
    assert (
        ask_mod.main(
            ["--spec", "ask_cli_spec:SPEC", "--inventory", str(tmp_path / "none"), "q?"],
            generator=g2,
        )
        == 2
    )


def _project(tmp_path: Path, inv_dir: Path, app: Path, table: bool = True) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    shutil.copy(FIX / "fixture_spec.py", p / "spec.py")
    (p / "spec.trace.json").write_text(TRACE.to_json())
    (p / "REQUIREMENTS.md").write_text(
        "# Fixture\n\n" + "\n".join(f"- {r.text}" for r in TRACE.rules) + "\n"
    )
    toml = f'[implementation]\ninventory = "{inv_dir}"\napp_root = "{app}"\n' if table else ""
    (p / "studio.toml").write_text(toml + '[llm]\nbackend = "stub"\n')
    return p


def test_studio_ask_passes_the_inventory_only_with_the_table(
    app: Path, inv, tmp_path: Path
) -> None:
    from lattice.studio.server import Studio

    on = Studio(
        _project(tmp_path, inv.directory, app),
        generator=ScriptedGenerator([reply("not_specified", "not_specified_not_implemented")]),
    )
    out = on.ask({"question": "Can tickets be exported?"})
    assert out["implementation"] == "not_specified_not_implemented"
    (tmp_path / "off").mkdir()
    off = Studio(
        _project(tmp_path / "off", inv.directory, app, table=False),
        generator=ScriptedGenerator([reply("not_specified", "not_specified_not_implemented")]),
    )
    out2 = off.ask({"question": "Can tickets be exported?"})
    assert "implementation" not in out2 and "implementation_basis" not in out2


def test_studio_ask_with_a_missing_inventory_is_422_naming_the_code(
    app: Path, inv, tmp_path: Path
) -> None:
    from lattice.studio.server import ApiError, Studio

    shutil.rmtree(inv.directory)
    s = Studio(
        _project(tmp_path, inv.directory, app),
        generator=ScriptedGenerator([reply("not_specified")]),
    )
    with pytest.raises(ApiError) as e:
        s.ask({"question": "q?"})
    assert e.value.detail.startswith("inventory_missing:") and int(e.value.status) == 422
