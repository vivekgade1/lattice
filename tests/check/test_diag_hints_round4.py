"""Node C3: diagnostics that lead to a fix in one round.

Each fixture under tests/fixtures/diag_hints/ is one refused round of the round-4 edit
slice (bench/results/edit_slice/round4_*_L.jsonl.artefacts/<model>/L/<ticket>/rep<N>/
round<k>/files/, copied verbatim: the refused program and its profile). The persistent
refusals of that round (bench/analysis/diag_persistence.py) now carry a hint written
with the program's own names and, where one edit fixes the error, a machine-applicable
`fix` (offered, never applied) that makes that diagnostic go away."""

from __future__ import annotations

import types
from pathlib import Path

from lattice.check import check_source
from lattice.diagnostics import Diagnostic, Fix, Severity, Span

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "diag_hints"


def _profile(name: str):
    module = types.ModuleType(f"_diag_hint_fixture_{name}")
    source = (FIXTURES / name / "profile.py").read_text()
    exec(compile(source, str(FIXTURES / name / "profile.py"), "exec"), module.__dict__)  # noqa: S102
    return module.profile()


def _source(name: str) -> str:
    (program,) = sorted((FIXTURES / name).glob("*.lattice.py"))
    return program.read_text()


def _errors(source: str, name: str) -> list[Diagnostic]:
    r = check_source(source, _profile(name))
    return [d for d in r.diagnostics if d.severity is Severity.ERROR]


def _at(name: str, code: str, line: int, col: int | None = None) -> Diagnostic:
    errors = _errors(_source(name), name)
    found = [
        d
        for d in errors
        if d.code == code and d.span.line == line and (col is None or d.span.col == col)
    ]
    assert len(found) == 1, [d.render() for d in errors]
    return found[0]


def apply_fix(source: str, fix: Fix) -> str:
    """The source with `fix` applied (1-based line/col; end exclusive)."""
    lines = source.splitlines(keepends=True)

    def offset(at: Span) -> int:
        return sum(len(line) for line in lines[: at.line - 1]) + at.col - 1

    return source[: offset(fix.start)] + fix.text + source[offset(fix.end) :]


def _fix_removes(name: str, d: Diagnostic) -> list[Diagnostic]:
    """Apply d.fix: exactly one fewer error of d's code and no new error code."""
    assert d.fix is not None
    before = _errors(_source(name), name)
    after = _errors(apply_fix(_source(name), d.fix), name)
    count = lambda ds, c: sum(x.code == c for x in ds)  # noqa: E731
    assert count(after, d.code) == count(before, d.code) - 1, [x.render() for x in after]
    assert {x.code for x in after} <= {x.code for x in before}, [x.render() for x in after]
    return after


# ---- L802: name the unprotected binding, write the line --------------------------------


def test_l802_names_the_fetch_binding_and_writes_the_filter_line() -> None:
    name = "sonnet_t23_note_filter_rep0_round3"
    d = _at(name, "L802", 8)
    assert d.hint == (
        "the rows fetched at line 6 are never filtered by owner: replace line 6 with "
        "`all_notes = fetch(Note)` and `notes = accessible(all_notes, by=user)` "
        "(the later lines keep using `notes`)"
    )
    assert d.fix is not None and d.fix.text == (
        "all_notes = fetch(Note)\n    notes = accessible(all_notes, by=user)"
    )
    _fix_removes(name, d)


def test_l802_of_a_nested_fetch_never_says_part_of_the_statement() -> None:
    name = "haiku_t11_title_trim_rep1_round1"
    d = _at(name, "L802", 29)
    assert "part of the statement" not in (d.hint or "")
    assert d.hint == (
        "the fetch(Item) at line 30 reads every Item row: filter it by owner, "
        "`accessible(fetch(Item), by=user)`"
    )
    assert d.fix is not None and d.fix.text == "accessible(fetch(Item), by=user)"
    assert d.fix.start == Span(30, 16)
    _fix_removes(name, d)


