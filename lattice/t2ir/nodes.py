"""T2 IR node set: frozen dataclasses only, no Python `ast` objects.

Every expression node's last field is `type: T2Type` (from `lattice.check.t2.types`).
Task 4 lowers checked T2 procedures into this tree; `lattice.t2ir.dump.dump` renders it
to a stable text form for golden tests, and the Python/Go emitters read it directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from lattice.check.t2.types import T2Type

# --- Expressions -------------------------------------------------------------------


@dataclass(frozen=True)
class Const:
    value: object
    type: T2Type


@dataclass(frozen=True)
class Var:
    name: str
    type: T2Type


@dataclass(frozen=True)
class Attr:
    obj: Expr
    name: str
    type: T2Type


@dataclass(frozen=True)
class Index:
    obj: Expr
    index: Expr
    type: T2Type


@dataclass(frozen=True)
class SliceOf:
    obj: Expr
    lo: Expr | None
    hi: Expr | None
    step: Expr | None
    type: T2Type


@dataclass(frozen=True)
class Unary:
    op: str  # "-" "+" "not" "~"
    operand: Expr
    type: T2Type


@dataclass(frozen=True)
class Binary:
    op: str  # "+" "-" "*" "/" "//" "%" "**" "<<" ">>" "&" "|" "^"
    left: Expr
    right: Expr
    type: T2Type


@dataclass(frozen=True)
class Compare:
    ops: tuple[str, ...]  # "==" "!=" "<" "<=" ">" ">=" "in" "not in" "is" "is not"
    operands: tuple[Expr, ...]
    type: T2Type


@dataclass(frozen=True)
class BoolOp:
    op: str
    values: tuple[Expr, ...]
    type: T2Type


@dataclass(frozen=True)
class IfExp:
    test: Expr
    body: Expr
    orelse: Expr
    type: T2Type


@dataclass(frozen=True)
class Call:
    target: str  # the checker's resolved string; methods pass the receiver as args[0]
    args: tuple[Expr, ...]
    kwargs: tuple[tuple[str, Expr], ...]
    type: T2Type


@dataclass(frozen=True)
class ListLit:
    elts: tuple[Expr, ...]
    type: T2Type


@dataclass(frozen=True)
class SetLit:
    elts: tuple[Expr, ...]
    type: T2Type


@dataclass(frozen=True)
class TupleLit:
    elts: tuple[Expr, ...]
    type: T2Type


@dataclass(frozen=True)
class DictLit:
    """`{k: v, **d}`: a `None` key marks a `**value` entry (as in Python's `ast.Dict`)."""

    keys: tuple[Expr | None, ...]
    values: tuple[Expr, ...]
    type: T2Type


@dataclass(frozen=True)
class Gen:
    """A `for target in iter if ifs...` clause inside a `Comp`; not itself typed."""

    target: AssignTarget
    iter: Expr
    ifs: tuple[Expr, ...]


@dataclass(frozen=True)
class Comp:
    kind: str  # "list" "set" "dict" "gen"
    elt: Expr
    value: Expr | None  # the value half of a dict comprehension; None otherwise
    gens: tuple[Gen, ...]
    type: T2Type


@dataclass(frozen=True)
class FPart:
    """A `{value!conversion:spec}` replacement field inside an `FStr`; not itself typed.
    `spec` is the literal spec text ("" when there is none), or an `FStr` when the spec
    has replacement fields of its own (`f"{s:>{n}}"`). `conversion` is "", "s", "r" or
    "a". Only a non-empty spec is charged (`lattice.t2.emit_py`: `<fuel>.fmt`)."""

    value: Expr
    spec: str | FStr
    conversion: str


@dataclass(frozen=True)
class FStr:
    parts: tuple[str | FPart, ...]
    type: T2Type


@dataclass(frozen=True)
class Lambda:
    params: tuple[str, ...]
    body: Expr
    type: T2Type


@dataclass(frozen=True)
class Walrus:
    name: str
    value: Expr
    type: T2Type


# --- Fuel-bearing expressions --------------------------------------------------------


@dataclass(frozen=True)
class Each:
    """Charge fuel one element at a time while iterating `iter`."""

    iter: Expr
    type: T2Type


@dataclass(frozen=True)
class Bulk:
    """Charge fuel for the length of `value` up front."""

    value: Expr
    type: T2Type


@dataclass(frozen=True)
class Charged:
    """Charge one fuel step, then perform `call`."""

    call: Call
    type: T2Type


@dataclass(frozen=True)
class Grow:
    """A size-checked `+ * ** <<`."""

    op: str
    left: Expr
    right: Expr
    type: T2Type


@dataclass(frozen=True)
class Sized:
    kind: str  # "replace" "join" "sub" "dumps" "pad"
    args: tuple[Expr, ...]
    type: T2Type


@dataclass(frozen=True)
class Scan:
    """A regex input, charged by length."""

    value: Expr
    type: T2Type


# --- Assignment targets ---------------------------------------------------------------


@dataclass(frozen=True)
class NameT:
    name: str


@dataclass(frozen=True)
class TupleTarget:
    elts: tuple[AssignTarget, ...]


@dataclass(frozen=True)
class IndexT:
    obj: Expr
    index: Expr


@dataclass(frozen=True)
class AttrT:
    obj: Expr
    name: str


@dataclass(frozen=True)
class SliceT:
    """`xs[lo:hi:step]` as an augmented-assignment target (`xs[a:b] += ys`)."""

    obj: Expr
    lo: Expr | None
    hi: Expr | None
    step: Expr | None


# --- Statements ------------------------------------------------------------------------


@dataclass(frozen=True)
class Assign:
    target: AssignTarget
    value: Expr


@dataclass(frozen=True)
class AugAssign:
    target: AssignTarget
    op: str
    value: Expr
    grow: bool


@dataclass(frozen=True)
class If:
    test: Expr
    body: tuple[Stmt, ...]
    orelse: tuple[Stmt, ...]


@dataclass(frozen=True)
class For:
    target: AssignTarget
    iter: Expr
    body: tuple[Stmt, ...]
    orelse: tuple[Stmt, ...]


@dataclass(frozen=True)
class While:
    test: Expr
    body: tuple[Stmt, ...]
    orelse: tuple[Stmt, ...]


@dataclass(frozen=True)
class Break:
    pass


@dataclass(frozen=True)
class Continue:
    pass


@dataclass(frozen=True)
class Return:
    value: Expr | None


@dataclass(frozen=True)
class ExprStmt:
    value: Expr


@dataclass(frozen=True)
class Handler:
    kinds: tuple[str, ...]
    name: str | None
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class Try:
    body: tuple[Stmt, ...]
    handlers: tuple[Handler, ...]
    orelse: tuple[Stmt, ...]
    final: tuple[Stmt, ...]


@dataclass(frozen=True)
class Raise:
    kind: str
    message: Expr | None


@dataclass(frozen=True)
class Pass:
    pass


@dataclass(frozen=True)
class Tick:
    """The first statement of every loop body: charges one fuel step."""

    pass


# --- Module ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Procedure:
    name: str
    params: tuple[tuple[str, T2Type], ...]
    returns: T2Type
    locals: Mapping[str, T2Type]
    body: tuple[Stmt, ...]
    recursive: bool


@dataclass(frozen=True)
class Record:
    name: str
    fields: tuple[tuple[str, T2Type], ...]
    defaults: Mapping[str, object]


@dataclass(frozen=True)
class T2Module:
    procedures: tuple[Procedure, ...]
    records: tuple[Record, ...]
    constants: tuple[tuple[str, T2Type, Expr], ...]
    imports: Mapping[str, str]
    natives: tuple[str, ...]


# --- Type aliases --------------------------------------------------------------------------

Expr = (
    Const | Var | Attr | Index | SliceOf | Unary | Binary | Compare | BoolOp | IfExp | Call
    | ListLit | SetLit | TupleLit | DictLit | Comp | FStr | Lambda | Walrus
    | Each | Bulk | Charged | Grow | Sized | Scan
)

AssignTarget = NameT | TupleTarget | IndexT | AttrT | SliceT

Stmt = (
    Assign | AugAssign | If | For | While | Break | Continue | Return | ExprStmt | Try
    | Raise | Pass | Tick
)
