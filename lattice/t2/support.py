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
import collections as t2_collections
import dataclasses as t2_dataclasses
import datetime as t2_dt
import importlib as t2_importlib
import logging as t2_logging
import re as t2_re
import types as t2_types
import uuid as t2_uuid

# A procedure call deeper than this is R620 (recursion is fuel-bounded; the depth keeps
# the Python stack far from its own limit).
T2_MAX_DEPTH = 200
# A whole-value call returns at most as many rows as its table arguments hold, or this
# many when that is larger (R621 otherwise): rows a procedure returns are bounded by its
# input.
T2_MIN_ROW_BOUND = 100
# The default largest value a procedure may build (ApiProfile.max_value_size): a str's
# characters, a list's / tuple's elements; larger is R622.
T2_DEFAULT_MAX_SIZE = 10_000_000
# An operation whose result grows with an operand costs one unit of fuel per this many
# characters / elements of its result (charged before it runs), so small values cost
# nothing extra and a large one is paid for before it is built.
T2_SIZE_UNIT = 16
# The largest Int a procedure may build, in bits (about 20,000 decimal digits): big-int
# arithmetic is superlinear, and no value of a web request needs more.
T2_MAX_INT_BITS = 1 << 16


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


class T2TooLarge(T2Error):
    """A procedure would build a value larger than the cap (ApiProfile.max_value_size),
    or an Int of more than T2_MAX_INT_BITS bits."""

    def __init__(self, message: str) -> None:
        super().__init__("R622", message)


# Semantics v1.1 (spec 2026-09-28 go-target §3.1): Int is 64-bit (R623 outside), sets
# iterate in insertion order, Counter.most_common keeps ties in insertion order. Only
# the Python emitter's differential test switches it off, to compare with the legacy
# instrumented compiler.
V11 = True


class T2IntOverflow(T2Error):
    """An Int result outside the signed 64-bit range (v1.1)."""

    def __init__(self, value: int) -> None:
        super().__init__("R623", f"integer {value} is outside the 64-bit range")


_T2_INT_MIN, _T2_INT_MAX = -(1 << 63), (1 << 63) - 1


def t2_int(v: object) -> object:
    """An Int-typed result, range-checked (R623); anything else passes unchanged."""
    if (
        V11
        and type(v) is int  # noqa: E721 - a bool is not an Int result
        and not (_T2_INT_MIN <= v <= _T2_INT_MAX)
    ):
        raise T2IntOverflow(v)
    return v


def _t2_set_like(other: object) -> bool:
    return isinstance(other, T2Set | set | frozenset)


