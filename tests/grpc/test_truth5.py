"""Round 5, Stage A: the truth /5 reader and the code-name table (H2), links in the truth replay (H3),
the layout rule (H5), the contract check (H6) and the 3-site library consistency fix (H1b).

The replay and the contract check run the stand-in ledger app ``tests/grpc/fixtures/harness5/sqlite``
(a harness written from HARNESS_API.md alone) and need the Go toolchain."""

from __future__ import annotations

# ruff: noqa: E501 -- data lines
import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.grpc.conftest import needs_go

DOGFOOD = Path(__file__).resolve().parents[2] / "dogfood"
sys.path.insert(0, str(DOGFOOD))

import grpc_contract5 as c5  # noqa: E402
import grpc_truth5 as t5  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
APP = FIX / "harness5" / "sqlite"
PROBES = FIX / "truth5" / "probe-flows.json"


def raw_truth() -> dict[str, Any]:
    return json.loads((APP / "truth.json").read_text())


# ---- H2 ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("num", "upper", "go"), t5.CODE_ROWS)
def test_h2_every_spelling_of_the_17_rows_maps(num: int, upper: str, go: str) -> None:
    for spelling in (upper, upper.lower(), go, num, str(num)):
        assert t5.normalise_code(spelling) == upper


def test_h2_the_two_cancel_spellings() -> None:
    assert t5.normalise_code("CANCELLED") == "CANCELLED"
    assert t5.normalise_code("CANCELED") == "CANCELLED"
    assert (
        t5.normalise_code("Canceled") == "CANCELLED"
    )  # grpc-go's String(), not the google.rpc.Code name


@pytest.mark.parametrize(
    "bad",
    [
        "FAILEDPRECONDITION",
        "failedPrecondition",
        17,
        -1,
        "",
        None,
        True,
        1.0,
        "01",
        " 3",
        "NotAFound",
    ],
)
def test_h2_refuses_with_the_json_path(bad: Any) -> None:
    with pytest.raises(t5.TruthError) as err:
        t5.normalise_code(bad, "$.rpcs[2].codes[0]")
    assert str(err.value) == f"unknown gRPC code {bad!r} at $.rpcs[2].codes[0]"


def test_h2_is_applied_to_every_code_the_chain_reads() -> None:
    raw = raw_truth()
    raw["rpcs"][2]["codes"] = ["AlreadyExists", "InvalidArgument"]
    raw["rpcs"][2]["code_sites"] = [{"code": "Internal", "at": "x.go:1", "via": "inline"}]
    raw["rpcs"][2]["interceptor_codes"] = ["Unauthenticated"]
    raw["flows"][0]["steps"][3]["expect"]["code"] = "FailedPrecondition"
    out, n = t5.normalise_codes_in(raw)
    assert n == 5
    assert out["rpcs"][2]["codes"] == ["ALREADY_EXISTS", "INVALID_ARGUMENT"]
    assert out["rpcs"][2]["code_sites"][0]["code"] == "INTERNAL"
    assert out["rpcs"][2]["interceptor_codes"] == ["UNAUTHENTICATED"]
    assert out["flows"][0]["steps"][3]["expect"]["code"] == "FAILED_PRECONDITION"


def test_h2_a_refused_code_is_named_before_anything_else() -> None:
    raw = raw_truth()
    raw["flows"][1]["steps"][0]["expect"]["code"] = "NOPE"
    with pytest.raises(
        t5.TruthError, match=r"unknown gRPC code 'NOPE' at \$\.flows\[1\]\.steps\[0\]\.expect\.code"
    ):
        t5.normalise_codes_in(raw)


# ---- the reader ------------------------------------------------------------------------------------------


def test_load_truth_reads_the_fixture() -> None:
    t = t5.load_truth(APP / "truth.json")
    assert t["codes_normalised"] == 0 and t["step_index_base"] == 0
    assert (
        t["rpcs"]["ledger.v1.Entries/Watch"]["state"] == "streaming"
    )  # the library's state for a streaming rpc
    assert t["rpcs"]["ledger.v1.Reports/Summary"]["state"] == "not_registered"
    assert "OK" not in t["rpcs"]["ledger.v1.Entries/Get"]["codes"]
    flow = t["flows"][0]
    assert (
        flow["steps"][3]["expect_code"] == "FAILED_PRECONDITION"
        and flow["steps"][0]["expect"]["tags.#"] == 2
    )
    assert t["links"][flow["name"]][0] == {
        "step": 2,
        "field": "id",
        "from_step": 1,
        "from_field": "id",
    }
    assert t["flow_links"][flow["name"]][0] == {
        "from_step": 0,
        "from_field": "id",
        "to_step": 1,
        "to_field": "id",
    }
    assert t["tables_by_rpc"]["ledger.v1.Entries/Create"] == ["entries", "sessions"]


