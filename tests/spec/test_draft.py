"""lattice.spec.draft: an LLM drafts a spec module + trace from markdown requirements;
the draft is imported in isolation, validated and its trace checked, with repair rounds.

Scripted (ScriptedGenerator) except the one live smoke test, which runs only with
LATTICE_LIVE_LLM=1 (the Claude Code CLI, haiku; never an API key)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from lattice.llm.client import ScriptedGenerator
from lattice.spec import CODES
from lattice.spec.draft import (
    DRAFTING_RULES,
    api_reference,
    check_trace,
    draft,
    draft_json_path,
    evaluate,
    load_spec,
    main,
    parse_answer,
    revise,
)
from lattice.spec.rules import Trace, extract_rules, trace_path

REFERENCE = api_reference()
EXAMPLE = REFERENCE[REFERENCE.index("## Worked example") :]
REQUIREMENTS = re.search(r"```markdown\n(.*?)```", EXAMPLE, re.S).group(1)
MODULE, TRACE = parse_answer(EXAMPLE)
assert MODULE is not None and TRACE is not None
CONTEXT = "FastAPI + SQLite; users ann (1), ben (2), ada (3, admin); service: sweeper."


def answer(
    module: str | None = MODULE,
    trace: dict | None = TRACE,
    *,
    trace_first: bool = False,
    module_tag: str = "python",
) -> str:
    parts = []
    if module is not None:
        parts.append(f"Here is the module:\n\n```{module_tag}\n{module}```\n")
    if trace is not None:
        parts.append(f"And the trace:\n\n```json\n{json.dumps(trace, indent=2)}\n```\n")
    if trace_first:
        parts.reverse()
    return "\n".join(parts)


def run(*texts: str, max_repairs: int = 3):
    gen = ScriptedGenerator(list(texts))
    result = draft(
        REQUIREMENTS, context_md=CONTEXT, generator=gen, model="scripted", max_repairs=max_repairs
    )
    return result, gen


def last_user(gen: ScriptedGenerator, call: int) -> str:
    return gen.calls[call][1][-1]["content"]


# ---- the reference --------------------------------------------------------------------------


def test_reference_renders_from_the_code():
    assert "{{" not in REFERENCE
    for name in (
        "Spec(",
        "Scenario(",
        "Step(",
        "Await(",
        "Service(",
        "Invariant(",
        "Machine(",
        "Unchecked(",
        "access_matrix(",
        "RoleColumn(",
    ):
        assert name in REFERENCE, name
    for code in CODES:
        assert code in REFERENCE
    assert "count bound" in REFERENCE and "aggregate equality" in REFERENCE
    assert '"links"' in REFERENCE and "scenario:" in REFERENCE
    assert len(REFERENCE) < 40_000  # compact: about 7k tokens


def test_reference_auth_section_has_conventions_messages_and_a_valid_snippet():
    head, _, rest = REFERENCE.partition("### Auth: what the spec declares")
    assert rest, "the auth section is missing"
    section = rest.split("\n## ", 1)[0]
    for text in (
        "token column 'token' is not a column of User",
        "ApiProfile.auth must be TokenColumn(...) or NativeToken(...)",
        "cannot log in as",
        "handle -> (login, password)",
        "TokenColumn(",
    ):
        assert text in section, text
    snippet = section.split("```python\n", 1)[1].split("```", 1)[0]
    scope: dict = {}
    exec(  # noqa: S102 -- the reference's own snippet, run to prove it is valid
        "from lattice.profile import AdminFlag, ApiProfile, TokenColumn\n"
        "from lattice.typesys import Scalar, Schema\nS = Scalar\n" + snippet,
        scope,
    )
    assert scope["api"].auth.column == "hashed_password"
    assert scope["USERS"]["ann"] == ("ann@example.com", "pw-ann")


def test_reference_worked_example_is_a_valid_draft():
    rules = extract_rules(REQUIREMENTS)
    assert [r.id for r in rules] == [f"R{i}" for i in range(1, 10)]
    spec, trace, problems = evaluate(MODULE, TRACE, rules, "REQUIREMENTS.md")
    assert problems == []
    assert spec is not None and len(spec.scenarios) > 5
    assert trace.untraced() == []


# ---- the loop -------------------------------------------------------------------------------


def test_a_valid_first_answer():
    result, gen = run(answer())
    assert result.valid and result.problems == []
    assert result.rounds == 1 and result.usage.calls == 1
    assert [r.id for r in result.trace.rules] == [r.id for r in extract_rules(REQUIREMENTS)]
    assert result.trace.source == "REQUIREMENTS.md"
    assert result.trace.questions[0].id == "Q1"
    assert result.spec is not None and result.module_source == MODULE
    system, messages = gen.calls[0]
    assert "Trace every rule" in system and "## The access matrix" in system
    assert DRAFTING_RULES.splitlines()[0] in system
    first = messages[0]["content"]
    assert "R4 [Todo app > Todos]: A member has at most 50 open todos" in first
    assert CONTEXT in first
    assert [t["role"] for t in result.transcript] == ["user", "assistant", "check"]


def test_an_s_code_then_repaired():
    bad = MODULE.replace('count(Todo, by="user_id"', 'count(Todo, by="owner"')
    result, gen = run(answer(bad), answer())
    assert result.valid and result.rounds == 2
    repair = last_user(gen, 1)
    assert "S003" in repair and "owner" in repair
    assert gen.calls[1][1][1]["role"] == "assistant"
    assert "```python" in gen.calls[1][1][1]["content"]


def test_an_untraced_rule_then_repaired():
    trace = {**TRACE, "unchecked": {}}
    result, gen = run(answer(trace=trace), answer())
    assert result.valid and result.rounds == 2
    assert "trace: R9 is not traced" in last_user(gen, 1)


def test_a_link_to_a_missing_scenario_then_repaired():
    links = {**TRACE["links"], "R3": ["scenario:title_limits"]}
    result, gen = run(answer(trace={**TRACE, "links": links}), answer())
    assert result.valid and result.rounds == 2
    repair = last_user(gen, 1)
    assert "R3 links 'scenario:title_limits', which names no scenario of SPEC" in repair


def test_a_broken_import_then_repaired():
    bad = MODULE.replace(
        "from lattice.spec import access_matrix", "from lattice.spec import acess_matrix"
    )
    result, gen = run(answer(bad), answer())
    assert result.valid and result.rounds == 2
    assert "import: ImportError" in last_user(gen, 1)


def test_a_runtime_error_names_its_line():
    bad = MODULE.replace('owner="user_id"', 'owner="owner_id"')
    spec, problems = load_spec(bad)
    assert spec is None
    assert problems[0].startswith("import: ValueError: owner column 'owner_id' not in schema")
    assert "(line " in problems[0]


def test_a_syntax_error_and_a_missing_spec():
    assert load_spec("SPEC = (")[1][0].startswith("import: SyntaxError")
    assert load_spec("x = 1\n")[1] == ["import: the module defines no SPEC"]
    assert load_spec("SPEC = 1\n")[1] == ["import: SPEC is a int, not a lattice.spec.Spec"]


def test_gives_up_after_max_repairs():
    bad = answer(trace={**TRACE, "context": []})
    result, gen = run(bad, bad, max_repairs=1)
    assert not result.valid and result.rounds == 2 and len(gen.calls) == 2
    assert any("R1 is not traced" in p for p in result.problems)


def test_a_repair_answer_may_send_only_the_module():
    bad = MODULE.replace('"open_todos_at_most_50", "A', '"open_todos_at_most_50", 7, "A')
    result, gen = run(answer(bad), answer(trace=None))
    assert result.valid and result.rounds == 2
    assert result.trace.links == TRACE["links"]


def test_no_trace_at_all_is_a_problem():
    result, _ = run(answer(trace=None), max_repairs=0)
    assert not result.valid
    assert any(p.startswith("trace: no trace JSON object") for p in result.problems)


# ---- the trace checks -----------------------------------------------------------------------


def _trace(**overrides) -> Trace:
    rules = extract_rules(REQUIREMENTS)
    _, trace, _ = evaluate(None, {**TRACE, **overrides}, rules, "x")
    return trace


def test_a_rule_in_two_places_and_an_unknown_rule_id():
    spec, _ = load_spec(MODULE)
    trace = _trace(context=["R1", "R8", "R42"])
    problems = check_trace(trace, spec)
    assert "trace: R8 is in links and context; put it in exactly one" in problems
    assert any(p.startswith("trace: R42 is not a rule id") for p in problems)


def test_every_link_kind_is_checked():
    spec, _ = load_spec(MODULE)
    links = {
        **TRACE["links"],
        "R8": ["machine:todos.state", "profile:Todo.title", "profile:todos", "service:sweeper"],
    }
    assert check_trace(_trace(links=links), spec) == []
    bad = {
        **TRACE["links"],
        "R8": [
            "machine:Todo.title",
            "profile:Todo.colour",
            "service:mailer",
            "invariant:nope",
            "widget:x",
            "nokind",
        ],
    }
    problems = check_trace(_trace(links=bad), spec)
    assert len(problems) == 6, problems


def test_an_unchecked_rule_needs_an_unchecked_note():
    spec, _ = load_spec(MODULE.replace('Unchecked("R9: ', 'Unchecked("'))
    problems = check_trace(_trace(), spec)
    assert any("R9 is unchecked but SPEC has no Unchecked note citing it" in p for p in problems)


def test_malformed_questions_are_reported():
    rules = extract_rules(REQUIREMENTS)
    raw = {
        **TRACE,
        "questions": [
            {"id": "Q1", "text": "?"},
            {"id": "Q2", "text": "t", "default": "d", "rules": ["R99"]},
        ],
    }
    _, _, problems = evaluate(MODULE, raw, rules, "x")
    assert "trace: question 1 needs an id, a text and a default (texts)" in problems
    assert "trace: question Q2 names R99, which is not a rule id" in problems


# ---- revise ---------------------------------------------------------------------------------


def _first_draft() -> tuple[str, Trace]:
    result, _ = run(answer())
    return result.module_source, result.trace


def test_revise_keeps_the_rule_ids_and_sends_the_feedback():
    module, trace = _first_draft()
    feedback = '{"findings": [{"rule": "R3", "text": "no 0-length title case"}]}'
    # the model's answer renumbers its own copy of the rules: ignored, ids are the tool's
    revised_trace = {**TRACE, "rules": [{"id": "X1", "text": "?"}]}
    gen = ScriptedGenerator([answer(trace=revised_trace)])
    result = revise(
        module,
        trace,
        feedback,
        requirements_md=REQUIREMENTS,
        context_md=CONTEXT,
        generator=gen,
        model="scripted",
    )
    assert result.valid and result.rounds == 1
    assert [r.id for r in result.trace.rules] == [r.id for r in trace.rules]
    first = gen.calls[0][1][0]["content"]
    assert feedback in first and "# The current spec module" in first
    assert "def nothing_with(title: str)" in first and '"Q1"' in first
    assert "keep them" in first


def test_revise_dropping_a_question_is_repaired():
    module, trace = _first_draft()
    gen = ScriptedGenerator([answer(trace={**TRACE, "questions": []}), answer()])
    result = revise(
        module, trace, "lint: fine", requirements_md=REQUIREMENTS, generator=gen, model="scripted"
    )
    assert result.valid and result.rounds == 2
    assert "question Q1 was dropped" in last_user(gen, 1)


# ---- parsing --------------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["python", "py", "Python", ""])
def test_parse_module_fences(tag):
    module, trace = parse_answer(answer(module_tag=tag))
    assert module == MODULE and trace == TRACE


def test_parse_trace_before_the_module():
    module, trace = parse_answer(answer(trace_first=True))
    assert module == MODULE and trace == TRACE


def test_parse_a_bare_trace_and_crlf():
    text = f"```python\n{MODULE}```\n\nTrace: {json.dumps(TRACE)}\nDone.".replace("\n", "\r\n")
    module, trace = parse_answer(text)
    assert module is not None and "SPEC = Spec(" in module and trace == TRACE


def test_parse_prefers_the_block_defining_spec():
    text = "```python\nprint('helper')\n```\n" + answer()
    assert parse_answer(text)[0] == MODULE


def test_parse_ignores_json_without_trace_keys():
    text = '```json\n{"example": 1}\n```\n' + answer()
    assert parse_answer(text)[1] == TRACE


def test_parse_nothing():
    assert parse_answer("I cannot help with that.") == (None, None)


# ---- the CLI --------------------------------------------------------------------------------


def test_main_writes_the_module_trace_and_record(tmp_path: Path):
    req = tmp_path / "REQUIREMENTS.md"
    req.write_text(REQUIREMENTS)
    ctx = tmp_path / "CONTEXT.md"
    ctx.write_text(CONTEXT)
    out = tmp_path / "spec_gen.py"
    code = main(
        ["draft", str(req), "--context", str(ctx), "--out", str(out)],
        generator=ScriptedGenerator([answer()]),
    )
    assert code == 0
    assert "SPEC = Spec(" in out.read_text()
    trace = Trace.load(trace_path(out))
    assert trace.source == str(req) and trace.untraced() == []
    record = json.loads(draft_json_path(out).read_text())
    assert record["valid"] and record["rounds"] == 1 and record["problems"] == []
    assert record["usage"]["calls"] == 1

    lint = tmp_path / "lint.json"
    lint.write_text('{"findings": []}')
    code = main(
        [str(req), "--out", str(out), "--revise", "--feedback", str(lint), "--max-repairs", "0"],
        generator=ScriptedGenerator([answer(trace={**TRACE, "context": []})]),
    )
    assert code == 1
    record = json.loads(draft_json_path(out).read_text())
    assert record["mode"] == "revise" and not record["valid"]


def test_main_bad_arguments(tmp_path: Path):
    assert main(["--out", "x.py"]) == 2
    assert (
        main(
            [str(tmp_path / "missing.md"), "--out", str(tmp_path / "x.py")],
            generator=ScriptedGenerator([]),
        )
        == 2
    )
    assert main([str(tmp_path / "missing.md"), "--out", "x.py", "--revise"]) == 2


# ---- live smoke test ------------------------------------------------------------------------

TOY_REQUIREMENTS = """\
# Notes app

