"""Rejection hints for loops (spec 2026-09-27 lattice-loops §5; T2 fallback and
T2-body warnings: spec 2026-09-27 lattice-t2-design §5).

A `for`/`while` statement stays L127 and a comprehension L121; only the hint changes:
it names the Lattice construct for the loop's shape, read off the loop's identifiers
and statements (a heuristic -- the code, not the hint, is the contract):

* repeat until nothing changes (changed/stable/tolerance, or a pass whose
  result is compared with the previous one)                                -> iterate
* a parent/child walk (`node = node.parent`, parent/children/visited, ...) -> traverse
* a state machine (branches on carried state updating 3+ pieces of it)     -> a @procedure
  (T2) in an API program, a native function in a tabular one
* an action per row (a call statement, no assignment)                      -> for_each
* a value carried across rows (running/prev/rank/balance, `+=`, ...)       -> window
* anything else                                                            -> a @procedure
  (T2) in an API program, a native function in a tabular one

`t1_shape_warning` is the reverse direction: inside a checked `@procedure` body (T2
permits the loop), a shape that matches `window`/`traverse`/`for_each` gets a warning
naming the stronger-guaranteed T1 construct, not a rejection. A state machine or
anything else has no T0/T1 equivalent (that is why it stays a procedure), so it is
never flagged.
"""

from __future__ import annotations

import ast

# Identifier words (split on "_", lower case) that mark a loop shape.
_TREE_WORDS = frozenset(
    {
        "parent", "parents", "child", "children", "manager", "managers", "ancestor",
        "ancestors", "descendant", "descendants", "subtree", "tree", "frontier", "visited",
        "breadcrumb", "breadcrumbs", "lineage", "popleft",
    }
)  # fmt: skip
_FIXPOINT_WORDS = frozenset(
    {
        "changed", "stable", "unstable", "converged", "converge", "convergence", "fixpoint",
        "settled", "tolerance", "tol", "eps", "epsilon",
    }
)  # fmt: skip
_WINDOW_WORDS = frozenset(
    {
        "running", "cumulative", "cumsum", "cum", "balance", "balances", "prev", "previous",
        "rank", "ranking", "moving", "rolling", "lag", "lead", "streak", "avg", "average",
        "mean", "total", "totals", "delta",
    }
)  # fmt: skip
# Calls that mutate a local container: bookkeeping, not an action per row.
_CONTAINER_METHODS = frozenset(
    {
        "append", "extend", "add", "update", "insert", "pop", "popleft", "remove", "discard",
        "clear", "setdefault", "appendleft", "sort", "reverse",
    }
)  # fmt: skip

# words in a function name that mark a call made for its effect (a comprehension of such
# calls is a per-row action)
_EFFECT_WORDS = frozenset(
    {
        "send", "notify", "post", "publish", "email", "mail", "enqueue", "emit", "alert",
        "push", "dispatch", "broadcast", "sms", "webhook", "deliver", "trigger", "fire",
        "log", "charge", "refund",
    }
)  # fmt: skip

_WINDOW = (
    "a value carried across rows (running total, previous row, rank): use "
    'window(t, by=[...], order=[...], name=win.cum_sum(col("x"))), or win.lag / '
    "win.rank / win.row_number / win.mean(x, rows=N); a plain total is "
    'group(t, by=[...], name=agg.sum(col("x")))'
)
_TRAVERSE = (
    'a parent/child walk: use traverse(edges, start, parent="parent_id", child="id", '
    'max_depth=N) (direction="up" for ancestors); cycle-safe, adds a depth column'
)
_ITERATE = (
    "a repeat-until-stable loop: write one round as a @step Table[S] -> Table[S] and call "
    'iterate(step, t, until="fixpoint", max=N) (or until=<Bool expr>)'
)
_ITERATE_API = (
    "a repeat-until-stable loop: iterate(...) exists only in tabular programs; in an API "
    "program use traverse(...) for a tree walk, otherwise write a @procedure (T2)"
)
_FOR_EACH = (
    'an action per row: call an effect native with for_each(t, native.<name>, col("x"), '
    "...) on a bounded table (one/insert/update, or page/limit <= 100); it runs after commit"
)
_FOR_EACH_TABULAR = (
    "an action per row: a tabular program's only effect is write(...); per-row side effects "
    "use for_each(t, native.<name>, ...) in API programs"
)
_NATIVE = (
    "other loop state (several accumulators, a state machine, early exit): keep that "
    "logic in a native function; per-row conditions are where/derive/when"
)
_PROCEDURE = (
    "other loop state (several accumulators, a state machine, early exit): write it as "
    "a @procedure (T2) -- ordinary Python in a checked, fuel-bounded subset -- and call "
    "it from derive/where (scalar) or call(proc, rows=t, ...) (whole-value); per-row "
    "conditions are where/derive/when"
)