def test_strict_reader_refuses_a_v4_document_and_the_dev_reader_reads_it() -> None:
    raw = raw_truth()
    raw["schema"] = t5.SCHEMA_4
    raw["tables"][0]["ddl"] = "go"
    raw["rpcs"][2]["codes"] = ["AlreadyExists", "InvalidArgument"]
    p = Path(__file__).with_name("_tmp_truth4.json")
    try:
        p.write_text(json.dumps(raw))
        with pytest.raises(t5.TruthError, match="schema must be"):
            t5.load_truth(p)
        t = t5.load_truth(p, strict=False)
        assert t["codes_normalised"] == 2 and t["rpcs"]["ledger.v1.Entries/Create"]["codes"] == {
            "ALREADY_EXISTS",
            "INVALID_ARGUMENT",
        }
    finally:
        p.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda r: r["conventions"].pop("step_index_base"), "step_index_base is required"),
        (lambda r: r["tables"][0].update(ddl="go"), "tables[0].ddl must be one of"),
        (
            lambda r: r["flows"][0]["links"].append(
                {"from_step": 2, "from_field": "id", "to_step": 1, "to_field": "id"}
            ),
            "outside the flow or not forward",
        ),
        (lambda r: r["flows"][1].update(name=r["flows"][0]["name"]), "duplicate flow name"),
        (
            lambda r: r["flows"][0]["steps"][0].update(rpc="ledger.v1.Entries/Nope"),
            "is not in rpcs",
        ),
        (lambda r: r["flows"][0]["steps"][0].update(**{"as": "zed"}), "is not in users"),
        (lambda r: r["flows"][0]["steps"][0].pop("expect"), "expect must be an object with a code"),
        (lambda r: r["rpcs"][0].update(state="served"), "state must be one of"),
        (lambda r: r["login"].pop("token_field"), "login must have"),
        (lambda r: r.update(schema="lattice.heldout.truth/3"), "schema must be"),
    ],
)
def test_schema_errors_name_the_first_problem(edit: Any, message: str) -> None:
    raw = raw_truth()
    edit(raw)
    errs = t5.schema_errors(raw)
    assert errs and message in errs[0]


# ---- H3: paths, links, comparison ------------------------------------------------------------------------


def test_dotted_paths() -> None:
    doc = {"a": {"b": [{"c": 1}, {"c": 2}]}, "n": 7}
    assert t5.get_path(doc, "a.b.1.c") == 2
    assert t5.get_path(doc, "a.b.#") == 2
    assert t5.get_path(doc, "a.#") == 1
    assert t5.get_path(doc, "a.x") is t5.MISSING and t5.get_path(doc, "n.#") is t5.MISSING
    out: dict[str, Any] = {}
    t5.set_path(out, "p.q.r", 5)
    assert out == {"p": {"q": {"r": 5}}}
    out2: dict[str, Any] = {"l": [{"x": 1}]}
    t5.set_path(out2, "l.0.x", 9)
    assert out2 == {"l": [{"x": 9}]}


def test_int64_as_a_number_or_its_decimal_string() -> None:
    assert t5.values_equal("9007199254740993", 9007199254740993) and t5.values_equal(
        9007199254740993, "9007199254740993"
    )
    assert not t5.values_equal("12a", 12) and not t5.values_equal(True, 1)
    assert t5.values_equal([1, "2"], ["1", 2])


def test_links_in_either_index_base_and_the_deskd_alias() -> None:
    canon = {"from_step": 0, "from_field": "id", "to_step": 1, "to_field": "id"}
    assert t5.canonical_links(
        [{"from_step": 0, "from_field": "id", "to_step": 1, "to_field": "id"}], 0, 2
    ) == [canon]
    assert t5.canonical_links(
        [{"from_step": 1, "from_field": "id", "to_step": 2, "to_field": "id"}], 1, 2
    ) == [canon]
    assert t5.canonical_links(
        [{"step": 2, "field": "id", "from_step": 1, "from_field": "id"}], 0, 2
    ) == [canon]  # 1-based whatever the convention says
    with pytest.raises(t5.TruthError):
        t5.canonical_links(
            [{"from_step": 1, "from_field": "id", "to_step": 1, "to_field": "id"}], 0, 2
        )


# ---- H3: the replay (Go) ---------------------------------------------------------------------------------


def harness(app: Path = APP) -> dict[str, Any]:
    sys.path.insert(0, str(DOGFOOD))
    import grpc_probes5 as p5

    return p5.harness_of(app)


