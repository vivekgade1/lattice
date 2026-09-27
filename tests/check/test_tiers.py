"""Tier reporting (spec 2026-09-27 lattice-t2-design §1, §5, node T2c):
`CheckedProgram.tiers` names every endpoint's and procedure's tier, and
`tier_summary()` gives a one-line tier mix for reports."""

from __future__ import annotations

from t2_support import PROFILE, ok

from lattice.check.tiers import T0, T1, T2, T3, tier_summary


def test_a_declarative_endpoint_is_t0() -> None:
    result = ok(
        """
        @endpoint("GET", "/items", auth=Customer)
        def list_items(user: CurrentUser) -> Page:
            items = fetch(Items)
            mine = accessible(items, by=user)
            shown = select(mine, cols=["id", "title"])
            result = page(shown, limit=20)
            return result
        """
    )
    assert result.program.tiers == {"list_items": T0}


def test_a_window_endpoint_is_t1() -> None:
    result = ok(
        """
        @endpoint("GET", "/items/ranked", auth=Customer)
        def ranked(user: CurrentUser) -> Page:
            items = fetch(Items)
            mine = accessible(items, by=user)
            numbered = window(mine, by=["owner_id"], order=["id"], n=win.row_number())
            shown = select(numbered, cols=["id", "title", "n"])
            result = page(shown, limit=20)
            return result
        """
    )
    assert result.program.tiers == {"ranked": T1}


def test_a_traverse_endpoint_is_t1() -> None:
    result = ok(
        """
        @endpoint("GET", "/logins/tree", auth=Public)
        def tree() -> Page:
            events = fetch(LoginEvents)
            walked = traverse(events, events, parent="user_id", child="id", max_depth=5)
            shown = select(walked, cols=["id", "depth"])
            result = page(shown, limit=20)
            return result
        """
    )
    assert result.program.tiers == {"tree": T1}


def test_an_endpoint_calling_a_procedure_scalar_is_t2() -> None:
    result = ok(
        """
        @procedure
        def shout(title: Str, n: Int) -> Str:
            return title.upper() + "!" * n


        @endpoint("GET", "/items/shout", auth=Customer)
        def loud(user: CurrentUser) -> Page:
            items = fetch(Items)
            mine = accessible(items, by=user)
            flagged = derive(mine, loud=shout(col("title"), 2))
            shown = select(flagged, cols=["id", "loud"])
            result = page(shown, limit=20)
            return result
        """
    )
    assert result.program.tiers == {"shout": T2, "loud": T2}


def test_an_endpoint_calling_a_procedure_whole_value_is_t2() -> None:
    result = ok(
        """
        @procedure
        def total(rows: list[Row[Items]]) -> Float:
            return sum(r.score for r in rows)


        @endpoint("PUT", "/items/{item_id}/score", auth=Customer)
        def rescore(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
            items = fetch(Items)
            mine = accessible(items, by=user)
            found = where(mine, col("id") == item_id)
            item = one(found, else_=NotFound("item"))
            s = call(total, rows=item)
            updated = update(item, score=s)
            shown = select(updated, cols=["id", "score"])
            return shown
        """
    )
    assert result.program.tiers == {"total": T2, "rescore": T2}


def test_a_procedure_calling_a_native_is_t3_and_so_is_its_caller() -> None:
    result = ok(
        """
        @procedure(effects=["mail"])
        def notify(to: Str, title: Str) -> None:
            native.send_mail(to, title)


        @endpoint("POST", "/items/notify", auth=Customer)
        def go(user: CurrentUser) -> Page:
            items = fetch(Items)
            mine = accessible(items, by=user)
            shown = select(mine, cols=["id", "title", "owner_id"])
            limited = limit(shown, n=10)
            for_each(limited, notify, col("title"), col("title"))
            result = page(shown, limit=20)
            return result
        """
    )
    assert result.program.tiers == {"notify": T3, "go": T3}


def test_a_procedure_calling_a_t3_helper_is_t3_transitively() -> None:
    """A procedure that only calls another procedure stays T2 (procedure-to-procedure
    calls do not weaken the guarantee); it becomes T3 only when the callee itself
    reaches a native, propagated through the call graph."""
    result = ok(
        """
        @procedure(effects=["mail"])
        def send(to: Str) -> None:
            native.send_mail(to, "hi")


        @procedure(effects=["mail"])
        def send_twice(to: Str) -> None:
            send(to)
            send(to)
        """
    )
    assert result.program.tiers == {"send": T3, "send_twice": T3}


def test_a_procedure_calling_only_procedures_stays_t2() -> None:
    result = ok(
        """
        @procedure
        def shout(title: Str) -> Str:
            return title.upper()


        @procedure
        def shout_twice(title: Str) -> Str:
            return shout(shout(title))
        """
    )
    assert result.program.tiers == {"shout": T2, "shout_twice": T2}


def test_tier_summary_counts_each_tier_and_omits_empty_ones() -> None:
    assert tier_summary({"a": T0, "b": T0, "c": T1}) == "T0: 2, T1: 1"
    assert tier_summary({}) == ""
    assert tier_summary({"a": T3}) == "T3: 1"


def test_tier_summary_orders_from_t0_to_t3() -> None:
    assert tier_summary({"a": T3, "b": T0, "c": T2, "d": T1}) == "T0: 1, T1: 1, T2: 1, T3: 1"


def test_checked_program_exposes_a_tier_summary_method() -> None:
    result = ok(
        """
        @endpoint("GET", "/items", auth=Customer)
        def list_items(user: CurrentUser) -> Page:
            items = fetch(Items)
            mine = accessible(items, by=user)
            shown = select(mine, cols=["id", "title"])
            result = page(shown, limit=20)
            return result
        """
    )
    assert result.program.tier_summary() == "T0: 1"


def test_a_program_without_procedures_has_no_t3_or_t2() -> None:
    result = ok(
        """
        @endpoint("GET", "/items", auth=Customer)
        def list_items(user: CurrentUser) -> Page:
            items = fetch(Items)
            mine = accessible(items, by=user)
            shown = select(mine, cols=["id", "title"])
            result = page(shown, limit=20)
            return result
        """,
        PROFILE,
    )
    assert set(result.program.tiers.values()) <= {T0, T1}
