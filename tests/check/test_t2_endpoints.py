"""T2b: procedures called from endpoints (spec 1c §3) -- the checker's T0 integration.

Scalar calls are the whole value of a derive column, the whole where predicate or the
whole require condition, and run in the app layer after the read's one statement
("T2-post"). Whole-value calls `x = call(proc, name=table, ...)` take bounded tables.
`insert_many(T, rows=x)` writes a procedure's rows. Effectful procedures go through
for_each. Codes: L880 (a procedure filter before page), L881 (a procedure where T0
needs SQL), L882 (effects), L883 (the T0/T2 boundary), plus the existing L439 (bound),
L802 (ownership), L803 (private) and L402/L404."""

from t2_support import errors, first, ok

from lattice.catalog import T2_T0_CODES
from lattice.typesys import Scalar, TableT

PROCS = """
@record
class Line:
    id: Int
    text: Str


@procedure
def locked(item: Row[Items], caller: Caller, now: DateTime) -> Bool:
    if caller.is_admin:
        return False
    return now - item.created_at > hours(24)


@procedure
def shout(title: Str, n: Int) -> Str:
    return title.upper() + "!" * n


@procedure
def maybe_due(due: Date | None) -> Str | None:
    if due is None:
        return None
    return due.isoformat()


@procedure
def weekday(d: Date) -> Int:
    return d.weekday()


@procedure
def is_long(title: Str) -> Bool:
    return len(title) > 3


@procedure
def digest(rows: list[Row[Items]], prefix: Str) -> list[Line]:
    return [Line(id=r.id, text=prefix + r.title) for r in rows]


@procedure
def total(rows: list[Row[Items]]) -> Float:
    return sum(r.score for r in rows)


@procedure(effects=["mail"])
def notify(to: Str, title: Str) -> None:
    native.send_mail(to, title)
"""


def program(body: str) -> str:
    return PROCS + "\n\n" + body


# ---- scalar calls --------------------------------------------------------------------------


def test_scalar_call_in_derive_with_row_caller_and_now():
    result = ok(
        program(
            """
@endpoint("GET", "/items", auth=Customer)
def list_items(user: CurrentUser) -> Page:
    items = fetch(Items)
    mine = accessible(items, by=user)
    flagged = derive(mine, is_locked=locked(row(), user, fn.now()), loud=shout(col("title"), 2))
    shown = select(flagged, cols=["id", "title", "is_locked", "loud"])
    result = page(shown, limit=20)
    return result
"""
        )
    )
    info = result.program.endpoints["list_items"]
    assert info.success == "Page"
    ret = info.node.body[-1]
    t = result.program.types[id(ret.value)]
    assert isinstance(t, TableT)
    assert dict(t.schema.columns)["is_locked"] is Scalar.BOOL
    assert dict(t.schema.columns)["loud"] is Scalar.STR


def test_an_optional_result_is_a_nullable_column():
    result = ok(
        program(
            """
@endpoint("GET", "/items/due", auth=Customer)
def due(user: CurrentUser) -> Page:
    items = fetch(Items)
    mine = accessible(items, by=user)
    shown = select(mine, cols=["id", "due"])
    labelled = derive(shown, label=maybe_due(col("due")))
    result = page(labelled, limit=20)
    return result
"""
        )
    )
    t = result.program.types[id(result.program.endpoints["due"].node.body[-1].value)]
    assert "label" in t.nullable


def test_where_after_page_and_require():
    ok(
        program(
            """
@endpoint("GET", "/items/long", auth=Customer)
def long_items(user: CurrentUser, limit: Int(ge=1, le=50) = 10) -> Page:
    items = fetch(Items)
    mine = accessible(items, by=user)
    shown = select(mine, cols=["id", "title"])
    ordered = sort(shown, by=["id"])
    paged = page(ordered, limit=limit)
    long = where(paged, is_long(col("title")))
    return long


@endpoint("GET", "/items/{item_id}", auth=Customer)
def get_item(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Items)
    mine = accessible(items, by=user)
    found = where(mine, col("id") == item_id)
    item = one(found, else_=NotFound("item"))
    open_item = require(item, is_long(col("title")), else_=Forbidden("x"))
    shown = select(open_item, cols=["id", "title"])
    return shown
"""
        )
    )