class T2Set:
    """A set whose iteration order is insertion order (v1.1). The set algebra orders
    its result like lattice/gogen/rt/sets.go: the left operand's elements first, in its
    order, then (union, symmetric difference) the right operand's new ones in its
    order. Re-adding an element keeps its position; discarding then adding moves it
    last."""

    __slots__ = ("_d",)
    __hash__ = None  # type: ignore[assignment] - mutable, like set

    def __init__(self, items: object = ()) -> None:
        self._d: dict[object, None] = dict.fromkeys(items)  # type: ignore[call-overload]

    # ---- reading ----
    def __iter__(self):  # noqa: ANN204
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, x: object) -> bool:
        return x in self._d

    def __repr__(self) -> str:
        if not self._d:
            return "set()"
        return "{" + ", ".join(repr(x) for x in self._d) + "}"

    def __eq__(self, other: object) -> bool:
        if not _t2_set_like(other):
            return NotImplemented
        return len(self) == len(other) and all(x in other for x in self._d)  # type: ignore[arg-type,operator]

    def __ne__(self, other: object) -> bool:
        eq = self.__eq__(other)
        return eq if eq is NotImplemented else not eq

    def issubset(self, other: object) -> bool:
        o = other if _t2_set_like(other) else T2Set(other)
        return len(self) <= len(o) and all(x in o for x in self._d)  # type: ignore[arg-type,operator]

    def issuperset(self, other: object) -> bool:
        return all(x in self._d for x in other)  # type: ignore[attr-defined]

    def isdisjoint(self, other: object) -> bool:
        return not any(x in self._d for x in other)  # type: ignore[attr-defined]

    def __le__(self, other: object) -> bool:
        return self.issubset(other) if _t2_set_like(other) else NotImplemented

    def __lt__(self, other: object) -> bool:
        if not _t2_set_like(other):
            return NotImplemented
        return len(self) < len(other) and self.issubset(other)  # type: ignore[arg-type]

    def __ge__(self, other: object) -> bool:
        return self.issuperset(other) if _t2_set_like(other) else NotImplemented

    def __gt__(self, other: object) -> bool:
        if not _t2_set_like(other):
            return NotImplemented
        return len(self) > len(other) and self.issuperset(other)  # type: ignore[arg-type]

    # ---- changing ----
    def add(self, x: object) -> None:
        self._d[x] = None

    def discard(self, x: object) -> None:
        self._d.pop(x, None)

    def remove(self, x: object) -> None:
        del self._d[x]  # KeyError(x), as set.remove

    def clear(self) -> None:
        self._d.clear()

    def update(self, *others: object) -> None:
        for other in others:
            for x in other:  # type: ignore[attr-defined]
                self._d[x] = None

    def copy(self) -> T2Set:
        return T2Set(self._d)

    __copy__ = copy

    # ---- algebra: the left operand's order first ----
    def union(self, *others: object) -> T2Set:
        out = self.copy()
        out.update(*others)
        return out

    def intersection(self, *others: object) -> T2Set:
        keep = [o if _t2_set_like(o) else T2Set(o) for o in others]
        return T2Set(x for x in self._d if all(x in o for o in keep))  # type: ignore[operator]

    def difference(self, *others: object) -> T2Set:
        drop = [o if _t2_set_like(o) else T2Set(o) for o in others]
        return T2Set(x for x in self._d if not any(x in o for o in drop))  # type: ignore[operator]

    def symmetric_difference(self, other: object) -> T2Set:
        o = other if isinstance(other, T2Set) else T2Set(other)
        out = T2Set(x for x in self._d if x not in o)
        out.update(x for x in o if x not in self._d)
        return out

    def __or__(self, other: object) -> T2Set:
        return self.union(other) if _t2_set_like(other) else NotImplemented

    def __and__(self, other: object) -> T2Set:
        return self.intersection(other) if _t2_set_like(other) else NotImplemented

    def __sub__(self, other: object) -> T2Set:
        return self.difference(other) if _t2_set_like(other) else NotImplemented

    def __xor__(self, other: object) -> T2Set:
        return self.symmetric_difference(other) if _t2_set_like(other) else NotImplemented

    # in place (`s |= t` changes s itself, as for set)
    def __ior__(self, other: object) -> T2Set:
        if not _t2_set_like(other):
            return NotImplemented
        self.update(other)
        return self

    def __iand__(self, other: object) -> T2Set:
        if not _t2_set_like(other):
            return NotImplemented
        for x in [x for x in self._d if x not in other]:  # type: ignore[operator]
            del self._d[x]
        return self

    def __isub__(self, other: object) -> T2Set:
        if not _t2_set_like(other):
            return NotImplemented
        for x in list(other):  # type: ignore[call-overload]
            self._d.pop(x, None)
        return self

    def __ixor__(self, other: object) -> T2Set:
        if not _t2_set_like(other):
            return NotImplemented
        o = other if isinstance(other, T2Set) else T2Set(other)
        common = [x for x in o if x in self._d]
        new = [x for x in o if x not in self._d]
        for x in common:
            del self._d[x]
        self.update(new)
        return self


class T2Counter(t2_collections.Counter):
    """collections.Counter whose most_common keeps ties in insertion order (v1.1)."""

    def most_common(self, n: int | None = None) -> list[tuple[object, int]]:
        ranked = sorted(self.items(), key=lambda kv: -kv[1])  # stable: ties keep order
        return ranked if n is None else ranked[: max(n, 0)]


_T2_SEQ = (str, list, tuple)


