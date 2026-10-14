"""``lattice improve draft`` (docs/results/2026-10-13-improve2-prereg.md): one test per probe
row. D1 one draft per kind, D2 refusals, D3 safety (nothing is modified, no LLM or network
import, no literal of the I-3 numbers in the code), D4 determinism, D5 the old commands."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

import pytest

from tests.improve.test_analyze import i4_checker, i4_lint_noise
from tests.improve.test_verify import (
    CLOSE,
    DATA,
    ROOT,
    entry,
    i3_lines,
    make_env,
    run,
    sub,
)
from tests.improve.test_verify import verify as verify_json

FIXTURE = '[rebuild]\ngates = ["./check.sh"]\n'
CR = ["What to change", "Why", "Evidence", "Acceptance gate", "Measure afterwards",
      "Not decided here"]  # fmt: skip
PREREG = [
    "1. Status",
    "2. Question",
    "3. Signal and evidence",
    "4. Probe",
    "5. Pass rule and blind prediction",
    "6. Measure afterwards",
    "7. Blind spots",
]
MODULE = ["Module skeleton", "What it must provide", "Evidence", "Acceptance gate",
          "Measure afterwards", "Not decided here"]  # fmt: skip


def headings(text: str) -> list[str]:
    return re.findall(r"^## (.+)$", text, re.M)


def tree_hash(*dirs: Path) -> dict[str, str]:
    out = {}
    for d in dirs:
        for p in sorted(d.rglob("*")):
            if p.is_file():
                out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def draft_json(capsys, project: Path, state: Path, ident: str, *extra: str):
    rc, out, err = run(capsys, project, state, "draft", ident, "--json", *extra)
    return rc, json.loads(out), err


def lines_of(events: list[dict]) -> list[str]:
    return [json.dumps(e) for e in events]


# ---------------------------------------------------------------- D1: one draft per kind


def test_d1a_change_request_from_a_lint_proposal(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, lines_of(i4_lint_noise()))
    rc, d, _ = draft_json(capsys, project, state, "imp-f377ca42")
    t = d["text"]
    assert rc == 0 and d["template"] == "change_request" and d["toml"] is None
    assert headings(t) == CR
    assert t.startswith("# Draft: Lint L012: tune it (it fired 6 time(s) at a cost)\n")
    assert "lattice/spec/lint.py, lattice/spec/preconditions.py" in t
    assert "signal lint:L012: 6 x 180.0 s = 1080.0 s [A]" in t
    for n in range(6):
        assert f"e-2026-10-01T00:0{n}:00Z 2026-10-01T00:0{n}:00Z 180.0 s" in t
    assert "As of 2026-10-01T01:00:00Z" in t and "the owner names the gate" in t
    assert "minimum 3 exposures (change events)" in t
    assert "NOT APPLIED: this draft changes nothing; the owner decides." in t
    assert d["sections"][0]["heading"] == "What to change"
    assert d["ledger_command"] == (
        "lattice improve ledger --signal lint:L012 --status closed --id imp-f377ca42 --commit <SHA>"
    )
    assert d["verify_command"] == "lattice improve verify imp-f377ca42"
    assert (
        d["ledger_command"] in t
        and "run it when the change lands, before the runs that measure it" in t
    )
    assert d["as_of"] == "2026-10-01T01:00:00Z" and d["proposal"]["id"] == "imp-f377ca42"


def test_d1b_config_for_the_x_repeats_proposal(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, i3_lines()[:9], toml=FIXTURE)
    rc, d, _ = draft_json(capsys, project, state, "imp-3e1cd7f9")
    t = d["text"]
    assert rc == 0 and d["template"] == "config" and headings(t) == [
        "What to change", "Expected", *CR[1:]]  # fmt: skip
    assert '[rebuild]\nlevel_gate_args = ["--x-repeats", "2"]' in t
    assert d["toml"] == '[rebuild]\nlevel_gate_args = ["--x-repeats", "2"]'
    assert "```toml" in t and "current: not set" in t
    assert "expected: X 119.5 s -> saving 71.7 s a gate (60%; x_repeats 5 -> 2)" in t
    assert "signal gate:level_regression:X: 3 x 119.5 s = 358.5 s [A]" in t
    for ref, v in (("e-e8d9c91c15964512", "119.8"), ("e-327c71cd641f49a0", "119.3"),
                   ("e-f6061c7895644d98", "119.5")):  # fmt: skip
        assert re.search(rf"^{ref} \S+ {v} s$", t, re.M)
    assert "`./check.sh`" in t and "As of 2026-10-12T23:07:11Z" in t
    assert "--id imp-3e1cd7f9 --commit <SHA>" in t and "lattice improve verify imp-3e1cd7f9" in t
    assert "minimum 3 occurrences" in t


def test_d1c_module_skeleton(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, i3_lines()[:9])
    rc, d, _ = draft_json(capsys, project, state, "imp-7fad29b4")
    t = d["text"]
    assert rc == 0 and d["template"] == "module" and headings(t) == MODULE
    for line in ('name = "stub_observability"', 'kind = "custom"', 'version = "0.0.0"',
                 'description = "Service stubs that record what they were sent"',
                 "provides = []", "requires = []"):  # fmt: skip
        assert line in d["toml"]
    assert "signal unchecked:stub_observability: f = 3 (no cost recorded) [C]" in t
    assert "e-ddf0ec95bc824d83" in t and "minimum 1 snapshot" in t
    assert "the owner names the gate" in t  # no studio.toml


def test_d1d_prereg_stub(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, i3_lines()[:9])
    rc, d, _ = draft_json(capsys, project, state, "imp-a8f0a344")
    t = d["text"]
    assert rc == 0 and d["template"] == "prereg" and headings(t) == PREREG
    assert t.startswith("# Unchecked rules (free text): pre-registration (stub)\n")
    assert "written BEFORE any probe" in t and "f = 5" in t and d["toml"] is None
    assert len(re.findall(r"^PREDICT:$", t, re.M)) == 2


def test_d1e_x_proposal_on_a_tie_is_an_experiment(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path)  # all 18 events, no ledger
    rc, d, _ = draft_json(capsys, project, state, "imp-3e1cd7f9")
    t = d["text"]
    assert rc == 0 and d["template"] == "prereg" and d["proposal"]["kind"] == "experiment"
    assert d["toml"] is None and "expected:" not in t and "6 x 89.0 s = 534.0 s [A]" in t
    assert headings(t) == PREREG


def test_d1f_await_cap_for_the_d_layer(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, i3_lines()[:9], toml=FIXTURE)
    rc, d, _ = draft_json(capsys, project, state, "imp-256fef6a")
    assert rc == 0 and d["template"] == "config"
    assert 'level_gate_args = ["--await-cap", "5"]' in d["toml"]
    assert "3 x 22.5 s = 67.5 s [A]" in d["text"] and "## Expected" not in d["text"]


def test_d1_config_reads_the_current_value(tmp_path: Path, capsys) -> None:
    toml = FIXTURE + 'level_gate_args = ["--x-repeats", "3"]\nscoped_gate_args = ["--a"]\n'
    project, state = make_env(tmp_path, i3_lines()[:9], toml=toml)
    _, d, _ = draft_json(capsys, project, state, "imp-3e1cd7f9")  # = : replaced
    assert (
        'current: level_gate_args = ["--x-repeats", "3"] (replaced by the line above)' in d["text"]
    )
    _, d, _ = draft_json(capsys, project, state, "imp-256fef6a")  # += : appended
    assert d["toml"] == '[rebuild]\nlevel_gate_args = ["--x-repeats", "3", "--await-cap", "5"]'


def test_d1_a_knob_names_no_value(tmp_path: Path, capsys) -> None:
    evs = [
        {"v": 1, "ts": f"2026-10-01T0{i}:00:00Z", "project": "t", "kind": "node_ended",
         "id": f"e-{i}", "status": "gave_up", "cause": "timeout", "wall_s": 600.0}
        for i in range(3)
    ]  # fmt: skip
    project, state = make_env(tmp_path, lines_of(evs))
    rc, out, _ = run(capsys, project, state, "report", "--json")
    pid = json.loads(out)["proposals"][0]["id"]
    rc, d, _ = draft_json(capsys, project, state, pid)
    assert rc == 0 and d["template"] == "change_request" and d["toml"] is None
    assert "Knob: [rebuild] node_timeout in studio.toml; value: the owner decides" in d["text"]


def test_d1_the_draft_text_is_what_stdout_and_out_carry(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, i3_lines()[:9], toml=FIXTURE)
    rc, text, err = run(capsys, project, state, "draft", "imp-3e1cd7f9")
    assert rc == 0 and err == "" and text.endswith("\n")
    _, d, _ = draft_json(capsys, project, state, "imp-3e1cd7f9")
    assert d["text"] == text
    assert list(d) == ["format", "command", "proposal", "as_of", "template", "sections", "toml",
                       "ledger_command", "verify_command", "text"]  # fmt: skip
    out_dir = sub(tmp_path, "out")
    rc, msg, _ = run(
        capsys, project, state, "draft", "imp-3e1cd7f9", "--out", str(out_dir / "d.md")
    )
    assert rc == 0 and msg == f"draft imp-3e1cd7f9: {len(text.encode())} bytes written\n"
    assert (out_dir / "d.md").read_text() == text and [p.name for p in out_dir.iterdir()] == [
        "d.md"
    ]


# ---------------------------------------------------------------- D2: refusals


def refused(capsys, project: Path, state: Path, ident: str, *extra: str):
    rc, d, err = draft_json(capsys, project, state, ident, *extra)
    assert rc == 3 and d["refused"] is True and d["command"] == "draft" and d["format"] == 1
    assert err == f"lattice improve: refused: {d['detail']}\n"
    return d


def test_d2_the_checker_is_never_drafted(tmp_path: Path, capsys) -> None:
    out = sub(tmp_path, "o") / "draft.md"
    project, state = make_env(sub(tmp_path, "a"), lines_of(i4_checker()))
    d = refused(capsys, project, state, "imp-56861ff8", "--out", str(out))
    assert d["reason"] == "needs_owner" and "lattice/spec/check.py" in d["detail"]
    assert "never drafted automatically" in d["detail"]
    project, state = make_env(sub(tmp_path, "b"), i3_lines()[:9])
    d = refused(capsys, project, state, "imp-56861ff8", "--out", str(out))
    assert d["reason"] == "needs_owner" and not out.exists()


def test_d2_the_spec_language_is_never_drafted(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, i3_lines()[:9])
    d = refused(capsys, project, state, "imp-adeea5ef")
    assert d["reason"] == "needs_owner" and "lattice/spec/model.py" in d["detail"]
    assert "never drafted automatically" in d["detail"]


def test_d2_a_closed_proposal_is_refused(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, ledger=[entry()])
    out = sub(tmp_path, "o") / "draft.md"
    d = refused(capsys, project, state, "imp-3e1cd7f9", "--out", str(out))
    assert d["reason"] == "closed" and d["detail"] == f"closed by I-01 at {CLOSE}"
    assert not out.exists()


def test_d2_an_unknown_id_is_refused(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, i3_lines()[:9])
    out = sub(tmp_path, "o") / "draft.md"
    d = refused(capsys, project, state, "imp-00000000", "--out", str(out))
    assert d["reason"] == "unknown_id" and not out.exists()


def test_d2_out_inside_the_project_or_state_is_refused_first(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, i3_lines()[:9], toml=FIXTURE)
    before = tree_hash(project, state)
    for out in (project / "draft.md", state / "draft.md", project / "sub" / "x.md"):
        d = refused(capsys, project, state, "imp-3e1cd7f9", "--out", str(out))
        assert d["reason"] == "out_in_project" and not out.exists()
    # first: even an unknown id reports the --out problem
    d = refused(capsys, project, state, "imp-00000000", "--out", str(project / "d.md"))
    assert d["reason"] == "out_in_project"
    assert tree_hash(project, state) == before


# ---------------------------------------------------------------- D3: safety


def test_d3_no_project_or_state_file_changes_and_out_adds_one(tmp_path: Path, capsys) -> None:
    project, state = make_env(tmp_path, i3_lines()[:9], ledger=[entry(signal="cause:timeout")],
                              toml=FIXTURE)  # fmt: skip
    outd = sub(tmp_path, "o")
    before = tree_hash(project, state)
    calls = [
        ("draft", "imp-3e1cd7f9"), ("draft", "imp-7fad29b4"), ("draft", "imp-a8f0a344"),
        ("draft", "imp-56861ff8"), ("draft", "imp-00000000"), ("draft", "imp-3e1cd7f9", "--json"),
        ("verify", "I-01"), ("verify", "I-01", "--json"), ("verify", "imp-3e1cd7f9"),
        ("verify", "I-99"), ("verify", "I-01", "--at", "bad"),
    ]  # fmt: skip
    for call in calls:
        run(capsys, project, state, *call)
        assert tree_hash(project, state) == before, call
    names = set()
    for i, ident in enumerate(("imp-3e1cd7f9", "imp-7fad29b4")):
        run(capsys, project, state, "draft", ident, "--out", str(outd / f"d{i}.md"))
        names.add(f"d{i}.md")
        assert {p.name for p in outd.iterdir()} == names
    assert tree_hash(project, state) == before


def test_d3_nothing_is_created_when_there_is_no_state_dir(tmp_path: Path, capsys) -> None:
    project = sub(tmp_path, "p")
    state = tmp_path / "nostate"
    for call in (("verify", "I-01"), ("draft", "imp-3e1cd7f9")):
        rc, _, _ = run(capsys, project, state, *call)
        assert rc == 3 and not state.exists() and list(project.iterdir()) == []


IMPROVE = ROOT / "lattice" / "improve"
ALLOWED = {"argparse", "fnmatch", "hashlib", "json", "re", "datetime", "pathlib", "tomllib",
           "collections", "dataclasses", "typing", "sys", "__future__", "lattice"}  # fmt: skip
FORBIDDEN = re.compile(
    r"subprocess|socket|urllib|http|requests|httpx|anthropic|lattice\.llm|claude"
)


@pytest.mark.parametrize("name", ["verify.py", "draft.py"])
def test_d3_imports_are_stdlib_and_lattice_improve_only(name: str) -> None:
    src = (IMPROVE / name).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
            if node.module and node.module.startswith("lattice"):
                assert node.module.startswith("lattice.improve"), node.module
        else:
            continue
        for m in mods:
            assert m.split(".")[0] in ALLOWED, m
    imports = [ln for ln in src.splitlines() if re.match(r"\s*(import|from) ", ln)]
    assert [ln for ln in imports if FORBIDDEN.search(ln)] == []
    assert not re.search(r"claude|lattice\.llm|subprocess", src, re.I)


def test_d3_the_code_holds_no_literal_of_the_i3_numbers() -> None:
    for p in IMPROVE.glob("*.py"):
        text = p.read_text()
        for lit in ("71.7", "119.5", "61.01", "3e1cd7f9"):
            assert lit not in text, f"{p.name}: {lit}"


# ---------------------------------------------------------------- D4: determinism


def test_d4_same_inputs_same_bytes_across_time_cwd_and_a_copy(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    project, state = make_env(tmp_path, ledger=[entry()], toml=FIXTURE)
    other = sub(tmp_path, "elsewhere")
    state2 = tmp_path / "state-copy"
    shutil.copytree(state, state2)
    out1, out2 = sub(tmp_path, "o1"), sub(tmp_path, "o2")
    cases = [("verify", "I-01"), ("verify", "I-01", "--json"), ("draft", "imp-00000000"),
             ("draft", "imp-3e1cd7f9", "--json")]  # fmt: skip
    first = [run(capsys, project, state, *c) for c in cases]
    (state / "improvements.jsonl").unlink()  # a draft of an open proposal needs no ledger
    first.append(run(capsys, project, state, "draft", "imp-3e1cd7f9"))
    run(capsys, project, state, "draft", "imp-3e1cd7f9", "--out", str(out1 / "d.md"))
    time.sleep(1.1)  # a wall-clock time anywhere in an output would differ now
    monkeypatch.chdir(other)
    (state / "improvements.jsonl").write_text(json.dumps(entry()) + "\n")
    second = [run(capsys, project, state2, *c) for c in cases]
    (state2 / "improvements.jsonl").unlink()
    second.append(run(capsys, project, state2, "draft", "imp-3e1cd7f9"))
    run(capsys, project, state2, "draft", "imp-3e1cd7f9", "--out", str(out2 / "d.md"))
    assert first == second
    assert (out1 / "d.md").read_bytes() == (out2 / "d.md").read_bytes()


# ---------------------------------------------------------------- D5: the old commands


def test_d5_report_text_equals_the_committed_reports(tmp_path: Path, capsys) -> None:
    project, state = make_env(sub(tmp_path, "a"), i3_lines()[:9])
    _, out, _ = run(capsys, project, state, "report", "--top", "5")
    assert out == (DATA / "i3-report-before.txt").read_text()
    project, state = make_env(sub(tmp_path, "b"), ledger=[entry("2026-10-12T23:12:00Z")])
    _, out, _ = run(capsys, project, state, "report", "--top", "2")
    assert out == (DATA / "i3-report-after-closed.txt").read_text()
    project, state = make_env(
        sub(tmp_path, "c"), (DATA / "ir-replay-events.jsonl").read_text().splitlines()
    )
    shutil.copy(DATA / "ir-replay-improvements.jsonl", state / "improvements.jsonl")
    _, out, _ = run(capsys, project, state, "report")
    assert out == (DATA / "ir-replay-report.txt").read_text()


def test_d5_verify_on_the_i_r_replay_ledger(tmp_path: Path, capsys) -> None:
    """The real ledger of the I-R replay: closed items verify without a crash and none is a
    pass it cannot back (the data has no measuring runs after the closes)."""
    lines = (DATA / "ir-replay-events.jsonl").read_text().splitlines()
    project, state = make_env(tmp_path, lines)
    shutil.copy(DATA / "ir-replay-improvements.jsonl", state / "improvements.jsonl")
    for e in map(json.loads, (DATA / "ir-replay-improvements.jsonl").read_text().splitlines()):
        rc, d = verify_json(capsys, project, state, e["id"])
        assert rc in (0, 1, 3) and d.get("verdict", "refused") in (
            "improved",
            "not_improved",
            "recurred",
            "not_enough_data",
            "refused",
        )
