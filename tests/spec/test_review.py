"""`lattice.spec.review`: the owner-facing review page over a `Spec` and its `Trace`
(`lattice.spec.rules`) -- the contract it must follow."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from lattice.profile import Profile
from lattice.spec.model import Await, Invariant, Machine, Scenario, Service, Spec, Step, Unchecked
from lattice.spec.review import main, review_html, review_markdown
from lattice.spec.rules import Question, Rule, Trace
from lattice.typesys import Scalar, Schema

S = Scalar

# ---- a small spec, exercising every rule status --------------------------------------------

ITEM = Schema.from_pairs(
    [("id", S.INT), ("status", S.STR)],
    keys=[("id",)],
    one_of={"status": ("pending", "done")},
)


def make_spec() -> Spec:
    return Spec(
        profile=Profile(schemas={"Item": ITEM}),
        invariants=(
            Invariant("item_id_positive", "An item's id is never negative", "Item.id >= 0"),
        ),
        machines=(Machine("Item", "status", {"pending": "done"}, initial="pending"),),
        services=(Service("worker", "drains the finish queue"),),
        scenarios=(
            Scenario(
                "ann_lists_items",
                steps=(
                    Step("GET", "/items", user="ann", expect_status=200),
                    Step(
                        "GET",
                        "/items/1",
                        user="ann",
                        expect_status=200,
                        expect_db={
                            "Item": {"where": {"id": 1}, "equals": {"status": "pending"}}
                        },
                    ),
                ),
            ),
            Scenario(
                "anon_blocked",
                steps=(Step("GET", "/items", user=None, expect_status=401),),
            ),
            Scenario(
                "worker_finishes",
                steps=(
                    Step("POST", "/items/1/finish", user="ann", expect_status=202),
                    Await(
                        "worker",
                        {"Item": {"where": {"id": 1}, "equals": {"status": "done"}}},
                        within_s=5,
                        note="the worker flips status",
                    ),
                ),
            ),
        ),
        unchecked=(Unchecked("rate limiting is not modelled", concerns="Item"),),
        users={"ann": ("ann", "pw-ann"), "bob": ("bob", "pw-bob")},
    )


SPEC = make_spec()

RULES = [
    Rule("R1", "Ann can list her own items", "Access", 3),
    Rule("R2", "Finishing an item is done by the worker service", "Workflow", 5),
    Rule("R3", "Writes are rate limited per caller", "Non-functional", 7),
    Rule("R4", "This system replaces the old spreadsheet", "Intro", 1),
    Rule("R5", "Items must be exportable to CSV", "Exports", 9),
]

LINKS = {
    "R1": ["scenario:ann_lists_items"],
    "R2": ["scenario:worker_finishes", "service:worker"],
}
UNCHECKED = {"R3": "rate limiting cannot be probed without a load generator"}
CONTEXT = ["R4"]
def _make_questions() -> list[Question]:
    # fresh instances every call: a Trace's Questions are mutated in place by some
    # tests (answering one), and must never leak into another test's Trace.
    return [
        Question("Q1", "Should an anonymous caller get 401 or 404?", "401", ["R1"], answer=None),
        Question(
            "Q2", "Should the worker retry on failure?", "yes, up to 3 times", ["R2"],
            answer="no retries",
        ),
    ]


def make_trace() -> Trace:
    return Trace(
        source="REQUIREMENTS.md",
        rules=[Rule(r.id, r.text, r.section, r.line) for r in RULES],
        links={k: list(v) for k, v in LINKS.items()},
        unchecked=dict(UNCHECKED),
        context=list(CONTEXT),
        questions=_make_questions(),
    )


TRACE = make_trace()

LINT = [
    {
        "code": "L1",
        "rule_id": "R1",
        "element": "scenario:ann_lists_items",
        "message": "the scenario never asserts the response body shape",
        "fix": "add expect_body to the GET /items step",
    }
]
PROBE = [
    {
        "rule_id": "R2",
        "status": "gap",
        "patch_summary": "the worker can leave the item pending past the timeout in a rare race",
    }
]

TITLE = "Item service review"

SECTION_HEADINGS = [
    "## Summary",
    "## Decisions for you",
    "## Gaps",
    "## Rule by rule",
    "## Access at a glance",
    "## What the spec checks",
    "## The back-translation",
]


def _render() -> str:
    return review_markdown(SPEC, TRACE, lint=LINT, probe=PROBE, title=TITLE)


# ---- sections appear, in order ---------------------------------------------------------


def test_sections_appear_in_order():
    text = _render()
    positions = [text.index(h) for h in SECTION_HEADINGS]
    assert positions == sorted(positions)
    assert positions[0] >= 0


def test_title_is_the_top_heading():
    text = _render()
    assert text.startswith(f"# {TITLE}\n")


# ---- rule-by-rule statuses ---------------------------------------------------------------


def test_every_rule_status_is_represented():
    text = _render()
    rule_table = text.split("## Rule by rule", 1)[1].split("## Access at a glance", 1)[0]
    # R1: checked (linked, no probe gap)
    assert re.search(r"\| R1 \|.*\| .*✅", rule_table)
    # R2: linked, but a probe gap overrides it
    assert re.search(r"\| R2 \|.*\U0001f573", rule_table)
    assert "✅" not in rule_table.split("| R2 |")[1].split("|")[2]
    # R3: unchecked, with its reason shown
    assert "R3" in rule_table and "rate limiting cannot be probed" in rule_table
    assert re.search(r"\| R3 \|.*⚠", rule_table)
    # R4: context
    assert re.search(r"\| R4 \|.*—.*\|", rule_table)
    # R5: untraced
    assert re.search(r"\| R5 \|.*❌", rule_table)


def test_checked_rule_links_into_section_6():
    text = _render()
    assert "[scenario:ann_lists_items](#el-scenario-ann-lists-items)" in text
    # and section 6 actually carries that anchor
    assert '<a id="el-scenario-ann-lists-items"></a>' in text


# ---- summary counts ----------------------------------------------------------------------


def test_summary_counts_and_verdict():
    text = _render()
    summary = text.split("## Summary", 1)[1].split("## Decisions for you", 1)[0]
    assert "1 checked" in summary
    assert "1 unchecked" in summary
    assert "1 context" in summary
    assert "1 untraced" in summary
    assert "1 probe gap" in summary
    assert "Open questions: 1" in summary
    assert "Lint findings: 1 (L1: 1)" in summary
    assert "Probe gaps: 1" in summary
    # untraced(1) + lint(1) + probe gaps(1) + open questions(1) = 4
    assert "4 items need your attention" in summary


def test_verdict_is_ready_to_approve_with_no_findings():
    trace = make_trace()
    # answer the one open question and drop the untraced rule's gap by tracing it
    trace.questions[0].answer = "401"
    trace.links["R5"] = ["profile:Item.status"]
    text = review_markdown(SPEC, trace, lint=None, probe=None, title=TITLE)
    assert "ready to approve" in text
    summary = text.split("## Summary", 1)[1].split("## Decisions for you", 1)[0]
    assert "Open questions: 0" in summary


# ---- decisions show defaults --------------------------------------------------------------


def test_questions_show_their_defaults():
    text = _render()
    decisions = text.split("## Decisions for you", 1)[1].split("## Gaps", 1)[0]
    assert "Q1" in decisions and "Default: 401" in decisions
    assert "(using default: 401)" in decisions  # Q1 has no answer
    assert "Q2" in decisions and "Default: yes, up to 3 times" in decisions
    assert "Answer: no retries" in decisions  # Q2 was answered


# ---- gaps section holds lint + probe + untraced + unchecked --------------------------------


def test_gaps_hold_lint_probe_untraced_and_unchecked():
    text = _render()
    gaps = text.split("## Gaps", 1)[1].split("## Rule by rule", 1)[0]
    assert "R5" in gaps and "Items must be exportable to CSV" in gaps  # untraced
    assert "L1" in gaps and "never asserts the response body shape" in gaps  # lint
    assert "leave the item pending past the timeout" in gaps  # probe gap
    assert "rate limiting cannot be probed" in gaps  # trace-level unchecked
    assert "rate limiting is not modelled" in gaps  # spec-level Unchecked note
    # and these must not appear before the Gaps section starts
    before = text.split("## Gaps", 1)[0]
    assert "leave the item pending past the timeout" not in before


# ---- access matrix -------------------------------------------------------------------------


def test_access_matrix_cells():
    text = _render()
    access = text.split("## Access at a glance", 1)[1].split("## What the spec checks", 1)[0]
    lines = [ln for ln in access.splitlines() if ln.startswith("|")]
    header = lines[0]
    cols = [c.strip() for c in header.strip("|").split("|")]
    assert cols[0] == "caller"
    assert "GET /items" in cols
    assert "GET /items/{}" in cols
    assert "POST /items/{}/finish" in cols

    def row(label: str) -> list[str]:
        for ln in lines[2:]:
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if cells[0] == label:
                return cells
        raise AssertionError(f"no row for {label!r}")

    ann = row("ann")
    assert ann[cols.index("GET /items")] == "200"
    assert ann[cols.index("GET /items/{}")] == "200"
    assert ann[cols.index("POST /items/{}/finish")] == "202"

    bob = row("bob")
    assert bob[cols.index("GET /items")] == "·"
    assert bob[cols.index("GET /items/{}")] == "·"

    no_token = row("no token")
    assert no_token[cols.index("GET /items")] == "401"
    assert no_token[cols.index("GET /items/{}")] == "·"


# ---- section 6 and the back-translation -----------------------------------------------------


def test_section6_covers_invariants_machines_services_scenarios():
    text = _render()
    s6 = text.split("## What the spec checks", 1)[1].split("## The back-translation", 1)[0]
    assert "An item's id is never negative" in s6
    assert "Item.status" in s6 and "pending -> done" in s6
    assert "worker" in s6 and "drains the finish queue" in s6
    assert "ann_lists_items" in s6
    assert "GET /items as ann -> 200" in s6
    assert "wait for the worker service" in s6


def test_back_translation_is_requirements_markdown():
    from lattice.spec.authoring import requirements_markdown

    text = _render()
    back = text.split("## The back-translation", 1)[1].strip()
    expected = requirements_markdown(SPEC, title=TITLE).strip()
    assert back == expected


# ---- determinism -----------------------------------------------------------------------------


def test_rendering_is_deterministic():
    a = review_markdown(SPEC, make_trace(), lint=list(LINT), probe=list(PROBE), title=TITLE)
    b = review_markdown(SPEC, make_trace(), lint=list(LINT), probe=list(PROBE), title=TITLE)
    assert a == b
    ha = review_html(SPEC, make_trace(), lint=list(LINT), probe=list(PROBE), title=TITLE)
    hb = review_html(SPEC, make_trace(), lint=list(LINT), probe=list(PROBE), title=TITLE)
    assert ha == hb


# ---- HTML is well-formed and carries the same content -----------------------------------------


class _BalanceChecker(HTMLParser):
    """A minimal well-formedness check: every non-void start tag is matched by an end
    tag, properly nested."""

    VOID = {"meta", "link", "br", "hr", "img", "input"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.saw_doctype = False

    def handle_decl(self, decl: str) -> None:
        if decl.lower().startswith("doctype"):
            self.saw_doctype = True

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self.stack and self.stack[-1] == tag, f"unbalanced </{tag}>: stack={self.stack}"
        self.stack.pop()


def test_html_is_well_formed():
    page = review_html(SPEC, TRACE, lint=LINT, probe=PROBE, title=TITLE)
    parser = _BalanceChecker()
    parser.feed(page)
    parser.close()
    assert parser.saw_doctype
    assert parser.stack == []
    assert f"<title>{TITLE}</title>" in page
    assert "prefers-color-scheme: dark" in page
    assert "<details" in page and "<summary>Summary</summary>" in page


def test_html_carries_the_same_facts_as_markdown():
    page = review_html(SPEC, TRACE, lint=LINT, probe=PROBE, title=TITLE)
    assert "ready to approve" not in page  # this fixture has findings
    assert "items need your attention" in page
    assert "rate limiting cannot be probed" in page
    assert "the worker can leave the item pending" in page
    assert 'href="#el-scenario-ann-lists-items"' in page
    assert 'id="el-scenario-ann-lists-items"' in page


# ---- CLI ---------------------------------------------------------------------------------------


def test_cli_writes_markdown_and_html(tmp_path):
    trace_path = tmp_path / "spec.trace.json"
    trace_path.write_text(TRACE.to_json())
    lint_path = tmp_path / "lint.json"
    lint_path.write_text(json.dumps(LINT))
    probe_path = tmp_path / "probe.json"
    probe_path.write_text(json.dumps(PROBE))
    out = tmp_path / "review.md"
    out_html = tmp_path / "review.html"

    code = main(
        [
            "--spec",
            "tests.spec.test_review:SPEC",
            "--trace",
            str(trace_path),
            "--lint",
            str(lint_path),
            "--probe",
            str(probe_path),
            "--out",
            str(out),
            "--html",
            str(out_html),
            "--title",
            TITLE,
        ]
    )
    assert code == 0
    assert out.read_text().startswith(f"# {TITLE}\n")
    assert "<!doctype html>" in out_html.read_text()


def test_cli_default_title_uses_trace_source(tmp_path):
    trace_path = tmp_path / "spec.trace.json"
    trace_path.write_text(TRACE.to_json())
    out = tmp_path / "review.md"
    code = main(
        [
            "--spec", "tests.spec.test_review:SPEC",
            "--trace", str(trace_path),
            "--out", str(out),
        ]
    )  # fmt: skip
    assert code == 0
    assert "REQUIREMENTS.md" in out.read_text().splitlines()[0]


def test_cli_bad_spec_ref_is_exit_2():
    assert main(["--spec", "tests.spec.test_review:NOPE", "--trace", "x", "--out", "x"]) == 2