@needs_go
def test_every_flow_of_the_fixture_app_replays_with_links_applied() -> None:
    h = harness()
    t = t5.load_truth(APP / "truth.json")
    import grpc_probes5 as p5

    res = p5.replay_all(h, t)
    assert [r.label(0) for r in res] == ["ok"] * 4


@needs_go
def test_a_flow_that_passes_only_with_links() -> None:
    h = harness()
    doc = json.loads(PROBES.read_text())
    flows = {f["name"]: f for f in doc["flows"]}
    f = flows["links_needed"]
    flow = {
        "name": f["name"],
        "steps": [
            {
                "rpc": s["rpc"],
                "as": s["as"],
                "message": s["request"],
                "expect_code": s["expect"]["code"],
                "expect": {k: v for k, v in s["expect"].items() if k != "code"},
            }
            for s in f["steps"]
        ],
    }
    links = t5.canonical_links(f["links"], 0, len(f["steps"]))
    common = {"make_app": h["make_app"], "build_db": h["build_db"], "runtime": h["runtime"]}
    assert t5.replay_flow(flow, links, **common).ok
    stripped = t5.replay_flow(flow, [], **common)
    assert not stripped.ok and stripped.reason == "code_mismatch" and stripped.step == 1


@needs_go
@pytest.mark.parametrize("name", [f["name"] for f in json.loads(PROBES.read_text())["flows"]])
def test_probe_flows_are_labelled_as_the_fixture_states(name: str) -> None:
    h = harness()
    f = next(x for x in json.loads(PROBES.read_text())["flows"] if x["name"] == name)
    base = f.get("base", 0)
    flow = {
        "name": f["name"],
        "steps": [
            {
                "rpc": s["rpc"],
                "as": s["as"],
                "message": s["request"],
                "expect_code": s["expect"]["code"],
                "expect": {k: v for k, v in s["expect"].items() if k != "code"},
            }
            for s in f["steps"]
        ],
    }
    links = (
        []
        if f.get("strip_links")
        else t5.canonical_links(f.get("links", []), base, len(f["steps"]))
    )
    res = t5.replay_flow(
        flow, links, make_app=h["make_app"], build_db=h["build_db"], runtime=h["runtime"]
    )
    assert res.label(base) == f["replay"]
    if res.ok:
        assert len(res.steps) == len(f["steps"]) and res.steps[0]["service"] == "ledger.v1.Entries"


@needs_go
def test_recorded_steps_are_library_rpc_steps_with_the_links_substituted() -> None:
    h = harness()
    t = t5.load_truth(APP / "truth.json")
    flow = t["flows"][0]
    res = t5.replay_flow(
        flow,
        t["flow_links"][flow["name"]],
        make_app=h["make_app"],
        build_db=h["build_db"],
        runtime=h["runtime"],
    )
    steps = t5.to_rpc_steps(res)
    assert [s.method for s in steps] == ["Create", "Get", "Close", "Close"]
    assert steps[1].message == {"id": 2} and steps[3].expect_code == "FAILED_PRECONDITION"


# ---- H1b --------------------------------------------------------------------------------------------------


def _none_returning_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "app_none"
    shutil.copytree(APP, dst, ignore=shutil.ignore_patterns("__pycache__"))
    h = dst / "harness.py"
    text = h.read_text()
    assert "    con.close()\n    return path" in text
    h.write_text(text.replace("    con.close()\n    return path", "    con.close()"))
    return dst


@needs_go
def test_h1b_a_build_db_answering_none_means_the_path_it_was_given(tmp_path: Path) -> None:
    """``reconcile_with`` and ``run_rpc_flows`` follow ``check()``'s convention: the harness's own path."""
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd
    from lattice.spec.grpc import reconcile_with

    outs = []
    for app in (APP, _none_returning_copy(tmp_path)):
        h = harness(app)
        model = GoGrpcFrontEnd().extract(app.parent, app.name)
        outs.append(
            reconcile_with(
                model,
                make_app=h["make_app"],
                build_db=h["build_db"],
                runtime=h["runtime"],
                users=h["users"],
            )
        )
    assert outs[0] == outs[1]
    assert not [
        k for k, v in outs[1].items() if isinstance(v, dict) and v.get("reason") == "login_failed"
    ]


# ---- H5: the layout rule -----------------------------------------------------------------------------------


