"""Arm S+ (docs/superpowers/plans/2026-10-05-pivot-spec-authoring.md, item C): arm S with
the generic edge-case checklist in the draft prompt and one self-review call.

* the checklist is generic (no table, column, response model, role or user of the
  multi-vendor app) and every example is a spec record the draft namespace can build;
* S+ end to end on t39 with a scripted model: draft, a review that adds a scenario,
  approval, code, check, correct;
* an invalid review keeps the first draft (recorded);
* the arms are registered, S+ is not an "aided" arm (no worked examples), and
  ``--estimate`` sizes the S+ prompts.
"""

from __future__ import annotations

import ast
import re

import pytest

from bench.edit_slice import run, sdraft, spec_checklist
from lattice.llm.client import ScriptedGenerator
from lattice.spec import Invariant, Machine, Scenario, Step, Unchecked

T39 = "t39_bulk_price"

DRAFT = """```python
TWICE = {"changes": [{"product_id": 1, "price": 13.0}, {"product_id": 1, "price": 14.0}]}
DELTA = SpecDelta(
    scenarios=(
        Scenario(
            "t39_listed_twice",
            steps=(Step("POST", "/products/bulk-price", "vicky", json=TWICE,
                        expect_body={"updated": 1}),),
        ),
    ),
)
```"""

REVIEWED = """Checked against the list: the draft misses the unauthenticated caller.

```python
TWICE = {"changes": [{"product_id": 1, "price": 13.0}, {"product_id": 1, "price": 14.0}]}
DELTA = SpecDelta(
    scenarios=(
        Scenario(
            "t39_listed_twice",
            steps=(Step("POST", "/products/bulk-price", "vicky", json=TWICE,
                        expect_body={"updated": 1}),),
        ),
        Scenario(
            "t39_anonymous_is_401",
            steps=(Step("POST", "/products/bulk-price", None, json=TWICE, expect_status=401,
                        expect_db={"products": {"where": {"id": 1}, "equals": {"price": 12.0}}}),),
        ),
    ),
)
```"""

INVALID_REVIEW = """```python
DELTA = SpecDelta(scenarios=(Scenario("t39_x", steps=(Step("GET", "/products", "nobody"),)),))
```"""


# ---- the checklist ----------------------------------------------------------------------------

GENERIC = {
    # columns, tables, roles any app may have: allowed in the checklist
    "id", "user_id", "status", "created_at", "name", "description", "version",
    "is_active", "email", "role", "full_name", "hashed_password",
    "User", "users", "UserRead", "Public",
}  # fmt: skip


def _app_names() -> set[str]:
    """The multi-vendor app's own names: tables (both spellings), columns, response
    models and roles, less the generic ones above (user handles: whole words, below)."""
    from bench.specs.multivendor import SPEC
    from lattice.spec.model import tables_of

    tables, sql = tables_of(SPEC.profile)
    api = SPEC.profile.api
    names = {*tables, *sql.values(), *api.responses, *api.roles}
    for schema in tables.values():
        names.update(schema.names())
    names.update(("Order", "Product", "Vendor", "stock_quantity", "price", "category"))
    return names - GENERIC


def test_the_checklist_names_nothing_of_the_multivendor_app():
    text = spec_checklist.CHECKLIST_SECTION + spec_checklist.REVIEW_REQUEST
    lower = text.lower()
    found = sorted(n for n in _app_names() if n.lower() in lower)
    assert not found, found
    # the app's seed handles as whole words too (short names: "vera" in "several" is not
    # a use, so whole words only)
    from bench.specs.multivendor import SPEC

    words = set(re.findall(r"[a-z_]+", lower))
    assert not (set(SPEC.users) & words)


def test_the_checklist_has_the_nine_items_each_with_a_rule_and_a_spec_example():
    keys = [item.key for item in spec_checklist.CHECKLIST]
    assert keys == [
        "bounds",
        "response_shape",
        "access_matrix",
        "state_edges",
        "atomicity",
        "duplicates",
        "empty_collections",
        "empty_aggregates",
        "statuses",
    ]
    namespace = {
        "Scenario": Scenario,
        "Step": Step,
        "Invariant": Invariant,
        "Machine": Machine,
        "Unchecked": Unchecked,
    }
    for item in spec_checklist.CHECKLIST:
        assert item.rule and "\n" not in item.rule, item.key
        assert item.title in spec_checklist.CHECKLIST_SECTION
        ast.parse(item.example, mode="eval")
        value = eval(item.example, {"__builtins__": {}}, namespace)  # noqa: S307 -- our own text
        records = value if isinstance(value, tuple) else (value,)
        assert records and all(
            isinstance(r, Scenario | Invariant | Machine | Unchecked) for r in records
        ), item.key
        assert item.example in spec_checklist.CHECKLIST_SECTION


def test_the_splus_draft_prompt_is_s_s_plus_the_checklist():
    s_system, s_user = sdraft.draft_prompt(T39)
    system, user = sdraft.draft_prompt(T39, checklist=True)
    assert spec_checklist.CHECKLIST_SECTION not in s_system
    assert system == s_system.rstrip("\n") + "\n\n" + spec_checklist.CHECKLIST_SECTION
    assert user == s_user
    assert run.arm_prompt("S+", T39) == (system, user)


