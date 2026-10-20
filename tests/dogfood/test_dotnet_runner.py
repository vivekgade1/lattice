"""The .NET runners (dogfood/dotnet_probes.py, dogfood/dotnet_mutants.py): their pure parts (matching, gates, the operator classifier, the ledger,
the self-hash check) and the Stage 1 rows against the real helper. No Docker, no held-out service; the rows of nodes B2-B4 are the self-test's."""

# ruff: noqa: E501
from __future__ import annotations

import ast
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dogfood"))

import dotnet_mutants as M  # noqa: E402
import dotnet_probes as G  # noqa: E402

needs_dotnet = pytest.mark.skipif(shutil.which("dotnet") is None, reason="needs the .NET SDK")


# ---- the row table -------------------------------------------------------------------------------------------------------------------------------------


def test_the_gated_row_count_is_the_preregistered_93() -> None:
    ids = G.GATED  # Stage 1: 21, 2: 5, 3: 6 (A-805 only on Q9 yes; K8 was refuted, DN805 is gated), 4: 9 + the 4 dnapp rows, 5: 16, Arm H: 19, Arm M: 2, cross: 10, P-J0: 1
    assert len(ids) == 93  # 89 + the four dnapp rows (Q8); 94 only with Q9 yes
    assert sum(1 for i in ids if i.startswith("H-")) == 19
    assert sum(1 for i in ids if i.startswith(("M-R",))) == 2
    assert {"H-PI5", "A-JUDGE-D1", "I-D1-5"}.isdisjoint(ids)


def test_every_row_has_a_function_and_a_known_arm_and_node() -> None:
    assert set(G.P) == set(G.ROW_FN) | {"A-DM"}
    assert {v[0] for v in G.P.values()} <= set(G.ARMS)
    assert {n for v in G.P.values() for n in v[3]} <= set(G.NODES)
    assert set(G.ORDER) == set(G.P) and G.ORDER[-1] == "PW"


def test_not_run_is_produced_only_by_one_row() -> None:
    tree = ast.parse((ROOT / G.RUNNER_REL).read_text())
    callers = {
        f.name
        for f in ast.walk(tree)
        if isinstance(f, ast.FunctionDef)
        for n in ast.walk(f)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "not_run"
    }
    assert callers == {"one_row"}


# ---- prerequisites: a NOT RUN only for a named one (RM1) -------------------------------------------------------------------------------------------------


