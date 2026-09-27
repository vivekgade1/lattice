"""T2 execution support: fuel, the restricted namespace compiled procedures run in,
and the boundary between stored rows and procedure values.

This module imports only the standard library. The Lattice runtime imports it, and
`lattice.api.codegen` copies its source (below the `# ---- begin` marker) into every
generated app that has procedures, so both run the same code: the same fuel
accounting, the same values at the boundary, the same error texts.

Every name defined here starts with `t2_` / `T2`, so the copy cannot clash with the
generated app's own names.

Fuel. A request has one budget (`ApiProfile.fuel`) shared by every procedure it runs.
One unit is charged per loop iteration, per call (procedure, builtin, method, native,
record constructor), per comprehension element, and per element a builtin consumes
(`sum`, `sorted`, `list`, `"".join`, ...: a sized argument is charged its length up
front, a lazy one per element). Recursion is also bounded by a depth
(`T2_MAX_DEPTH`). Running out raises `T2FuelExhausted`; the host answers it as runtime
diagnostic R620 (a 500). Fuel bounds work; it is not a termination proof.

Values. Stored values become Python values of their Lattice scalar (Int int, Float
float, Str str, Bool bool, Date `datetime.date`, DateTime an aware UTC
`datetime.datetime`, Uuid `uuid.UUID`). A `Row[Table]` is an immutable `T2Row`
(attribute access to its visible columns), a `@record` a frozen dataclass, the caller
a `T2Row` named Caller. Lists are fresh lists: a procedure never shares a container
with its caller.
"""

from __future__ import annotations

# ---- begin (copied into generated apps) --------------------------------------------------
import dataclasses as t2_dataclasses
import datetime as t2_dt
import importlib as t2_importlib
import logging as t2_logging
import types as t2_types
import uuid as t2_uuid

# A procedure call deeper than this is R620 (recursion is fuel-bounded; the depth keeps
# the Python stack far from its own limit).
T2_MAX_DEPTH = 200
# A whole-value call returns at most as many rows as its table arguments hold, or this
# many when that is larger (R621 otherwise): rows a procedure returns are bounded by its
# input.
T2_MIN_ROW_BOUND = 100


class T2Error(Exception):
    """A runtime diagnostic raised by T2 code: its code (R620, R621) and message. The
    host renders it with the span of the endpoint statement that ran the procedure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


t2_log = t2_logging.getLogger("lattice.t2")


def t2_report(where: str, err: T2Error) -> str:
    """The located message of a T2 runtime diagnostic ("line:col: error R620: ..."),
    logged (logger `lattice.t2`) as the host answers it with a 500."""
    text = f"{where}: error {err.code}: {err.message}"
    t2_log.error("%s", text)
    return text


def t2_role(
    columns: tuple[str, ...],
    row: tuple,
    role_column: str,
    admin_flag: str | None,
    admin_role: str,
    flag_role: str,
) -> str:
    """The caller's role from its users row (a role column, or an Admin flag column)."""
    if admin_flag is not None:
        return admin_role if row[columns.index(admin_flag)] else flag_role
    return str(row[columns.index(role_column)])


class T2FuelExhausted(T2Error):
    """The request's fuel is spent, or procedure calls nest deeper than the limit."""

    def __init__(self, message: str) -> None:
        super().__init__("R620", message)


class T2Fuel:
    """The fuel of one request (or one outbox dispatch): `left` units, and the current
    procedure call depth."""

    __slots__ = ("budget", "depth", "left", "max_depth")

    def __init__(self, budget: int, max_depth: int = T2_MAX_DEPTH) -> None:
        self.budget = budget
        self.left = budget
        self.depth = 0
        self.max_depth = max_depth

    def _spent(self) -> T2FuelExhausted:
        self.left = -1
        return T2FuelExhausted(
            f"procedure ran out of fuel: the request's budget of {self.budget} steps is "
            "spent (every loop iteration, call and comprehension element costs one)"
        )

    def tick(self) -> None:
        """One step: a loop iteration."""
        self.left -= 1
        if self.left < 0:
            raise self._spent()

    def call(self, fn: object) -> object:
        """One step for a call: `fuel.call(f)(args)` charges, then calls `f`."""
        self.left -= 1
        if self.left < 0:
            raise self._spent()
        return fn

    def each(self, iterable: object):  # noqa: ANN201 - a generator
        """The elements of a comprehension's iterable, one step each."""
        for x in iterable:  # type: ignore[attr-defined]
            self.left -= 1
            if self.left < 0:
                raise self._spent()
            yield x

    def bulk(self, iterable: object) -> object:
        """An argument a builtin consumes whole: a sized value is charged its length up
        front (and passed unchanged), a lazy one per element as it is consumed."""
        try:
            n = len(iterable)  # type: ignore[arg-type]
        except TypeError:
            return self.each(iterable)
        self.left -= n
        if self.left < 0:
            raise self._spent()
        return iterable

    def enter(self) -> None:
        """A procedure call: one step, one level deeper."""
        self.left -= 1
        if self.left < 0:
            raise self._spent()
        self.depth += 1
        if self.depth > self.max_depth:
            raise T2FuelExhausted(
                f"procedure calls nest deeper than {self.max_depth} (recursion is bounded)"
            )

    def leave(self) -> None:
        self.depth -= 1


