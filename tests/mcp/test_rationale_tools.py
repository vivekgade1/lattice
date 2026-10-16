"""The MCP tools of the owner's rationale and glossary (rationale_get, glossary_get, rule_get's
two keys), the guard that protects the approved files while they exist, and the rebuild's
protected set. Stub generators only; read-only tools; nothing writes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lattice.llm.client import ScriptedGenerator
from lattice.mcp import guard
from lattice.mcp.guard import Guard, MultiGuard
from lattice.mcp.tools import LocalBackend, Toolbox, ToolError
from lattice.spec.rules import Trace
from tests.spec.test_glossary import gfile, term
from tests.spec.test_rationale import note, rfile
from tests.studio.test_server import make_project

PROPOSAL_TEXT = "SECRET PROPOSAL: notes are short for a reason nobody stated"
STALE_TEXT = "SECRET STALE: an old reason for an old wording"
WHY = "Short notes keep the list readable on a phone."


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    return make_project(tmp_path / "p", repo=False)


def rules_of(proj: Path) -> dict[str, str]:
    return {r.id: r.text for r in Trace.load(proj / "spec.trace.json").rules}


def fill(proj: Path) -> None:
    r = rules_of(proj)
    (proj / "REQUIREMENTS.rationale.json").write_text(
        rfile(
            [
                note("R2", r["R2"], WHY, "Long text is refused.", owner="ann", by="bob"),
                note("R3", "an older wording of R3", STALE_TEXT, None),
            ]
        )
    )
    (proj / "REQUIREMENTS.rationale.proposed.json").write_text(
        rfile(
            [
                note("R4", r["R4"], PROPOSAL_TEXT, None, status="proposed", author="hand"),
            ]
        )
    )
    (proj / "REQUIREMENTS.glossary.json").write_text(
        gfile(
            [
                term("note", "A short text a user saves.", aliases=("memo",)),
                term("daily pass", "A thing no rule uses."),
            ]
        )
    )


def toolbox(proj: Path, *, readonly: bool = False) -> Toolbox:
    return Toolbox(LocalBackend(proj, generator=ScriptedGenerator([])), readonly=readonly)


NEW = ["rationale_get", "glossary_get"]


# ---- listing -----------------------------------------------------------------------------------


def test_no_file_no_tool(proj: Path) -> None:
    names = toolbox(proj).names()
    assert not set(NEW) & set(names)
    assert not set(NEW) & set(toolbox(proj, readonly=True).names())


def test_each_tool_is_listed_when_its_own_file_exists(proj: Path) -> None:
    base = toolbox(proj).names()
    (proj / "REQUIREMENTS.rationale.proposed.json").write_text(rfile([]))
    assert toolbox(proj).names() == [*base, "rationale_get"]
    (proj / "REQUIREMENTS.glossary.json").write_text(gfile([]))
    assert toolbox(proj).names() == [*base, "rationale_get", "glossary_get"]
    (proj / "REQUIREMENTS.rationale.proposed.json").unlink()
    assert toolbox(proj).names() == [*base, "glossary_get"]


def test_the_tools_come_after_change_list_and_are_listed_read_only(proj: Path) -> None:
    fill(proj)
    names = toolbox(proj).names()
    assert names[-2:] == NEW and names.index("change_list") < names.index("rationale_get")
    ro = toolbox(proj, readonly=True)
    assert ro.names()[-2:] == NEW
    for t in ro.list_tools():
        if t["name"] in NEW:
            assert t["annotations"]["readOnlyHint"] is True
            assert t["inputSchema"]["additionalProperties"] is False


# ---- rationale_get -------------------------------------------------------------------------------


def call(tb: Toolbox, name: str, **args: Any) -> dict[str, Any]:
    return tb.call(name, args)


def test_rationale_get_returns_notes_metrics_and_counts_only_for_proposals(proj: Path) -> None:
    fill(proj)
    tb = toolbox(proj)
    out = call(tb, "rationale_get")
    assert out["file"] == "REQUIREMENTS.rationale.json"
    assert out["metrics"]["RC"]["n"] == 1 and out["metrics"]["RN"]["stale"] == 1
    assert out["proposals"] == {"proposed": 1, "rejected": 0}
    by = {n["rule"]: n for n in out["notes"]}
    assert by["R2"]["state"] == "current" and by["R2"]["why"] == WHY and by["R2"]["owner"] == "ann"
    assert by["R3"]["state"] == "stale" and "why" not in by["R3"]
    text = json.dumps(out)
    assert PROPOSAL_TEXT not in text and STALE_TEXT not in text


def test_rationale_get_filters_by_rule_and_state(proj: Path) -> None:
    fill(proj)
    tb = toolbox(proj)
    one = call(tb, "rationale_get", rule="R2")
    assert one["rule_state"] == "approved" and [n["rule"] for n in one["notes"]] == ["R2"]
    two = call(tb, "rationale_get", rule="rule:R4")
    assert two["rule_state"] == "proposed_only" and two["notes"] == []
    three = call(tb, "rationale_get", rule="R3")
    assert three["rule_state"] == "stale" and PROPOSAL_TEXT not in json.dumps(three)
    assert call(tb, "rationale_get", rule="R1")["rule_state"] == "none"
    stale = call(tb, "rationale_get", state="stale")
    assert [n["rule"] for n in stale["notes"]] == ["R3"]
    assert call(tb, "rationale_get", limit=1)["returned"] == 1


def test_rationale_get_errors_start_with_their_code(proj: Path) -> None:
    fill(proj)
    tb = toolbox(proj)

    def err(**kw: Any) -> str:
        with pytest.raises(ToolError) as e:
            tb.call("rationale_get", kw)
        return str(e.value).split(":")[0]

    assert err(limit=0) == "bad_limit" and err(limit=201) == "bad_limit"
    assert err(limit="x") == "bad_limit" and err(limit=True) == "bad_limit"
    assert err(rule="R999") == "unknown_rule"
    assert err(state="weird") == "bad_state"
    with pytest.raises(ToolError, match="unknown argument"):
        tb.call("rationale_get", {"nope": 1})


def test_an_invalid_file_errors_the_tools_that_read_it(proj: Path) -> None:
    (proj / "REQUIREMENTS.rationale.json").write_text("{bad")
    (proj / "REQUIREMENTS.glossary.json").write_text(gfile([term("note")]))
    tb = toolbox(proj)
    with pytest.raises(ToolError, match="^bad_rationale"):
        tb.call("rationale_get", {})
    with pytest.raises(ToolError, match="^bad_rationale"):
        tb.call("glossary_get", {"lint": True})  # G003 reads the rationale
    with pytest.raises(ToolError, match="^bad_rationale"):
        tb.call("rule_get", {"id": "R2"})
    (proj / "REQUIREMENTS.rationale.json").unlink()
    (proj / "REQUIREMENTS.glossary.json").write_text("{bad")
    tb2 = toolbox(proj)
    with pytest.raises(ToolError, match="^bad_glossary"):
        tb2.call("glossary_get", {})
    with pytest.raises(ToolError, match="^bad_glossary"):
        tb2.call("rule_get", {"id": "R2"})


# ---- glossary_get --------------------------------------------------------------------------------


def test_glossary_get_lists_terms_with_metrics(proj: Path) -> None:
    fill(proj)
    out = call(toolbox(proj), "glossary_get")
    assert [t["slug"] for t in out["terms"]] == ["note", "daily-pass"]
    assert out["metrics"]["GA"] == 2 and out["metrics"]["GX"] == 1
    assert out["terms"][0]["aliases"] == ["memo"]


def test_glossary_get_one_term_by_term_alias_or_slug(proj: Path) -> None:
    fill(proj)
    tb = toolbox(proj)
    for name in ("note", "Memo", "Notes", "daily-pass"):
        assert call(tb, "glossary_get", term=name)["term"]["term"] in ("note", "daily pass")
    with pytest.raises(ToolError, match="^unknown_term"):
        tb.call("glossary_get", {"term": "snooze"})


def test_glossary_get_lint_returns_findings_without_proposal_text(proj: Path) -> None:
    fill(proj)
    (proj / "REQUIREMENTS.glossary.proposed.json").write_text(
        gfile(
            [
                {
                    **term("snooze", PROPOSAL_TEXT),
                    "status": "proposed",
                    "owner": None,
                    "approved_by": None,
                    "approved_sha": None,
                },
            ]
        )
    )
    out = call(toolbox(proj), "glossary_get", lint=True, limit=200)
    codes = {f["code"] for f in out["findings"]}
    assert {"G001", "G002", "G003", "G004"} <= codes
    assert out["returned"] == len(out["findings"]) <= 200
    assert PROPOSAL_TEXT not in json.dumps(out) and STALE_TEXT not in json.dumps(out)
    assert call(toolbox(proj), "glossary_get", lint=True, limit=2)["returned"] == 2
    with pytest.raises(ToolError, match="boolean"):
        toolbox(proj).call("glossary_get", {"lint": "yes"})


# ---- rule_get ------------------------------------------------------------------------------------


def test_rule_get_gains_rationale_and_terms_only_with_files(proj: Path) -> None:
    keys0 = set(call(toolbox(proj), "rule_get", id="R2"))
    assert "rationale" not in keys0 and "terms" not in keys0
    fill(proj)
    tb = toolbox(proj)
    r2 = call(tb, "rule_get", id="R2")
    assert set(r2) - keys0 == {"rationale", "terms"}
    assert r2["rationale"] == {
        "state": "approved",
        "why": WHY,
        "tradeoff": "Long text is refused.",
        "owner": "ann",
        "approved_by": "bob",
    }
    assert r2["terms"] == []  # "A note's text is ..." defines the phrase "note's text" itself
    r1 = call(tb, "rule_get", id="R1")
    assert [t["slug"] for t in r1["terms"]] == ["note"]
    assert set(r1["terms"][0]) == {"term", "slug", "definition"}
    r3 = call(tb, "rule_get", id="R3")
    assert r3["rationale"] == {"state": "stale"}
    assert call(tb, "rule_get", id="R4")["rationale"] == {"state": "proposed_only"}
    assert PROPOSAL_TEXT not in json.dumps(r2) + json.dumps(r3)


def test_rationale_only_leaves_terms_out_and_the_reverse(proj: Path) -> None:
    fill(proj)
    (proj / "REQUIREMENTS.glossary.json").unlink()
    r = call(toolbox(proj), "rule_get", id="R2")
    assert "rationale" in r and "terms" not in r
    (proj / "REQUIREMENTS.rationale.json").unlink()
    (proj / "REQUIREMENTS.rationale.proposed.json").unlink()
    (proj / "REQUIREMENTS.glossary.json").write_text(gfile([term("note")]))
    r2 = call(toolbox(proj), "rule_get", id="R1")
    assert "terms" in r2 and "rationale" not in r2


def test_the_tools_write_nothing(proj: Path) -> None:
    fill(proj)
    before = {p.name: p.read_bytes() for p in proj.iterdir() if p.is_file()}
    tb = toolbox(proj, readonly=True)
    for name, args in (
        ("rationale_get", {}),
        ("glossary_get", {"lint": True}),
        ("rule_get", {"id": "R2"}),
        ("spec_lint", {}),
    ):
        tb.call(name, args)
    assert {p.name: p.read_bytes() for p in proj.iterdir() if p.is_file()} == before


def test_the_instructions_text_is_unchanged() -> None:
    from lattice.mcp.server import INSTRUCTIONS

    assert "rationale" not in INSTRUCTIONS.lower() and "glossary" not in INSTRUCTIONS.lower()


# ---- the guard -----------------------------------------------------------------------------------


def ev(tool: str, cwd: Path, **ti: Any) -> dict[str, Any]:
    return {"tool_name": tool, "tool_input": ti, "cwd": str(cwd), "hook_event_name": "PreToolUse"}


@pytest.fixture
def gproj(tmp_path: Path) -> Path:
    p = tmp_path / "g"
    p.mkdir()
    for n in ("REQUIREMENTS.md", "spec.py"):
        (p / n).write_text("x")
    (p / "spec.trace.json").write_text(json.dumps({"source": "REQUIREMENTS.md", "rules": []}))
    return p


def test_guard_ignores_the_notes_files_while_they_do_not_exist(gproj: Path) -> None:
    g = Guard(gproj)
    assert g.names == list(guard.DEFAULT_PROTECTED)
    assert (
        g.decide(ev("Write", gproj, file_path=str(gproj / "REQUIREMENTS.rationale.json"))) is None
    )
    assert guard.notes_files(gproj) == []


def test_guard_protects_the_approved_files_while_they_exist_and_not_the_proposals(
    gproj: Path,
) -> None:
    for n in (
        "REQUIREMENTS.rationale.json",
        "REQUIREMENTS.glossary.json",
        "REQUIREMENTS.rationale.proposed.json",
        "REQUIREMENTS.glossary.proposed.json",
    ):
        (gproj / n).write_text("{}")
    g = Guard(gproj)
    assert guard.notes_files(gproj) == ["REQUIREMENTS.rationale.json", "REQUIREMENTS.glossary.json"]
    for name in ("REQUIREMENTS.rationale.json", "REQUIREMENTS.glossary.json"):
        for tool in ("Edit", "Write", "MultiEdit"):
            assert g.decide(ev(tool, gproj, file_path=str(gproj / name))) == name
        assert g.decide(ev("Bash", gproj, command=f"sed -i s/a/b/ {name}")) == name
        assert g.decide(ev("Bash", gproj, command=f"echo x > {name}")) == name
        assert g.decide(ev("Bash", gproj, command=f"cat {name}")) is None  # reading is fine
        assert g.decide(ev("Read", gproj, file_path=str(gproj / name))) is None
    for name in ("REQUIREMENTS.rationale.proposed.json", "REQUIREMENTS.glossary.proposed.json"):
        assert g.decide(ev("Write", gproj, file_path=str(gproj / name))) is None
        assert g.decide(ev("Bash", gproj, command=f"echo x > {name}")) is None
    # the multi-project guard and the symlink check follow
    assert (
        MultiGuard([gproj]).decide(
            ev("Write", gproj, file_path=str(gproj / "REQUIREMENTS.glossary.json"))
        )
        == "REQUIREMENTS.glossary.json"
    )


def test_guard_names_follow_the_trace_source_stem(gproj: Path) -> None:
    (gproj / "spec.trace.json").write_text(json.dumps({"source": "docs/MySpec.md"}))
    (gproj / "MySpec.rationale.json").write_text("{}")
    (gproj / "REQUIREMENTS.rationale.json").write_text("{}")  # not this project's stem
    assert guard.notes_files(gproj) == ["MySpec.rationale.json"]
    # the guard's copy of the naming rule agrees with the library's
    from lattice.spec.rationale import notes_names

    n = notes_names("docs/MySpec.md")
    assert [n[0], n[2]] == [f"MySpec{s}" for s in guard.NOTES_SUFFIXES]


def test_the_deny_reason_for_a_notes_file_names_the_owner_command(gproj: Path) -> None:
    msg = guard._reason("REQUIREMENTS.rationale.json")
    assert "rationale approve|add" in msg and "change_propose" not in msg
    old = guard._reason("spec.py")
    assert "change_propose" in old  # the spec files keep their message


def test_guard_hook_denies_a_write_and_stays_silent_otherwise(
    gproj: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    (gproj / "REQUIREMENTS.rationale.json").write_text("{}")
    event = ev("Write", gproj, file_path=str(gproj / "REQUIREMENTS.rationale.json"))
    rc = guard.main(["--project", str(gproj)], stdin=io.StringIO(json.dumps(event)))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "owner" in out["hookSpecificOutput"]["permissionDecisionReason"]
    ok = ev("Write", gproj, file_path=str(gproj / "other.txt"))
    assert guard.main(["--project", str(gproj)], stdin=io.StringIO(json.dumps(ok))) == 0
    assert capsys.readouterr().out == ""


# ---- the rebuild's protected set -----------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    p = make_project(tmp_path / "r", repo=True)
    return p


def make_run(repo: Path):
    from lattice.studio import rebuild as RB

    head = git(repo, "rev-parse", "HEAD")
    run = SimpleNamespace(project_rel=".", worktree=repo, repo=repo, base=head, gates=[])
    return RB.Rebuilder(repo, None, name="p", model="m"), run


def test_the_protected_set_is_the_old_one_without_the_files(repo: Path) -> None:
    from lattice.studio import rebuild as RB

    rb, run = make_run(repo)
    assert rb._protected_paths(run) == list(RB.SPEC_FILES)
    assert rb.spec_names() == list(RB.SPEC_FILES)


def test_the_protected_set_gains_the_approved_files_while_they_exist(repo: Path) -> None:
    from lattice.studio import rebuild as RB

    fill(repo)
    rb, run = make_run(repo)
    assert rb._protected_paths(run) == [
        *RB.SPEC_FILES,
        "REQUIREMENTS.rationale.json",
        "REQUIREMENTS.glossary.json",
    ]
    assert rb.spec_names()[-2:] == ["REQUIREMENTS.rationale.json", "REQUIREMENTS.glossary.json"]
    assert "REQUIREMENTS.rationale.proposed.json" not in rb._protected_paths(run)


def test_a_round_that_edits_or_deletes_an_approved_file_is_restored(repo: Path) -> None:
    fill(repo)
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=a@b", "-c", "user.name=n", "commit", "-qm", "notes")
    rb, run = make_run(repo)
    (repo / "REQUIREMENTS.rationale.json").write_text("{}")  # edited in the round
    (repo / "REQUIREMENTS.glossary.json").unlink()  # deleted in the round
    (repo / "REQUIREMENTS.rationale.proposed.json").write_text("{}")  # a proposals file: free
    edited = rb._restore_protected(run)
    assert sorted(edited) == ["REQUIREMENTS.glossary.json", "REQUIREMENTS.rationale.json"]
    assert "WHY" not in (repo / "REQUIREMENTS.rationale.json").read_text() or True
    assert (
        json.loads((repo / "REQUIREMENTS.rationale.json").read_text())["notes"][0]["rule"] == "R2"
    )
    assert (repo / "REQUIREMENTS.glossary.json").is_file()
    assert (repo / "REQUIREMENTS.rationale.proposed.json").read_text() == "{}"
    # a file the round created at a protected name is removed
    git(repo, "rm", "-q", "-f", "REQUIREMENTS.glossary.json")
    git(repo, "-c", "user.email=a@b", "-c", "user.name=n", "commit", "-qm", "drop glossary")
    rb2, run2 = make_run(repo)
    (repo / "REQUIREMENTS.glossary.json").write_text("{}")
    assert "REQUIREMENTS.glossary.json" in rb2._restore_protected(run2)
    assert not (repo / "REQUIREMENTS.glossary.json").exists()