def _general_fallback(api: bool) -> str:
    """The hint for a loop shape no T1 construct fits: a @procedure (T2) in an API
    program (T2 exists only there), otherwise a native function."""
    return _PROCEDURE if api else _NATIVE


def _words(node: ast.AST) -> set[str]:
    """Lower-case identifier words of a subtree: names, attributes, parameters and
    string keys, each whole and split on '_'."""
    names: list[str] = []
    for sub in ast.walk(node):
        match sub:
            case ast.Name(id=name) | ast.Attribute(attr=name) | ast.arg(arg=name):
                names.append(name)
            case ast.Constant(value=str() as text) if text.isidentifier():
                names.append(text)
    words: set[str] = set()
    for name in names:
        lower = name.lower()
        words.add(lower)
        words.update(part for part in lower.split("_") if part)
    return words


def _call_name(call: ast.Call) -> str | None:
    match call.func:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name
    return None


def _is_action(call: ast.AST) -> bool:
    """A call made for its effect: not a local container mutation."""
    return isinstance(call, ast.Call) and _call_name(call) not in _CONTAINER_METHODS


def _is_effect_call(node: ast.AST) -> bool:
    """`send_email(u)`: a bare-name call whose name reads as a side effect. A method call
    (`s.strip()`) or another function (`round(v, 2)`) is taken for a map."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return False
    parts = {part for part in node.func.id.lower().split("_") if part}
    return bool(parts & _EFFECT_WORDS)


def _walks_links(loop: ast.While) -> bool:
    """`node = node.parent` / `node = parents[node]` / `node = by_id.get(node)` inside a
    while loop: following a link from the current node."""
    for sub in ast.walk(loop):
        if not (isinstance(sub, ast.Assign) and len(sub.targets) == 1):
            continue
        target = sub.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = sub.value
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            if value.value.id == target.id:
                return True
        if isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Name):
            if value.slice.id == target.id:
                return True
        if isinstance(value, ast.Call) and _call_name(value) == "get" and value.args:
            first = value.args[0]
            if isinstance(first, ast.Name) and first.id == target.id:
                return True
    return False


def _pure_action_loop(body: list[ast.stmt]) -> bool:
    """Call statements (at least one an action, possibly inside try/if), no assignment
    of any kind."""
    has_action = False
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.NamedExpr):
                return False
            if isinstance(sub, ast.Expr) and _is_action(sub.value):
                has_action = True
    return has_action


def _state_keys(node: ast.AST) -> set[str]:
    """Names, attributes and string subscript keys read or written in `node`."""
    keys: set[str] = set()
    for sub in ast.walk(node):
        match sub:
            case ast.Name(id=name) | ast.Attribute(attr=name):
                keys.add(name)
            case ast.Subscript(slice=ast.Constant(value=str() as key)):
                keys.add(key)
    return keys


def _assigned_keys(stmts: list[ast.stmt]) -> set[str]:
    keys: set[str] = set()
    for stmt in stmts:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Assign):
                for target in sub.targets:
                    keys |= _state_keys(target)
            elif isinstance(sub, ast.AugAssign):
                keys |= _state_keys(sub.target)
    return keys


def _state_machine(loop: ast.For | ast.AsyncFor | ast.While) -> bool:
    """Branches that update three or more pieces of carried state, chosen by a test that
    reads that state: a state machine, not a single running value."""
    tests: set[str] = set()
    updated: set[str] = set()
    for sub in ast.walk(loop):
        if isinstance(sub, ast.If):
            tests |= _state_keys(sub.test)
            updated |= _assigned_keys(sub.body + sub.orelse)
    return len(updated) >= 3 and bool(tests & updated)


def _until_unchanged(loop: ast.While) -> bool:
    """A pass recomputes a value, compares it with the previous one and stops when they
    agree (`if len(kept) == len(rows): break` ... `rows = kept`)."""
    rebinds = {
        (stmt.targets[0].id, stmt.value.id)
        for stmt in loop.body
        if isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and isinstance(stmt.value, ast.Name)
    }
    for sub in ast.walk(loop):
        if isinstance(sub, ast.If) and isinstance(sub.test, ast.Compare):
            names = {n.id for n in ast.walk(sub.test) if isinstance(n, ast.Name)}
            if any(old in names and new in names for old, new in rebinds):
                return True
    return False


def loop_hint(loop: ast.For | ast.AsyncFor | ast.While, api: bool) -> str:
    """The hint for a rejected loop statement (L127)."""
    words = _words(loop)
    is_while = isinstance(loop, ast.While)
    if words & _FIXPOINT_WORDS or (is_while and _until_unchanged(loop)):
        return _ITERATE_API if api else _ITERATE
    if words & _TREE_WORDS or (is_while and _walks_links(loop)):
        return _TRAVERSE
    if _state_machine(loop):
        return _general_fallback(api)
    if _pure_action_loop(loop.body):
        return _FOR_EACH if api else _FOR_EACH_TABULAR
    has_aug = any(isinstance(sub, ast.AugAssign) for sub in ast.walk(loop))
    if words & _WINDOW_WORDS or has_aug:
        return _WINDOW
    return _general_fallback(api)


# ---- T2 body warnings (spec 2026-09-27 lattice-t2-design §5) -----------------------------

_WINDOW_T1 = (
    "this loop's shape matches window (T1): a value carried across rows (running "
    "total, previous row, rank) -- window(t, by=[...], order=[...], "
    'name=win.cum_sum(col("x"))), or win.lag/win.rank/win.mean, gives the same result '
    "with T1's guarantees"
)
_TRAVERSE_T1 = (
    "this loop's shape matches traverse (T1): a parent/child walk -- "
    'traverse(edges, start, parent="parent_id", child="id", max_depth=N) -- is '
    "cycle-safe and gives the same result with T1's guarantees"
)
_FOR_EACH_T1 = (
    "this loop's shape matches for_each (T1): an action per row -- "
    "for_each(t, native.<name>, ...) on a bounded table -- runs it after commit with "
    "T1's guarantees, without a @procedure"
)


def t1_shape_warning(loop: ast.For | ast.While) -> str | None:
    """Inside a checked `@procedure` body, the T1 hint for a loop shaped like
    `window`/`traverse`/`for_each`, or None when no T1 construct fits (a state
    machine or anything else -- the reason the loop is a procedure at all, per
    l12_login_state_machine). A warning, never an error: T2 does not reject the loop."""
    words = _words(loop)
    is_while = isinstance(loop, ast.While)
    if words & _TREE_WORDS or (is_while and _walks_links(loop)):
        return _TRAVERSE_T1
    if _state_machine(loop):
        return None
    if _pure_action_loop(loop.body):
        return _FOR_EACH_T1
    has_aug = any(isinstance(sub, ast.AugAssign) for sub in ast.walk(loop))
    if words & _WINDOW_WORDS or has_aug:
        return _WINDOW_T1
    return None


def comprehension_hint(
    node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    api: bool,
    default: str,
    *,
    discarded: bool = False,
) -> str:
    """The hint for a rejected comprehension (L121): a construct only when the shape is
    clear, otherwise `default` (a comprehension is usually a map/filter: derive/where).
    It is a per-row action (for_each) when its value is `discarded` (a bare expression
    statement) and its element is a call, or when the element calls an effect-named
    function (`send_email(u)`)."""
    words = _words(node)
    if words & _TREE_WORDS:
        return _TRAVERSE
    element = node.key if isinstance(node, ast.DictComp) else node.elt
    action = (discarded and _is_action(element)) or _is_effect_call(element)
    if action and not isinstance(node, ast.DictComp):
        return _FOR_EACH if api else _FOR_EACH_TABULAR
    if words & _WINDOW_WORDS:
        return _WINDOW
    return default