- A signed-in user creates a note with POST /notes {"text": ...}; it answers 201 with the note.
- A note's text is 1 to 200 characters; anything else answers 422.
- GET /notes/{id} answers the caller's own note; another user's note answers 404.
- A user has at most 3 notes; a fourth answers 409.
- Requests without a token answer 401.
"""
TOY_CONTEXT = """\
Stack: FastAPI + SQLite, errors as {"detail": ...}, bearer tokens stored in users.token.
Tables (SQL names): users(id INTEGER, email TEXT, password TEXT, token TEXT) and
notes(id INTEGER, user_id INTEGER -> users.id, text TEXT NOT NULL). No roles: every user
is a "Member" (NoRoles("Member"); roles Public and Member).
Seed: users ann (id 1, login "ann@example.com", password "pw-ann") and ben (id 2,
"ben@example.com", "pw-ben"); no notes. No background services.
"""


@pytest.mark.skipif(
    os.environ.get("LATTICE_LIVE_LLM") != "1", reason="live LLM smoke test: set LATTICE_LIVE_LLM=1"
)
def test_live_smoke_haiku_draft_validates(tmp_path: Path):
    from lattice.llm.cli_client import ClaudeCliClient

    gen = ClaudeCliClient(model="haiku", max_tokens=32000, effort="", timeout=900)
    result = draft(TOY_REQUIREMENTS, context_md=TOY_CONTEXT, generator=gen, model="haiku")
    out = tmp_path / "toy.draft.json"
    out.write_text(
        json.dumps(
            {"usage": result.usage.as_dict(), "rounds": result.rounds, "problems": result.problems},
            indent=2,
        )
    )
    print(json.dumps({"rounds": result.rounds, "usage": result.usage.as_dict()}, indent=2))
    assert len(result.trace.rules) == 5
    assert result.problems == [], result.problems
    assert result.trace.untraced() == []