def _ctx(tmp_path: Path, **kw: object) -> G.Ctx:
    ctx = G.Ctx(tmp_path / "s", tmp_path / "o")
    ctx.dotnet = True
    ctx.docker = ctx.driver_ok = True
    ctx.streak_src = ctx.dnapp_src = ctx.changes_dir = ROOT
    (tmp_path / "streak").mkdir(exist_ok=True)
    (tmp_path / "streak" / "spec.py").write_text("SPEC = 1\n")
    ctx.streak_src = tmp_path / "streak"
    ctx.h_target = {"label": "x", "runnable": True}
    ctx.j0_path = ROOT / "dogfood/dn_standin/j0_standin.md"
    ctx.base_dir = ROOT
    ctx.d1 = {"build_nuget": False}
    ctx.standin = set(G.NODES)
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def test_missing_names_the_prerequisite_and_none_when_present(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.standin = set(G.NODES)
    assert all(
        G.missing(ctx, i) in (None, "no go on PATH") or "go is not" in str(G.missing(ctx, i))
        for i in G.P
    )
    ctx.dotnet = False
    assert "SDK" in G.missing(ctx, "P0-ROUTES")
    ctx.dotnet, ctx.standin = True, set()
    assert "node B2" in (G.missing(ctx, "F-MODEL") or "") or G.node_present("B2")
    ctx.no_docker, ctx.docker = True, False
    assert "docker" in (G.missing(ctx, "M-R1") or "") or "B3" in (G.missing(ctx, "M-R1") or "")
    ctx.standin = set(G.NODES)
    ctx.self_test = True
    assert G.missing(ctx, "M-R1") is None  # the stand-in never starts a container


def test_rm1_a_not_run_with_every_prerequisite_present_is_a_problem(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    rows = [{"id": "P0-ROUTES", "pass": None}, {"id": "D-ROUTES", "pass": None}]
    assert len(G.self_test_problems(rows, ctx)) == 2
    ctx.dotnet = False
    assert (
        G.self_test_problems(rows, ctx) == []
    )  # a named prerequisite is missing: a legitimate NOT RUN
    assert G.selftest_rm1(_ctx(tmp_path))["not_run_only_in_one_row"]


# ---- the self-hash check (RM2) and the ledger ------------------------------------------------------------------------------------------------------------


def _freeze(tmp_path: Path, drop: str | None = None, edit: str | None = None) -> Path:
    lines = []
    for rel in G.must_list("shakedown"):
        f = ROOT / rel
        if f.is_file() and rel != drop:
            lines.append(f"{'0' * 64 if rel == edit else G.sha(f)}  {rel}")
    p = tmp_path / "f.sha256"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_the_freeze_check_passes_a_complete_list_and_catches_each_defect(tmp_path: Path) -> None:
    this = ROOT / G.RUNNER_REL
    assert G.verify_freeze(_freeze(tmp_path), this)["mismatches"] == 0
    for drop in (
        G.RUNNER_REL,
        G.MUTANTS_REL,
        "lattice/dncheck/src/Routes.cs",
        G.standin_files()[0],
    ):
        with pytest.raises(G.FreezeError):
            G.verify_freeze(_freeze(tmp_path, drop=drop), this)
    with pytest.raises(G.FreezeError):
        G.verify_freeze(_freeze(tmp_path, edit="lattice/dncheck/src/Routes.cs"), this)
    assert G.selftest_rm2()["caught"]


def test_a_scored_run_must_list_every_node_file_a_shakedown_only_those_present() -> None:
    assert set(G.must_list("shakedown")) <= set(G.must_list("scored"))
    assert "lattice/dncheck/policy.py" in G.must_list("scored")
    assert ("lattice/dncheck/policy.py" in G.must_list("shakedown")) == (
        ROOT / "lattice/dncheck/policy.py"
    ).is_file()


def test_the_ledger_refuses_a_second_scored_run_and_an_unjustified_rerun() -> None:
    e: list[dict] = []
    assert G.ledger_refusal(e, "DN:0", "scored", "r", "f") is None
    e += [
        {
            "arm": "DN:0",
            "kind": "scored",
            "run_id": "a",
            "status": "started",
            "runner_sha256": "r",
            "freeze_sha256": "f",
        }
    ]
    assert "already has a scored run" in G.ledger_refusal(e, "DN:0", "scored", "r", "f")
    assert "not crash_before_metric" in G.ledger_refusal(
        e, "DN:0", "rerun", "r", "f"
    )  # still `started`
    e += [
        {
            "arm": "DN:0",
            "kind": "scored",
            "run_id": "a",
            "status": "crash_before_metric",
            "runner_sha256": "r",
            "freeze_sha256": "f",
        }
    ]
    assert G.ledger_refusal(e, "DN:0", "rerun", "r", "f") is None
    assert "frozen bytes" in G.ledger_refusal(e, "DN:0", "rerun", "r2", "f")
    e += [
        {
            "arm": "DN:0",
            "kind": "rerun",
            "run_id": "b",
            "status": "completed",
            "runner_sha256": "r",
            "freeze_sha256": "f",
        }
    ]
    assert "one rerun" in G.ledger_refusal(e, "DN:0", "rerun", "r", "f")
    assert (
        G.ledger_refusal(e, "DN:0", "shakedown", "x", "y") is None
        and G.ledger_refusal(e, "DN:LM", "mutants", "x", "y") is None
    )
    assert (
        G.LEDGER.name == "ledger.jsonl" and G.LEDGER.parent == G.DATA
    )  # fixed, independent of --out


def test_rows_cli_refuses_to_run_in_the_real_worktree() -> None:
    with pytest.raises(SystemExit, match="scratch copies"):
        G.rows_cli(["--out", "x", "--ids", "P0-ROUTES"])


# ---- the held-out operator classifier (the post hoc lesson of Go round 3) -----------------------------------------------------------------------------------


def test_the_operator_is_read_from_the_operators_cell_by_exact_name() -> None:
    assert M.classify_operator("owner_read") == "owner_read"
    assert (
        M.classify_operator("Drop the owner condition of an owner-scoped write or state change")
        == "owner_write"
    )
    assert M.classify_operator("`status_403`") == "status_403"
    assert M.classify_operator("operator=drop_login") == "drop_login"
    # Go round 3: a free text that merely mentions a 403 or a login names no operator
    assert M.classify_operator("drops the owner filter (removes the 403 owner check)") is None
    assert M.classify_operator("answers 403") is None and M.classify_operator("login") is None


def test_mutants_md_operator_cell_and_the_fallback_to_the_id() -> None:
    md = "| id | operator | route | file:line | substitutions |\n|---|---|---|---|---|\n| HM1 | owner_read | GET /v1/notes/{id} | A/B.cs:12 | none |\n| HM2 | drop_login | GET /v1/x | A/B.cs:30 | none |\n"
    rows = M.parse_mutants_md(md)
    assert (
        rows[1].operator_cell == "owner_read"
        and rows[1].path == "/v1/notes/{id}"
        and rows[1].file_line == 12
    )
    assert M.operator_of(1, rows[1].operator_cell)["source"] == "list"
    shifted = M.operator_of(
        2, rows[2].operator_cell
    )  # the instance rule: HM2 may be another operator than its id's
    assert shifted["key"] == "drop_login" and shifted["shifted"] is True
    dm = M.parse_mutants_md("HM1 | GET /v1/habits/{id} | Application/HabitService.cs:109 | text\n")
    assert dm[1].operator_cell is None and M.operator_of(1, None)["source"] == "id"


def test_an_unregistered_operator_cell_is_an_input_error(tmp_path: Path) -> None:
    p = tmp_path / "p"
    p.mkdir()
    for i in M.IDS:
        (p / M.patch_name(i)).write_text("")
    lines = [f"| HM{i} | {M.INDEX_OPERATOR[i]} | GET /a | x.cs:1 | none |" for i in M.IDS]
    lines[2] = "| HM3 | drops the 403 check | GET /a | x.cs:1 | none |"
    (p / "MUTANTS.md").write_text("\n".join(lines) + "\n")
    t = tmp_path / "t.json"
    t.write_text(json.dumps({"routes": [{"method": "GET", "template": "/a"}]}))
    with pytest.raises(SystemExit, match="names no registered operator"):
        M.validate_inputs(p, t)


def test_gate_report_class_gates_and_the_overall_gate() -> None:
    ok = dict.fromkeys(range(1, 17), True)
    assert M.gate_report(ok)["verdict"] == "pass"
    three = {**ok, 12: False, 13: False, 14: False}
    assert M.gate_report(three)["verdict"] == "pass" and M.gate_report(three)["killed"] == 13
    assert M.gate_report({**three, 15: False})["verdict"] == "fail"  # 12 of 16
    assert M.gate_report({**ok, 1: False, 2: False})["classes"]["HM1-4"]["pass"] is False
    assert (
        M.gate_report({**ok, 7: False})["verdict"] == "fail"
    )  # 15 of 16 but the HM7-8 class gate is 2 of 2
    assert M.gate_report({**ok, 5: None})["verdict"] == "incomplete"


def test_strict_control_fails_on_a_false_positive_a_refuted_item_or_a_dirty_copy() -> None:
    route = ("GET", "/a/{}")
    base = dict(
        routes=[route],
        registered_r=["R1 GET /a/{id}"],
        a_control=[],
        r_control={"R1 GET /a/{id}": "verified"},
        pristine_new=[],
        copy_equals_h1=True,
        injection_works=True,
    )
    assert M.judge_control(**base)["passes"]
    assert not M.judge_control(
        **{**base, "a_control": [{"code": "DN801", "key": "DN801|GET /a/{id}|Note"}]}
    )["passes"]
    assert not M.judge_control(**{**base, "r_control": {"R1 GET /a/{id}": "refuted"}})["passes"]
    assert not M.judge_control(**{**base, "pristine_new": [{"code": "DN801", "key": "k"}]})[
        "passes"
    ]
    assert not M.judge_control(**{**base, "copy_equals_h1": False})["passes"]
    assert not M.judge_control(**{**base, "injection_works": False})["passes"]


def test_a_detector_flips_only_when_armed() -> None:
    route = ("GET", "/a/{}")
    new = [{"code": "DN801", "key": "DN801|GET /a/{id}|Note"}]
    assert G.eval_a("DN801", route, [], new)["flipped"]
    assert not G.eval_a("DN801", route, new, new)["flipped"]  # the control already has it
    ctl = {"R1 GET /a/{id}": "verified", "R3 GET /a/{id} 404 x": "verified"}
    assert G.eval_r("R1", route, ctl, {"R1 GET /a/{id}": "refuted"})["flipped"]
    assert not G.eval_r(
        "R1", route, {"R1 GET /a/{id}": "unexercised"}, {"R1 GET /a/{id}": "refuted"}
    )["flipped"]
    assert not G.eval_r("R3", route, ctl, {"R3 GET /a/{id} 404 x": "refuted"}, [403])["flipped"]
    assert G.eval_r("R3", route, ctl, {"R3 GET /a/{id} 404 x": "refuted"}, [404])["flipped"]
    assert not G.eval_r(
        "R1", route, ctl, {"R1 GET /a/{id}": "refuted"}, unstable=["R1 GET /a/{id}"]
    )["flipped"]


def test_the_library_mutant_edits_that_exist_apply_exactly_once() -> None:
    for mid, spec in M.load_lm().items():
        for rel, old, _new in spec["edits"]:
            assert (ROOT / rel).read_text().count(old) == 1, (mid, rel)
    assert set(M.load_lm()) == set(M.LM_IDS)
    assert {
        m for m, s in M.load_lm().items() if not s["edits"]
    } == set()  # LM20, LM21, LM23, LM24 filled by node S


# ---- the 4.5 scoring on synthetic inputs -------------------------------------------------------------------------------------------------------------------

TRUTH = {
    "routes": [
        {
            "method": "GET",
            "template": "/v1/Notes/{id}",
            "auth": "Member",
            "style": "controller",
            "file": "A.cs",
            "line": 10,
        },
        {
            "method": "POST",
            "template": "/login",
            "auth": "Public",
            "style": "minimal",
            "file": "P.cs",
            "line": 20,
        },
        {
            "method": "GET",
            "template": "/gone",
            "auth": "Public",
            "style": "minimal",
            "file": "P.cs",
            "line": 30,
        },
    ],
    "tables": [
        {
            "entity": "Note",
            "table": "notes",
            "columns": [
                {
                    "name": "id",
                    "wire": "id",
                    "private": False,
                    "nullable": False,
                    "unique": True,
                    "pk": True,
                    "max_length": None,
                    "fk": None,
                },
                {
                    "name": "hash",
                    "wire": None,
                    "private": True,
                    "nullable": False,
                    "unique": False,
                    "pk": False,
                    "max_length": 60,
                    "fk": None,
                },
            ],
        }
    ],
    "refusals": [
        {"route": "GET /v1/Notes/{id}", "status": 404, "cause": "not_found"},
        {"route": "POST /login", "status": 401, "cause": "auth"},
    ],
    "bounds": [
        {
            "route": "POST /login",
            "field": "email",
            "required": True,
            "ge": None,
            "le": None,
            "min_length": 1,
            "max_length": 254,
        }
    ],
    "owners": {"Note": "user_id"},
}


def _ex(**over: object) -> dict:
    ex = {
        "dialect": "sqlite", "dotnet": {"providers": ["sqlite"], "query_filters": []}, "unread": [], "excluded": [],
        "routes": [
            {"method": "GET", "path": "/v1/notes/{id:int}", "style": "controller", "auth": "Member", "owner_uses": [{"model": "Note"}], "params": [], "raises": [{"status": 404}], "unread": []},
            {"method": "POST", "path": "/login", "style": "minimal", "auth": "Public", "owner_uses": [], "raises": [{"status": 401}, {"status": 400}], "unread": [], "params": [{"name": "email", "wire": "email", "source": "body", "required": True, "min_length": 1, "max_length": 254, "ge": None, "le": None}]},
        ],
        "tables": [{"table": "notes", "columns": [{"name": "id", "wire": "id", "private": False, "nullable": False, "unique": False, "primary_key": True, "max_length": None, "foreign_key": None}, {"name": "hash", "wire": None, "private": True, "nullable": False, "unique": False, "primary_key": False, "max_length": 60, "foreign_key": None}]}],
    }  # fmt: skip
    ex.update(over)
    return ex


def test_matching_is_by_method_and_normalised_template() -> None:
    assert G.norm_template("/V1/Notes/{id:int}/") == "/v1/notes/{}"
    s = G.score_routes(TRUTH, _ex())
    assert s["recall"] == 0.6667 and s["missing"] == ["GET /gone"] and s["extra"] == []
    assert G.score_auth(TRUTH, _ex())["share_of_matched"] == 1.0


def test_unread_names_a_dropped_route_by_file_and_line() -> None:
    assert G.score_unread(TRUTH, _ex())["n"] == 1
    assert G.score_unread(TRUTH, _ex(unread=[{"file": "P.cs", "line": 31}]))["n"] == 0
    assert G.score_unread(TRUTH, _ex(unread=[{"file": "Q.cs", "line": 30}]))["n"] == 1


def test_tables_wire_raise_bounds_and_own() -> None:
    t = G.score_tables(TRUTH, _ex())
    assert t["table_recall"] == 1.0 and t["fact_accuracy"] == 1.0  # a pk counts as unique
    assert G.score_wire(TRUTH, _ex())["share"] == 1.0
    r = G.score_raise(TRUTH, _ex())
    assert (
        r["precision"] == 0.6667
        and r["recall"] == 1.0
        and r["false_positives"] == ["POST /login 400"]
    )
    b = G.score_bounds(TRUTH, _ex())
    assert (
        b["precision"] == 1.0 and b["recall"] == 1.0
    )  # min_length, max_length and required: the truth has the same three
    extra = _ex()
    extra["routes"][1]["params"][0]["max_length"] = 99
    assert G.score_bounds(TRUTH, extra)["precision"] < 1.0
    own = G.score_own(TRUTH, _ex())
    assert own["share"] == 1.0 and own["evidence_on_tables_truth_says_are_not_owned"] == []
    bad = _ex()
    bad["routes"][1]["owner_uses"] = [{"model": "Tag"}]
    assert (
        G.score_own(TRUTH, bad)["evidence_on_tables_truth_says_are_not_owned"]
        == ["Tag", "Note"][:1]
        or "Tag" in G.score_own(TRUTH, bad)["evidence_on_tables_truth_says_are_not_owned"]
    )


def test_table_names_are_exact_on_sqlite_and_case_insensitive_on_mysql() -> None:
    ex = _ex()
    ex["tables"][0]["table"] = "Notes"
    assert G.score_tables(TRUTH, ex)["table_recall"] == 0.0
    ex["dialect"], ex["dotnet"]["providers"] = "mysql", ["mysql"]
    assert G.score_tables(TRUTH, ex)["table_recall"] == 1.0


def test_citation_scan_and_check(tmp_path: Path) -> None:
    (tmp_path / "A").mkdir()
    (tmp_path / "A/B.cs").write_text("one\nvar x = db.Habits.Where(h => h.UserId == id);\n")
    cites = G.scan_citations("see code:A/B.cs:2 and code:A/B.cs:9 and code:Z.cs:1")
    r = G.check_citation_lines(tmp_path, sorted(cites), {("A/B.cs", 2): "db.Habits.Where"})
    assert r["checked"] == 3 and sorted(r["bad"]) == ["A/B.cs:9 no line", "Z.cs:1 no file"]
    assert G.check_citation_lines(tmp_path, [("A/B.cs", 2)], {("A/B.cs", 2): "not there"})[
        "bad"
    ] == ["A/B.cs:2 quote does not match"]


# ---- the Stage 1 rows against the real helper (dnparity1 and D1) -----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ctx(tmp_path_factory: pytest.TempPathFactory):
    import os

    cache = tmp_path_factory.mktemp("cache")
    keep = os.environ.get("LATTICE_DNCHECK_CACHE")
    os.environ["LATTICE_DNCHECK_CACHE"] = str(cache)
    scratch = tmp_path_factory.mktemp("scratch")
    c = G.Ctx(scratch, scratch / "out")
    c.runs = 2
    c.dotnet = shutil.which("dotnet") is not None
    c.d1 = G.materialise("d1", G.D1_TGZ, scratch / "targets", G.D1_TRUTH)
    yield c
    if keep is None:
        os.environ.pop("LATTICE_DNCHECK_CACHE", None)
    else:
        os.environ["LATTICE_DNCHECK_CACHE"] = keep


@needs_dotnet
@pytest.mark.parametrize(
    "fn",
    [
        G.p0_routes,
        G.p0_auth,
        G.p0_excl,
        G.p0_bind,
        G.p0_tables,
        G.p0_owner,
        G.p0_state,
        G.p0_raise,
        G.p0_di,
        G.p0_json,
        G.det_10,
        G.det_path,
    ],
)
def test_arm_0_rows_are_exact_on_dnparity1(ctx, fn) -> None:
    r = fn(ctx)
    assert r["pass"] is True, r["observed"]


@needs_dotnet
@pytest.mark.parametrize(
    "fn", [G.d_routes, G.d_auth, G.d_tables, G.d_wire, G.d_own, G.d_raise, G.d_unread, G.d_x]
)
def test_the_d1_dev_rows_pass(ctx, fn) -> None:
    assert fn(ctx)["pass"] is True


@needs_dotnet
def test_the_standin_truth_of_dnparity1_is_in_the_held_out_schema_and_scores_exactly(ctx) -> None:
    t = json.loads((ROOT / "dogfood/dn_standin/dnparity1/truth.json").read_text())
    assert {
        "routes",
        "tables",
        "owners",
        "owner_via",
        "machines",
        "refusals",
        "bounds",
        "bind_status",
        "json_naming",
        "login",
        "seed_users",
        "not_returned",
    } <= set(t)
    ex = G.extract(ctx, G.FIXTURE)
    assert G.score_routes(t, ex)["recall"] == 1.0 and not G.score_routes(t, ex)["extra"]
    assert (
        G.score_auth(t, ex)["share_of_matched"] == 1.0
        and G.score_tables(t, ex)["fact_accuracy"] == 1.0
    )


@needs_dotnet
def test_the_target_is_never_written_by_the_rows(ctx) -> None:
    before = G.snapshot(G.FIXTURE)
    G.p0_routes(ctx)
    G.det_10(ctx)
    assert (
        G.snapshot(G.FIXTURE) == before
        and not (G.REPO / "lattice/dncheck/src/bin").exists()
        and not (G.REPO / "lattice/dncheck/src/obj").exists()
    )


# ---- main(): the freeze check, the ledger and the evidence files, end to end on two rows -------------------------------------------------------------------


@needs_dotnet
def test_main_verifies_the_freeze_writes_the_ledger_and_the_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(G, "LEDGER", ledger)
    monkeypatch.setenv("LATTICE_DNCHECK_CACHE", str(tmp_path / "cache"))
    freeze = tmp_path / "dn.sha256"
    G.write_freeze(freeze)
    out = tmp_path / "out"
    argv = [
        "--arm",
        "0",
        "--only",
        "P0-ROUTES,P0-AUTH",
        "--freeze",
        str(freeze),
        "--out",
        str(out),
        "--scratch",
        str(tmp_path / "scr"),
        "--runs",
        "2",
    ]
    assert G.main(argv) == 0
    doc = json.loads((out / "probes-dn.json").read_text())
    assert [r["id"] for r in doc["results"]] == ["P0-ROUTES", "P0-AUTH"] and all(
        r["pass"] for r in doc["results"]
    )
    assert doc["freeze"]["mismatches"] == 0 and doc["lattice_file"].startswith(str(ROOT))
    assert (out / "evidence" / "P0-ROUTES.json").is_file()
    entries = [json.loads(ln) for ln in ledger.read_text().splitlines()]
    assert (
        [e["status"] for e in entries] == ["started", "completed"]
        and entries[0]["arm"] == "DN:0"
        and entries[0]["runner_sha256"] == G.sha(ROOT / G.RUNNER_REL)
    )
    # an edited frozen file aborts before any metric, and the ledger records the crash
    bad = tmp_path / "bad.sha256"
    bad.write_text(
        freeze.read_text().replace(G.sha(ROOT / "lattice/dncheck/src/Routes.cs"), "0" * 64)
    )
    assert (
        G.main(
            [
                "--arm",
                "0",
                "--only",
                "P0-ROUTES",
                "--freeze",
                str(bad),
                "--out",
                str(tmp_path / "o2"),
                "--scratch",
                str(tmp_path / "s2"),
            ]
        )
        == 2
    )
    assert json.loads(ledger.read_text().splitlines()[-1])["status"] == "crash_before_metric"
    # a scored run of the same arm is refused once it has been scored, and the freeze file is required
    with pytest.raises(SystemExit, match="--freeze is required"):
        G.main(["--arm", "0", "--out", str(tmp_path / "o3")])