def test_where_on_a_read_without_page():
    ok(
        program(
            """
@endpoint("GET", "/items/{item_id}/check", auth=Customer)
def check_item(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    items = fetch(Items)
    mine = accessible(items, by=user)
    found = where(mine, col("id") == item_id)
    long = where(found, is_long(col("title")))
    shown = select(long, cols=["id", "title"])
    item = one(shown, else_=NotFound("item"))
    return item
"""
        )
    )


def test_constants_and_parameters_as_arguments():
    ok(
        program(
            """
TIMES = 3


@endpoint("GET", "/items/shout", auth=Customer)
def shouted(user: CurrentUser, n: Int(ge=0, le=5) = 1) -> Page:
    items = fetch(Items)
    mine = accessible(items, by=user)
    a = derive(mine, loud=shout(col("title"), n), louder=shout(title=col("title"), n=TIMES))
    shown = select(a, cols=["id", "loud", "louder"])
    result = page(shown, limit=20)
    return result
"""
        )
    )


def test_a_program_without_procedures_does_not_know_the_t2_names():
    src = """
@endpoint("GET", "/items", auth=Customer)
def list_items(user: CurrentUser) -> Page:
    items = fetch(Items)
    mine = accessible(items, by=user)
    x = call(digest, rows=mine)
    return x
"""
    assert "L203" in errors(src)


# ---- scalar call rules ---------------------------------------------------------------------


def _derive(expr: str, extra: str = "") -> str:
    return program(
        f"""
@endpoint("GET", "/items", auth=Customer)
def list_items(user: CurrentUser) -> Page:
    items = fetch(Items)
    mine = accessible(items, by=user)
    d = derive(mine, x={expr})
    shown = select(d, cols=["id", "x"])
    result = page(shown, limit=20)
    return result
{extra}"""
    )


def test_a_call_nested_in_an_expression_is_l881():
    d = first(_derive('shout(col("title"), 1) + "?"'), "L881")
    assert "whole value" in d.message


def test_an_argument_that_is_not_a_column_or_value_is_l881():
    d = first(_derive('shout(col("title") + "x", 1)'), "L881")
    assert "argument" in d.message


def test_row_outside_a_procedure_call_is_l881():
    assert "L881" in errors(_derive("row()"))


def test_an_effectful_procedure_in_an_expression_is_l882():
    d = first(_derive('notify(col("title"), col("title"))'), "L882")
    assert "for_each" in (d.hint or "")


def test_a_nullable_argument_for_a_plain_parameter_is_l883():
    d = first(_derive('weekday(col("due"))'), "L883")
    assert "None" in d.message or "null" in d.message


def test_an_argument_of_the_wrong_type_is_l402():
    assert "L402" in errors(_derive('shout(col("id"), 1)'))


def test_a_result_without_a_column_type_is_l883():
    extra = """

@procedure
def pair(n: Int) -> list[Int]:
    return [n, n]
"""
    assert "L883" in errors(_derive('pair(col("id"))', extra))


def test_wrong_arguments_are_l404():
    assert "L404" in errors(_derive('shout(col("title"))'))
    assert "L404" in errors(_derive('shout(col("title"), 1, 2)'))
    assert "L404" in errors(_derive('shout(col("title"), m=1)'))


def test_a_private_row_needs_an_admin_endpoint():
    extra = """

@procedure
def peek(item: Row(Items, private=True)) -> Str:
    return item.secret
"""
    assert "L803" in errors(_derive("peek(row())", extra))


def test_a_where_before_page_is_l880():
    src = program(
        """
@endpoint("GET", "/items", auth=Customer)
def list_items(user: CurrentUser) -> Page:
    items = fetch(Items)
    mine = accessible(items, by=user)
    long = where(mine, is_long(col("title")))
    shown = select(long, cols=["id", "title"])
    result = page(shown, limit=20)
    return result
"""
    )
    d = first(src, "L880")
    assert "page" in d.message
    assert d.hint


def test_sql_ops_over_rows_a_procedure_computed_are_l881():
    src = program(
        """
@endpoint("GET", "/items", auth=Customer)
def list_items(user: CurrentUser) -> Page:
    items = fetch(Items)
    mine = accessible(items, by=user)
    d = derive(mine, loud=shout(col("title"), 1))
    ordered = sort(d, by=["loud"])
    shown = select(ordered, cols=["id", "loud"])
    result = page(shown, limit=20)
    return result
"""
    )
    d = first(src, "L881")
    assert "sort" in d.message