def test_l802_uses_the_filtered_binding_when_there_is_one() -> None:
    """accessible() already exists, but a later line reads the unfiltered name."""
    name = "sonnet_t23_note_filter_rep0_round3"
    src = _source(name).replace(
        "    notes = fetch(Note)\n    found = where(notes, col(\"id\") == id)\n"
        "    note = one(found, else_=NotFound(\"Note not found\"))\n    checked",
        "    notes = fetch(Note)\n    visible = accessible(notes, by=user)\n"
        "    found = where(notes, col(\"id\") == id)\n"
        "    note = one(found, else_=NotFound(\"Note not found\"))\n    checked",
        1,
    )
    errors = [d for d in _errors(src, name) if d.code == "L802"]
    d = errors[0]
    assert d.hint == "use `visible` (line 7, checked by accessible()) instead of `notes` at line 8"
    assert d.fix is not None and d.fix.text == "visible"
    fixed = apply_fix(src, d.fix)
    assert len([x for x in _errors(fixed, name) if x.code == "L802"]) == len(errors) - 1


def test_l802_after_owner_is_names_the_checked_row() -> None:
    """require(item, owner_is(user)) is there, but the insert reads the unchecked row."""
    d = _at("sonnet_t15_duplicate_item_rep0_round1", "L802", 15)
    assert d.hint == (
        "use `allowed` (line 10, checked by owner_is()) instead of `item` at lines 14, 15"
    )
    assert d.fix is None  # two uses to change: not one edit


# ---- L404: a table without an owner ----------------------------------------------------


def test_mine_on_an_unowned_table_says_what_to_declare() -> None:
    d = _at("sonnet_t23_note_filter_rep0_round1", "L404", 45)
    assert "only no tables" not in (d.hint or "")
    assert d.message == (
        "mine() needs rows of a table with an owner column; the profile declares none "
        "for Note, so its rows are shared"
    )
    assert d.hint == (
        "if each Note belongs to the user in its 'owner_id' column, declare it in "
        "profile.py: add owner='owner_id' to Note's Schema.from_pairs(...) (then every "
        "endpoint that returns or writes Note rows filters them with accessible(..., "
        "by=user)); otherwise drop mine() and use `notes` itself"
    )
    assert d.fix is None  # two legal fixes: no machine suggestion


def test_insert_owner_on_an_unowned_table_sets_the_owner_column() -> None:
    name = "sonnet_t22_copy_note_rep0_round1"
    d = _at(name, "L404", 12, 28)
    assert d.message == "'Note' has no owner declared in the profile, so insert() takes no owner="
    assert d.hint == "set its 'owner_id' column to the caller: owner_id=user.id"
    assert d.fix is not None and d.fix.text == "owner_id=user.id"
    # one mistake, one diagnostic: the missing-columns error no longer lists owner_id
    missing = _at(name, "L404", 12, 15)
    assert "owner_id" not in missing.message and "'createdAt', 'updatedAt'" in missing.message
    _fix_removes(name, d)


def test_update_owner_on_an_unowned_table_sets_the_owner_column() -> None:
    name = "sonnet_t28_task_reassign_rep0_round2"
    d = _at(name, "L404", 93)
    assert d.hint == "set its 'owner_id' column instead: owner_id=new_owner.id"
    assert d.fix is not None and d.fix.text == "owner_id=new_owner.id"
    assert _fix_removes(name, d) == []


# ---- L404: computing with an `= UNSET` parameter ---------------------------------------


def test_unset_parameter_points_at_the_stored_column() -> None:
    d = _at("haiku_t18_duplicate_title_guard_rep1_round1", "L404", 50)
    assert d.hint == (
        "it has no value when the request omits it. Compute with the stored value "
        "instead: after line 53 `updated = update(allowed, title=title, ...)`, use "
        "col(\"title\") of `updated` guarded by sent(title), e.g. `checked = "
        "require(updated, ~sent(title) | <condition on col(\"title\")>, else_=...)`"
    )


# ---- L834 / L803 at a return: one diagnostic, the exact select line --------------------


def test_response_model_mismatch_is_one_diagnostic_with_the_select_line() -> None:
    name = "sonnet_t3_soft_delete_rep0_round1"
    at_return = [d for d in _errors(_source(name), name) if d.span == Span(11, 5)]
    assert [d.code for d in at_return] == ["L834"]  # no L803 cascade at the same return
    d = at_return[0]
    assert d.message == (
        "the returned columns do not match response model 'ItemPublic': extra "
        "'deleted_at' (private)"
    )
    assert d.hint == (
        'return exactly its fields: replace line 11 with `shown = select(result, '
        'cols=["title", "description", "id", "owner_id", "created_at"])` and `return shown`'
    )
    assert d.fix is not None
    _fix_removes(name, d)


