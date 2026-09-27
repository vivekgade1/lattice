"""T2 effects and privacy: declared effects are profile capabilities, effect natives
need their effect, pure procedures never call effectful ones, and Row[S] hides the
private columns unless declared Row(S, private=True) (L803)."""

from t2_support import errors, first, ok, proc


def test_declared_effects_must_be_capabilities():
    ok(proc("return x", deco='@procedure(effects=["mail", "http", "ping"])'))
    d = first(proc("return x", deco='@procedure(effects=["sms"])'), "L920")
    assert "mail" in d.hint and "http" in d.hint


def test_effects_must_be_literal_list():
    assert "L148" in errors(proc("return x", deco="@procedure(effects=mail)"))
    assert "L148" in errors(proc("return x", deco='@procedure(effects=["mail", "mail"])'))


def test_effect_native_requires_effect():
    body = 'native.send_mail("a@b.c", "hi")\nreturn x'
    d = first(proc(body), "L921")
    assert "mail" in d.hint
    ok(proc(body, deco='@procedure(effects=["mail"])'))


def test_effect_native_default_capability_is_its_name():
    assert errors(proc('native.ping("a")\nreturn x')) == ["L921"]
    ok(proc('native.ping("a")\nreturn x', deco='@procedure(effects=["ping"])'))


def test_pure_native_needs_no_effect():
    ok(proc("return native.slugify(s)", sig="(s: Str) -> Str"))


def test_native_arguments_typed():
    assert errors(proc("return native.slugify(1)", sig="(s: Str) -> Str")) == ["L904"]
    assert errors(proc("return native.nope(s)", sig="(s: Str) -> Str")) == ["L905"]


def test_pure_procedure_cannot_call_effectful():
    src = """
    @procedure(effects=["mail"])
    def notify(to: Str) -> None:
        native.send_mail(to, "hello")

    @procedure
    def g(to: Str) -> Int:
        notify(to)
        return 1
    """
    d = first(src, "L922")
    assert "notify" in d.message and "mail" in d.hint


def test_effectful_caller_needs_superset():
    src = """
    @procedure(effects=["mail"])
    def notify(to: Str) -> None:
        native.send_mail(to, "hello")

    @procedure(effects=["http"])
    def g(to: Str) -> None:
        notify(to)

    @procedure(effects=["mail", "http"])
    def h(to: Str) -> None:
        notify(to)
    """
    assert errors(src) == ["L922"]


def test_private_column_hidden():
    d = first(proc("return item.secret", sig="(item: Row[Items]) -> Str"), "L803")
    assert "private=True" in d.hint


def test_private_column_visible_when_declared():
    ok(proc("return item.secret", sig="(item: Row(Items, private=True)) -> Str"))


def test_private_row_passes_as_public_row():
    src = """
    @procedure
    def title(item: Row[Items]) -> Str:
        return item.title

    @procedure
    def g(item: Row(Items, private=True)) -> Str:
        return title(item)
    """
    ok(src)


def test_public_row_does_not_pass_as_private():
    src = """
    @procedure
    def peek(item: Row(Items, private=True)) -> Str:
        return item.secret

    @procedure
    def g(item: Row[Items]) -> Str:
        return peek(item)
    """
    assert errors(src) == ["L904"]


def test_caller_fields():
    ok(
        proc(
            "n = caller.display_name\nreturn caller.role + caller.email + str(caller.id)"
            " + (n or '') + str(caller.is_admin)",
            sig="(caller: Caller) -> Str",
        )
    )
    assert errors(proc("return caller.token", sig="(caller: Caller) -> Str")) == ["L803"]
    assert errors(proc("return caller.nope", sig="(caller: Caller) -> Str")) == ["L905"]


def test_profile_capabilities():
    import pytest
    from t2_support import API

    from lattice.profile import ApiProfile, NativeSig
    from lattice.typesys import Scalar

    assert API.effect_capabilities() == frozenset({"mail", "ping", "http"})
    with pytest.raises(ValueError, match="capability"):
        ApiProfile(
            tables={},
            table_names={},
            roles=frozenset({"Public"}),
            natives={"f": NativeSig((Scalar.STR,), Scalar.STR, capability="mail")},
        )
    with pytest.raises(ValueError, match="capabilities"):
        ApiProfile(tables={}, table_names={}, roles=frozenset(), capabilities=frozenset({"a b"}))