class T2Row:
    """An immutable row: attribute access to its columns (a `Row[Table]` value, or the
    Caller)."""

    __slots__ = ("__name", "__values")

    def __init__(self, name: str, values: dict[str, object]) -> None:
        object.__setattr__(self, "_T2Row__name", name)
        object.__setattr__(self, "_T2Row__values", dict(values))

    def __getattr__(self, attr: str) -> object:
        try:
            return self.__values[attr]
        except KeyError:
            raise AttributeError(f"{self.__name} has no column {attr!r}") from None

    def __setattr__(self, attr: str, value: object) -> None:
        raise AttributeError(f"{self.__name} is immutable")

    def __delattr__(self, attr: str) -> None:
        raise AttributeError(f"{self.__name} is immutable")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, T2Row):
            return NotImplemented
        return (self.__name, self.__values) == (other.__name, other.__values)

    def __hash__(self) -> int:
        return hash((self.__name, tuple(self.__values.items())))

    def __repr__(self) -> str:
        cells = ", ".join(f"{k}={v!r}" for k, v in self.__values.items())
        return f"{self.__name}({cells})"


# ---- the restricted namespace -------------------------------------------------------------

# The builtins a procedure may call (catalog.T2_BUILTINS), the factories defaultdict
# takes, and the errors an except clause may name (catalog.T2_ERRORS). Nothing else of
# Python's builtins is reachable: the namespace's __builtins__ is empty.
T2_BUILTIN_NAMES = (
    "len",
    "abs",
    "round",
    "min",
    "max",
    "sum",
    "sorted",
    "range",
    "enumerate",
    "zip",
    "reversed",
    "any",
    "all",
    "str",
    "int",
    "float",
    "list",
    "set",
    "dict",
    "ValueError",
    "KeyError",
    "IndexError",
    "ZeroDivisionError",
)
_T2_UNITS = ("days", "hours", "minutes", "seconds")


def _t2_builtins() -> dict[str, object]:
    import builtins

    return {name: getattr(builtins, name) for name in T2_BUILTIN_NAMES}


def _t2_std_module(module: str, names: tuple[str, ...]) -> object:
    """An allowlisted stdlib module as a namespace holding only `names`."""
    real = t2_importlib.import_module(module)
    return t2_types.SimpleNamespace(**{n: getattr(real, n) for n in names})


def _t2_std(module: str, name: str) -> object:
    return getattr(t2_importlib.import_module(module), name)


def _t2_helper(unit: str) -> object:
    if unit not in _T2_UNITS:
        raise ValueError(f"unknown Duration helper {unit!r}")

    def make(n: float) -> t2_dt.timedelta:
        return t2_dt.timedelta(**{unit: n})

    make.__name__ = unit
    return make


def _t2_record(name: str, fields: tuple[str, ...], defaults: dict[str, object]) -> type:
    """A @record: a frozen dataclass with the declared fields (literal defaults)."""
    spec = [
        (f, object, t2_dataclasses.field(default=defaults[f])) if f in defaults else (f, object)
        for f in fields
    ]
    return t2_dataclasses.make_dataclass(name, spec, frozen=True)


# Modules the standard library itself imports while a procedure runs (datetime's
# strftime imports `time`). The namespace's only builtin is an `__import__` limited to
# these: procedures cannot name it (the checker rejects `__import__`, L140).
_T2_INTERNAL_IMPORTS = frozenset({"time", "_strptime", "calendar", "locale", "unicodedata"})


def _t2_import(name: str, *args: object, **kwargs: object) -> object:
    if name not in _T2_INTERNAL_IMPORTS:
        raise ImportError(f"procedures cannot import {name!r}")
    return t2_importlib.import_module(name)