def test_response_model_with_missing_fields_names_them() -> None:
    d = _at("haiku_t11_title_trim_rep1_round1", "L834", 60)
    assert d.hint is not None and d.hint.startswith(
        'return exactly its fields: replace line 60 with `shown = select(updated, cols=['
    )


# ---- L803: the private column and the one legal alternative ----------------------------


def test_filter_on_a_private_column_names_it_and_the_alternative() -> None:
    d = _at("haiku_t3_soft_delete_rep1_round3", "L803", 7)
    assert d.message == (
        "where() reads the private column 'deleted_at'; a Customer endpoint's response "
        "would reveal its values"
    )
    assert d.hint == (
        "only Admin endpoints may filter on 'deleted_at'. If it is not secret (it only "
        "has to stay out of responses), declare it in profile.py without private: "
        "select() and the response model keep it out of responses"
    )


def test_filter_on_an_undeclared_owner_column_says_declare_it_and_use_mine() -> None:
    d = _at("sonnet_t23_note_filter_rep0_round2", "L803", 45)
    assert d.hint == (
        "to keep the caller's own rows, declare owner='owner_id' on Note in profile.py "
        "and write `my_notes = mine(notes, by=user)` (or accessible(notes, by=user) to "
        "let Admin callers see every row)"
    )


# ---- L401: col() where there is no input table -----------------------------------------


def test_col_in_an_insert_value_reads_the_one_row_in_scope() -> None:
    name = "haiku_t15_duplicate_item_rep1_round1"
    d = _at(name, "L401", 39, 49)
    assert "table has {}" not in d.message
    assert d.message == (
        "col(\"title\") has no input rows here (insert() values, constants and call() "
        "arguments read no table) ('new_title' is defined at line 38)"
    )
    assert d.hint == "read the value of the one row `allowed`: allowed.title"
    assert d.fix is None  # the col() is written at line 38, not where it is reported
    direct = _at(name, "L401", 39, 72)  # description=col("description")
    assert direct.hint == "read the value of the one row `allowed`: allowed.description"
    assert direct.fix is not None and direct.fix.text == "allowed.description"
    _fix_removes(name, direct)


# ---- synthetic cases (bench.api_datasets profile) --------------------------------------


def _api_errors(src: str) -> list[Diagnostic]:
    from textwrap import dedent

    from bench.api_datasets import api_profile

    r = check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), api_profile())
    return [d for d in r.diagnostics if d.severity is Severity.ERROR]


def test_unset_parameter_without_an_update_says_how_to_redeclare_it() -> None:
    (d,) = _api_errors(
        """
        @endpoint("GET", "/orders", auth=Customer)
        def list_orders(user: CurrentUser, status: Str(max_len=20) = UNSET) -> Page:
            orders = fetch(Orders)
            visible = accessible(orders, by=user)
            matching = where(visible, col("status") == status)
            result = page(matching, limit=20)
            return result
        """
    )
    assert d.code == "L404"
    assert d.hint == (
        "it has no value when the request omits it: make it required (drop `= UNSET`), "
        "or make it `status: Str(max_len=20) | None = None` and test is_null(status)"
    )


def test_two_fetches_of_the_table_get_a_hint_but_no_fix() -> None:
    errors = _api_errors(
        """
        @endpoint("GET", "/orders", auth=Customer)
        def list_orders(user: CurrentUser) -> Page:
            orders = fetch(Orders)
            again = fetch(Orders)
            first = limit(again, n=1)
            result = page(orders, limit=20)
            return result
        """
    )
    l802 = [d for d in errors if d.code == "L802"]
    assert l802 and l802[0].fix is None
    assert "orders = accessible(all_orders, by=user)" in (l802[0].hint or "")


def test_fix_renders_as_a_replacement() -> None:
    fix = Fix(Span(3, 5), Span(3, 24), "x = 1")
    assert fix.render() == "replace line 3 col 5 to line 3 col 24 with: x = 1"