# ---- whole-value calls ---------------------------------------------------------------------


def test_whole_value_call_returned_as_a_list():
    result = ok(
        program(
            """
@endpoint("GET", "/digest", auth=Customer)
def get_digest(user: CurrentUser) -> List:
    items = fetch(Items)
    mine = accessible(items, by=user)
    ordered = sort(mine, by=["id"])
    few = limit(ordered, n=10)
    lines = call(digest, rows=few, prefix="- ")
    return lines
"""
        )
    )
    info = result.program.endpoints["get_digest"]
    assert info.success == "List"
    t = result.program.types[id(info.node.body[-1].value)]
    assert t.schema.names() == ("id", "text")


def test_a_scalar_whole_value_result_is_an_update_value():
    ok(
        program(
            """
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
    )


def test_a_whole_value_call_over_an_unbounded_table_is_l439():
    src = program(
        """
@endpoint("GET", "/digest", auth=Customer)
def get_digest(user: CurrentUser) -> List:
    items = fetch(Items)
    mine = accessible(items, by=user)
    lines = call(digest, rows=mine, prefix="- ")
    return lines
"""
    )
    assert "L439" in errors(src)


def test_a_whole_value_call_over_rows_not_checked_for_ownership_is_l802():
    src = program(
        """
@endpoint("GET", "/digest", auth=Customer)
def get_digest(user: CurrentUser) -> List:
    items = fetch(Items)
    ordered = sort(items, by=["id"])
    few = limit(ordered, n=10)
    lines = call(digest, rows=few, prefix="- ")
    return lines
"""
    )
    assert "L802" in errors(src)


def test_whole_value_call_arguments():
    base = """
@endpoint("GET", "/digest", auth=Customer)
def get_digest(user: CurrentUser) -> List:
    items = fetch(Items)
    mine = accessible(items, by=user)
    ordered = sort(mine, by=["id"])
    few = limit(ordered, n=10)
    lines = call({args})
    return lines
"""
    assert "L404" in errors(program(base.format(args="digest, rows=few")))  # missing prefix
    assert "L404" in errors(program(base.format(args='digest, rows=few, prefix="", x=1')))
    assert "L404" in errors(program(base.format(args='digest, few, prefix=""')))
    assert "L402" in errors(program(base.format(args="digest, rows=few, prefix=1")))
    assert "L882" in errors(program(base.format(args='notify, to="a", title="b"')))


def test_a_table_lacking_the_row_columns_is_l883():
    src = program(
        """
@endpoint("GET", "/digest", auth=Customer)
def get_digest(user: CurrentUser) -> List:
    items = fetch(Items)
    mine = accessible(items, by=user)
    slim = select(mine, cols=["id", "title"])
    few = limit(slim, n=10)
    lines = call(digest, rows=few, prefix="- ")
    return lines
"""
    )
    d = first(src, "L883")
    assert "created_at" in d.message


def test_list_response_needs_bounded_rows():
    src = program(
        """
@endpoint("GET", "/items/all", auth=Customer)
def all_items(user: CurrentUser) -> List:
    items = fetch(Items)
    mine = accessible(items, by=user)
    shown = select(mine, cols=["id", "title"])
    return shown
"""
    )
    assert "L831" in errors(src)


# ---- insert_many and effects -----------------------------------------------------------------


def test_insert_many_writes_a_procedures_rows():
    ok(
        """
@record
class NewEvent:
    user_id: Int
    minute: Int
    kind: Str


@procedure
def expand(n: Int, uid: Int) -> list[NewEvent]:
    return [NewEvent(user_id=uid, minute=i, kind="fail") for i in range(n)]


@endpoint("POST", "/events", auth=Admin)
def add_events(user: CurrentUser, n: Int(ge=0, le=5)) -> List:
    events = call(expand, n=n, uid=user.id)
    written = insert_many(LoginEvents, rows=events)
    return written
"""
    )


def test_insert_many_rows_must_match_the_table():
    src = """
@record
class Bad:
    user_id: Int
    minute: Str


@procedure
def expand(n: Int) -> list[Bad]:
    return [Bad(user_id=n, minute="x")]


@endpoint("POST", "/events", auth=Admin)
def add_events(user: CurrentUser, n: Int(ge=0, le=5)) -> List:
    events = call(expand, n=n)
    written = insert_many(LoginEvents, rows=events)
    return written
"""
    codes = errors(src)
    assert "L402" in codes  # minute is Int
    assert "L404" in codes  # kind is required


def test_effectful_procedures_go_through_for_each():
    ok(
        program(
            """
@endpoint("POST", "/items/{item_id}/ping", auth=Customer)
def ping_item(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    items = fetch(Items)
    mine = accessible(items, by=user)
    found = where(mine, col("id") == item_id)
    item = one(found, else_=NotFound("item"))
    for_each(item, notify, user.email, col("title"))
    shown = select(item, cols=["id", "title"])
    return shown
"""
        )
    )
    src = program(
        """
@endpoint("POST", "/items/{item_id}/ping", auth=Customer)
def ping_item(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    items = fetch(Items)
    mine = accessible(items, by=user)
    found = where(mine, col("id") == item_id)
    item = one(found, else_=NotFound("item"))
    for_each(item, is_long, col("title"))
    shown = select(item, cols=["id", "title"])
    return shown
"""
    )
    assert "L882" in errors(src)


def test_the_new_codes_are_documented():
    assert set(T2_T0_CODES) == {"L880", "L881", "L882", "L883"}


def test_a_computed_constant_is_not_a_procedure_argument():
    src = program(
        """
BASE = 2
TIMES = BASE * 2


@endpoint("GET", "/items/shout", auth=Customer)
def shouted(user: CurrentUser) -> Page:
    items = fetch(Items)
    mine = accessible(items, by=user)
    a = derive(mine, loud=shout(col("title"), TIMES))
    shown = select(a, cols=["id", "loud"])
    result = page(shown, limit=20)
    return result
"""
    )
    d = first(src, "L881")
    assert "TIMES" in d.message


def test_a_scalar_result_is_an_argument_of_another_call():
    ok(
        program(
            """
@endpoint("GET", "/items/{item_id}/twice", auth=Customer)
def twice(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    items = fetch(Items)
    mine = accessible(items, by=user)
    found = where(mine, col("id") == item_id)
    item = one(found, else_=NotFound("item"))
    s = call(total, rows=item)
    t = call(shout, title="x", n=2)
    both = derive(item, loud=shout(t, 1), score2=col("score") + s)
    shown = select(both, cols=["id", "loud", "score2"])
    return shown
"""
        )
    )


def test_private_columns_stay_private_through_procedure_arguments():
    # a procedure filter over a private column is an oracle (L803), like a T0 filter
    where_src = program(
        """
@endpoint("GET", "/items/{item_id}/probe", auth=Customer)
def probe(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    items = fetch(Items)
    mine = accessible(items, by=user)
    found = where(mine, col("id") == item_id)
    shown = select(found, cols=["id", "title", "secret"])
    paged = limit(shown, n=1)
    long = where(paged, is_long(col("secret")))
    slim = select(long, cols=["id", "title"])
    item = one(slim, else_=NotFound("item"))
    return item
"""
    )
    assert "L803" in errors(where_src)
    # a value computed from a private column is private: it cannot be returned
    derive_src = program(
        """
@endpoint("GET", "/items/{item_id}/loud", auth=Customer)
def loud(item_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    items = fetch(Items)
    mine = accessible(items, by=user)
    found = where(mine, col("id") == item_id)
    item = one(found, else_=NotFound("item"))
    d = derive(item, x=shout(col("secret"), 1))
    shown = select(d, cols=["id", "x"])
    return shown
"""
    )
    assert "L803" in errors(derive_src)


def test_admin_endpoints_may_hand_private_rows_over():
    ok(
        program(
            """
@procedure
def peek(item: Row(Items, private=True)) -> Int:
    return len(item.secret)


@endpoint("GET", "/admin/items", auth=Admin)
def admin_items(user: CurrentUser) -> Page:
    items = fetch(Items)
    ordered = sort(items, by=["id"])
    d = derive(ordered, n=peek(row()))
    shown = select(d, cols=["id", "title"])
    result = page(shown, limit=20)
    return result
"""
        )
    )