class T2Fuel:
    """The fuel of one request (or one outbox dispatch): `left` units, the current
    procedure call depth, and the largest value a procedure may build (`max_size`).

    Besides steps, fuel pays for size: an operation whose result grows with an operand
    (`s * n`, `s + t`, `s.ljust(n)`, `b ** n`, `b << n`, a format spec's width, ...)
    is compiled into a call of one of the methods below, which computes the result's
    size from the operands *before* the operation runs, refuses it when it is larger
    than `max_size` (R622), charges it (one unit per T2_SIZE_UNIT) and only then
    computes it."""

    __slots__ = ("budget", "depth", "left", "max_depth", "max_size")

    def __init__(
        self, budget: int, max_depth: int = T2_MAX_DEPTH, max_size: int = T2_DEFAULT_MAX_SIZE
    ) -> None:
        self.budget = budget
        self.left = budget
        self.depth = 0
        self.max_depth = max_depth
        self.max_size = max_size

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

    # ---- size -------------------------------------------------------------------------

    def grow(self, size: int) -> None:
        """A value of `size` characters / elements is about to be built: refused when
        larger than the cap (R622), else charged by size."""
        if size > self.max_size:
            raise T2TooLarge(
                f"a procedure would build a value of {size} characters or elements; the "
                f"largest allowed is {self.max_size} (ApiProfile.max_value_size)"
            )
        self.left -= size // T2_SIZE_UNIT
        if self.left < 0:
            raise self._spent()

    def bits(self, n: int) -> None:
        """An Int of about `n` bits is about to be computed."""
        if n > T2_MAX_INT_BITS:
            raise T2TooLarge(
                f"a procedure would build an Int of about {n} bits; the largest allowed "
                f"has {T2_MAX_INT_BITS}"
            )
        self.left -= n // (8 * T2_SIZE_UNIT)
        if self.left < 0:
            raise self._spent()

    def add(self, a: object, b: object) -> object:
        """`a + b` (and `a += b`): a concatenation is charged its length."""
        if isinstance(a, _T2_SEQ) and isinstance(b, _T2_SEQ):
            self.grow(len(a) + len(b))
        return a + b  # type: ignore[operator]

    def _mul_size(self, a: object, b: object) -> None:
        if isinstance(a, _T2_SEQ) and isinstance(b, int):
            self.grow(len(a) * max(b, 0))
        elif isinstance(b, _T2_SEQ) and isinstance(a, int):
            self.grow(len(b) * max(a, 0))
        elif isinstance(a, int) and isinstance(b, int):
            self.bits(a.bit_length() + b.bit_length())

    def mul(self, a: object, b: object) -> object:
        """`a * b`: a repetition is charged its length, an Int product its bits."""
        self._mul_size(a, b)
        return a * b  # type: ignore[operator]

    def _pow_size(self, a: object, b: object) -> None:
        if isinstance(a, int) and isinstance(b, int) and b > 1 and abs(a) > 1:
            self.bits(abs(a).bit_length() * b)

    def pow(self, a: object, b: object) -> object:
        """`a ** b`: an Int power is charged its bits (at most bit_length(a) * b)."""
        self._pow_size(a, b)
        return a**b  # type: ignore[operator]

    def _lshift_size(self, a: object, b: object) -> None:
        if isinstance(a, int) and isinstance(b, int) and a != 0 and b > 0:
            self.bits(a.bit_length() + b)

    def lshift(self, a: object, b: object) -> object:
        """`a << b`: charged the result's bits."""
        self._lshift_size(a, b)
        return a << b  # type: ignore[operator]

    def aug(self, op: str, a: object, b: object) -> None:
        """The size check of an augmented assignment `a op= b` (run just before it, so
        an in-place `xs += ys` keeps its meaning)."""
        if op == "add":
            if isinstance(a, _T2_SEQ) and isinstance(b, _T2_SEQ):
                self.grow(len(a) + len(b))
        elif op == "mul":
            self._mul_size(a, b)
        elif op == "pow":
            self._pow_size(a, b)
        elif op == "lshift":
            self._lshift_size(a, b)

    def width(self, n: object) -> object:
        """The width of `s.ljust(n)` / `rjust` / `center` / `zfill`: the result has at
        most max(len(s), n) characters -- `n` is charged and capped."""
        if isinstance(n, int) and n > 0:
            self.grow(n)
        return n

    def fmt(self, value: object, spec: str, conversion: int) -> str:
        """A format spec `f"{value!c:spec}"`: its numbers (width, precision) bound what
        it adds to the value's own text, charged and capped before formatting."""
        if conversion == 115:  # !s
            value = str(value)
        elif conversion == 114:  # !r
            value = repr(value)
        elif conversion == 97:  # !a
            value = ascii(value)
        self.grow(sum(int(d) for d in t2_re.findall(r"\d+", spec)))
        return format(value, spec)

    def replace(self, s: str, old: str, new: str, count: int = -1) -> str:
        """`s.replace(old, new[, count])`: charged its scan and the grown size."""
        self.grow(len(s))
        if len(new) > len(old):
            n = len(s) + 1 if old == "" else s.count(old)
            if count >= 0:
                n = min(n, count)
            self.grow(len(s) + n * (len(new) - len(old)))
        return s.replace(old, new, count)

    def join(self, sep: str, items: object) -> str:
        """`sep.join(items)`: the items are charged one each (like any consumer), the
        result its length."""
        parts = list(self.bulk(items))  # type: ignore[call-overload]
        size = sum(len(p) for p in parts) + len(sep) * max(len(parts) - 1, 0)
        self.grow(size)
        return sep.join(parts)

    def scan(self, s: object) -> object:
        """A regular expression's input: charged its length (a pattern's work grows
        with it; catastrophic patterns are refused by the checker, L925)."""
        if isinstance(s, str):
            self.grow(len(s))
        return s

    def sub(self, fn: object, pattern: str, repl: str, s: str, *rest: object) -> str:
        """`re.sub(pattern, repl, s)`: charged its input, then the most it can grow to --
        each match replaced by `repl`, whose group references expand to at most the
        longest match -- before it runs. `\\d \\w \\s \\b` are ASCII (like `fn`, below)."""
        self.scan(s)
        count = 0
        longest = 0
        for m in t2_re.finditer(pattern, s, flags=t2_re.ASCII):
            count += 1
            longest = max(longest, m.end() - m.start())
        self.grow(len(s) + count * len(repl) * max(longest, 1))
        return fn(pattern, repl, s, *rest)  # type: ignore[operator]

    def dumps(self, fn: object, value: object, *args: object, **kw: object) -> str:
        """`json.dumps(value, indent=...)`: the value's nodes are charged one each, the
        indentation's size (nodes x depth x indent) is capped before it runs, the text
        after."""
        nodes, depth, text = 0, 0, 0
        stack = [(value, 1)]
        while stack:
            v, d = stack.pop()
            nodes += 1
            depth = max(depth, d)
            if isinstance(v, dict):
                stack.extend((x, d + 1) for x in v.values())
                text += sum(len(str(k)) for k in v)
            elif isinstance(v, list | tuple):
                stack.extend((x, d + 1) for x in v)
            elif isinstance(v, str):
                text += len(v)
        self.left -= nodes
        if self.left < 0:
            raise self._spent()
        indent = kw.get("indent")
        step = len(indent) if isinstance(indent, str) else indent if isinstance(indent, int) else 0
        self.grow(text + nodes * (max(step, 0) * depth + 4))
        out = fn(value, *args, **kw)  # type: ignore[operator]
        self.grow(len(out))
        return out


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
    """The builtins by name; under v1.1 `set` is the insertion-ordered T2Set."""
    import builtins

    names = {name: getattr(builtins, name) for name in T2_BUILTIN_NAMES}
    if V11:
        names["set"] = T2Set
    return names


