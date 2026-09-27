"""form login via natives: an OAuth2 password form, an ``authenticate`` helper, the
active check and a token -> ``one`` / ``require(native.<verify>)`` / ``require(active)``
/ ``derive(native.<token>)``.

The route::

    user = authenticate(session=session, email=form.username, password=form.password)
    if not user: raise HTTPException(400, "<d1>")
    elif not user.<active>: raise HTTPException(400, "<d2>")
    expires = ...                                         # optional, a bound native argument
    return Token(<token field>=create_token(user.<key>, expires_delta=expires))

and the helper (resolved through the imports, any name)::

    def authenticate(*, session, email, password):
        db_user = <lookup of the users table by one column == email>
        if not db_user:
            verify(password, DUMMY)                       # optional (timing), ignored
            return None
        verified, new_hash = verify(password, db_user.<hash column>)   # or `if not verify(...)`
        if not verified:
            return None
        if new_hash: <store it: db_user.<hash column> = new_hash; add/commit/refresh>  # optional
        return db_user

``verify`` and ``create_token`` must be natives of the profile (the tuple-unpacked verify
one whose wrapper takes the first item); the other fields of the token model need
literal defaults. Dropping the rehash-on-login write is recorded as a behaviour change.
"""

from __future__ import annotations

import ast

from lattice.importer.extract import NativeCandidate, RepoModel
from lattice.importer.lifters._orm import bind_call, lookup
from lattice.importer.lifters.base import (
    ERROR_KINDS,
    LiftFragment,
    Param,
    RouteSource,
    Step,
    assigned,
    attr_of,
    error,
    is_falsy_test,
    is_name,
    lit,
    plural,
    str_list,
)

IDIOM = "OAuth2 form + authenticate helper + active check + token -> one/require(native)/derive"
FORM_FIELDS = ("username", "password")
REHASH_NOTE = (
    "the original stores an upgraded password hash when verification returns one "
    "(authenticate's rehash); the import never rewrites the hash on login"
)


def _native(route: RouteSource, node: ast.expr, module: str) -> NativeCandidate | None:
    qual = route.resolve(node, module)
    n = next((n for n in route.model.natives if n.origin == qual and n.expressible), None)
    return n if n is not None and n.name in route.api.natives else None


def _return_none(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Return) and (
        stmt.value is None or (isinstance(stmt.value, ast.Constant) and stmt.value.value is None)
    )


