"""Scenario selection (``check(only=...)``, ``--only``; lattice.spec.selection) and the
report's ``timings``, on the mini-shop (tests/spec/minishop.py) and the racy toy apps
(tests/spec/racy.py). Without ``only`` the report is the one HEAD wrote, byte for byte,
but for the new ``timings`` key (tests/spec/golden: written before the change)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lattice.spec import Report, check
from lattice.spec.__main__ import main
from lattice.spec.selection import (
    SelectionError,
    parse_selector,
    read_only_file,
    select_scenarios,
)
from tests.spec import minishop, racy

SPEC = minishop.SPEC
CANCEL = "ann cancels her pending order"
ADD = "ann adds a line to a new order"
GOLDEN = Path(__file__).parent / "golden"
VARIANTS = ("ok", "partial_write", "wrong_answer", "total_drift", "wrong_quantity")
# the keys consumers (Studio, check.sh) read: all kept
REPORT_KEYS = {"verdict", "layers", "counts", "findings", "cases", "notes", "unchecked",
               "accepted", "stale_baseline"}  # fmt: skip


@pytest.fixture(scope="module")
def apps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, str]]:
    root = tmp_path_factory.mktemp("apps")
    return {v: minishop.write_app(root / v, v) for v in VARIANTS}


def run(apps: dict[str, tuple[Path, str]], variant: str, **kw: Any) -> Report:
    app_dir, package = apps[variant]
    return check(
        kw.pop("spec", SPEC),
        minishop.app_factory(package),
        minishop.build_db,
        app_dir=app_dir,
        package=package,
        login=minishop.login,
        **kw,
    )


def without_timings(report: Report) -> dict[str, Any]:
    data = json.loads(report.to_json())
    data.pop("timings")
    return data


# ---- selectors ------------------------------------------------------------------------------


def test_parse_selector_kinds():
    assert parse_selector("scenario:a b") == ("scenario", "a b")
    assert parse_selector("rule:R4") == ("rule", "R4")
    assert parse_selector("  plain name ") == ("scenario", "plain name")
    # a colon with an unknown prefix is part of a bare name or glob
    assert parse_selector("access:*") == ("scenario", "access:*")
    with pytest.raises(SelectionError):
        parse_selector("rule:")
    with pytest.raises(SelectionError):
        parse_selector("   ")


def test_select_by_name_kind_and_glob_in_spec_order():
    assert select_scenarios(SPEC, [ADD]) == [ADD]
    assert select_scenarios(SPEC, [f"scenario:{CANCEL}"]) == [CANCEL]
    # the spec's order, each once, whatever the selectors' order
    assert select_scenarios(SPEC, [ADD, CANCEL, "ann *"]) == [CANCEL, ADD]
    assert select_scenarios(SPEC, ["*cancels*"]) == [CANCEL]
    assert select_scenarios(SPEC, ["scenario:ann adds*"]) == [ADD]


def test_an_unknown_selector_is_an_error_naming_the_nearest():
    with pytest.raises(SelectionError) as err:
        select_scenarios(SPEC, [CANCEL, "ann cancel her pending order"])
    text = str(err.value)
    assert "matches no scenario" in text and CANCEL in text
    with pytest.raises(SelectionError, match="matches no scenario"):
        select_scenarios(SPEC, ["nothing_*"])


def test_an_empty_selection_is_an_error():
    with pytest.raises(SelectionError, match="empty"):
        select_scenarios(SPEC, [])


TRACE = {
    "source": "REQUIREMENTS.md",
    "rules": [{"id": f"R{i}", "text": "t", "section": "s", "line": i} for i in (1, 2, 3)],
    "links": {
        "R1": [f"scenario:{CANCEL}", "invariant:total_is_sum"],
        "R2": ["invariant:total_is_sum"],
        "R3": [f"scenario:{ADD}", f"scenario:{CANCEL}"],
    },
}


def test_rule_selectors_read_the_trace(tmp_path):
    assert select_scenarios(SPEC, ["rule:R1"], TRACE) == [CANCEL]
    assert select_scenarios(SPEC, ["rule:R3"], TRACE) == [CANCEL, ADD]
    path = tmp_path / "spec.trace.json"
    path.write_text(json.dumps(TRACE))
    assert select_scenarios(SPEC, ["rule:R1", ADD], path) == [CANCEL, ADD]
    with pytest.raises(SelectionError, match="links no scenario"):
        select_scenarios(SPEC, ["rule:R2"], TRACE)  # only an invariant
    with pytest.raises(SelectionError, match="nearest: R1"):
        select_scenarios(SPEC, ["rule:R11"], TRACE)
    with pytest.raises(SelectionError, match="need the project trace"):
        select_scenarios(SPEC, ["rule:R1"], None)


def test_only_file_json_or_lines(tmp_path):
    j = tmp_path / "only.json"
    j.write_text(json.dumps([CANCEL, "rule:R3"]))
    assert read_only_file(j) == [CANCEL, "rule:R3"]
    t = tmp_path / "only.txt"
    t.write_text(f"# the node's scenarios\n{CANCEL}\n\n  scenario:{ADD}  \n")
    assert read_only_file(t) == [CANCEL, f"scenario:{ADD}"]
    bad = tmp_path / "bad.json"
    bad.write_text('[1, "x"]')
    with pytest.raises(SelectionError):
        read_only_file(bad)


# ---- the check, narrowed ----------------------------------------------------------------------


def test_selecting_every_scenario_gives_the_full_runs_findings(apps):
    full = run(apps, "wrong_quantity", layers="CD")
    every = run(apps, "wrong_quantity", layers="CD", only=[CANCEL, ADD])
    assert [f.to_dict() for f in every.findings] == [f.to_dict() for f in full.findings]
    assert every.cases == full.cases and every.counts == full.counts
    assert every.findings  # the broken app fails either way
    # the only differences: the note saying what was narrowed, and the selection
    assert [n for n in every.notes if not n.startswith("--only")] == full.notes
    assert every.selection == {"selectors": [CANCEL, ADD], "scenarios": [CANCEL, ADD], "of": 2}
    assert full.selection is None and "selection" not in json.loads(full.to_json())


def test_only_narrows_layers_d_and_x_not_b(apps):
    full = run(apps, "wrong_quantity", layers="BCD")
    one = run(apps, "wrong_quantity", layers="BCD", only=[CANCEL])
    d_cases = {k for k, c in one.cases.items() if c["layer"] == "D"}
    assert d_cases and all(k.startswith(CANCEL) for k in d_cases)
    # layer B ran whole; the D finding of the unselected scenario is gone
    assert {k for k, c in one.cases.items() if c["layer"] == "B"} == {
        k for k, c in full.cases.items() if c["layer"] == "B"
    }
    assert one.counts["D"] == 0 and full.counts["D"] == 1
    assert [s["name"] for s in one.timings["scenarios"]] == [CANCEL]


def test_only_narrows_layer_x():
    names = [s.name for s in racy.RUNS_SPEC.scenarios]
    full = check(racy.RUNS_SPEC, racy.runs_app(), racy.build_runs_db, login=racy.login,
                 layers="X")  # fmt: skip
    every = check(racy.RUNS_SPEC, racy.runs_app(), racy.build_runs_db, login=racy.login,
                  layers="X", only=names)  # fmt: skip
    assert [r["race"] for r in every.races] == [r["race"] for r in full.races]
    one = check(racy.RUNS_SPEC, racy.runs_app(), racy.build_runs_db, login=racy.login,
                layers="X", only=[names[1]])  # fmt: skip
    assert one.races and {r["race"].split("/")[0] for r in one.races} == {names[1]}
    assert len(one.races) < len(full.races)


# ---- timings ----------------------------------------------------------------------------------


def test_timings_are_present_and_sane(apps):
    report = run(apps, "ok", layers="ABCD")
    t = report.timings
    assert set(t["layers"]) == set("ABCD")
    assert all(v >= 0 for v in t["layers"].values())
    assert t["total_s"] >= sum(t["layers"].values()) - 1e-3
    scenarios = t["scenarios"]
    assert [s["name"] for s in scenarios] == [CANCEL, ADD]
    assert sum(s["seconds"] for s in scenarios) <= t["layers"]["D"] + 1e-3
    for s in scenarios:
        parts = sum(s[k] for k in ("db_clone_s", "given_s", "app_start_s", "steps_s",
                                   "app_stop_s", "db_release_s"))  # fmt: skip
        assert all(s[k] >= 0 for k in s if k.endswith("_s"))
        assert parts <= s["seconds"] + 1e-3
        assert s["login_s"] <= s["steps_s"] + 1e-3
    assert [s["requests"] for s in scenarios] == [2, 1]
    assert [s["logins"] for s in scenarios] == [1, 1]
    assert scenarios[1]["given_s"] > 0 and scenarios[0]["given_s"] == 0
    assert "invariants_s" in t and scenarios[0]["invariants_s"] > 0
    data = json.loads(report.to_json())
    assert REPORT_KEYS <= set(data) and data["timings"] == t
    text = report.render()
    assert "\ntimings: total " in text and "A " in text.split("timings:")[1].splitlines()[0]
    assert "slowest 2:" in text


def test_the_text_summary_names_the_five_slowest(apps):
    report = Report(layers="D")
    report.timings = {
        "total_s": 12.0,
        "layers": {"C": 1.0, "D": 10.0},
        "scenarios": [
            {
                "name": f"s{i}",
                "seconds": float(i),
                "app_start_s": 0.5,
                "db_clone_s": 0.1,
                "steps_s": i - 0.7,
                "app_stop_s": 0.1,
                "requests": i,
            }  # fmt: skip
            for i in range(1, 8)
        ],
    }
    text = report.render()
    tail = text[text.index("timings:") :].splitlines()
    assert tail[0] == "timings: total 12.0s (C 1.00s, D 10.0s)"
    assert tail[2] == "slowest 5:"
    assert [line.split()[1] for line in tail[3:]] == ["s7", "s6", "s5", "s4", "s3"]


# ---- no flag: HEAD's report ---------------------------------------------------------------------


@pytest.mark.parametrize("variant", VARIANTS)
def test_without_only_the_report_is_heads(apps, variant):
    app_dir, package = apps[variant]
    report = run(apps, variant, layers="ABCD")

    def norm(text: str) -> str:
        return text.replace(str(app_dir), "<APP>").replace(package, "<PKG>")

    data = without_timings(report)
    assert set(data) == REPORT_KEYS
    golden = (GOLDEN / f"minishop_{variant}.json").read_text()
    assert norm(json.dumps(data, indent=2, default=str)) + "\n" == golden
    text = report.render().split("\n\ntimings:")[0]
    assert norm(text) + "\n" == (GOLDEN / f"minishop_{variant}.txt").read_text()


# ---- the CLI ------------------------------------------------------------------------------------


def cli(apps, variant: str, *extra: str) -> list[str]:
    app_dir, package = apps[variant]
    return ["check", "--spec", "tests.spec.minishop:SPEC", "--app", f"{package}.main:make_app",
            "--db", "tests.spec.minishop:build_db", "--login", "tests.spec.minishop:login",
            "--app-dir", str(app_dir), "--package", package, *extra]  # fmt: skip


def test_cli_only_runs_the_selected_scenarios(apps, capsys):
    rc = main(cli(apps, "wrong_quantity", "--layers", "D", "--json", "--only", CANCEL))
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["counts"] == {"D": 0}
    assert data["selection"]["scenarios"] == [CANCEL]
    assert [s["name"] for s in data["timings"]["scenarios"]] == [CANCEL]
    # repeatable, several per flag
    rc = main(cli(apps, "wrong_quantity", "--layers", "D", "--json", "--only", CANCEL,
                  "--only", ADD))  # fmt: skip
    data = json.loads(capsys.readouterr().out)
    assert rc == 1 and data["selection"]["scenarios"] == [CANCEL, ADD]


def test_cli_unknown_or_empty_selection_exits_2(apps, capsys, tmp_path):
    assert main(cli(apps, "ok", "--layers", "D", "--only", "ann cancel")) == 2
    err = capsys.readouterr().err
    assert "matches no scenario" in err and CANCEL in err
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing selected\n")
    assert main(cli(apps, "ok", "--layers", "D", "--only-file", str(empty))) == 2
    assert "empty" in capsys.readouterr().err


def test_cli_rule_selector_with_a_trace(apps, capsys, tmp_path):
    trace = tmp_path / "t.trace.json"
    trace.write_text(json.dumps(TRACE))
    rc = main(cli(apps, "ok", "--layers", "D", "--json", "--only", "rule:R1", "--trace",
                  str(trace)))  # fmt: skip
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["selection"]["scenarios"] == [CANCEL]
    # no trace next to tests/spec/minishop.py: a rule selector cannot run
    assert main(cli(apps, "ok", "--layers", "D", "--only", "rule:R1")) == 2
    assert "trace" in capsys.readouterr().err