def t2_load(source: str, natives: dict[str, object] | None = None) -> dict[str, object]:
    """Run compiled T2 source (`lattice.t2.compile`) in a fresh restricted namespace:
    no ambient builtins (only a restricted `__import__` the standard library uses
    internally), only what its preamble binds through the loader functions
    (allowlisted builtins, stdlib names, Duration helpers, records, constants) and
    `native` (the natives the procedures call). Returns the namespace (procedure name
    -> function taking the fuel first)."""
    namespace: dict[str, object] = {
        "__builtins__": {"__import__": _t2_import},
        "t2_builtin": _t2_builtins().__getitem__,
        "t2_std_module": _t2_std_module,
        "t2_std": _t2_std,
        "t2_helper": _t2_helper,
        "t2_record": _t2_record,
        "native": t2_types.SimpleNamespace(**(natives or {})),
    }
    exec(compile(source, "<lattice-procedures>", "exec"), namespace)  # noqa: S102
    for loader in ("t2_builtin", "t2_std_module", "t2_std", "t2_helper", "t2_record"):
        del namespace[loader]
    return namespace


# ---- values at the boundary -----------------------------------------------------------------


def t2_decode(value: object, scalar: str) -> object:
    """A stored value as the procedure sees it (None stays None)."""
    if value is None:
        return None
    if scalar == "Int":
        return int(value)  # type: ignore[call-overload]
    if scalar == "Float":
        return float(value)  # type: ignore[arg-type]
    if scalar == "Bool":
        return bool(value)
    if scalar == "Date":
        if isinstance(value, t2_dt.date):
            return value
        return t2_dt.date.fromisoformat(str(value))
    if scalar == "DateTime":
        parsed = value if isinstance(value, t2_dt.datetime) else None
        if parsed is None:
            parsed = t2_dt.datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=t2_dt.UTC)
        return parsed.astimezone(t2_dt.UTC)
    if scalar == "Uuid":
        return value if isinstance(value, t2_uuid.UUID) else t2_uuid.UUID(str(value))
    return value


def t2_result(value: object, scalar: str) -> object:
    """A procedure's value of a Lattice scalar, normalized (an Int where Float is
    declared widens; a naive DateTime is UTC). The host then stores it."""
    if value is None:
        return None
    if scalar == "Float" and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if scalar == "DateTime" and isinstance(value, t2_dt.datetime) and value.tzinfo is None:
        return value.replace(tzinfo=t2_dt.UTC)
    return value


class T2Request:
    """What argument descriptors read in one request: the endpoint's parameter values
    (`unset`: the host's marker of an optional parameter that was not sent, passed as
    None), the caller's row (column -> stored value) and role, and fn.now()."""

    def __init__(
        self,
        values: dict[str, object],
        unset: object,
        caller: dict[str, object] | None,
        caller_types: tuple[tuple[str, str], ...],
        caller_key: str,
        is_admin: bool,
        role: str,
        now: object,
    ) -> None:
        self.values = values
        self.unset = unset
        self.caller_row = caller
        self.caller_types = caller_types
        self.caller_key = caller_key
        self.is_admin = is_admin
        self.role = role
        self.now = now
        self._caller: T2Row | None = None

    def param(self, name: str) -> object:
        value = self.values[name]
        return None if value is self.unset else value

    def caller(self) -> T2Row:
        """The Caller: id (the users-table key), is_admin, role, public columns."""
        if self._caller is None:
            row = self.caller_row or {}
            cells: dict[str, object] = {
                c: t2_decode(row.get(c), s) for c, s in self.caller_types if c in row
            }
            key_type = dict(self.caller_types).get(self.caller_key, "Int")
            cells["id"] = t2_decode(row.get(self.caller_key), key_type)
            cells["is_admin"] = self.is_admin
            cells["role"] = self.role
            self._caller = T2Row("Caller", cells)
        return self._caller


def t2_row_value(
    kind: str, name: str, fields: tuple[tuple[str, str], ...], cells: dict[str, object], ns: dict
) -> object:
    """A row (stored cells by column) as a procedure argument: a `Row[Table]`
    (kind "row") or a record (kind "record", built by the program's own class)."""
    values = {f: t2_decode(cells[f], s) for f, s in fields}
    if kind == "record":
        return ns[name](**values)  # type: ignore[operator]
    return T2Row(name, values)


def t2_arg(arg: tuple, cells: dict[str, object] | None, req: T2Request, ns: dict) -> object:
    """One argument descriptor's value (a column of the current row `cells`, an
    endpoint parameter, a constant, fn.now(), the caller, the whole row)."""
    kind = arg[0]
    if kind == "col":
        return t2_decode((cells or {})[arg[1]], arg[2])
    if kind == "param":
        return req.param(arg[1])
    if kind == "const":
        return arg[1]
    if kind == "now":
        return req.now
    if kind == "caller":
        return req.caller()
    if kind == "caller_col":
        return t2_decode((req.caller_row or {}).get(arg[1]), arg[2])
    if kind == "is_admin":
        return req.is_admin
    if kind == "row":
        return t2_row_value(arg[1], arg[2], arg[3], cells or {}, ns)
    raise ValueError(f"unknown procedure argument {arg!r}")


