"""The business glossary (lattice.spec.glossary): each rule of D6 on unit fixtures, the lint codes
G001-G004 (kept out of lint()), the metrics, the files, the deterministic draft, the owner's
approval and its guard."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from lattice.llm.client import ScriptedGenerator
from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Spec
from lattice.spec import glossary as gl
from lattice.spec import rationale as rt
from lattice.spec.rationale import NotesError
from lattice.spec.rules import Rule, Trace
from lattice.typesys import Scalar, Schema
from tests.spec import racy

S = Scalar
NUDGE = Schema.from_pairs(
    [("id", S.INT), ("kind", S.STR), ("delivery", S.STR)],
    keys=[("id",)],
    one_of={"kind": ("on_track", "behind"), "delivery": ("pending", "sent", "failed")},
)
HABIT_TARGET = Schema.from_pairs([("id", S.INT), ("count", S.INT)], keys=[("id",)])
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={
            "User": racy.USER,
            "NotificationPrefs": racy.CONFIG,
            "Nudge": NUDGE,
            "HabitTarget": HABIT_TARGET,
        },
        table_names={
            "User": "users",
            "NotificationPrefs": "notification_prefs",
            "Nudge": "nudges",
            "HabitTarget": "habit_targets",
        },
        roles=frozenset({"Public", "Member"}),
        users_table="User",
        roles_from=RoleColumn("name"),
        error_body="fastapi",
        unauthorized_detail="Not authenticated",
    ),
)
SPEC = Spec(profile=PROFILE, services={"nudger": "runs the daily pass"}, users=racy.USERS)


def trace_of(*texts: str, context: tuple[str, ...] = ()) -> Trace:
    rules = [Rule(f"R{i}", t, "Sec", i) for i, t in enumerate(texts, 1)]
    return Trace("REQUIREMENTS.md", rules, context=list(context))


def term(
    name: str, definition: str = "fixture", aliases: tuple[str, ...] = (), owner: str = "o"
) -> dict[str, Any]:
    return {
        "term": name,
        "aliases": list(aliases),
        "definition": definition,
        "owner": owner,
        "status": "approved",
        "author": "owner",
        "approved_by": "o",
        "approved_sha": hashlib.sha256(
            json.dumps(
                {"definition": definition, "owner": owner},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
        "basis": [],
    }


def gfile(terms: list[dict[str, Any]], ignore: list[str] | None = None) -> str:
    return json.dumps(
        {
            "schema": "lattice.glossary/1",
            "source": "REQUIREMENTS.md",
            "terms": terms,
            "ignore": ignore or [],
        }
    )


def uses(
    trace: Trace,
    glossary: list[dict[str, Any]] | None = None,
    ignore: list[str] | None = None,
    tmp: Path | None = None,
) -> list[tuple[str, str]]:
    """(rule, normal form) of every G001 finding."""
    gset = None
    if glossary is not None:
        assert tmp is not None
        p = tmp / "REQUIREMENTS.glossary.json"
        p.write_text(gfile(glossary, ignore))
        paths = rt.notes_paths(tmp / "x.json", source="REQUIREMENTS.md")
        gset = gl.load_glossary(paths)
    res = gl.lint_glossary(gl.analyse(SPEC, trace), trace, gset)
    return [(f["rule"], f["normal"]) for f in res.findings if f["code"] == "G001"]


def pairs_all(*texts: str) -> set[tuple[str, str]]:
    return set(uses(trace_of(*texts)))


# ---- normal forms ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Active Window", ("active", "window")),
        ("on_track", ("on", "track")),
        ("in-app", ("in", "app")),
        ("today's", ("today",)),
        ("users'", ("user",)),
        ("habit's target", ("habit's", "target")),  # the possessive goes from the last word only
        ("devices", ("device",)),
        ("activities", ("activity",)),
        ("dies", ("die",)),  # 'ies' needs a word longer than 4 letters; else the 's' goes
        ("status", ("status",)),
        ("class", ("class",)),
        ("this", ("this",)),
        ("ios", ("ios",)),
        ("days", ("day",)),
        ("grace days", ("grace", "day")),
        ("Grace Day", ("grace", "day")),
        ("it’s", ("it",)),
    ],
)
def test_normal_form(text: str, want: tuple[str, ...]) -> None:
    assert gl.norm_phrase(text) == want


def test_slug() -> None:
    assert gl.slug("on_track") == "on-track"
    assert gl.slug("active window") == "active-window"
    assert gl.slug("  --Past  Period-- ") == "past-period"


# ---- C1 entity ------------------------------------------------------------------------------


def test_c1_matches_the_schema_name_split_at_camel_case_and_the_sql_name() -> None:
    assert pairs_all("A notification prefs row exists.") == {("R1", "notification pref")}
    assert pairs_all("The notification_prefs table has one row.") == {("R1", "notification pref")}
    assert pairs_all("Each habit_targets row holds a count.") >= {("R1", "habit target")}
    assert ("R1", "habit target") in pairs_all("A habit target is a number.")


def test_c1_plurals_and_code_tokens_match_whole_tokens() -> None:
    assert ("R1", "user") in pairs_all("Users sign in.")
    assert ("R1", "user") in pairs_all("See `users` for the table.")
    # a code token is one phrase: `habit_targets` is the entity, `target_habit` is nothing
    assert ("R1", "habit target") in pairs_all("Rows of `habit_targets` change.")
    assert pairs_all("Rows of `target_habit` change.") == set()


def test_possessive_is_stripped_from_the_last_word_only() -> None:
    assert ("R1", "user") in pairs_all("A user's own data.")
    assert ("R1", "habit target") not in pairs_all("A habit's target is a number.")
    assert ("R1", "habit target") in pairs_all("The habit target's value.")


# ---- C2 enum values ------------------------------------------------------------------------------


def test_c2_enum_values_match_in_prose_and_in_code() -> None:
    assert ("R1", "on track") in pairs_all("A habit that is on track has no nudge.")
    assert ("R1", "on track") in pairs_all("`kind` is `on_track`.")
    assert ("R1", "failed") in pairs_all("The nudge `failed`.")
    assert ("R1", "failed") in pairs_all("The push failed.")  # ordinary English counts too
    assert ("R1", "failed") not in pairs_all("Answers `validation_failed`.")  # whole token
    assert ("R1", "sent") in pairs_all("The message was sent.")


# ---- C5 service ----------------------------------------------------------------------------------


def test_c5_service_names_match_in_prose_and_code() -> None:
    assert ("R1", "nudger") in pairs_all("The nudger runs every minute.")
    assert ("R1", "nudger") in pairs_all("`nudger` runs every minute.")


# ---- C3 marked phrases ---------------------------------------------------------------------------


def c3(text: str) -> list[str]:
    return gl.marked_phrases(gl.prose_and_code(text)[0])


def test_c3_quoted_and_emphasised_phrases_of_one_to_four_words() -> None:
    assert c3('A "grace day" is allowed.') == ["grace day"]
    assert c3("A “grace day” is allowed.") == ["grace day"]
    assert c3("A *grace day* is allowed.") == ["grace day"]
    assert c3("A **grace day** is allowed.") == ["grace day"]
    assert c3("A _grace day_ is allowed.") == ["grace day"]
    assert c3('The "one two three four" phrase.') == ["one two three four"]
    assert c3('The "one two three four five" phrase.') == []  # five words
    assert c3('The "item 7" phrase.') == []  # a digit
    assert c3("A snake_case_word is not emphasised.") == []
    assert c3("2 * 3 * 4 is not either.") == []


def test_c3_excludes_json_brackets_and_keys() -> None:
    assert c3('A login answers {"token_type": "bearer"} as JSON.') == []
    assert c3('The list ["alpha", "beta"] is fixed.') == []
    assert c3('The field "email": must be valid.') == []
    assert c3('Outside "grace day" but {"inside": 1}.') == ["grace day"]


def test_c3_ignores_backtick_spans() -> None:
    assert c3('`"quoted"` code and "real phrase".') == ["real phrase"]


def test_c3_becomes_a_candidate_everywhere() -> None:
    t = trace_of(
        'A "grace day" lets a user skip.', "Each grace day counts once.", "Two grace days a month."
    )
    assert set(uses(t)) >= {("R1", "grace day"), ("R2", "grace day"), ("R3", "grace day")}


def test_unmarked_phrases_and_unknown_backticks_are_not_seen() -> None:
    assert pairs_all("A user on a vacation pause receives no pushes.") == {("R1", "user")}
    assert ("R1", "snoozed") not in pairs_all("A habit in `snoozed` state gets no nudge.")


# ---- C6 inline definitions -----------------------------------------------------------------------


def c6(text: str) -> str | None:
    return gl.inline_definition(gl.prose_and_code(text)[0])


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("The active window is the span of days.", "active window"),
        ("A focus habit is a habit the user pins.", "focus habit"),
        ("An access token is short lived.", "access token"),
        ("The user's habit name is unique.", "habit name"),
        ("The refresh token (opaque) is a random string.", "refresh token"),
        ("A past period is one whose last day is before today.", "past period"),
        ('A "grace day" is allowed.', "grace day"),  # quote marks removed first
        ("The *own* habits are listed.", "own habits"),  # marks are removed; 'are' defines too
        ("The password means a secret.", "password"),
        ("the lowercase start is ignored.", None),
        ("A habit of the user is odd.", None),  # a stop word inside P
        ("The one two three four five is long.", None),  # P is 1-4 words
        ("Every user is signed in.", None),  # not A / An / The
        ("A user without a habit is skipped.", None),
    ],
)
def test_c6(text: str, want: str | None) -> None:
    assert c6(text) == want


def test_c6_records_the_defining_rule_and_is_project_wide() -> None:
    t = trace_of(
        "The active window is the span of days.", "Inside the active window a nudge is sent."
    )
    an = gl.analyse(SPEC, t)
    c = an.candidates[("active", "window")]
    assert c.defined_in == ["R1"] and "C6" in c.sources
    assert ("R2", "active window") in {
        (r, c_.normal) for r, u in an.uses.items() for c_ in [an.candidates[k] for k in u]
    }


# ---- longest first, no overlap -------------------------------------------------------------------


def test_longest_match_consumes_its_words() -> None:
    t = trace_of("A dismissed nudge is one the user closed.", "The nudge is shown.")
    got = set(uses(t))
    assert ("R1", "dismissed nudge") in got
    assert ("R1", "nudge") not in got  # consumed by the longer phrase in R1
    assert ("R2", "nudge") in got


def test_a_candidate_counts_once_per_rule() -> None:
    t = trace_of("A user, another user and a third user.")
    assert uses(t).count(("R1", "user")) == 1


def test_context_rules_are_scanned_too() -> None:
    t = trace_of("A user signs in.", context=("R1",))
    assert ("R1", "user") in uses(t)


# ---- defined, ignored, edited --------------------------------------------------------------------


def test_defined_by_a_term_an_alias_or_the_ignore_list(tmp_path: Path) -> None:
    t = trace_of('A "grace day" skips a nudge.', "A user signs in.")
    base = set(uses(t))
    assert {("R1", "grace day"), ("R1", "nudge"), ("R2", "user")} <= base
    got = set(uses(t, [term("grace day"), term("nudge"), term("user")], tmp=tmp_path))
    assert got == set()
    got2 = set(
        uses(
            t,
            [term("allowance", aliases=("grace days",)), term("nudge")],
            ignore=["user"],
            tmp=tmp_path,
        )
    )
    assert got2 == set()
    assert set(uses(t, [term("nudge")], ignore=["User"], tmp=tmp_path)) == {("R1", "grace day")}


def test_an_edited_term_defines_nothing_and_is_a_g003(tmp_path: Path) -> None:
    t = trace_of("A user signs in.")
    edited = {**term("user"), "definition": "changed after approval"}
    p = tmp_path / "REQUIREMENTS.glossary.json"
    p.write_text(gfile([edited]))
    gset = gl.load_glossary(rt.notes_paths(tmp_path / "x.json", source="REQUIREMENTS.md"))
    res = gl.lint_glossary(gl.analyse(SPEC, t), t, gset)
    codes = [f["code"] for f in res.findings]
    assert codes.count("G001") == 1 and codes.count("G003") == 1 and res.exit_code == 1


# ---- the lint, its codes and its order -----------------------------------------------------------


def test_findings_are_sorted_by_code_rule_number_then_term(tmp_path: Path) -> None:
    t = trace_of("A user sees a nudge.", "A user sees a sent nudge.", *(["x"] * 8), "A nudge.")
    res = gl.lint_glossary(gl.analyse(SPEC, t), t, None)
    got = [(f["code"], f["rule"], f["term"]) for f in res.findings]
    assert got == sorted(got, key=lambda g: (g[0], int(g[1][1:]), g[2]))
    assert got[0][1] == "R1" and got[-1][1] == "R11"


def test_finding_fields_and_fix_text(tmp_path: Path) -> None:
    t = trace_of("The active window is the span of days.", "Use the active window.")
    res = gl.lint_glossary(gl.analyse(SPEC, t), t, None, names=("G.json", "G.proposed.json"))
    f = next(x for x in res.findings if x["rule"] == "R2" and x["normal"] == "active window")
    assert f["source"] == "C6" and f["code"] == "G001" and f["match"] == "active window"
    assert "G.json" in f["fix"] and "'The active window is the span of days.'" in f["fix"]


def test_a_pending_proposal_changes_the_fix(tmp_path: Path) -> None:
    t = trace_of("A user signs in.")
    prop = {
        **term("user"),
        "status": "proposed",
        "owner": None,
        "approved_by": None,
        "approved_sha": None,
    }
    (tmp_path / "REQUIREMENTS.glossary.proposed.json").write_text(gfile([prop]))
    gset = gl.load_glossary(rt.notes_paths(tmp_path / "x.json", source="REQUIREMENTS.md"))
    res = gl.lint_glossary(gl.analyse(SPEC, t), t, gset)
    g001 = next(f for f in res.findings if f["code"] == "G001")
    assert "approve the proposed definition" in g001["fix"]
    assert [f["code"] for f in res.findings].count("G004") == 1
    assert res.metrics["GP"] == 1 and res.exit_code == 1


def test_g002_reports_an_approved_term_no_rule_uses(tmp_path: Path) -> None:
    t = trace_of("A user signs in.")
    (tmp_path / "REQUIREMENTS.glossary.json").write_text(
        gfile([term("user"), term("daily pass"), term("sent")])
    )
    gset = gl.load_glossary(rt.notes_paths(tmp_path / "x.json", source="REQUIREMENTS.md"))
    res = gl.lint_glossary(gl.analyse(SPEC, t), t, gset)
    assert sorted(f["term"] for f in res.findings if f["code"] == "G002") == ["daily pass", "sent"]
    assert res.metrics["GX"] == 2 and res.exit_code == 0  # G002 is information


def test_g003_reports_a_rationale_note_that_is_not_current(tmp_path: Path) -> None:
    t = trace_of("A user signs in.")
    n = {
        "rule": "R1",
        "anchor": "0" * 64,
        "why": "w",
        "tradeoff": None,
        "owner": "o",
        "status": "approved",
        "author": "owner",
        "approved_by": "o",
        "basis": [],
        "refs": [],
        "approved_sha": rt.approved_sha("w", None, "o"),
    }
    (tmp_path / "REQUIREMENTS.rationale.json").write_text(
        json.dumps({"schema": "lattice.rationale/1", "source": "REQUIREMENTS.md", "notes": [n]})
    )
    notes = gl.load_notes(t, rt.notes_paths(tmp_path / "x.json", source="REQUIREMENTS.md"))
    res = gl.lint_glossary(gl.analyse(SPEC, t), t, None, notes.rationale)
    (f,) = [x for x in res.findings if x["code"] == "G003"]
    assert f["rule"] == "R1" and f["state"] == "stale" and res.exit_code == 1


def test_the_metrics_are_integers_and_exact_ratios(tmp_path: Path) -> None:
    t = trace_of("A user signs in.", "Nothing here.", "A nudge is sent.", "A user.")
    (tmp_path / "REQUIREMENTS.glossary.json").write_text(gfile([term("user")]))
    gset = gl.load_glossary(rt.notes_paths(tmp_path / "x.json", source="REQUIREMENTS.md"))
    m = gl.lint_glossary(gl.analyse(SPEC, t), t, gset).metrics
    assert (m["GU"], m["GT"], m["GR"]) == (2, 2, 1)  # R3: nudge, sent
    assert m["GC"] == {"n": 2, "d": 3, "pct": 66.7}  # R1 and R4 clean; R2 uses nothing
    assert (m["GA"], m["GP"], m["GX"]) == (1, 0, 0)
    assert m["file"] == "REQUIREMENTS.glossary.json"


def test_an_empty_glossary_file_is_loud_and_no_file_is_null() -> None:
    t = trace_of("A user signs in.")
    m = gl.lint_glossary(gl.analyse(SPEC, t), t, None).metrics
    assert m["file"] is None and m["GU"] == 1 and m["GA"] == 0


def test_glossary_findings_never_enter_lint() -> None:
    from lattice.spec.lint import CODES, lint

    assert not any(c.startswith("G") for c in CODES)
    t = trace_of('An unlisted "grace day" word.')
    assert all(not f.code.startswith("G") for f in lint(racy.RUNS_SPEC, t))


# ---- files (D5, D14) -----------------------------------------------------------------------------


def bad_g(tmp_path: Path, text: str, name: str = "REQUIREMENTS.glossary.json") -> str:
    (tmp_path / name).write_text(text)
    with pytest.raises(NotesError) as e:
        gl.load_glossary(rt.notes_paths(tmp_path / "x.json", source="REQUIREMENTS.md"))
    assert e.value.code == "bad_glossary"
    return str(e.value)


def test_invalid_glossary_files_answer_bad_glossary(tmp_path: Path) -> None:
    ok = term("grace day", aliases=("allowance day",))
    assert "valid JSON" in bad_g(tmp_path, "{x")
    assert "top level" in bad_g(tmp_path, "[]")
    assert "schema" in bad_g(tmp_path, json.dumps({"schema": "no", "terms": []}))
    assert "terms must be a list" in bad_g(tmp_path, json.dumps({"schema": "lattice.glossary/1"}))
    assert "definition is empty" in bad_g(tmp_path, gfile([{**ok, "definition": ""}]))
    assert "longer than 2000" in bad_g(tmp_path, gfile([{**ok, "definition": "x" * 2001}]))
    assert "aliases" in bad_g(tmp_path, gfile([{**ok, "aliases": [1]}]))
    assert "term must be" in bad_g(tmp_path, gfile([{**ok, "term": ""}]))
    assert "author" in bad_g(tmp_path, gfile([{**ok, "author": "bot"}]))
    assert "duplicate" in bad_g(tmp_path, gfile([ok, term("Grace Days")]))
    assert "duplicate" in bad_g(tmp_path, gfile([ok, term("allowance days")]))  # alias collision
    assert "authoritative file holds only approved" in bad_g(
        tmp_path, gfile([{**ok, "status": "proposed"}])
    )
    assert "ignore must be" in bad_g(
        tmp_path, json.dumps({"schema": "lattice.glossary/1", "terms": [], "ignore": [3]})
    )


def test_invalid_proposals_files_answer_bad_glossary(tmp_path: Path) -> None:
    name = "REQUIREMENTS.glossary.proposed.json"
    ok = term("grace day")
    assert "cannot hold an approved term" in bad_g(tmp_path, gfile([ok]), name)
    prop = {
        **ok,
        "status": "proposed",
        "owner": None,
        "approved_by": None,
        "approved_sha": None,
        "author": "llm:sonnet",
    }
    assert "never names the owner" in bad_g(tmp_path, gfile([{**prop, "owner": "Ann"}]), name)
    assert "ignore lives only" in bad_g(tmp_path, gfile([prop], ["x"]), name)
    (tmp_path / name).write_text(gfile([prop, prop]))  # duplicates are fine in proposals
    assert (
        len(
            gl.load_glossary(
                rt.notes_paths(tmp_path / "x.json", source="REQUIREMENTS.md")
            ).proposals
        )
        == 2
    )


def test_term_lookup_by_term_alias_or_slug(tmp_path: Path) -> None:
    (tmp_path / "REQUIREMENTS.glossary.json").write_text(
        gfile([term("active window", aliases=("window",))])
    )
    g = gl.load_glossary(rt.notes_paths(tmp_path / "x.json", source="REQUIREMENTS.md"))
    assert g.term_by("active-window").term == "active window"
    assert g.term_by("Windows").term == "active window"
    assert g.term_by("nothing") is None
    assert gl.find_terms("What is the active window of a habit?", g)[0].slug == "active-window"
    assert gl.find_terms("what about a daily pass", g) == []


# ---- the draft (D9) ------------------------------------------------------------------------------


def test_draft_is_deterministic_and_verbatim(tmp_path: Path) -> None:
    t = trace_of("The active window is the span of days.", "A nudge is sent to a user.")
    out = tmp_path / "p.json"
    paths = rt.notes_paths(tmp_path / "x.json", source="REQUIREMENTS.md")
    rep = gl.draft(SPEC, t, paths, out=out)
    assert rep["approved"] == 0 and rep["llm"] == 0
    terms = json.loads(out.read_text())["terms"]
    by = {x["term"]: x for x in terms}
    assert by["active window"]["definition"] == t.rules[0].text
    assert by["active window"]["author"] == "extracted"
    assert by["active window"]["basis"] == [{"target": "rule:R1", "quote": t.rules[0].text}]
    assert by["on_track"]["definition"] == (
        "From the spec: one of the values of Nudge.kind (on_track, behind)."
    )
    assert by["User"]["definition"] == "From the spec: the table users (User)."
    assert by["nudger"]["definition"] == "runs the daily pass"
    assert all(x["status"] == "proposed" and x["owner"] is None for x in terms)
    # idempotent: a second draft adds nothing and keeps the file
    before = out.read_text()
    assert gl.draft(SPEC, t, paths, out=out)["proposals"] == 0 and out.read_text() == before


def test_a_c6_definition_wins_over_the_table_stub(tmp_path: Path) -> None:
    t = trace_of("A nudge is a message to the user.")
    terms = gl.draft_terms(SPEC, t)
    (nudge,) = [x for x in terms if gl.norm_phrase(x.term) == ("nudge",)]
    assert nudge.definition == t.rules[0].text


def test_draft_refuses_the_approved_file_as_out(tmp_path: Path) -> None:
    t = trace_of("A user signs in.")
    paths = rt.notes_paths(tmp_path / "x.json", source="REQUIREMENTS.md")
    with pytest.raises(NotesError) as e:
        gl.draft(SPEC, t, paths, out=paths.glossary)
    assert e.value.code == "draft_out_is_authoritative"


def test_model_draft_proposes_only_marked_terms_and_drops_forged_fields(tmp_path: Path) -> None:
    t = trace_of('A "grace day" lets a user skip.', "Each grace day counts once.")
    paths = rt.notes_paths(tmp_path / "x.json", source="REQUIREMENTS.md")
    reply = json.dumps(
        {
            "definition": "A day off.",
            "owner": "Eve",
            "status": "approved",
            "basis": [
                {"target": "rule:R1", "quote": "lets a user skip"},
                {"target": "rule:R1", "quote": "not in the rule at all"},
            ],
        }
    )
    gen = ScriptedGenerator([reply])
    rep = gl.draft(SPEC, t, paths, out=tmp_path / "p.json", generator=gen, model="stub")
    assert rep["llm"] == 1 and len(gen.calls) == 1
    (llm,) = [
        x
        for x in json.loads((tmp_path / "p.json").read_text())["terms"]
        if x["author"].startswith("llm:")
    ]
    assert llm["owner"] is None and llm["status"] == "proposed" and llm["author"] == "llm:stub"
    assert {"owner", "status"} <= set(llm["dropped"])
    assert len(llm["basis"]) == 1 and len(llm["rejected_basis"]) == 1


# ---- the CLI and the owner's approval ------------------------------------------------------------


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    (tmp_path / "spec.trace.json").write_text(
        trace_of('A "grace day" lets a user skip.', "A user signs in.").to_json()
    )
    return tmp_path


@pytest.fixture
def human(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(rt, "_terminal", lambda: True)


def cli(*argv: str, **kw: Any) -> int:
    return gl.main(list(argv), **kw)


LINT = ("lint", "--spec", "tests.spec.racy:RUNS_SPEC")


def test_lint_cli_exit_codes_and_json(proj: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tr = str(proj / "spec.trace.json")
    assert cli(*LINT, "--trace", tr, "--json") == 1
    body = json.loads(capsys.readouterr().out)
    assert body["counts"]["G001"] == body["metrics"]["GU"] > 0
    assert cli(*LINT, "--trace", tr) == 1
    assert "G001" in capsys.readouterr().out
    # everything defined: exit 0
    (proj / "REQUIREMENTS.glossary.json").write_text(gfile([term("grace day"), term("user")]))
    assert cli(*LINT, "--trace", tr) == 0
    # an invalid file: exit 2
    (proj / "REQUIREMENTS.glossary.json").write_text("{")
    assert cli(*LINT, "--trace", tr) == 2
    assert "bad_glossary" in capsys.readouterr().err


def test_lint_cli_cannot_run_without_a_trace_or_a_spec(tmp_path: Path) -> None:
    assert cli(*LINT, "--trace", str(tmp_path / "none.json")) == 2
    (tmp_path / "spec.trace.json").write_text(trace_of("A user.").to_json())
    assert cli("lint", "--spec", "no.such:SPEC", "--trace", str(tmp_path / "spec.trace.json")) == 2


def test_show_lists_terms_and_pending_proposals(
    proj: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (proj / "REQUIREMENTS.glossary.json").write_text(
        gfile([term("grace day", "A day off.", aliases=("allowance",))], ["user"])
    )
    prop = {
        **term("pause"),
        "status": "proposed",
        "owner": None,
        "approved_by": None,
        "approved_sha": None,
    }
    (proj / "REQUIREMENTS.glossary.proposed.json").write_text(gfile([prop]))
    tr = str(proj / "spec.trace.json")
    assert cli("show", "--trace", tr) == 0
    out = capsys.readouterr().out
    assert "grace-day: grace day (also: allowance)" in out and "A day off." in out
    assert "pause proposed" in out and "ignore: user" in out
    assert cli("show", "--trace", tr, "--json") == 0
    body = json.loads(capsys.readouterr().out)
    assert body["terms"][0]["slug"] == "grace-day" and body["ignore"] == ["user"]


def test_approve_and_add_write_exactly_the_approved_file(proj: Path, human: None) -> None:
    tr = str(proj / "spec.trace.json")
    assert (
        cli(
            "draft",
            "--trace",
            tr,
            "--spec",
            "tests.spec.racy:RUNS_SPEC",
            "--out",
            str(proj / "p.json"),
        )
        == 0
    )
    before = {p.name for p in proj.iterdir()}
    assert (
        cli(
            "approve",
            "--trace",
            tr,
            "--from",
            str(proj / "p.json"),
            "--term",
            "user",
            "--approver",
            "ann",
            "--owner",
            "bob",
        )
        == 0
    )
    assert {p.name for p in proj.iterdir()} - before == {"REQUIREMENTS.glossary.json"}
    g = gl.load_glossary(rt.notes_paths(proj / "spec.trace.json"))
    (t,) = g.terms
    assert t.term == "User" and t.owner == "bob" and t.sha_ok and t.approved_by == "ann"
    assert t.author == "extracted"  # unedited: the proposal's author is kept
    assert (
        cli(
            "add",
            "--trace",
            tr,
            "--term",
            "grace day",
            "--alias",
            "allowance",
            "--definition",
            "A day off.",
            "--owner",
            "bob",
            "--approver",
            "ann",
        )
        == 0
    )
    assert cli("add", "--trace", tr, "--ignore", "password", "--approver", "ann") == 0
    g = gl.load_glossary(rt.notes_paths(proj / "spec.trace.json"))
    assert sorted(x.slug for x in g.terms) == ["grace-day", "user"] and g.ignore == ["password"]
    assert all(x.sha_ok for x in g.terms)
    # an edited definition makes the owner the author
    assert (
        cli(
            "approve",
            "--trace",
            tr,
            "--from",
            str(proj / "p.json"),
            "--term",
            "queued",
            "--approver",
            "ann",
            "--owner",
            "bob",
            "--definition",
            "My own words.",
            "--retire-proposal",
        )
        == 0
    )
    g = gl.load_glossary(rt.notes_paths(proj / "spec.trace.json"))
    assert g.term_by("queued").author == "owner"
    props = json.loads((proj / "p.json").read_text())["terms"]
    assert next(x for x in props if x["term"] == "queued")["status"] == "rejected"


def test_approval_refuses_without_a_terminal_or_with_claudecode(
    proj: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tr = str(proj / "spec.trace.json")
    cli(
        "draft", "--trace", tr, "--spec", "tests.spec.racy:RUNS_SPEC", "--out", str(proj / "p.json")
    )
    args = [
        "approve",
        "--trace",
        tr,
        "--from",
        str(proj / "p.json"),
        "--term",
        "user",
        "--approver",
        "a",
        "--owner",
        "o",
    ]
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(rt, "_terminal", lambda: False)
    assert cli(*args) == 2 and "approve_needs_terminal" in capsys.readouterr().err
    add = [
        "add",
        "--trace",
        tr,
        "--term",
        "x y",
        "--definition",
        "d",
        "--owner",
        "o",
        "--approver",
        "a",
    ]
    assert cli(*add) == 2
    monkeypatch.setattr(rt, "_terminal", lambda: True)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert cli(*args) == 2 and cli(*add) == 2
    assert not (proj / "REQUIREMENTS.glossary.json").exists()


def test_approve_refuses_the_approved_file_and_unknown_terms(
    proj: Path, human: None, capsys: pytest.CaptureFixture[str]
) -> None:
    tr = str(proj / "spec.trace.json")
    cli(
        "draft", "--trace", tr, "--spec", "tests.spec.racy:RUNS_SPEC", "--out", str(proj / "p.json")
    )
    capsys.readouterr()
    assert (
        cli(
            "approve",
            "--trace",
            tr,
            "--from",
            str(proj / "REQUIREMENTS.glossary.json"),
            "--term",
            "user",
            "--approver",
            "a",
            "--owner",
            "o",
        )
        == 2
    )
    assert "approve_from_authoritative" in capsys.readouterr().err
    assert (
        cli(
            "approve",
            "--trace",
            tr,
            "--from",
            str(proj / "p.json"),
            "--term",
            "no such term",
            "--approver",
            "a",
            "--owner",
            "o",
        )
        == 2
    )
    assert "no_proposal" in capsys.readouterr().err


def test_read_paths_write_nothing(proj: Path) -> None:
    (proj / "REQUIREMENTS.glossary.json").write_text(gfile([term("user")]))
    before = {p.name: p.read_bytes() for p in proj.iterdir()}
    tr = str(proj / "spec.trace.json")
    cli(*LINT, "--trace", tr)
    cli("show", "--trace", tr)
    assert {p.name: p.read_bytes() for p in proj.iterdir()} == before