def _authenticate(route: RouteSource, call: ast.Call, form: str):  # noqa: ANN202, C901
    """The helper's lookup and verification: (users table, lookup column, verify native,
    hash column, notes) when the helper has exactly the shape in the module docstring."""
    found = route.index.node(route.resolve(call.func))
    if found is None or not isinstance(found[1], ast.FunctionDef):
        return None
    module, fn = found
    bound = bind_call(fn, call)
    if bound is None:
        return None
    by_arg = {}
    session = None
    for name, value in bound.items():
        if route.is_session(value):
            session = name
        elif (field := attr_of(value, form)) in FORM_FIELDS:
            by_arg[field] = name
        else:
            return None
    if session is None or set(by_arg) != set(FORM_FIELDS):
        return None
    username, password = by_arg["username"], by_arg["password"]
    body = [
        s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    if len(body) < 4:
        return None
    # db_user = <lookup by column == username>
    a = assigned(body[0])
    if a is None:
        return None
    account, value = a
    got = lookup(route, value, session=session, module=module)
    if got is None or not is_name(got[2], username) or got[0] != route.users_table:
        return None
    table, column, _ = got
    # if not db_user: [verify(password, CONSTANT)]; return None
    s1 = body[1]
    if not (isinstance(s1, ast.If) and not s1.orelse and is_falsy_test(s1.test, account)):
        return None
    if not _return_none(s1.body[-1]):
        return None
    for extra in s1.body[:-1]:  # a pure native call whose result is dropped
        if not (isinstance(extra, ast.Expr) and isinstance(extra.value, ast.Call)):
            return None
        if _native(route, extra.value.func, module) is None:
            return None
    # verified, new_hash = verify(password, db_user.<hash>); if not verified: return None
    rest = body[2:]
    verify_call: ast.expr | None = None
    unpacked = False
    match rest[0]:
        case ast.Assign(
            targets=[ast.Tuple(elts=[ast.Name(id=ok), ast.Name(id=new_hash)])], value=value
        ):
            verify_call, unpacked = value, True
            nxt = rest[1]
            if not (
                isinstance(nxt, ast.If)
                and not nxt.orelse
                and len(nxt.body) == 1
                and is_falsy_test(nxt.test, ok)
                and _return_none(nxt.body[0])
            ):
                return None
            rest = rest[2:]
        case ast.If(test=ast.UnaryOp(op=ast.Not(), operand=value), body=[ret], orelse=[]) if (
            _return_none(ret)
        ):
            verify_call, new_hash = value, None
            rest = rest[1:]
        case _:
            return None
    if not (
        isinstance(verify_call, ast.Call)
        and not verify_call.keywords
        and len(verify_call.args) == 2
    ):
        return None
    native = _native(route, verify_call.func, module)
    if native is None or (native.adapter == "first") is not unpacked:
        return None
    if not is_name(verify_call.args[0], password):
        return None
    hash_column = attr_of(verify_call.args[1], account)
    if hash_column is None:
        return None
    notes: list[str] = []
    # optional: if new_hash: <store it>
    if (
        rest
        and isinstance(rest[0], ast.If)
        and new_hash is not None
        and is_name(rest[0].test, new_hash)
    ):
        if rest[0].orelse or not _rehash(
            route, rest[0].body, account, hash_column, new_hash, session
        ):
            return None
        notes.append(REHASH_NOTE)
        rest = rest[1:]
    if len(rest) != 1 or not (isinstance(rest[0], ast.Return) and is_name(rest[0].value, account)):
        return None
    return table, column, native, hash_column, tuple(notes)


def _rehash(
    route: RouteSource,
    stmts: list[ast.stmt],
    account: str,
    column: str,
    new_hash: str,
    session: str,
) -> bool:
    """``account.<column> = new_hash`` then session add/commit/refresh of the account."""
    match stmts[0]:
        case ast.Assign(
            targets=[ast.Attribute(value=ast.Name(id=a), attr=c)], value=ast.Name(id=v)
        ) if (a, c, v) == (account, column, new_hash):
            pass
        case _:
            return False
    for stmt in stmts[1:]:
        match stmt:
            case ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(value=ast.Name(id=s), attr=m), args=args, keywords=[]
                )
            ) if s == session:
                if m == "commit" and not args:
                    continue
                if m in ("add", "refresh") and len(args) == 1 and is_name(args[0], account):
                    continue
        return False
    return True


def _active_checks(route: RouteSource, stmts: list[ast.stmt], user: str):  # noqa: ANN202
    """``if not user: raise A; elif not user.<col>: raise B`` (or two ifs): ((status,
    detail) of A, (status, detail, col) of B, statements used)."""
    first = stmts[0] if stmts else None
    if not (isinstance(first, ast.If) and len(first.body) == 1 and is_falsy_test(first.test, user)):
        return None
    missing = route.http_raise(first.body[0])
    if len(first.orelse) == 1:
        second, used = first.orelse[0], 1
    elif not first.orelse and len(stmts) > 1:
        second, used = stmts[1], 2
    else:
        return None
    guard = route.guard_raise(second)
    if missing is None or guard is None:
        return None
    test, status, detail = guard
    if not (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)):
        return None
    col = attr_of(test.operand, user)
    return (missing, (status, detail, col), used) if col is not None else None