def t2_invoke(ns: dict, fuel: T2Fuel, proc: str, args: dict[str, object]) -> object:
    """Call procedure `proc` with keyword arguments (the fuel goes first)."""
    fn = ns[proc]
    return fn(fuel, **args)  # type: ignore[operator]


def t2_post(
    ns: dict,
    fuel: T2Fuel,
    req: T2Request,
    columns: tuple[str, ...],
    rows: list[tuple],
    ops: tuple,
    out: tuple[str, ...],
    encode: object,
) -> list[tuple]:
    """A T2-post read: the row-wise procedure ops after the read's one statement, in
    order, over its rows (stored values), giving rows of the `out` columns.

    ops: ("derive", column, procedure, ((param, arg), ...), scalar) sets a column to
    the procedure's value; ("where", procedure, args) keeps the rows it holds for;
    ("select", (columns...)) and ("rename", ((old, new), ...)) reshape."""
    result = []
    for row in rows:
        cells = dict(zip(columns, row, strict=True))
        keep = True
        for op in ops:
            kind = op[0]
            if kind == "derive":
                _, column, proc, args, scalar = op
                value = t2_invoke(ns, fuel, proc, {p: t2_arg(a, cells, req, ns) for p, a in args})
                cells[column] = encode(t2_result(value, scalar))  # type: ignore[operator]
            elif kind == "where":
                _, proc, args = op
                value = t2_invoke(ns, fuel, proc, {p: t2_arg(a, cells, req, ns) for p, a in args})
                if not value:
                    keep = False
                    break
            elif kind == "select":
                cells = {c: cells[c] for c in op[1]}
            elif kind == "rename":
                mapping = dict(op[1])
                cells = {mapping.get(c, c): v for c, v in cells.items()}
            else:
                raise ValueError(f"unknown T2 op {kind!r}")
        if keep:
            result.append(tuple(cells[c] for c in out))
    return result


def t2_holds(
    ns: dict,
    fuel: T2Fuel,
    req: T2Request,
    columns: tuple[str, ...],
    rows: list[tuple],
    proc: str,
    args: tuple,
) -> bool:
    """require(t, proc(...)): the procedure holds on every row (a None fails)."""
    for row in rows:
        cells = dict(zip(columns, row, strict=True))
        if not t2_invoke(ns, fuel, proc, {p: t2_arg(a, cells, req, ns) for p, a in args}):
            return False
    return True


def t2_call(
    ns: dict,
    fuel: T2Fuel,
    req: T2Request,
    proc: str,
    args: tuple,
    results: dict,
    result: tuple,
    encode: object,
) -> object:
    """A whole-value call `x = call(proc, name=..., ...)`: table arguments
    ("rows", step, kind, name, fields) become lists of rows/records (fresh lists), the
    others are argument descriptors. `result` says what the value becomes:
    ("rows", ((field, scalar), ...), many) -> (columns, stored rows) -- `many` for a
    list, else the one record/row; ("scalar", scalar) -> the Python value."""
    values: dict[str, object] = {}
    bound = 0
    for param, arg in args:
        if arg[0] == "rows":
            _, step, kind, name, fields = arg
            cols, rows = results[step]
            bound += len(rows)
            values[param] = [
                t2_row_value(kind, name, fields, dict(zip(cols, r, strict=True)), ns) for r in rows
            ]
        else:
            values[param] = t2_arg(arg, None, req, ns)
    value = t2_invoke(ns, fuel, proc, values)
    if result[0] == "scalar":
        return t2_result(value, result[1])
    _, fields, many = result
    items = list(value) if many else [value]  # type: ignore[call-overload]
    limit = max(bound, T2_MIN_ROW_BOUND)
    if len(items) > limit:
        raise T2Error(
            "R621",
            f"procedure '{proc}' returned {len(items)} rows; its input bounds the result "
            f"to {limit}",
        )
    names = tuple(f for f, _ in fields)
    return names, [
        tuple(encode(t2_result(getattr(item, f), s)) for f, s in fields)  # type: ignore[operator]
        for item in items
    ]


def t2_effect(ns: dict, budget: int, proc: str, params: tuple[str, ...]) -> object:
    """An effectful procedure as the outbox dispatches it: called with the row's
    arguments (in parameter order) on a fresh budget, after COMMIT."""

    def send(*args: object) -> object:
        return t2_invoke(ns, T2Fuel(budget), proc, dict(zip(params, args, strict=True)))

    send.__name__ = proc
    return send
