"""A stable text form of T2 IR for golden tests: one node per line, two-space indent,
types rendered with `T2Type.render()`. General over the whole node set in `nodes.py` --
not special-cased to any particular procedure.
"""

from __future__ import annotations

from dataclasses import Field, fields, is_dataclass

from lattice.t2ir import nodes as n

# Field names whose (single- or tuple-valued) node children get a "name: " label instead
# of being shown as plain, unlabeled children -- disambiguates e.g. an `If`'s `test` from
# its `body`, or a `Try`'s four child blocks from one another. Slice bounds are labeled
# too (an absent bound is not shown, so `xs[::2]` must not read like `xs[2:]`), and an
# `FPart`'s `spec` (an `FStr` when it has replacement fields of its own).
_LABELED = frozenset(
    {"test", "target", "body", "orelse", "final", "handlers", "lo", "hi", "step", "spec"}
)


def _is_scalar_value(field: Field) -> bool:
    """True only for `Const.value`: a literal Python object, not a nested IR node.
    Every other node's `value` field is `Expr` (or `Expr | None`) and is a child."""
    return field.name == "value" and field.type == "object"


def _op_text(node: object) -> str | None:
    """The operator-like token for a node's head, if it has one: `op`/`kind` (a single
    string) or `ops` (a tuple of strings, joined) -- e.g. `Binary.op`, `Compare.ops`."""
    for f in fields(node):  # type: ignore[arg-type]
        value = getattr(node, f.name)
        if f.name in ("op", "kind") and isinstance(value, str):
            return value
        if f.name == "ops" and isinstance(value, tuple) and value and all(
            isinstance(v, str) for v in value
        ):
            return " ".join(value)
    return None


def _title_text(node: object) -> str | None:
    """The identifying name for a node's head, if it has one: `name` or `target` (only
    when it is a plain string, e.g. `Call.target`; an `AssignTarget` node is a child)."""
    for f in fields(node):  # type: ignore[arg-type]
        value = getattr(node, f.name)
        if f.name in ("name", "target") and isinstance(value, str):
            return value
    return None


def _head(node: object) -> str:
    name = type(node).__name__
    for f in fields(node):  # type: ignore[arg-type]
        if _is_scalar_value(f):
            return f"{name} {getattr(node, f.name)!r}"
    op = _op_text(node)
    if op is not None:
        return f"{name}({op})"
    title = _title_text(node)
    if title is not None:
        return f"{name} {title}"
    return name


def _children(node: object) -> list[tuple[str, object]]:
    """Fields still worth recursing into: everything except `type` and whatever `_head`
    already rendered (a scalar `value`, an operator field, or a string name/target)."""
    out: list[tuple[str, object]] = []
    for f in fields(node):  # type: ignore[arg-type]
        if f.name == "type" or _is_scalar_value(f):
            continue
        value = getattr(node, f.name)
        if f.name in ("op", "kind") and isinstance(value, str):
            continue
        if f.name == "ops" and isinstance(value, tuple) and value and all(
            isinstance(v, str) for v in value
        ):
            continue
        if f.name in ("name", "target") and isinstance(value, str):
            continue
        out.append((f.name, value))
    return out


def _is_node_tuple(value: object) -> bool:
    """A tuple of nodes -- possibly with `None` holes, e.g. `DictLit.keys` of `{**d}`."""
    return (
        isinstance(value, tuple)
        and any(is_dataclass(e) for e in value)
        and all(is_dataclass(e) or e is None for e in value)
    )


def _elt_lines(elt: object, depth: int) -> list[str]:
    return [f"{'  ' * depth}None"] if elt is None else _lines(elt, depth)


def _other_tuple_lines(field_name: str, value: tuple, pad: str, depth: int) -> list[str]:
    """A tuple field that mixes nodes with plain values, or holds no nodes at all --
    e.g. `Call.kwargs` (name, Expr) pairs, `FStr.parts` (str | FPart), or a plain
    tuple[str, ...] such as `Lambda.params` or `Handler.kinds`."""
    if field_name == "kwargs":
        out = [f"{pad}  kwargs:"]
        for key, expr in value:
            out += _lines(expr, depth + 2, f"{key}: ")
        return out
    if field_name == "parts":
        out = [f"{pad}  parts:"]
        for part in value:
            out += [f"{pad}    {part!r}"] if isinstance(part, str) else _lines(part, depth + 2)
        return out
    if all(isinstance(v, str) for v in value):
        return [f"{pad}  {field_name}: {', '.join(value)}"]
    return [f"{pad}  {field_name}: {value!r}"]


def _lines(node: object, depth: int, label: str = "") -> list[str]:
    pad = "  " * depth
    typ = getattr(node, "type", None)
    suffix = f" : {typ.render()}" if typ is not None else ""
    out = [f"{pad}{label}{_head(node)}{suffix}"]
    for field_name, value in _children(node):
        if value is None or value == ():
            continue
        if is_dataclass(value):
            out += _lines(value, depth + 1, f"{field_name}: " if field_name in _LABELED else "")
        elif field_name == "parts" and isinstance(value, tuple):
            out += _other_tuple_lines(field_name, value, pad, depth)  # always "parts:"
        elif _is_node_tuple(value):
            if field_name in _LABELED:
                out.append(f"{pad}  {field_name}:")
                for elt in value:
                    out += _elt_lines(elt, depth + 2)
            else:
                for elt in value:
                    out += _elt_lines(elt, depth + 1)
        elif isinstance(value, tuple):
            out += _other_tuple_lines(field_name, value, pad, depth)
        else:
            out.append(f"{pad}  {field_name}: {value!r}")
    return out


def dump(module: n.T2Module) -> str:
    """Every field of `T2Module`/`Procedure`/`Record` that distinguishes two otherwise
    identical trees is rendered -- including `Procedure.recursive`, `Procedure.locals`
    and `Record.defaults` -- so two modules that differ only in one of those fields
    always dump differently. `imports`, `locals` and `defaults` are `Mapping`s with no
    guaranteed iteration order, so each is sorted by key for a stable dump."""
    out: list[str] = []
    for native in module.natives:
        out.append(f"native {native}")
    for alias, target in sorted(module.imports.items()):
        out.append(f"import {alias} = {target}")
    for r in module.records:
        field_list = ", ".join(f"{k}: {t.render()}" for k, t in r.fields)
        out.append(f"record {r.name}({field_list})")
        if r.defaults:
            defaults = ", ".join(f"{k}={v!r}" for k, v in sorted(r.defaults.items()))
            out.append(f"  defaults: {defaults}")
    for cname, ctype, cexpr in module.constants:
        out.append(f"const {cname} : {ctype.render()}")
        out += _lines(cexpr, 1)
    for p in module.procedures:
        params = ", ".join(f"{k}: {t.render()}" for k, t in p.params)
        out.append(f"procedure {p.name}({params}) -> {p.returns.render()}")
        out.append(f"  recursive: {p.recursive}")
        if p.locals:
            locals_str = ", ".join(f"{k}: {t.render()}" for k, t in sorted(p.locals.items()))
            out.append(f"  locals: {locals_str}")
        for s in p.body:
            out += _lines(s, 1)
    return "\n".join(out) + "\n"