def _app_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "app"
    shutil.copytree(APP, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def test_layout_accepts_the_fixture_app(tmp_path: Path) -> None:
    assert t5.check_layout(_app_copy(tmp_path)) == []
    assert t5.check_layout(APP) == []


def test_layout_refuses_a_symlinked_app_and_a_symlink_in_the_tree(tmp_path: Path) -> None:
    app = _app_copy(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(app)
    assert "symlink" in t5.check_layout(link)[0]
    (app / "alias").symlink_to(".")
    assert any("symlink in the app tree: alias" in p for p in t5.check_layout(app))


def test_layout_checks_the_freeze_with_sha256sum_semantics(tmp_path: Path) -> None:
    app = _app_copy(tmp_path)
    (app / "harness.py").write_text((app / "harness.py").read_text() + "\n# edited\n")
    assert t5.check_layout(app) == ["freeze: changed harness.py"]
    freeze = app / "freeze.sha256"
    lines = freeze.read_text().splitlines()
    freeze.write_text(
        "\n".join(
            [
                *lines,
                "0" * 64 + "  /etc/passwd",
                "0" * 64 + "  ../x",
                "not a hash line",
                "0" * 64 + "  gone.go",
            ]
        )
        + "\n"
    )
    probs = t5.check_layout(app)
    assert "freeze: changed harness.py" in probs
    assert any("not relative" in p and "/etc/passwd" in p for p in probs) and any(
        "../x" in p for p in probs
    )
    assert any("improperly formatted" in p for p in probs) and "freeze: missing gone.go" in probs
    (app / "freeze.sha256").unlink()
    assert "missing freeze.sha256 at the app root" in t5.check_layout(app)


# ---- H6: the contract check -----------------------------------------------------------------------------------


@needs_go
def test_contract_check_prints_one_ok_line_per_fact() -> None:
    code, lines = c5.run(APP)
    assert code == 0
    assert lines[:8] == [
        "truth schema ok",
        "codes ok (0 normalised)",
        "import ok",
        "RUNTIME is process_runtime ok",
        "build_db returned the path ok",
        "make_app returned ProcessApp ok",
        "ProcessApp ready=grpc protos=1 exist ok",
        "server started ok",
    ]
    assert lines[8:10] == ["login ann ok", "login bob ok"]
    assert [ln for ln in lines[10:]] == [f"flow {f['name']} ok" for f in raw_truth()["flows"]]


def _patched(tmp_path: Path, old: str, new: str) -> Path:
    app = _app_copy(tmp_path)
    h = app / "harness.py"
    text = h.read_text()
    assert old in text
    h.write_text(text.replace(old, new))
    return app


@needs_go
@pytest.mark.parametrize(
    ("old", "new", "line"),
    [
        (
            'RUNTIME = process_runtime(framework="go", auth=AUTH)',
            "RUNTIME = AUTH",
            "RUNTIME is AuthDriver",
        ),
        ("    con.close()\n    return path", "    con.close()", "build_db returned NoneType"),
        (
            'username_field="name",\n    password_field="password",\n    token_field="token",\n)',
            '"token",\n)',
            "import TypeError: AuthDriver.rpc() takes 4 positional arguments",
        ),
        (
            '"ledger/v1/ledger.proto"',
            '"ledger/v1/gone.proto"',
            "ProcessApp ready=grpc protos=1 missing ledger/v1/gone.proto",
        ),
        (
            '"Login",\n    USERS,',
            '"Login",\n    {**USERS, "bob": ("bob", "wrong")},',
            "login bob UNAUTHENTICATED bad credentials",
        ),
    ],
)
def test_contract_check_names_the_failing_fact(
    tmp_path: Path, old: str, new: str, line: str
) -> None:
    code, lines = c5.run(_patched(tmp_path, old, new))
    assert code != 0
    assert any(ln.startswith(line) for ln in lines), lines
    assert not any("Traceback" in ln for ln in lines)


@needs_go
def test_contract_check_names_a_failing_flow_step_and_a_bad_code(tmp_path: Path) -> None:
    app = _app_copy(tmp_path)
    raw = json.loads((app / "truth.json").read_text())
    raw["flows"][0]["steps"][2]["expect"]["state"] = "OPEN"  # the entry is CLOSED after Close
    (app / "truth.json").write_text(json.dumps(raw))
    code, lines = c5.run(app)
    assert code != 0 and "flow create_get_close step 2: expect_mismatch (OK vs OK)" in lines
    raw = json.loads((app / "truth.json").read_text())
    raw["rpcs"][2]["codes"] = ["NOPE"]
    (app / "truth.json").write_text(json.dumps(raw))
    code, lines = c5.run(app)
    assert "codes unknown gRPC code 'NOPE' at $.rpcs[2].codes[0]" in lines


def test_contract_check_stops_at_a_truth_schema_error_without_a_traceback(tmp_path: Path) -> None:
    app = _app_copy(tmp_path)
    raw = copy.deepcopy(raw_truth())
    raw["conventions"] = {}
    (app / "truth.json").write_text(json.dumps(raw))
    code, lines = c5.run(app)
    assert code != 0 and lines[0].startswith("truth schema conventions.step_index_base is required")
