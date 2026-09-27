"""P7 — backend capabilities (API profile only): reject programs the profile's backend
cannot run with the language's guarantees (spec §3).

* L870 — an endpoint with more than one write needs `transactions` (its writes must
  commit or roll back together).
* L871 — `page(..., total=True)` needs `window_functions` (the total is computed in
  the same statement as the page).
* L872 — an endpoint returning the rows of an insert/update needs `returning` (no
  dialect offers a re-read strategy yet). A delete's result is its input rows, which
  were read before the delete, so it needs neither.
* L873 — a `native.<name>(...)` call needs `native_functions` (natives run inside the
  query).

The backend is `ApiProfile.backend` ("sqlite" when absent); every SQLite capability
holds, so none of these fire for it. An unknown backend name is left to profile
validation (`lattice.api.backend.dialect_for` raises for it).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from lattice.api.backend import Capabilities, dialect_for
from lattice.check.astutil import endpoints_of, steps_of
from lattice.check.context import CheckContext

_WRITES = frozenset({"insert", "update", "delete"})
_RETURNING_WRITES = frozenset({"insert", "update"})


def _called(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _is_native(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "native"
    )


class _Backend:
    def __init__(self, module: ast.Module, ctx: CheckContext, caps: Capabilities, name: str):
        self.module = module
        self.ctx = ctx
        self.caps = caps
        self.name = name
        self.steps = steps_of(module)

    def _walk(self, fn: ast.FunctionDef) -> Iterator[ast.AST]:
        """Every node of `fn`'s body and of the steps it calls (transitively), each
        step once."""
        seen: set[str] = set()
        todo: list[ast.FunctionDef] = [fn]
        while todo:
            current = todo.pop()
            for stmt in current.body:
                for node in ast.walk(stmt):
                    yield node
                    callee = _called(node)
                    if callee in self.steps and callee not in seen:
                        seen.add(callee)
                        todo.append(self.steps[callee])

    def _writes_in(self, node: ast.AST, kinds: frozenset[str]) -> list[ast.Call]:
        """The write calls in `node`, including those inside the steps it calls."""
        found: list[ast.Call] = []
        seen: set[str] = set()
        todo: list[ast.AST] = [node]
        while todo:
            for sub in ast.walk(todo.pop()):
                callee = _called(sub)
                if callee in kinds:
                    assert isinstance(sub, ast.Call)
                    found.append(sub)
                elif callee in self.steps and callee not in seen:
                    seen.add(callee)
                    todo.extend(self.steps[callee].body)
        return found

    def run(self) -> None:
        for fn in endpoints_of(self.module):
            self._endpoint(fn)

    def _endpoint(self, fn: ast.FunctionDef) -> None:
        nodes = list(self._walk(fn))
        if not self.caps.transactions:
            writes = [n for n in nodes if _called(n) in _WRITES]
            if len(writes) > 1:
                self.ctx.error(
                    "L870",
                    writes[1],
                    f"endpoint '{fn.name}' makes {len(writes)} writes, but the "
                    f"'{self.name}' backend has no transactions to make them atomic",
                    "make one write per endpoint, or use a backend with transactions",
                )
        if not self.caps.window_functions:
            for n in nodes:
                if _called(n) == "page" and _total(n):
                    self.ctx.error(
                        "L871",
                        n,
                        f"page(total=True) needs window functions, which the "
                        f"'{self.name}' backend lacks (the total must come from the "
                        f"same statement as the page)",
                        "drop total=True, or use a backend with window functions",
                    )
        if not self.caps.returning:
            for write in self._returned_writes(fn):
                self.ctx.error(
                    "L872",
                    write,
                    f"endpoint '{fn.name}' returns the rows of {_called(write)}(), but the "
                    f"'{self.name}' backend has no RETURNING and no re-read strategy",
                    "return rows read before the write, or use a backend with RETURNING",
                )
        if not self.caps.native_functions:
            for n in nodes:
                if _is_native(n):
                    assert isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    self.ctx.error(
                        "L873",
                        n,
                        f"native.{n.func.attr}() runs inside the query, but the "
                        f"'{self.name}' backend cannot call user functions in queries",
                        "use a backend with native functions",
                    )

    def _returned_writes(self, fn: ast.FunctionDef) -> list[ast.Call]:
        """The insert/update calls whose rows flow into `fn`'s return value (through
        the endpoint's assignments and the steps it calls)."""
        assigns: dict[str, ast.expr] = {}
        returned: list[ast.expr] = []
        for stmt in fn.body:
            match stmt:
                case ast.Assign(targets=[ast.Name(id=name)], value=value):
                    assigns[name] = value
                case ast.Return(value=value) if value is not None:
                    returned.append(value)
        found: list[ast.Call] = []
        seen: set[str] = set()
        todo = list(returned)
        while todo:
            expr = todo.pop()
            found += self._writes_in(expr, _RETURNING_WRITES)
            for sub in ast.walk(expr):
                if isinstance(sub, ast.Name) and sub.id in assigns and sub.id not in seen:
                    seen.add(sub.id)
                    todo.append(assigns[sub.id])
        return sorted(found, key=lambda n: (n.lineno, n.col_offset))


def _total(call: ast.AST) -> bool:
    assert isinstance(call, ast.Call)
    return any(
        kw.arg == "total" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
        for kw in call.keywords
    )


def run(module: ast.Module, ctx: CheckContext) -> None:
    api = ctx.profile.api
    if api is None:
        return
    name = getattr(api, "backend", "sqlite")
    try:
        dialect = dialect_for(name)
    except ValueError:
        return  # profile validation reports an unknown backend
    _Backend(module, ctx, dialect.capabilities, name).run()