def match(route: RouteSource, model: RepoModel) -> LiftFragment | None:  # noqa: C901
    forms = [p for p in route.route.params if p.source == "form"]
    if len(forms) != 1 or route.params_of("body") or route.params_of("query"):
        return None
    form = forms[0].name
    form_class = ast.parse(forms[0].type or "_", mode="eval").body
    if not route.resolves_to(
        form_class,
        "fastapi.security:OAuth2PasswordRequestForm",
        "fastapi:OAuth2PasswordRequestForm",
    ):
        return None
    body = list(route.body)
    a = assigned(body[0]) if body else None
    if a is None or not isinstance(a[1], ast.Call):
        return None
    user, call = a
    auth = _authenticate(route, call, form)
    checks = _active_checks(route, body[1:], user)
    if auth is None or checks is None:
        return None
    table, column, verify, hash_column, notes = auth
    (s1, d1), (s2, d2, active), used = checks
    k1, k2 = ERROR_KINDS.get(s1), ERROR_KINDS.get(s2)
    columns = {c.name: c for c in table.columns}
    if k1 is None or k2 is None or getattr(columns.get(active), "type", None) != "Bool":
        return None
    rest = body[1 + used :]
    # optional local bound to a native's bound argument, then the token response
    local: tuple[str, ast.expr] | None = None
    if len(rest) == 2:
        local = assigned(rest[0])
        if local is None:
            return None
        rest = rest[1:]
    if len(rest) != 1 or not isinstance(rest[0], ast.Return):
        return None
    ret = rest[0].value
    token_model = route.data_model(route.route.response_model)
    if not (
        isinstance(ret, ast.Call)
        and isinstance(ret.func, ast.Name)
        and token_model
        and ret.func.id == token_model.name
        and not ret.args
        and len(ret.keywords) == 1
    ):
        return None
    field, value = ret.keywords[0].arg, ret.keywords[0].value
    if not isinstance(value, ast.Call):
        return None
    token = _native(route, value.func, route.route.module)
    key = route.key(table)
    free = [p for p in token.params if p.bound is None] if token else []
    if token is None or key is None or len(free) != 1 or len(value.args) != 1:
        return None
    if attr_of(value.args[0], user) != key.name:
        return None
    bound_names = {p.name for p in token.params if p.bound is not None}
    for kw in value.keywords:
        if kw.arg not in bound_names:
            return None
        if local is not None and is_name(kw.value, local[0]):
            local = None  # used, as the wrapper's bound argument
    if local is not None:
        return None
    derived: list[tuple[str, str]] = []
    for f in token_model.fields:
        if f.name == field:
            derived.append((f.name, f"native.{token.name}(col({lit(key.name)}))"))
            continue
        try:
            default = ast.literal_eval(f.default) if f.default is not None else None
        except (ValueError, SyntaxError):
            return None
        if f.default is None or f.required:
            return None
        derived.append((f.name, lit(default)))
    rows = plural(table)
    return LiftFragment(
        lifter=__name__.rsplit(".", 1)[-1],
        idiom=IDIOM,
        stmts=tuple(range(len(body))),
        steps=(
            Step(rows, "fetch", (table.name,)),
            Step("found", "where", ("{#" + rows + "}", f"col({lit(column)}) == username")),
            Step("account", "one", ("{#found}",), (("else_", error(k1, d1)),)),
            Step(
                "verified",
                "require",
                ("{#account}", f"native.{verify.name}(password, col({lit(hash_column)}))"),
                (("else_", error(k1, d1)),),
            ),
            Step(
                "active",
                "require",
                ("{#verified}", f"col({lit(active)})"),
                (("else_", error(k2, d2)),),
            ),
            Step("token", "derive", ("{#active}",), tuple(derived)),
            Step(
                "shown",
                "select",
                ("{#token}",),
                (("cols", str_list([f.name for f in token_model.fields])),),
            ),
        ),
        params=(Param("username", "body", "Str"), Param("password", "body", "Str")),
        consumes=(form,),
        errors=(k1, k2),
        returns="{#shown}",
        endpoint=(("body", lit("form")),),
        notes=notes,
    )