# ---- S+ end to end ------------------------------------------------------------------------------


def test_arm_splus_drafts_reviews_approves_and_codes_on_t39():
    reference = run.reference_answer("P", T39)
    gen = ScriptedGenerator([DRAFT, REVIEWED, reference])
    record = run.run_arm("S+", T39, gen, model="scripted")

    assert isinstance(record, run.SpecRunRecord)
    assert record.spec_rounds == 1 and not record.spec_failed
    assert record.spec_review_rounds == 1 and not record.spec_review_failed
    assert record.spec_drafted_before_review == 1
    assert record.spec_drafted_after_review == 2
    assert record.spec_drafted == 2 and record.spec_rejected == 0
    # the draft call and the review call both carry the checklist
    assert all(spec_checklist.CHECKLIST_SECTION in gen.calls[k][0] for k in (0, 1))
    review = gen.calls[1][1]
    assert [m["role"] for m in review] == ["user", "assistant", "user"]
    assert run.ticket(T39).text.strip() in review[0]["content"]
    assert "t39_listed_twice" in review[1]["content"]
    assert review[2]["content"] == spec_checklist.REVIEW_REQUEST
    # the code prompt carries the reviewed draft
    code_user = gen.calls[2][1][0]["content"]
    assert "t39_listed_twice" in code_user and "t39_anonymous_is_401" in code_user
    assert spec_checklist.CHECKLIST_SECTION not in gen.calls[2][0]
    assert record.examples == []  # S+ is not an aided arm: no worked examples
    assert record.round_problems == [None] and record.final_check_findings == 0
    assert record.outcome == "correct", (record.failure, record.detail)
    assert record.rounds == 1


def test_an_invalid_review_keeps_the_first_draft():
    reference = run.reference_answer("P", T39)
    gen = ScriptedGenerator([DRAFT, INVALID_REVIEW, reference])
    record = run.run_arm("S+", T39, gen, model="scripted")

    assert record.spec_review_rounds == 1 and record.spec_review_failed
    assert record.spec_drafted_before_review == record.spec_drafted_after_review == 1
    assert record.spec_drafted == 1 and not record.spec_failed
    code_user = gen.calls[2][1][0]["content"]
    assert "t39_listed_twice" in code_user and "t39_x" not in code_user
    assert record.outcome == "correct", (record.failure, record.detail)


def test_review_spec_on_a_refusal_keeps_the_draft():
    from lattice.llm.client import GenerationRefused

    class Refuses:
        calls: list = []

        def generate(self, system, messages):
            raise GenerationRefused("no")

    first = sdraft.parse_delta(DRAFT)
    review = sdraft.review_spec(T39, Refuses(), first)
    assert review.failed and review.delta == first and len(review.refused) == 1
    assert review.rounds == 1


def test_a_failed_draft_is_not_reviewed():
    reference = run.reference_answer("P", T39)
    gen = ScriptedGenerator([INVALID_REVIEW] * 3 + [reference])
    record = run.run_arm("S+", T39, gen, model="scripted")
    assert record.spec_failed and record.spec_rounds == 3
    assert record.spec_review_rounds == 0 and not record.spec_review_failed
    assert record.spec_drafted_before_review == record.spec_drafted_after_review == 0
    assert len(gen.calls) == 4
    assert record.outcome == "correct"


def test_the_splus_dry_run_feeds_the_owner_s_delta_through_draft_and_review():
    record = run.dry_run("S+", T39)
    assert record.outcome == "correct", (record.failure, record.detail)
    assert record.spec_review_rounds == 1 and not record.spec_review_failed
    assert record.spec_drafted_before_review == record.spec_drafted_after_review > 0


# ---- registration and the estimate --------------------------------------------------------


def test_the_pivot_arms_are_registered():
    from bench.edit_slice import rescore

    assert run.PIVOT_ARMS == ("S", "S+", "S*")
    assert run.PHASE1_ARMS == ("P", "Q", "S", "S*")
    assert "S+" in run.ALL_ARMS and "S+" in run.SPEC_ARMS and "S+" in rescore.PYTHON_ARMS
    assert run.language("S+") == "P"
    assert not run.is_aided("S+") and run.is_aided("Q+") and run.is_aided("L+")


def test_the_estimate_sizes_the_splus_prompts():
    est = run.estimate(["claude-sonnet-5"], [T39], 1, ["S", "S+"])
    by_arm = {r["arm"]: r for r in est["rows"]}
    assert by_arm["S+"]["prompt_tokens"] > by_arm["S"]["prompt_tokens"]
    rows = sdraft.estimate_rows([T39])
    assert rows[0]["splus_draft_chars"] > rows[0]["draft_chars"]
    assert rows[0]["review_chars"] > rows[0]["splus_draft_chars"]
    table = sdraft.render_estimate(rows)
    assert "S+ draft" in table and "review" in table


@pytest.mark.parametrize("argv", [["--estimate", "--arm", "S,S+,S*", "--tickets", T39]])
def test_the_cli_estimate_accepts_splus(argv, capsys):
    assert run.main(argv) == 0
    out = capsys.readouterr().out
    assert "| S+ |" in out