def _t2_re_ascii(name: str) -> object:
    """`re.fullmatch`/`match`/`search`/`sub`/`findall`/`split`, bound with `re.ASCII`:
    `\\d \\w \\s \\b` match only ASCII on every target (spec 2026-09-28 go-target §3.1),
    so Python and Go's RE2 agree."""
    fn = getattr(t2_re, name)

    def wrapped(*args: object, **kwargs: object) -> object:
        return fn(*args, flags=t2_re.ASCII, **kwargs)

    wrapped.__name__ = name
    return wrapped


def _t2_std_module(module: str, names: tuple[str, ...]) -> object:
    """An allowlisted stdlib module as a namespace holding only `names` (the `re`
    module's functions bound with `re.ASCII`, see `_t2_re_ascii`)."""
    return t2_types.SimpleNamespace(**{n: _t2_std(module, n) for n in names})


def _t2_std(module: str, name: str) -> object:
    """An allowlisted stdlib name: `re` functions bound with `re.ASCII`, and under v1.1
    `collections.Counter` is T2Counter (most_common ties in insertion order)."""
    if module == "re":
        return _t2_re_ascii(name)
    if V11 and module == "collections" and name == "Counter":
        return T2Counter
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
    """Run compiled T2 source (`lattice.t2.emit_py`) in a fresh restricted namespace:
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
        "t2_int": t2_int,
        "native": t2_types.SimpleNamespace(**(natives or {})),
    }
    exec(compile(source, "<lattice-procedures>", "exec"), namespace)  # noqa: S102
    for loader in ("t2_builtin", "t2_std_module", "t2_std", "t2_helper", "t2_record", "t2_int"):
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


def t2_effect(
    ns: dict,
    budget: int,
    proc: str,
    params: tuple[str, ...],
    max_size: int = T2_DEFAULT_MAX_SIZE,
) -> object:
    """An effectful procedure as the outbox dispatches it: called with the row's
    arguments (in parameter order) on a fresh budget (and the value size cap), after
    COMMIT."""

    def send(*args: object) -> object:
        fuel = T2Fuel(budget, max_size=max_size)
        return t2_invoke(ns, fuel, proc, dict(zip(params, args, strict=True)))

    send.__name__ = proc
    return send
