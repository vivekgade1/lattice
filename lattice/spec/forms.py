"""Typed rule conditions (Spike H): a small closed form for four rule shapes, from which
Lattice generates a scenario and an invariant, so the two cannot disagree.

Pre-registration: docs/results/2026-10-17-typed-conditions-prereg.md (with Amendment 1).

A **form** is JSON over a closed node set (D1): the kinds ``bound`` (a field's value),
``row_limit`` (a count of rows), ``ownership`` and ``transition`` (a state machine's edge).
Nothing else type-checks (F001/F017). Forms live beside the requirements, as Spike G's
rationale does: ``<stem>.forms.json`` (approved, authoritative) and
``<stem>.forms.proposed.json`` (drafts). Only an approved form whose state is ``current``
or ``moved`` generates anything; a proposal never does.

The functions here are pure unless they say otherwise: :func:`check_forms` (the type
checker, codes F001-F019), :func:`generate` (D3: scenarios and invariants, no I/O, no clock,
no randomness, sorted output), :func:`effective_spec` (the spec plus the generated elements,
in memory) and :func:`coverage`. The contradiction procedures (D5) are in
``lattice.spec.forms_tier2``. The CLI is ``python -m lattice.spec forms ...``.

Interpretations the pre-registration leaves open (stated, never silent):

* A diagnostic's ``span`` is ``Span(1, 1)`` and the form's JSON pointer leads its message
  (``Diagnostic.span`` cannot hold a pointer).
* ``Outcome.db == "unchanged"`` and ``{"equals"|"count": ..}`` are asserted on the *locus*
  the form implies: the table of the field (or ``stores``) and the columns the witness
  request carries (a path placeholder's name, a JSON or query key naming a column). The
  generator counts only the form's own ``given`` rows for the "unchanged" baseline.
* For a column field with an endpoint, the value is placed where the witness request
  carries a key named like the column (a JSON key, a query key, a path placeholder).
* Round 2 (D1'): ``node`` tags are optional; ``accepted`` is derived from the spec's own steps
  when absent; ownership ``row`` is a key or a request path; transition ``row`` is ``{key: Lit}``;
  ``given`` is the prior state and counted rows are synthesised (rules B, C; F021); a column
  ``bound`` without ``endpoint`` generates an invariant only (class K6, by design).
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.diagnostics import Diagnostic, Severity, Span
from lattice.spec import rationale as _rat
from lattice.spec.model import (
    Invariant,
    Scenario,
    Spec,
    Step,
    resolve_table,
    scope_parts,
    tables_of,
    valid_scope,
)
from lattice.spec.rules import Trace
from lattice.typesys import NUMERIC, ORDERED, Scalar, Schema

FORMS_SCHEMA = "lattice.forms/1"
KINDS = ("bound", "row_limit", "ownership", "transition")
STATUSES = ("approved", "proposed", "rejected")
STATES = _rat.STATES
APPROVED_STATES = _rat.APPROVED_STATES
RESERVED_PREFIX = "form:"
CMP_OPS = ("ge", "gt", "le", "lt", "eq", "ne")
LEN_OPS = ("min_len", "max_len")
IN_MAX = 32
WHERE_MAX = 4
NOT_EXPRESSIBLE = (
    "time",
    "money",
    "rate",
    "external",
    "cross_request",
    "background",
    "aggregate",
    "cross_table",
    "response_shape",
    "role",
    "effect_value",
    "disjunction",
    "other",
)
# node names that are not expressible when found as a value's key (F017, D4')
NOT_EXPR_NODES = frozenset(
    "now today date sum avg min max count or any all param column ref add sub mul div".split()
)
# keys and node tags that name a construct outside the closed set (F017, never F001)
NOT_EXPR_KEYS = frozenset(
    {
        "now",
        "sum",
        "avg",
        "min",
        "max_of",
        "or",
        "any_of",
        "and",
        "not",
        "exists",
        "join",
        "within",
        "after",
        "before",
        "deadline",
        "duration",
        "per",
        "rate",
        "call",
        "func",
        "expr",
        "sequence",
        "then",
        "email",
        "push",
        "payment",
        "var",
        "ref",
        "loop",
        "date",
        "time",
    }
)

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_PLACEHOLDER = re.compile(r"\{[^}/]*\}")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class FormsError(Exception):
    """A refusal or an invalid file: ``code`` and ``detail``; ``str()`` is ``code: detail``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# ---- canonical text and hashes --------------------------------------------------------------


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def form_sha(kind: str, form: Any) -> str:
    """The sha256 (hex) of the canonical JSON of ``{kind, form}`` (D2)."""
    return hashlib.sha256(canon({"kind": kind, "form": form}).encode("utf-8")).hexdigest()


# ---- the form records and their files ---------------------------------------------------------


@dataclass(frozen=True)
class Form:
    id: str
    rule: str
    anchor: str
    kind: str
    form: Mapping[str, Any] = field(compare=False)
    status: str
    author: str
    approved_by: str | None = None
    approved_sha: str | None = None

    @property
    def sha_ok(self) -> bool:
        """Whether ``approved_sha`` is the sha of the form's current ``{kind, form}``."""
        return self.approved_sha is not None and self.approved_sha == form_sha(self.kind, self.form)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule": self.rule,
            "anchor": self.anchor,
            "kind": self.kind,
            "form": self.form,
            "status": self.status,
            "author": self.author,
            "approved_by": self.approved_by,
            "approved_sha": self.approved_sha,
        }


def _names_node(v: Any) -> bool:
    """Whether ``v`` is an object keyed by a construct outside the closed set (D4')."""
    return isinstance(v, dict) and any(k in NOT_EXPR_KEYS | NOT_EXPR_NODES for k in v)


def norm_cond(c: Any) -> Any:
    """``c`` with its ``node`` tag inferred from its keys (D1' i); as written when it has none."""
    if not isinstance(c, dict) or "node" in c:
        return c
    op = c.get("op")
    tag = "Cmp" if op in CMP_OPS else "Len" if op in LEN_OPS else "In" if "values" in c else None
    return {"node": tag, **c} if tag else c


def normalised(f: Form) -> Form:
    """``f`` with its condition tagged. ``approved_sha`` stays over the form as written."""
    c = f.form.get("cond")
    if f.kind != "bound" or norm_cond(c) is c:
        return f
    return dataclasses.replace(f, form={**f.form, "cond": norm_cond(c)})


@dataclass(frozen=True)
class FormsPaths:
    forms: Path
    proposed: Path

    def existing(self) -> list[Path]:
        return [p for p in (self.forms, self.proposed) if p.is_file()]

    @property
    def any(self) -> bool:
        return self.forms.is_file() or self.proposed.is_file()


def forms_names(source: str) -> tuple[str, str]:
    stem = Path(source).stem
    return f"{stem}.forms.json", f"{stem}.forms.proposed.json"


def forms_paths(
    trace_path: Path | str, *, forms: Path | str | None = None, source: str | None = None
) -> FormsPaths | None:
    """The two files beside the trace at ``trace_path`` (``forms`` overrides the
    authoritative path and implies its ``.proposed.json`` sibling). None: no override and
    the trace names no source."""
    tp = Path(trace_path)
    src = source if source is not None else _rat.trace_source(tp)
    if forms is not None:
        f = Path(forms)
        return FormsPaths(f, _rat.proposals_sibling(f))
    if not src:
        return None
    a, b = forms_names(src)
    return FormsPaths(tp.parent / a, tp.parent / b)


def _bad(detail: str) -> FormsError:
    return FormsError("bad_forms", detail)


def _parse_form(d: Any, i: int) -> Form:
    if not isinstance(d, dict):
        raise _bad(f"forms[{i}] is not an object")
    need = ("id", "rule", "anchor", "kind", "form", "status", "author")
    for k in need:
        if k not in d:
            raise _bad(f"forms[{i}] has no {k}")
    for k in ("id", "rule", "anchor", "kind", "status", "author"):
        if not isinstance(d[k], str) or not d[k]:
            raise _bad(f"forms[{i}].{k} is not a non-empty string")
    if d["status"] not in STATUSES:
        raise _bad(f"forms[{i}].status {d['status']!r} is not one of {STATUSES}")
    if not isinstance(d["form"], dict):
        raise _bad(f"forms[{i}].form is not an object")
    for k in ("approved_by", "approved_sha"):
        v = d.get(k)
        if v is not None and not isinstance(v, str):
            raise _bad(f"forms[{i}].{k} is not a string")
    return Form(
        d["id"],
        d["rule"],
        d["anchor"],
        d["kind"],
        d["form"],
        d["status"],
        d["author"],
        d.get("approved_by"),
        d.get("approved_sha"),
    )


@dataclass(frozen=True)
class FormsFile:
    forms: tuple[Form, ...]
    not_expressible: Mapping[str, Mapping[str, str]]


def load_forms_file(path: Path | None) -> FormsFile:
    """The forms of a file (empty when the file is absent). Raises :class:`FormsError`."""
    if path is None or not path.is_file():
        return FormsFile((), {})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise _bad(f"cannot read {path.name}: {err}") from None
    if not isinstance(data, dict) or data.get("schema") != FORMS_SCHEMA:
        raise _bad(f"{path.name} is not a {FORMS_SCHEMA} file")
    raw = data.get("forms")
    if not isinstance(raw, list):
        raise _bad(f"{path.name}: forms is not a list")
    forms = tuple(_parse_form(d, i) for i, d in enumerate(raw))
    ne = data.get("not_expressible") or {}
    if not isinstance(ne, dict):
        raise _bad(f"{path.name}: not_expressible is not an object")
    clean: dict[str, Mapping[str, str]] = {}
    for rid, v in ne.items():
        if not isinstance(v, dict) or v.get("category") not in NOT_EXPRESSIBLE:
            raise _bad(f"{path.name}: not_expressible[{rid}] needs a category of {NOT_EXPRESSIBLE}")
        clean[str(rid)] = {"category": v["category"], "reason": str(v.get("reason", ""))}
    return FormsFile(forms, clean)


def forms_file_obj(
    source: str,
    forms: Sequence[Form],
    not_expressible: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    ordered = sorted(forms, key=lambda f: (_fnum(f.id), f.id))
    obj: dict[str, Any] = {
        "schema": FORMS_SCHEMA,
        "source": source,
        "forms": [f.to_dict() for f in ordered],
    }
    if not_expressible:
        obj["not_expressible"] = {k: dict(v) for k, v in sorted(not_expressible.items())}
    return obj


def _fnum(fid: str) -> int:
    m = re.match(r"^F(\d+)$", fid)
    return int(m.group(1)) if m else 10**9


# ---- states (D2): the same five as Spike G ------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    """An approved form against the current rules: its ``state`` and the ``rule`` it applies
    to now (the form's own unless ``moved``)."""

    form: Form
    state: str
    rule: str
    matches: tuple[str, ...] = ()


def resolve(trace: Trace, forms: Sequence[Form]) -> list[Resolved]:
    """Each *approved* form's state (``current|moved|edited|ambiguous|stale``) against
    ``trace``. A form whose status is not ``approved`` is not resolved at all."""
    anchors = _rat.rule_anchors(trace)
    by_anchor: dict[str, list[str]] = {}
    for rid, a in anchors.items():
        by_anchor.setdefault(a, []).append(rid)
    out = []
    for f in forms:
        if f.status != "approved":
            continue
        hits = tuple(by_anchor.get(f.anchor, ()))
        if not f.sha_ok:
            out.append(Resolved(f, "edited", f.rule, hits))
        elif anchors.get(f.rule) == f.anchor:
            out.append(Resolved(f, "current", f.rule, hits))
        elif len(hits) == 1:
            out.append(Resolved(f, "moved", hits[0], hits))
        elif len(hits) >= 2:
            out.append(Resolved(f, "ambiguous", f.rule, hits))
        else:
            out.append(Resolved(f, "stale", f.rule, ()))
    return out


def usable(resolved: Sequence[Resolved]) -> list[Resolved]:
    """The ``current`` and ``moved`` ones: the only forms that generate."""
    return [r for r in resolved if r.state in APPROVED_STATES]


@dataclass
class FormSet:
    trace: Trace
    approved: list[Form]
    proposals: list[Form]
    resolved: list[Resolved]
    not_expressible: Mapping[str, Mapping[str, str]]

    def generating(self) -> list[Resolved]:
        return usable(self.resolved)


def load_set(trace: Trace, paths: FormsPaths | None) -> FormSet | None:
    """None when neither file exists. Raises :class:`FormsError` for an invalid file."""
    if paths is None or not paths.any:
        return None
    a = load_forms_file(paths.forms)
    p = load_forms_file(paths.proposed)
    ne = {**p.not_expressible, **a.not_expressible}
    return FormSet(trace, list(a.forms), list(p.forms), resolve(trace, a.forms), ne)


# ---- the type checker (D4) ----------------------------------------------------------------


def _err(code: str, ptr: str, msg: str, hint: str | None = None) -> Diagnostic:
    return Diagnostic(code, Severity.ERROR, Span(1, 1), f"{ptr}: {msg}", hint)


def norm_template(path: str) -> str:
    """A path or template with every parameter as ``{}``: ``{name}``, digit and UUID segments."""
    bare = path.split("?")[0]
    segs = ["{}" if s.isdigit() or _UUID.match(s) or _PLACEHOLDER.fullmatch(s) else s
            for s in bare.split("/")]  # fmt: skip
    return "/".join(segs) or "/"


def known_templates(spec: Spec) -> dict[str, str]:
    """``METHOD {}-template`` -> a named template when one is known (contracts, programs), else
    the ``{}`` one: the endpoints a form may name (D1 Endpoint)."""
    from lattice.spec.validate import _endpoints

    out: dict[str, str] = {}
    for c in spec.contracts:
        out[f"{c.method.upper()} {norm_template(c.path)}"] = f"{c.method.upper()} {c.path}"
    for src in spec.programs.values():
        for _name, method, path in _endpoints(src):
            out.setdefault(f"{method} {norm_template(path)}", f"{method} {path}")
    for sc in spec.scenarios:
        for st in sc.steps:
            if isinstance(st, Step):
                key = f"{st.method.upper()} {norm_template(st.path)}"
                out.setdefault(key, key)
    return out


def _ordered_scalar(s: Scalar | None) -> bool:
    return s is None or s in ORDERED


def lit_ok(scalar: Scalar | None, v: Any) -> bool:
    """Whether the literal ``v`` types against ``scalar`` (None: untyped field)."""
    if v is None or isinstance(v, dict | list | tuple):
        return False
    if scalar is None:
        return True
    if scalar == Scalar.INT:
        return isinstance(v, int) and not isinstance(v, bool)
    if scalar == Scalar.FLOAT:
        return isinstance(v, int | float) and not isinstance(v, bool)
    if scalar == Scalar.BOOL:
        return isinstance(v, bool)
    return isinstance(v, str)


@dataclass(frozen=True)
class FieldRef:
    """A resolved field: the table (schema name) and column it is stored in (None for a
    param with no known column), its scalar (None: unknown) and where the request carries it."""

    table: str | None
    column: str | None
    scalar: Scalar | None
    one_of: tuple[str, ...] | None
    endpoint: str | None  # the param's endpoint (named template as given)
    path: str | None  # "body.a.b" | "query.x" | "path.x"


class _Checker:
    def __init__(self, spec: Spec, trace: Trace | None) -> None:
        self.spec = spec
        self.trace = trace
        self.rules = {r.id for r in trace.rules} if trace else None
        self.templates = known_templates(spec)
        self.users = set(spec.users)
        self.diags: list[Diagnostic] = []

    def err(self, code: str, ptr: str, msg: str, hint: str | None = None) -> None:
        self.diags.append(_err(code, ptr, msg, hint))

    # -- generic pieces
    def keys(
        self, ptr: str, obj: Any, allowed: Sequence[str], required: Sequence[str] = ()
    ) -> bool:
        if not isinstance(obj, dict):
            self.err("F001", ptr, "expected an object")
            return False
        ok = True
        for k in obj:
            if k not in allowed:
                code = "F017" if k in NOT_EXPR_KEYS else "F001"
                why = "not expressible as a form" if code == "F017" else "unknown key"
                self.err(code, f"{ptr}/{k}", f"{why}: {k!r}", f"allowed: {', '.join(allowed)}")
                ok = False
        for k in required:
            if k not in obj:
                self.err("F001", ptr, f"missing key {k!r}")
                ok = False
        return ok

    def table(self, ptr: str, name: Any) -> tuple[str, str, Schema] | None:
        found = resolve_table(self.spec.profile, name) if isinstance(name, str) else None
        if found is None:
            self.err("F002", ptr, f"unknown table {name!r}")
        return found

    def column(self, ptr: str, ref: Any) -> tuple[str, str, Schema] | None:
        """``Table.col`` -> (schema name, column, Schema)."""
        if not isinstance(ref, str) or ref.count(".") != 1:
            self.err("F002", ptr, f"{ref!r} is not Table.column")
            return None
        t, c = ref.split(".")
        found = self.table(ptr, t)
        if found is None:
            return None
        if c not in dict(found[2].columns):
            self.err("F002", ptr, f"unknown column {ref!r}")
            return None
        return found[0], c, found[2]

    def lit(self, ptr: str, scalar: Scalar | None, v: Any) -> bool:
        if isinstance(v, dict | list):  # a value position: F017 for a node name, else F001
            return self.value_lit(ptr, scalar, v)
        if not lit_ok(scalar, v):
            self.err("F004", ptr, f"literal {v!r} does not type against {scalar or 'the field'}")
            return False
        return True

    def value_lit(self, ptr: str, scalar: Scalar | None, v: Any) -> bool:
        """A condition's value: F017 for a node name, F001 for another non-literal (D4')."""
        if v is None or isinstance(v, dict | list | tuple):
            self.err("F017" if _names_node(v) else "F001", ptr, "not a literal")
            return False
        return self.lit(ptr, scalar, v)

    def endpoint(self, ptr: str, ep: Any) -> str | None:
        """The endpoint's named template (``METHOD /tpl``) or None after F003."""
        if not isinstance(ep, str) or " " not in ep.strip():
            self.err("F003", ptr, f"{ep!r} is not 'METHOD /path'")
            return None
        method, _, path = ep.strip().partition(" ")
        key = f"{method.upper()} {norm_template(path)}"
        if key not in self.templates:
            self.err("F003", ptr, f"no endpoint template {ep!r} in the spec")
            return None
        named = self.templates[key]
        return named if "{" in path and path != norm_template(path) else named

    def caller(self, ptr: str, c: Any, extra: Sequence[str] = ("anon",)) -> None:
        if not isinstance(c, str) or (c not in self.users and c not in extra):
            self.err("F007", ptr, f"unknown caller {c!r}", "a handle of Spec.users, or 'anon'")

    def request(self, ptr: str, req: Any, endpoint: str | None) -> None:
        if not self.keys(ptr, req, ("path", "json", "params"), ("path",)):
            return
        if not isinstance(req["path"], str):
            self.err("F014", f"{ptr}/path", "path is not a string")
            return
        params = req.get("params")
        if params is not None and not isinstance(params, dict):
            self.err("F001", f"{ptr}/params", "params is not an object")
        if endpoint is not None:
            method_tpl = endpoint.partition(" ")[2]
            if norm_template(req["path"]) != norm_template(method_tpl):
                self.err(
                    "F014",
                    f"{ptr}/path",
                    f"{req['path']!r} does not match the endpoint template {endpoint!r}",
                )

    def given(self, ptr: str, g: Any) -> None:
        if not isinstance(g, dict):
            self.err("F001", ptr, "given is not an object")
            return
        for t, rows in g.items():
            found = self.table(f"{ptr}/{t}", t)
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                self.err("F001", f"{ptr}/{t}", "expected a list of row objects")
                continue
            if found is None:
                continue
            cols = dict(found[2].columns)
            for i, row in enumerate(rows):
                for c, v in row.items():
                    if c not in cols:
                        self.err("F002", f"{ptr}/{t}/{i}/{c}", f"unknown column {t}.{c}")
                    elif v is not None:
                        self.lit(f"{ptr}/{t}/{i}/{c}", cols[c], v)

    def outcome(self, ptr: str, o: Any) -> None:
        if not self.keys(ptr, o, ("status", "db"), ("status",)):
            return
        s = o["status"]
        if not isinstance(s, int) or isinstance(s, bool) or not 100 <= s <= 599:
            self.err("F018", f"{ptr}/status", f"status {s!r} is outside 100-599")
        db = o.get("db")
        if db is None or db == "unchanged":
            return
        if not isinstance(db, dict) or len(db) != 1 or next(iter(db)) not in ("equals", "count"):
            self.err("F001", f"{ptr}/db", "db is 'unchanged', {equals: {...}} or {count: n}")
            return
        k = next(iter(db))
        if k == "count":
            if not isinstance(db[k], int) or isinstance(db[k], bool) or db[k] < 0:
                self.err("F011", f"{ptr}/db/count", "count must be a non-negative integer")
        elif not isinstance(db[k], dict):
            self.err("F001", f"{ptr}/db/equals", "equals is not an object")
        else:
            for c, v in db[k].items():
                if isinstance(v, dict | list):
                    code = "F017" if _names_node(v) else "F001"
                    self.err(code, f"{ptr}/db/equals/{c}", "not a literal")

    # -- fields and conditions
    def field(self, ptr: str, f: Any, stores: Any = None) -> FieldRef | None:
        if not isinstance(f, dict):
            self.err("F001", ptr, "field is not an object")
            return None
        if "column" in f:
            if not self.keys(ptr, f, ("column",)):
                return None
            col = self.column(f"{ptr}/column", f["column"])
            if col is None:
                return None
            t, c, schema = col
            return FieldRef(t, c, dict(schema.columns)[c], _one_of(schema, c), None, None)
        if not self.keys(ptr, f, ("param", "path"), ("param", "path")):
            return None
        ep = self.endpoint(f"{ptr}/param", f["param"])
        path = f["path"]
        if not isinstance(path, str) or not re.fullmatch(
            r"(body|query|path)\.[^.\s]+(\.[^.\s]+)*", path
        ):
            self.err("F001", f"{ptr}/path", "path is 'body.a.b', 'query.x' or 'path.x'")
            return None
        if ep is None:
            return None
        table = column = scalar = one_of = None
        if stores is not None:
            col = self.column("/stores", stores)
            if col is not None:
                table, column, schema = col
                scalar = dict(schema.columns)[column]
                one_of = _one_of(schema, column)
        name = path.split(".", 1)[1]
        for c in self.spec.contracts:
            if (
                f"{c.method.upper()} {norm_template(c.path)}"
                == f"{ep.partition(' ')[0].upper()} {norm_template(ep.partition(' ')[2])}"
            ):
                for p in c.params:
                    if p.name == name and p.source == path.split(".")[0]:
                        scalar = scalar or p.scalar
                        if p.one_of is not None and one_of is None:
                            one_of = tuple(p.one_of)
        return FieldRef(table, column, scalar, one_of, ep, path)

    def cond(self, ptr: str, cond: Any, ref: FieldRef | None) -> None:
        if not isinstance(cond, dict):
            self.err("F001", ptr, "cond is not an object")
            return
        node = cond.get("node")
        if isinstance(node, str) and node in NOT_EXPR_KEYS:
            self.err("F017", f"{ptr}/node", f"node {node!r} is not expressible as a form")
            return
        scalar = ref.scalar if ref else None
        if node == "Cmp":
            if not self.keys(ptr, cond, ("node", "op", "value"), ("op", "value")):
                return
            op, v = cond["op"], cond["value"]
            if op not in CMP_OPS:
                self.err("F001", f"{ptr}/op", f"op {op!r} is not one of {CMP_OPS}")
                return
            if op in ("ge", "gt", "le", "lt"):
                if ref is not None and ref.one_of is not None:
                    self.err("F006", f"{ptr}/op", "an ordering on a one_of column")
                    return
                if scalar is not None and scalar not in ORDERED:
                    self.err("F004", f"{ptr}/op", f"{op} on a {scalar} field")
                    return
            self.value_lit(f"{ptr}/value", scalar, v)
        elif node == "Len":
            if not self.keys(ptr, cond, ("node", "op", "value"), ("op", "value")):
                return
            if cond["op"] not in LEN_OPS:
                self.err("F001", f"{ptr}/op", f"op {cond['op']!r} is not one of {LEN_OPS}")
                return
            v = cond["value"]
            if isinstance(v, dict | list):
                self.value_lit(f"{ptr}/value", scalar, v)
            elif not isinstance(v, int) or isinstance(v, bool):
                self.err("F004", f"{ptr}/value", "a length is an integer")
            elif v < 0:
                self.err("F011", f"{ptr}/value", "a negative length")
            if scalar is not None and scalar != Scalar.STR:
                self.err("F004", f"{ptr}/op", f"{cond['op']} on a {scalar} field")
        elif node == "In":
            if not self.keys(ptr, cond, ("node", "values"), ("values",)):
                return
            vals = cond["values"]
            if not isinstance(vals, list) or not vals or len(vals) > IN_MAX:
                self.err("F001", f"{ptr}/values", f"values is a list of 1 to {IN_MAX} literals")
                return
            for i, v in enumerate(vals):
                if not self.value_lit(f"{ptr}/values/{i}", scalar, v):
                    continue
                if ref is not None and ref.one_of is not None and v not in ref.one_of:
                    self.err("F005", f"{ptr}/values/{i}", f"{v!r} is outside the one_of values")
        else:
            code = "F017" if _names_node({str(node): 0, **cond}) else "F001"
            self.err(code, f"{ptr}/node", f"node {node!r} is not Cmp, Len or In")

    def where(self, ptr: str, w: Any, table: str, schema: Schema) -> None:
        if not isinstance(w, dict):
            self.err("F001", ptr, "where is not an object")
            return
        if len(w) > WHERE_MAX:
            self.err("F001", ptr, f"where has more than {WHERE_MAX} keys")
        cols = dict(schema.columns)
        for c, v in w.items():
            if c not in cols:
                self.err("F012", f"{ptr}/{c}", f"where column {c!r} is not in {table}")
            else:
                self.lit(f"{ptr}/{c}", cols[c], v)

    # -- the four kinds
    def machine(self, ptr: str, ref: Any) -> Any:
        col = self.column(ptr, ref)
        if col is None:
            return None
        for m in self.spec.machines:
            mt = resolve_table(self.spec.profile, m.table)
            if mt is not None and mt[0] == col[0] and m.column == col[1]:
                return m
        self.err("F008", ptr, f"no machine on {ref!r}")
        return None

    def check(self, f: Form) -> None:
        f = normalised(f)
        ptr = f"/forms/{f.id}"
        if self.rules is not None and f.rule not in self.rules:
            self.err("F016", ptr, f"rule {f.rule} is not in the trace")
        if f.kind not in KINDS:
            code = "F017" if f.kind in NOT_EXPR_KEYS else "F001"
            self.err(code, f"{ptr}/kind", f"unknown kind {f.kind!r}", f"one of {KINDS}")
            return
        n = len(self.diags)
        getattr(self, f"_{f.kind}")(f"{ptr}/form", f.form)
        if len(self.diags) == n and f.kind in ("row_limit", "transition"):
            try:  # D3 preconditions: a row the generator cannot complete is F021 here
                _Gen(self.spec).prepare(f)
            except FormsError as err:
                self.err(err.code, f"{ptr}/form", err.detail)

    def _bound(self, ptr: str, form: Mapping[str, Any]) -> None:
        allowed = (
            "field", "cond", "endpoint", "caller", "request", "given", "refused", "accepted",
            "stores",
        )  # fmt: skip
        if not self.keys(ptr, form, allowed, ("field", "cond")):
            return
        n0 = len(self.diags)
        ref = self.field(f"{ptr}/field", form["field"], form.get("stores"))
        if "stores" in form and (ref is None or ref.endpoint is None):
            self.column(f"{ptr}/stores", form["stores"])
        self.cond(f"{ptr}/cond", form["cond"], ref)
        if ref is not None and (ref.path or "").startswith("path.") and len(self.diags) == n0:
            vals = bound_values(form["cond"], ref.scalar)
            if any(v is not None and re.search(r"^$|[/?#]", str(v)) for v in vals):
                self.err("F021", f"{ptr}/cond", "a path value the placeholder cannot hold")
        ep = None
        if "endpoint" in form:
            ep = self.endpoint(f"{ptr}/endpoint", form["endpoint"])
        if ref is not None and ref.endpoint is not None:
            if ep is not None and norm_template(ep.partition(" ")[2]) != norm_template(
                ref.endpoint.partition(" ")[2]
            ):
                self.err("F003", f"{ptr}/endpoint", "endpoint differs from the param's endpoint")
            ep = ep or ref.endpoint
        if ep is not None:
            for k in ("request", "refused"):
                if k not in form:
                    self.err("F001", ptr, f"an endpoint needs {k!r}")
            if "caller" in form:
                self.caller(f"{ptr}/caller", form["caller"])
            else:
                self.err("F001", ptr, "an endpoint needs 'caller'")
        if "request" in form:
            self.request(f"{ptr}/request", form["request"], ep)
        if "given" in form:
            self.given(f"{ptr}/given", form["given"])
        for k in ("refused", "accepted"):
            if k in form:
                self.outcome(f"{ptr}/{k}", form[k])

    def _row_limit(self, ptr: str, form: Mapping[str, Any]) -> None:
        allowed = (
            "table", "by", "where", "max", "endpoint", "caller", "request", "given", "refused",
            "accepted",
        )  # fmt: skip
        req = ("table", "by", "max", "endpoint", "caller", "request", "refused")
        if not self.keys(ptr, form, allowed, req):
            return
        found = self.table(f"{ptr}/table", form["table"])
        mx = form["max"]
        if not isinstance(mx, int) or isinstance(mx, bool) or mx < 1:
            self.err("F011", f"{ptr}/max", "max must be an integer >= 1")
        by = form["by"]
        if not isinstance(by, list) or not by:
            self.err("F001", f"{ptr}/by", "by is a non-empty list of columns")
        elif found is not None:
            for i, c in enumerate(by):
                if c not in dict(found[2].columns):
                    self.err("F002", f"{ptr}/by/{i}", f"unknown column {c!r}")
        if "where" in form and found is not None:
            self.where(f"{ptr}/where", form["where"], found[0], found[2])
        ep = self.endpoint(f"{ptr}/endpoint", form["endpoint"])
        self.caller(f"{ptr}/caller", form["caller"])
        self.request(f"{ptr}/request", form["request"], ep)
        g = form.get("given")
        if g is not None:
            self.given(f"{ptr}/given", g)
        for k in ("refused", "accepted"):
            if k in form:
                self.outcome(f"{ptr}/{k}", form[k])

    def _ownership(self, ptr: str, form: Mapping[str, Any]) -> None:
        allowed = (
            "table", "scope", "endpoints", "owner", "other", "given", "row", "refused_other",
            "refused_anon", "accepted", "bodies",
        )  # fmt: skip
        req = ("table", "scope", "endpoints", "owner", "other", "row", "refused_other",
               "refused_anon")  # fmt: skip
        if not self.keys(ptr, form, allowed, req):
            return
        found = self.table(f"{ptr}/table", form["table"])
        if found is not None:
            schema = found[2]
            if schema.owner is None and schema.owner_via is None:
                self.err("F013", f"{ptr}/table", f"{found[0]} has no owner or owner_via")
        scope = form["scope"]
        if not isinstance(scope, str) or not valid_scope(scope) or scope == "unknown":
            self.err("F001", f"{ptr}/scope", f"{scope!r} is not an owner scope")
        elif scope_parts(scope)[1] is not None and found is not None:
            if scope_parts(scope)[1] not in dict(found[2].columns):
                self.err("F002", f"{ptr}/scope", f"unknown column in {scope!r}")
        eps = form["endpoints"]
        if not isinstance(eps, list) or not eps:
            self.err("F001", f"{ptr}/endpoints", "endpoints is a non-empty list")
            eps = []
        named = {}
        for i, e in enumerate(eps):
            n = self.endpoint(f"{ptr}/endpoints/{i}", e)
            if n is not None:
                named[e] = n
                tpl = norm_template(n.partition(" ")[2])
                if tpl.count("{}") != 1:
                    self.err("F014", f"{ptr}/endpoints/{i}", "exactly one path parameter (the row)")
        for k in ("owner", "other"):
            h = form[k]
            if not isinstance(h, str) or h not in self.users:
                self.err("F007", f"{ptr}/{k}", f"unknown user handle {h!r}")
        if form.get("owner") == form.get("other"):
            self.err("F007", f"{ptr}/other", "owner and other are the same user")
        if isinstance(form["row"], dict | list) or form["row"] is None:
            self.err("F014", f"{ptr}/row", "row is a key or a request path (int or string)")
        elif _row_key(form["row"], list(named.values())) is None:
            self.err("F014", f"{ptr}/row", "the request path matches none of the endpoints")
        if "given" in form:
            self.given(f"{ptr}/given", form["given"])
        for k in ("refused_other", "refused_anon"):
            self.outcome(f"{ptr}/{k}", form[k])
        for k, v in (
            (form.get("accepted") or {}).items() if isinstance(form.get("accepted"), dict) else ()
        ):
            if k not in named:
                self.err("F003", f"{ptr}/accepted/{k}", "not one of endpoints")
            self.outcome(f"{ptr}/accepted/{k}", v)
        for k in (form.get("bodies") or {}) if isinstance(form.get("bodies"), dict) else ():
            if k not in named:
                self.err("F003", f"{ptr}/bodies/{k}", "not one of endpoints")

    def _transition(self, ptr: str, form: Mapping[str, Any]) -> None:
        allowed = (
            "machine", "trigger", "caller", "request", "row", "from", "to", "accepted", "refused",
            "given",
        )  # fmt: skip
        req = ("machine", "trigger", "caller", "request", "row", "from", "to", "accepted",
               "refused")  # fmt: skip
        if not self.keys(ptr, form, allowed, req):
            return
        m = self.machine(f"{ptr}/machine", form["machine"])
        ep = self.endpoint(f"{ptr}/trigger", form["trigger"])
        self.caller(f"{ptr}/caller", form["caller"])
        self.request(f"{ptr}/request", form["request"], ep)
        col = self.column(f"{ptr}/machine", form["machine"])
        row = form["row"]
        if not isinstance(row, dict) or not row:
            self.err("F001", f"{ptr}/row", "row is a non-empty object of literals")
        elif col is not None:
            cols = dict(col[2].columns)
            for c, v in row.items():
                if c not in cols:
                    self.err("F002", f"{ptr}/row/{c}", f"unknown column {c!r}")
                elif v is not None:
                    self.lit(f"{ptr}/row/{c}", cols[c], v)
            keycol = col[2].keys[0][0] if col[2].keys else None
            if keycol is not None and keycol not in row:
                self.err("F002", f"{ptr}/row", f"row has no key column {keycol!r}")
        frm, to = form["from"], form["to"]
        if not isinstance(frm, list) or not frm:
            self.err("F009", f"{ptr}/from", "from must be a non-empty list of states")
            frm = []
        states = m.states() if m is not None else ()
        for i, s in enumerate(frm):
            if m is not None and s not in states:
                self.err("F008", f"{ptr}/from/{i}", f"{s!r} is not a state of the machine")
        if to is not None and m is not None and to not in states:
            self.err("F008", f"{ptr}/to", f"{to!r} is not a state of the machine")
        method = (ep or "").partition(" ")[0]
        if to is not None and method not in READ_METHODS and to in frm:
            self.err("F009", f"{ptr}/to", "to is among from for a writing trigger")
        if to is not None and m is not None and to in states:
            for s in frm:
                if s in states and (s, to) not in set(m.edges):
                    self.err("F019", f"{ptr}/to", f"the machine has no edge {s} -> {to}")
        for k in ("accepted", "refused"):
            self.outcome(f"{ptr}/{k}", form[k])
        if "given" in form:
            self.given(f"{ptr}/given", form["given"])


def _one_of(schema: Schema, col: str) -> tuple[str, ...] | None:
    for c, vals in schema.one_of:
        if c == col:
            return tuple(vals)
    return None


def check_forms(spec: Spec, trace: Trace | None, forms: Sequence[Form]) -> list[Diagnostic]:
    """Every diagnostic (F001-F019) of ``forms`` against ``spec`` (and ``trace`` for F016).
    Pure; the list is in form order."""
    ck = _Checker(spec, trace)
    for sc in spec.scenarios:
        if sc.name.startswith(RESERVED_PREFIX):
            ck.err(
                "F010", f"/spec/scenarios/{sc.name}", "a hand-written element uses a reserved name"
            )
    for inv in spec.invariants:
        if inv.name.startswith(RESERVED_PREFIX):
            ck.err(
                "F010",
                f"/spec/invariants/{inv.name}",
                "a hand-written element uses a reserved name",
            )
    seen: set[str] = set()
    for f in forms:
        if f.id in seen:
            ck.err("F015", f"/forms/{f.id}", f"two forms have the id {f.id}")
            continue
        seen.add(f.id)
        ck.check(f)
    return ck.diags


# ---- derivations (D1', rules B and C): what a form leaves out, decided from the spec ----------


def _word_table(spec: Spec, word: str) -> str | None:
    """The schema name a path segment or ``x_id`` stem names (``orders``, ``order``)."""
    tables, sql = tables_of(spec.profile)
    w = word.lower().replace("_", "")
    names = lambda t: {t.lower(), t.lower() + "s", sql.get(t, t).lower().replace("_", "")}  # noqa: E731
    return next((t for t in tables if w and w in names(t)), None)


def _fk_target(spec: Spec, schema: Schema, col: str) -> str | None:
    """The table ``col`` references: users (owner), the ``owner_via`` parent, else ``x_id``."""
    api = spec.profile.api
    if col == schema.owner:
        return api.users_table if api else None
    if schema.owner_via and schema.owner_via[0] == col:
        parent = resolve_table(spec.profile, schema.owner_via[1])
        return parent[0] if parent else None
    return _word_table(spec, col[:-3]) if col.endswith("_id") else None


def _ph_table(spec: Spec, named: str) -> str | None:
    """The table a template's first placeholder addresses (by its name, else the segment before)."""
    segs = named.partition(" ")[2].split("/")
    i = next((i for i, seg in enumerate(segs) if _PLACEHOLDER.fullmatch(seg)), 0)
    by_name = _word_table(spec, segs[i][1:-4]) if segs[i].endswith("_id}") else None
    return by_name or (_word_table(spec, segs[i - 1]) if i else None)


def _first_ph(named: str, concrete: str) -> str | None:
    """The value a concrete path binds to the template's first placeholder."""
    pairs = zip(named.partition(" ")[2].split("/"), concrete.split("?")[0].split("/"), strict=False)
    return next((c for t, c in pairs if _PLACEHOLDER.fullmatch(t)), None)


def _row_key(row: Any, named: Sequence[str]) -> Any:
    """An ownership ``row``'s key: a literal, or what a request path binds to the first placeholder
    of one of the endpoints (D1' iii). None: a path matching none."""
    if not (isinstance(row, str) and row.startswith("/")):
        return row
    cs = row.split("?")[0].split("/")
    for tpl in named:
        ts = tpl.partition(" ")[2].split("/")
        first = next((i for i, t in enumerate(ts) if _PLACEHOLDER.fullmatch(t)), None)
        if first is None or first >= len(cs) or len(cs) > len(ts):
            continue
        if all(_PLACEHOLDER.fullmatch(t) or t == c for t, c in zip(ts, cs, strict=False)):
            return _coerce(Scalar.INT, cs[first])
    return None


def accepted_status(spec: Spec, named: str) -> int | None:
    """The accepted status when a form gives none (D1' ii): the unique 2xx the spec's steps expect
    on the endpoint, else 200 if a program serves it, else None (the caller notes the 200)."""
    method, _, path = named.partition(" ")
    key = f"{method.upper()} {norm_template(path)}"
    got = {
        st.expect_status
        for sc in spec.scenarios if not sc.name.startswith(RESERVED_PREFIX)
        for st in sc.steps
        if isinstance(st, Step) and st.expect_status is not None and 200 <= st.expect_status < 300
        and f"{st.method.upper()} {norm_template(st.path)}" == key
    }  # fmt: skip
    if got:
        return next(iter(got)) if len(got) == 1 else None
    from lattice.spec.validate import _endpoints

    served = any(
        f"{m} {norm_template(p)}" == key
        for src in spec.programs.values()
        for _n, m, p in _endpoints(src)
    )
    return 200 if served else None


def _user_id(spec: Spec, handle: str | None) -> int | None:
    """A caller's user id: its 1-based position in ``Spec.users`` (the seed's order)."""
    handles = list(spec.users)
    return handles.index(handle) + 1 if handle in handles else None


def _witness_col(w: Any, named: str, col: str) -> Any:
    """What the witness request carries for ``col`` (a JSON key, query key or path placeholder)."""
    if isinstance(w.json, dict) and w.json.get(col) is not None:
        return w.json[col]
    if w.params and col in w.params:
        return w.params[col]
    got = _match_path(named.partition(" ")[2], w.path).get(col)
    return None if got is None else _coerce(Scalar.INT, got)


def bind_column(spec: Spec, schema: Schema, col: str, w: Any, named: str, caller: str) -> Any:
    """Rule B, a ``by`` column's value: (1) the witness carries it; (2) the first placeholder, for a
    foreign key to the table it addresses; (3) the caller's id for the owner column; else None."""
    v = _witness_col(w, named, col)
    if v is not None:
        return v
    ph = _first_ph(named, w.path)
    if ph is not None and (tgt := _fk_target(spec, schema, col)) and tgt == _ph_table(spec, named):
        return _coerce(dict(schema.columns)[col], ph)
    return _user_id(spec, caller) if col == schema.owner else None


def complete_row(
    spec: Spec, found: tuple[str, str, Schema], row: Mapping[str, Any], w: Any, named: str,
    caller: str, given: Mapping[str, Any] | None,
) -> dict[str, Any]:  # fmt: skip
    """Rule C: ``row`` with every NOT NULL column filled, in schema order. The owner column gets
    the caller's id; any other column the value the witness request carries under its name;
    then a foreign key the smallest key among the ``given`` rows of the referenced table (1
    for a users key), a machine column its initial state, a one_of column its first sorted
    value, else the typed zero. F021 names a column it cannot fill."""
    table, _sql, schema = found
    api = spec.profile.api
    out = dict(row)
    for col, scalar in schema.columns:
        if col in out or col in schema.nullable:
            continue
        tgt = _fk_target(spec, schema, col)
        mt = ((resolve_table(spec.profile, m.table) or ("",))[0] == table for m in spec.machines)
        init = next((m.initial or m.states() for m, ok in zip(spec.machines, mt, strict=True)
                     if ok and m.column == col), None)  # fmt: skip
        v = _user_id(spec, caller) if col == schema.owner else _witness_col(w, named, col)
        if v is None and tgt is not None:
            tf = resolve_table(spec.profile, tgt)
            keys = [r[k] for r in _given_rows(given, spec, tgt, {})
                    if tf and (k := _keycol(tf[2])) in r]  # fmt: skip
            v = min(keys) if keys else 1 if api and tgt == api.users_table else None
        elif v is None and init:
            v = init[0]
        elif v is None and (vals := _one_of(schema, col)):
            v = sorted(vals)[0]
        elif v is None:
            v = {Scalar.INT: 0, Scalar.FLOAT: 0.0, Scalar.BOOL: False}.get(scalar, "")
        if v is None:
            raise FormsError("F021", f"cannot complete a {table} row: no value for {col!r}")
        out[col] = v
    return out


# ---- generation (D3) ------------------------------------------------------------------------


@dataclass(frozen=True)
class Generated:
    scenarios: tuple[Scenario, ...]
    invariants: tuple[Invariant, ...]
    links: Mapping[str, tuple[str, ...]]
    no_invariant: Mapping[str, str]  # form id -> reason ("length")
    notes: tuple[str, ...] = ()

    def names(self) -> list[str]:
        return [s.name for s in self.scenarios] + [i.name for i in self.invariants]


def _step_size(v: float) -> float:
    """The nearest step that differs by more than 2x the checker's 1e-9 relative tolerance."""
    return 4e-9 * max(1.0, abs(v))


def _is_int_scalar(scalar: Scalar | None, v: Any) -> bool:
    return scalar == Scalar.INT or (
        scalar is None and isinstance(v, int) and not isinstance(v, bool)
    )


def bound_values(cond: Mapping[str, Any], scalar: Scalar | None) -> tuple[Any, Any | None]:
    """(a value just inside the bound, a value one step beyond it or None)."""
    node = cond["node"]
    if node == "Len":
        n = cond["value"]
        if cond["op"] == "max_len":
            return "a" * n, "a" * (n + 1)
        return "a" * n, ("a" * (n - 1) if n >= 1 else None)
    if node == "In":
        vals = list(cond["values"])
        if all(isinstance(v, str) for v in vals):
            k = 1
            while "~" * k in vals:
                k += 1
            return vals[0], "~" * k
        nums = [v for v in vals if isinstance(v, int | float) and not isinstance(v, bool)]
        outside = (max(nums) + 1) if nums else 1
        while outside in vals:
            outside += 1
        return vals[0], outside
    op, v = cond["op"], cond["value"]
    if isinstance(v, bool) or isinstance(v, str):
        return (v, _other_lit(v)) if op == "eq" else (_other_lit(v), v)
    one = 1 if _is_int_scalar(scalar, v) else _step_size(v)
    if op == "ge":
        return v, v - one
    if op == "gt":
        return v + one, v
    if op == "le":
        return v, v + one
    if op == "lt":
        return v - one, v
    if op == "eq":
        return v, v + one
    return v + one, v  # ne


def _other_lit(v: Any) -> Any:
    if isinstance(v, bool):
        return not v
    return v + "~"


def cond_holds(cond: Mapping[str, Any], v: Any) -> bool:
    """Whether the literal/value ``v`` satisfies ``cond`` (Len: its length)."""
    node = cond["node"]
    if node == "In":
        return v in cond["values"]
    if node == "Len":
        n = len(str(v))
        return n >= cond["value"] if cond["op"] == "min_len" else n <= cond["value"]
    op, x = cond["op"], cond["value"]
    try:
        if op == "eq":
            return bool(v == x)
        if op == "ne":
            return bool(v != x)
        return bool({"ge": v >= x, "gt": v > x, "le": v <= x, "lt": v < x}[op])
    except TypeError:
        return False


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _lit_src(v: Any) -> str:
    return repr(v)


def _row_rule(table: str, where: Mapping[str, Any]) -> str:
    return " and ".join(f"{table}.{c} == {_lit_src(where[c])}" for c in sorted(where))


def _set_nested(obj: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _has_nested(obj: Any, dotted: str) -> bool:
    cur = obj
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return False
        cur = cur[p]
    return True


def _path_names(named_tpl: str) -> list[str | None]:
    """The path placeholders' names in order (None for an unnamed ``{}``)."""
    return [m[1:-1] or None for m in _PLACEHOLDER.findall(named_tpl)]


def _match_path(named_tpl: str, concrete: str) -> dict[str, str]:
    """The named path parameters of a concrete path against a named template."""
    t = named_tpl.split("?")[0].split("/")
    c = concrete.split("?")[0].split("/")
    out: dict[str, str] = {}
    if len(t) != len(c):
        return out
    for a, b in zip(t, c, strict=True):
        if _PLACEHOLDER.fullmatch(a) and a != "{}":
            out[a[1:-1]] = b
    return out


@dataclass
class _Witness:
    """A concrete request being built: method, path, json, params."""

    method: str
    path: str
    json: Any
    params: dict[str, Any] | None

    def copy(self) -> _Witness:
        return _Witness(
            self.method, self.path, copy.deepcopy(self.json), copy.deepcopy(self.params)
        )


def _witness_of(endpoint: str, req: Mapping[str, Any]) -> _Witness:
    method = endpoint.partition(" ")[0].upper()
    params = req.get("params")
    return _Witness(
        method, req["path"], copy.deepcopy(req.get("json")), dict(params) if params else None
    )


def _place(
    w: _Witness, where: str, name: str, value: Any, tpl: str | None = None
) -> _Witness | None:
    """The witness with the field set at ``where`` ('body'|'query'|'path') and ``name``
    (dotted for a body); None when the witness does not carry the field. A path placeholder is
    found by the endpoint's template ``tpl`` (cause 8): only that segment changes."""
    out = w.copy()
    if where == "body":
        if out.json is None:
            out.json = {}
        if not isinstance(out.json, dict):
            return None
        _set_nested(out.json, name, value)
        return out
    if where == "query":
        out.params = dict(out.params or {})
        out.params[name] = value
        return out
    ph = "{" + name + "}"
    if ph in out.path:
        out.path = out.path.replace(ph, str(value))
        return out
    cs, ts = out.path.split("?")[0].split("/"), (tpl or "").partition(" ")[2].split("/")
    if len(cs) != len(ts) or ph not in ts:
        return None
    cs[ts.index(ph)] = str(value)
    out.path = "/".join(cs) + out.path[len(out.path.split("?")[0]) :]
    return out


def _locate_column(w: _Witness, column: str) -> str | None:
    if _has_nested(w.json, column):
        return "body"
    if w.params and column in w.params:
        return "query"
    if "{" + column + "}" in w.path:
        return "path"
    return None


def _db_locus(
    table: str | None, schema_cols: Mapping[str, Scalar], w: _Witness, named_tpl: str | None,
    field_col: str | None, field_value: Any,
) -> dict[str, Any]:  # fmt: skip
    """The where-clause a witness implies on ``table``: the columns its request carries."""
    where: dict[str, Any] = {}
    if table is None:
        return where
    if isinstance(w.json, dict):
        for k, v in w.json.items():
            if k in schema_cols and not isinstance(v, dict | list) and v is not None:
                where[k] = v
    for k, v in (w.params or {}).items():
        if k in schema_cols and not isinstance(v, dict | list):
            where[k] = v
    if named_tpl is not None:
        for k, v in _match_path(named_tpl.partition(" ")[2], w.path).items():
            if k in schema_cols:
                where[k] = _coerce(schema_cols[k], v)
    if field_col is not None and field_col in schema_cols:
        where[field_col] = field_value
    return where


def _coerce(scalar: Scalar, text: str) -> Any:
    if scalar == Scalar.INT and re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def _refused(outcome: Mapping[str, Any]) -> Mapping[str, Any]:
    """A refused outcome expects ``db: unchanged`` unless the form says otherwise (D3)."""
    return outcome if "db" in outcome else {**outcome, "db": "unchanged"}


def _given_rows(
    given: Mapping[str, Any] | None, spec: Spec, table: str, where: Mapping[str, Any]
) -> list[dict[str, Any]]:
    out = []
    for t, rows in (given or {}).items():
        found = resolve_table(spec.profile, t)
        if found is None or found[0] != table:
            continue
        out.extend(r for r in rows if all(r.get(c) == v for c, v in where.items()))
    return out


def _expect_db(
    spec: Spec, outcome: Mapping[str, Any], table: str | None, where: Mapping[str, Any],
    given: Mapping[str, Any] | None,
) -> dict[str, Any] | None:  # fmt: skip
    """The ``Step.expect_db`` of an Outcome's ``db`` on ``table``/``where`` (None: unchecked).
    ``unchanged`` needs a non-empty locus (the seed's rows are unknown to the form): the
    given rows it matches are the baseline (one row: its values; none: a count of 0)."""
    db = outcome.get("db")
    if db == "unchanged" and table is None:
        return _given_only(spec, given)
    if db is None or table is None:
        return None
    cond: dict[str, Any] = {}
    if where:
        cond["where"] = dict(where)
    if db == "unchanged":
        if not where:
            return None
        rows = _given_rows(given, spec, table, where)
        if len(rows) == 1:
            cond["equals"] = {c: v for c, v in rows[0].items() if v is not None}
        else:
            cond["count"] = len(rows)
    elif "count" in db:
        cond["count"] = db["count"]
    else:
        cond["equals"] = dict(db["equals"])
    return {table: cond}


def _given_only(spec: Spec, given: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """``unchanged`` with no derivable table: the first given row of each table, by key."""
    out: dict[str, Any] = {}
    for t, rows in (given or {}).items():
        found = resolve_table(spec.profile, t)
        key = _keycol(found[2]) if found else None
        if found and key and rows and key in rows[0]:
            eq = {c: v for c, v in rows[0].items() if v is not None}
            out.setdefault(found[0], {"where": {key: rows[0][key]}, "equals": eq})
    return out or None


def derive_table(spec: Spec, named: str, w: Any) -> str | None:
    """The table of a param ``bound`` without ``stores`` (cause 5): (a) the one table of the spec's
    contract or program for the endpoint; (b) the unique table with a foreign key to the table
    its first placeholder addresses whose columns cover the witness's JSON keys."""
    method, _, path = named.partition(" ")
    key = f"{method.upper()} {norm_template(path)}"
    for c in spec.contracts:
        if f"{c.method.upper()} {norm_template(c.path)}" == key and c.table:
            found = resolve_table(spec.profile, c.table)
            return found[0] if found else None
    for src in spec.programs.values():
        for chunk in src.split("@endpoint(")[1:]:
            m = re.match(r'\s*"(\w+)"\s*,\s*"([^"]+)"', chunk)
            if m and f"{m[1].upper()} {norm_template(m[2])}" == key:
                names = {x for x in re.findall(r"(?:fetch|insert)\(\s*(\w+)", chunk)}
                tabs = {found[0] for n in names if (found := resolve_table(spec.profile, n))}
                if len(tabs) == 1:
                    return next(iter(tabs))
    parent = _ph_table(spec, named)
    sent = set(w.json) if isinstance(w.json, dict) else set()
    tables, _sql = tables_of(spec.profile)
    cands = [
        t for t, sc in tables.items()
        if parent and sent <= set(sc.names())
        and any(_fk_target(spec, sc, c) == parent for c in sc.names())
    ]  # fmt: skip
    return cands[0] if len(cands) == 1 else None


def _merge_given(
    a: Mapping[str, Any] | None, extra: Mapping[str, list[dict[str, Any]]] | None = None
) -> dict[str, list[dict[str, Any]]] | None:
    out: dict[str, list[dict[str, Any]]] = {}
    for src in (a or {}, extra or {}):
        for t, rows in src.items():
            out.setdefault(t, []).extend(copy.deepcopy(rows))
    return out or None


def _keycol(schema: Schema) -> str | None:
    return schema.keys[0][0] if schema.keys else None


def _fresh_base(spec: Spec, given: Mapping[str, Any] | None, table: str, key: str | None) -> int:
    hi = 0
    for t, rows in (given or {}).items():
        found = resolve_table(spec.profile, t)
        if found is None or found[0] != table or key is None:
            continue
        for r in rows:
            v = r.get(key)
            if isinstance(v, int) and not isinstance(v, bool):
                hi = max(hi, v)
    return hi + 1000


class _Gen:
    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.scenarios: list[Scenario] = []
        self.invariants: list[Invariant] = []
        self.links: dict[str, list[str]] = {}
        self.no_invariant: dict[str, str] = {}
        self.notes: list[str] = []
        self.limits: list[Form] = []  # the set's row_limit forms (siblings of the one generating)
        self.templates = known_templates(spec)

    def named(self, endpoint: str) -> str:
        method, _, path = endpoint.strip().partition(" ")
        key = f"{method.upper()} {norm_template(path)}"
        return self.templates.get(key, key)

    def user_of(self, caller: str | None) -> str | None:
        return None if caller in (None, "anon") else caller

    def accepted(self, given: Mapping[str, Any] | None, named: str) -> Mapping[str, Any]:
        """The accepted outcome: the form's, else derived from the spec (200 and a note if not)."""
        if given:
            return given
        status = accepted_status(self.spec, named)
        if status is None:
            self.notes.append(f"accepted_default:{named}")
        return {"status": 200 if status is None else status}

    def prepare(self, f: Form) -> Any:
        """What a row_limit or transition needs before it generates (rules B and C); raises
        :class:`FormsError` (F011, F021) for a form the generator cannot complete."""
        form = f.form
        named = self.named(form["endpoint"] if f.kind == "row_limit" else form["trigger"])
        w = _witness_of(named, form["request"])
        if f.kind == "transition":
            col = form["machine"]
            found = resolve_table(self.spec.profile, col.split(".")[0])
            assert found is not None
            row = complete_row(
                self.spec, found, form["row"], w, named, form["caller"], form.get("given")
            )
            return named, w, found, row
        found = resolve_table(self.spec.profile, form["table"])
        assert found is not None
        table, schema = found[0], found[2]
        have = _given_rows(form.get("given"), self.spec, table, {})
        tmpl = have[0] if have else {}
        locus = {}
        for c in form["by"]:
            v = bind_column(self.spec, schema, c, w, named, form["caller"])
            v = tmpl.get(c) if v is None else v
            if v is None:
                raise FormsError("F021", f"cannot bind the by-column {table}.{c}")
            locus[c] = v
        locus.update(form.get("where") or {})
        m = sum(1 for r in have if all(r.get(c) == v for c, v in locus.items()))
        if m > form["max"] - 1:
            raise FormsError(
                "F011", f"{m} given {table} rows already match the locus; max is {form['max']}"
            )
        key = _keycol(schema)
        proto = {**tmpl, **locus, **({key: 0} if key else {})}
        row = complete_row(self.spec, found, proto, w, named, form["caller"], form.get("given"))
        return named, w, found, (locus, row, m)

    def add_invariant(self, f: Form, rule: str, name: str, sentence: str, expr: str) -> None:
        inv = Invariant(name, sentence, expr)
        self.invariants.append(inv)
        self.links.setdefault(rule, []).append(f"invariant:{name}")

    def add_scenario(
        self, f: Form, rule: str, case: str, given: Any, step: Step
    ) -> None:  # fmt: skip
        name = f"{RESERVED_PREFIX}{f.id}:{case}"
        self.scenarios.append(Scenario(name, given=given, steps=(step,)))
        self.links.setdefault(rule, []).append(f"scenario:{name}")

    @staticmethod
    def step(
        w: _Witness, user: str | None, status: int, db: dict[str, Any] | None
    ) -> Step:  # fmt: skip
        return Step(
            w.method, w.path, user=user, json=w.json, params=w.params or None,
            expect_status=status, expect_db=db,
        )  # fmt: skip

    # -- bound
    def bound(self, f: Form, rule: str) -> None:
        form = f.form
        fld, cond = form["field"], form["cond"]
        col_ref = fld.get("column")
        table = column = None
        scalar: Scalar | None = None
        schema_cols: Mapping[str, Scalar] = {}
        if col_ref is not None:
            found = resolve_table(self.spec.profile, col_ref.split(".")[0])
            assert found is not None
            table, column = found[0], col_ref.split(".")[1]
            schema_cols = dict(found[2].columns)
            scalar = schema_cols[column]
        else:
            stores = form.get("stores")
            if stores:
                found = resolve_table(self.spec.profile, stores.split(".")[0])
                if found is not None:
                    table = found[0]
                    schema_cols = dict(found[2].columns)
                    scalar = schema_cols.get(stores.split(".")[1])
            if scalar is None:
                scalar = _infer_scalar(cond)
        if col_ref is not None:
            self._bound_invariant(f, rule, table or "", column or "", cond)
        endpoint = form.get("endpoint") or fld.get("param")
        if endpoint is None:
            return
        named = self.named(endpoint)
        at, beyond = bound_values(cond, scalar)
        base = _witness_of(named, form["request"])
        if table is None:
            table = derive_table(self.spec, named, base)
            found = resolve_table(self.spec.profile, table) if table else None
            schema_cols = dict(found[2].columns) if found else {}
        if "param" in fld:
            where, name = fld["path"].split(".", 1)
        else:
            where = _locate_column(base, column or "") or ""
            name = column or ""
        user = self.user_of(form["caller"])
        accepted = self.accepted(form.get("accepted"), named)
        refused = form["refused"]
        for case, value, outcome in (
            ("at_bound", at, accepted),
            ("beyond", beyond, _refused(refused)),
        ):
            if value is None:
                self.notes.append(f"{f.id}:{case}: no value beyond the bound")
                continue
            w = _place(base, where, name, value, named) if where else None
            if w is None:
                self.notes.append(f"{f.id}:{case}: the witness request does not carry the field")
                continue
            locus = _db_locus(table, schema_cols, w, named, column, value)
            first = _path_names(named.partition(" ")[2])[:1]
            tf = resolve_table(self.spec.profile, table) if table else None
            if where == "path" and tf and first == [name] and _ph_table(self.spec, named) == table:
                if (kc := _keycol(tf[2])) and isinstance(value, int) and abs(value) < 2**63:
                    locus.setdefault(kc, value)  # the row the first placeholder addresses
            given = _merge_given(form.get("given"))
            note = f"{f.id}: db_locus:given_only"
            if table is None and outcome.get("db") == "unchanged" and note not in self.notes:
                self.notes.append(note)
            db = _expect_db(self.spec, outcome, table, locus, given)
            self.add_scenario(f, rule, case, given, self.step(w, user, outcome["status"], db))

    def _bound_invariant(self, f: Form, rule: str, table: str, column: str, cond: Any) -> None:
        name = f"{RESERVED_PREFIX}{f.id}:invariant"
        node = cond["node"]
        if node == "Len":
            self.no_invariant[f.id] = "length"
            return
        ref = f"{table}.{column}"
        if node == "In":
            vals = ", ".join(_lit_src(v) for v in cond["values"])
            expr = f"{ref} in ({vals},)" if len(cond["values"]) == 1 else f"{ref} in ({vals})"
            what = f"is one of {cond['values']}"
        else:
            sym = {"ge": ">=", "gt": ">", "le": "<=", "lt": "<", "eq": "==", "ne": "!="}
            expr = f"{ref} {sym[cond['op']]} {_lit_src(cond['value'])}"
            what = f"{cond['op']} {cond['value']!r}"
        self.add_invariant(f, rule, name, f"{ref} {what} (form {f.id})", expr)

    # -- row_limit
    def row_limit(self, f: Form, rule: str) -> None:
        form = f.form
        found = resolve_table(self.spec.profile, form["table"])
        assert found is not None
        table, schema = found[0], found[2]
        by = list(form["by"])
        where = dict(form.get("where") or {})
        mx = form["max"]
        by_src = repr(by[0]) if len(by) == 1 else repr(tuple(by))
        expr = f"count({table}, by={by_src}"
        if where:
            expr += f", where={_row_rule(table, where)}"
        expr += f") <= {mx}"
        self.add_invariant(
            f, rule, f"{RESERVED_PREFIX}{f.id}:invariant",
            f"at most {mx} {table} rows per {', '.join(by)} (form {f.id})", expr,
        )  # fmt: skip
        named, w, _found, (locus, proto, m) = self.prepare(f)
        user = self.user_of(form["caller"])
        key = _keycol(schema)
        given0 = form.get("given") or {}
        have = _given_rows(given0, self.spec, table, {})
        other = {
            t: rows
            for t, rows in given0.items()
            if (resolve_table(self.spec.profile, t) or (t,))[0] != table
        }
        base = _fresh_base(self.spec, given0, table, key)
        accepted = self.accepted(form.get("accepted"), named)
        sib = {  # the columns another row_limit form of the set counts this table's rows by
            c for g in self.limits if g.id != f.id for c in g.form["by"]
            if (resolve_table(self.spec.profile, g.form["table"]) or ("",))[0] == table
        }  # fmt: skip
        spread = sorted((sib & set(proto)) - set(locus) - {key})
        for case, k, outcome in (
            ("below", mx - 1, accepted),
            ("at_limit", mx, form["refused"]),
        ):
            rows = [
                {
                    **proto,
                    **({key: base + i} if key else {}),
                    **{c: _vary(proto[c], i) for c in spread},
                }
                for i in range(k - m)
            ]
            given = _merge_given(other, {table: [*have, *rows]} if have or rows else None)
            db = {table: {"where": dict(locus), "count": mx}}
            self.add_scenario(f, rule, case, given, self.step(w, user, outcome["status"], db))

    # -- ownership
    def ownership(self, f: Form, rule: str) -> None:
        form = f.form
        found = resolve_table(self.spec.profile, form["table"])
        assert found is not None
        table, schema_cols = found[0], dict(found[2].columns)
        accepted = form.get("accepted") or {}
        bodies = form.get("bodies") or {}
        row = _row_key(form["row"], [self.named(e) for e in form["endpoints"]])
        for ep in form["endpoints"]:
            named = self.named(ep)
            method, _, tpl = named.partition(" ")
            path = _PLACEHOLDER.sub(str(row), tpl, count=1)
            w = _Witness(method.upper(), path, copy.deepcopy(bodies.get(ep)), None)
            slug = _slug(ep)
            given = _merge_given(form.get("given"))
            keycol = _keycol(found[2])
            for case, user, outcome in (
                ("owner", form["owner"], self.accepted(accepted.get(ep), named)),
                ("other", form["other"], form["refused_other"]),
                ("anon", None, form["refused_anon"]),
            ):
                locus = _db_locus(table, schema_cols, w, named, None, None)
                if keycol is not None and _given_rows(given, self.spec, table, {keycol: row}):
                    locus = {**locus, keycol: row}
                if case != "owner":
                    outcome = _refused(outcome)
                db = _expect_db(self.spec, outcome, table, locus, given)
                if db is None and outcome.get("db") == "unchanged":
                    self.notes.append(f"{f.id}: db_unchecked:{case}_{slug}")
                self.add_scenario(
                    f, rule, f"{case}_{slug}", given, self.step(w, user, outcome["status"], db)
                )

    # -- transition
    def transition(self, f: Form, rule: str) -> None:
        form = f.form
        col = form["machine"]
        found = resolve_table(self.spec.profile, col.split(".")[0])
        assert found is not None
        table, column, schema = found[0], col.split(".")[1], found[2]
        machine = next(
            m for m in self.spec.machines
            if (resolve_table(self.spec.profile, m.table) or ("",))[0] == table
            and m.column == column
        )  # fmt: skip
        states = machine.states()
        quoted = ", ".join(_lit_src(s) for s in states)
        self.add_invariant(
            f, rule, f"{RESERVED_PREFIX}{f.id}:invariant",
            f"{table}.{column} is one of the machine's states (form {f.id})",
            f"{table}.{column} in ({quoted}{',' if len(states) == 1 else ''})",
        )  # fmt: skip
        _named, w, _found, full = self.prepare(f)
        user = self.user_of(form["caller"])
        key = _keycol(schema)
        frm, to = list(form["from"]), form["to"]
        where = {key: form["row"][key]} if key and key in form["row"] else {}
        for s in sorted(set(states)):
            row = {**full, column: s}
            given = _merge_given(form.get("given"), {table: [row]})
            if s in frm:
                outcome, final, case = form["accepted"], (to if to is not None else s), f"from_{s}"
            else:
                outcome, final, case = form["refused"], s, f"refused_{s}"
            db = {table: {"where": dict(where), "equals": {column: final}}}
            self.add_scenario(f, rule, case, given, self.step(w, user, outcome["status"], db))


def _vary(v: Any, i: int) -> Any:
    """The ``i``-th value distinct from ``v`` and from the other ``i`` (a bool has none)."""
    if isinstance(v, bool):
        return v
    return v + i + 1 if isinstance(v, int | float) else f"{v}{i + 1}"


def _infer_scalar(cond: Mapping[str, Any]) -> Scalar | None:
    if cond["node"] == "Len":
        return Scalar.STR
    vals = cond["values"] if cond["node"] == "In" else [cond["value"]]
    if all(isinstance(v, bool) for v in vals):
        return Scalar.BOOL
    if all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return Scalar.INT
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in vals):
        return Scalar.FLOAT
    if all(isinstance(v, str) for v in vals):
        return Scalar.STR
    return None


def generate(spec: Spec, forms: Sequence[Form | Resolved]) -> Generated:
    """The scenarios and invariants of ``forms`` (D3). Pure and deterministic: forms in id
    order, case names fixed, no I/O. The caller passes only forms that may generate (the
    approved ones in state ``current`` or ``moved``, :func:`usable`)."""
    items: list[tuple[Form, str]] = []
    for x in forms:
        items.append((normalised(x.form if isinstance(x, Resolved) else x), x.rule))
    items.sort(key=lambda p: (_fnum(p[0].id), p[0].id))
    gen = _Gen(spec)
    gen.limits = [f for f, _ in items if f.kind == "row_limit"]
    for f, rule in items:
        getattr(gen, f.kind)(f, rule)
    return Generated(
        tuple(gen.scenarios),
        tuple(gen.invariants),
        {r: tuple(v) for r, v in sorted(gen.links.items())},
        dict(sorted(gen.no_invariant.items())),
        tuple(gen.notes),
    )


def effective_spec(spec: Spec, gen: Generated) -> Spec:
    """``spec`` plus the generated elements (in memory; the spec file is not touched)."""
    return dataclasses.replace(
        spec,
        scenarios=(*spec.scenarios, *gen.scenarios),
        invariants=(*spec.invariants, *gen.invariants),
    )


def effective_trace(trace: Trace, gen: Generated) -> Trace:
    """``trace`` with the generated links merged (in memory; the trace file is not touched)."""
    links = {k: list(v) for k, v in trace.links.items()}
    for rule, targets in gen.links.items():
        links.setdefault(rule, [])
        links[rule] = [*links[rule], *(t for t in targets if t not in links[rule])]
    return dataclasses.replace(trace, links=links)


def self_check(spec: Spec, gen: Generated) -> list[str]:
    """F020 problems of the generated elements (a generator bug, never shipped silently):
    an invariant that does not compile for sqlite and mysql, a scenario that fails
    ``validate``, an L011/L012 finding among the generated elements."""
    from lattice.spec.invariants import InvariantError, compile_invariant
    from lattice.spec.lint import conflicts
    from lattice.spec.preconditions import contradictions
    from lattice.spec.validate import validate

    problems: list[str] = []
    for inv in gen.invariants:
        for dialect in ("sqlite", "mysql"):
            try:
                compile_invariant(inv, spec.profile, dialect)
            except InvariantError as e:
                problems.append(f"F020 {inv.name} ({dialect}): {e.code} {e.message}")
    eff = effective_spec(spec, gen)
    base_problems = {(p.code, p.message) for p in validate(spec)}
    names = {s.name for s in gen.scenarios} | {i.name for i in gen.invariants}
    for p in validate(eff):
        if (p.code, p.message) not in base_problems:
            problems.append(f"F020 validate {p.code}: {p.message}")
    gen_only = dataclasses.replace(eff, scenarios=gen.scenarios)
    for c in conflicts(gen_only):
        if c.scenario_a in names and c.scenario_b in names:
            problems.append(f"F020 L011 {c.scenario_a} / {c.scenario_b}")
    for sc in gen.scenarios:
        for c in contradictions(sc):
            problems.append(f"F020 L012 {sc.name} step {c.step_a}/{c.step_b}")
    return problems


# ---- coverage (D6) ----------------------------------------------------------------------------


def coverage(trace: Trace, fs: FormSet | None) -> dict[str, Any]:
    """Per rule: ``formed`` (an approved form in state current/moved), ``not_expressible:<cat>``
    (the owner's or drafter's classification, from the forms file) or ``english``. Context
    rules are left out of the scope."""
    context = set(trace.context)
    formed: dict[str, list[str]] = {}
    proposed: dict[str, list[str]] = {}
    ne: Mapping[str, Mapping[str, str]] = {}
    if fs is not None:
        for r in usable(fs.resolved):
            formed.setdefault(r.rule, []).append(r.form.id)
        for p in fs.proposals:
            if p.status == "proposed":
                proposed.setdefault(p.rule, []).append(p.id)
        ne = fs.not_expressible
    rules = []
    for r in trace.rules:
        if r.id in context:
            continue
        if r.id in formed:
            state = "formed"
        elif r.id in ne:
            state = f"not_expressible:{ne[r.id]['category']}"
        else:
            state = "english"
        rules.append(
            {
                "rule": r.id,
                "state": state,
                "forms": sorted(formed.get(r.id, [])),
                "proposed": sorted(proposed.get(r.id, [])),
            }  # fmt: skip
        )
    counts: dict[str, int] = {}
    for x in rules:
        k = x["state"].split(":")[0]
        counts[k] = counts.get(k, 0) + 1
    return {"rules": rules, "counts": dict(sorted(counts.items())), "scope": len(rules)}


# ---- a check run with the generated elements --------------------------------------------------


def generated_for(spec: Spec, trace: Trace | None, paths: FormsPaths | None) -> Generated | None:
    """The generated elements of the project's approved forms (state current or moved), or
    None when there is no authoritative forms file or nothing in it may generate. Reads the
    forms file; writes nothing. This is the only door through which forms enter a check run."""
    if trace is None or paths is None or not paths.forms.is_file():
        return None
    fs = load_set(trace, paths)
    if fs is None or not fs.generating():
        return None
    return generate(spec, fs.generating())


def check_with_forms(
    spec: Spec,
    trace: Trace | None,
    paths: FormsPaths | None,
    app_factory: Any,
    db_factory: Any,
    **kwargs: Any,
) -> tuple[Any, Generated | None]:
    """``lattice.spec.check.check`` over ``spec`` plus the generated elements when an approved
    forms file exists and resolves; otherwise exactly ``check(spec, ...)`` and None. Used by
    the ``forms`` tooling only: a plain ``lattice spec check`` never reads a forms file
    (Amendment 1, O3)."""
    from lattice.spec.check import check

    gen = generated_for(spec, trace, paths)
    eff = effective_spec(spec, gen) if gen is not None else spec
    return check(eff, app_factory, db_factory, **kwargs), gen


def report_dict(report: Any, gen: Generated | None) -> dict[str, Any]:
    """The check report as JSON; a ``forms`` key (the generated element names) only when forms
    took part, so a run with no forms file is byte-identical to a plain one."""
    d: dict[str, Any] = report.to_dict()
    if gen is not None:
        d["forms"] = {"generated": gen.names()}
    return d


# ---- the CLI --------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m lattice.spec forms",
        description="Typed rule conditions: forms that generate a rule's scenario and invariant.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def common(s: argparse.ArgumentParser, spec: bool = True) -> None:
        s.add_argument("--trace", type=Path, required=True, help="the spec's trace JSON")
        s.add_argument("--forms", type=Path, help="the authoritative forms file (default: D2)")
        s.add_argument("--json", action="store_true")
        if spec:
            s.add_argument("--spec", required=True, help="module:SPEC (a Spec, or a function)")

    s = sub.add_parser("show", help="the forms with their states")
    common(s, spec=False)
    s.add_argument("--rule")
    for name, helptext in (
        ("check", "type-check every form against the spec"),
        ("generate", "print the generated scenarios and invariants"),
        ("contradictions", "contradictions decided from the forms (Tier 2)"),
    ):
        s = sub.add_parser(name, help=helptext)
        common(s)
    s = sub.add_parser("coverage", help="formed / not_expressible / english, per rule")
    common(s, spec=False)
    s = sub.add_parser("draft", help="draft proposals with the claude CLI (never authoritative)")
    common(s)
    s.add_argument("--rule", action="append", default=[], required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--model", default="sonnet")
    s = sub.add_parser("approve", help="approve one proposal (owner, in a terminal)")
    common(s)
    s.add_argument("--from", dest="from_path", type=Path, required=True)
    s.add_argument("--form", required=True)
    s.add_argument("--approver")
    s.add_argument("--replace", action="store_true")
    s = sub.add_parser("reject", help="mark a proposal rejected")
    common(s, spec=False)
    s.add_argument("--from", dest="from_path", type=Path, required=True)
    s.add_argument("--form", required=True)
    return p


def _load_trace(path: Path) -> Trace:
    try:
        return Trace.load(path)
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise FormsError("no_trace", f"cannot read {path}: {e}") from None


def _load_spec(ref: str) -> Spec:
    try:
        return _rat.load_spec_ref(ref)  # type: ignore[no-any-return]
    except _rat.NotesError as e:
        raise FormsError("no_spec", e.detail) from None


def render_show(fs: FormSet | None, rule: str | None = None) -> str:
    if fs is None:
        return "no forms file\n"
    out = []
    for r in fs.resolved:
        if rule and r.rule != rule:
            continue
        out.append(
            f"{r.form.id} {r.rule} {r.form.kind} {r.state} (approved by {r.form.approved_by})"
        )
    for p in fs.proposals:
        if rule and p.rule != rule:
            continue
        out.append(f"{p.id} {p.rule} {p.kind} {p.status} (proposal; {p.author}; generates nothing)")
    return "\n".join(out) + ("\n" if out else "(no forms)\n")


def _scenario_view(s: Scenario) -> dict[str, Any]:
    return {
        "name": s.name,
        "given": s.given,
        "steps": [
            {
                "method": st.method,
                "path": st.path,
                "user": st.user,
                "json": st.json,
                "params": st.params,
                "expect_status": st.expect_status,
                "expect_db": st.expect_db,
            }  # fmt: skip
            for st in s.steps
            if isinstance(st, Step)
        ],
    }


def generated_view(gen: Generated) -> dict[str, Any]:
    return {
        "scenarios": [_scenario_view(s) for s in gen.scenarios],
        "invariants": [
            {"name": i.name, "sentence": i.sentence, "expr": i.expr} for i in gen.invariants
        ],
        "links": {k: list(v) for k, v in gen.links.items()},
        "no_invariant": dict(gen.no_invariant),
        "notes": list(gen.notes),
    }


DRAFT_SYSTEM = (
    "You turn ONE English rule of an application into a typed condition form, or say it "
    "cannot be.\n"
    'Reply with ONE JSON object and nothing else: {"kind": '
    '"bound|row_limit|ownership|transition", "form": {...}}\n'
    'or {"not_expressible": "<category>", "reason": "<one line>"}.\n'
    "Categories: time, money, rate, external, cross_request, background, aggregate, "
    "cross_table, response_shape, role, effect_value, disjunction, other.\n"
    "The grammar is closed: no variables, references, arithmetic, functions or loops.\n"
    'Cond nodes: {"node":"Cmp","op":"ge|gt|le|lt|eq|ne","value":Lit}, '
    '{"node":"Len","op":"min_len|max_len","value":n}, {"node":"In","values":[Lit,...]}.\n'
    'Field: {"column":"Table.col"} or {"param":"METHOD /tpl","path":"body.a"}.\n'
    "Never include status, approved_by, approved_sha, author, anchor or rule: they are ignored."
)


def draft_forms(
    spec: Spec,
    trace: Trace,
    paths: FormsPaths,
    *,
    rules: Sequence[str],
    out: Path,
    generator: Any,
    model: str,
) -> dict[str, Any]:
    """Draft proposals for ``rules`` into ``out`` (calls only the generator, here the
    ``claude`` CLI). Writes only ``out``; refuses the authoritative file."""
    if _rat.same_file(out, paths.forms):
        raise FormsError("draft_out_is_authoritative", "--out is the authoritative file")
    inside = out.resolve().parent == paths.forms.resolve().parent
    if inside and not out.name.endswith(".proposed.json"):
        raise FormsError(
            "draft_out_not_proposals", "inside the project --out must be *.proposed.json"
        )
    by_id = {r.id: r for r in trace.rules}
    existing = load_forms_file(out).forms
    taken = {f.id for f in existing} | {f.id for f in load_forms_file(paths.forms).forms}
    new: list[Form] = []
    reports = []
    n = max([_fnum(i) for i in taken if _fnum(i) < 10**9] + [0])
    stopped = None
    for rid in rules:
        if rid not in by_id:
            raise FormsError("unknown_rule", f"the trace has no rule {rid}")
        r = by_id[rid]
        user = f"Rule {rid}: {r.text}\nSection: {r.section}\nTables: " + ", ".join(
            sorted(spec.profile.api.tables) if spec.profile.api else []
        )
        try:
            gen = generator.generate(DRAFT_SYSTEM, [{"role": "user", "content": user}])
        except Exception as err:  # noqa: BLE001 -- a usage limit ends the run
            stopped = f"{rid}: {type(err).__name__}: {err}"
            break
        rep: dict[str, Any] = {"rule": rid, "cost_usd": getattr(gen, "cost_usd", None)}
        try:
            data = json.loads(gen.text[gen.text.index("{") : gen.text.rindex("}") + 1])
        except ValueError:
            rep["error"] = "the reply is not one JSON object"
            reports.append(rep)
            continue
        if not isinstance(data, dict) or data.get("kind") not in KINDS:
            rep["error"] = "no kind" if "not_expressible" not in (data or {}) else None
            rep["not_expressible"] = (data or {}).get("not_expressible")
            reports.append(rep)
            continue
        n += 1
        new.append(
            Form(
                f"F{n}",
                rid,
                _rat.anchor_of(r.text),
                data["kind"],
                data.get("form") or {},
                "proposed",
                f"llm:{model}",
            )  # fmt: skip
        )
        reports.append(rep)
        _rat.atomic_write_json(out, forms_file_obj(trace.source, [*existing, *new]))
    return {
        "out": str(out),
        "drafted": len(new),
        "rules": reports,
        "cost_usd": round(sum((r.get("cost_usd") or 0.0) for r in reports), 6),
        "stopped": stopped,
    }


def approve(
    paths: FormsPaths,
    spec: Spec,
    trace: Trace,
    *,
    from_path: Path,
    form_id: str,
    approver: str | None,
    replace: bool = False,
    prompt: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Copy one proposal into the authoritative file (atomically). Needs a terminal."""
    try:
        _rat.human_guard("forms approve")
    except _rat.NotesError as e:
        raise FormsError(e.code, e.detail) from None
    if _rat.same_file(from_path, paths.forms):
        raise FormsError("approve_from_authoritative", "--from is the authoritative file")
    props = load_forms_file(from_path).forms
    cands = [p for p in props if p.id == form_id and p.status == "proposed"]
    if not cands:
        raise FormsError("no_proposal", f"{from_path.name} has no proposed form {form_id}")
    prop = cands[-1]
    rule = next((r for r in trace.rules if r.id == prop.rule), None)
    if rule is None:
        raise FormsError("unknown_rule", f"the trace has no rule {prop.rule}")
    if prop.anchor != _rat.anchor_of(rule.text):
        raise FormsError("proposal_stale", f"the wording of {prop.rule} changed; draft it again")
    diags = [d for d in check_forms(spec, trace, [prop]) if d.severity is Severity.ERROR]
    if diags:
        raise FormsError("form_invalid", "; ".join(d.render() for d in diags[:3]))
    by = (approver or "").strip()
    if not by and prompt is not None:
        by = prompt("approver: ").strip()
    if not by:
        raise FormsError("missing_value", "approver is required")
    existing = list(load_forms_file(paths.forms).forms)
    clash = next((f for f in existing if f.id == prop.id), None)
    if clash is not None and not replace:
        raise FormsError("form_exists", f"{prop.id} is already approved; pass --replace")
    done = Form(
        prop.id, prop.rule, prop.anchor, prop.kind, prop.form, "approved", prop.author, by,
        form_sha(prop.kind, prop.form),
    )  # fmt: skip
    ne = load_forms_file(paths.forms).not_expressible
    _rat.atomic_write_json(
        paths.forms,
        forms_file_obj(trace.source, [f for f in existing if f.id != prop.id] + [done], ne),
    )
    return {"written": str(paths.forms), "form": prop.id, "approved_sha": done.approved_sha}


def reject(paths: FormsPaths, trace: Trace, *, from_path: Path, form_id: str) -> dict[str, Any]:
    if _rat.same_file(from_path, paths.forms):
        raise FormsError("reject_authoritative", "--from is the authoritative file")
    ff = load_forms_file(from_path)
    if not any(f.id == form_id for f in ff.forms):
        raise FormsError("no_proposal", f"{from_path.name} has no form {form_id}")
    marked = [dataclasses.replace(f, status="rejected") if f.id == form_id else f for f in ff.forms]
    _rat.atomic_write_json(from_path, forms_file_obj(trace.source, marked, ff.not_expressible))
    return {"written": str(from_path), "form": form_id}


def main(
    argv: Sequence[str] | None = None,
    *,
    generator: Any = None,
    prompt: Callable[[str], str] | None = input,
) -> int:
    """Exit 0 done, 1 ``check`` found an error, 2 could not run or refused."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "forms":
        argv = argv[1:]
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        trace = _load_trace(args.trace)
        paths = forms_paths(args.trace, forms=args.forms, source=trace.source)
        if paths is None:
            raise FormsError("no_trace", "the trace names no source")
        cmd = args.command
        fs = load_set(trace, paths)
        if cmd == "show":
            if args.json:
                print(json.dumps({
                    "forms": [{"id": r.form.id, "rule": r.rule, "kind": r.form.kind,
                               "state": r.state} for r in (fs.resolved if fs else [])],
                    "proposals": [p.to_dict() for p in (fs.proposals if fs else [])],
                }, indent=2))  # fmt: skip
            else:
                sys.stdout.write(render_show(fs, args.rule))
            return 0
        if cmd == "coverage":
            rep = coverage(trace, fs)
            if args.json:
                print(json.dumps(rep, indent=2))
            else:
                for x in rep["rules"]:
                    print(
                        f"{x['rule']} {x['state']}"
                        + (f" {','.join(x['forms'])}" if x["forms"] else "")
                    )
                print(", ".join(f"{k} {v}" for k, v in rep["counts"].items()))
            return 0
        if cmd == "reject":
            res = reject(paths, trace, from_path=args.from_path, form_id=args.form)
            print(
                json.dumps(res, indent=2) if args.json else f"wrote {res['written']} ({args.form})"
            )
            return 0
        spec = _load_spec(args.spec)
        if cmd == "check":
            every = [*(fs.approved if fs else []), *(fs.proposals if fs else [])]
            diags = check_forms(spec, trace, every)
            if args.json:
                print(json.dumps([{"code": d.code, "message": d.message, "hint": d.hint}
                                  for d in diags], indent=2))  # fmt: skip
            else:
                for d in diags:
                    print(d.render())
                print(f"{len(diags)} problem(s) in {len(every)} form(s)")
            return 1 if diags else 0
        if cmd in ("generate", "contradictions"):
            use = fs.generating() if fs else []
            if cmd == "generate":
                gen = generate(spec, use)
                print(json.dumps(generated_view(gen), indent=2, sort_keys=True))
                return 0
            from lattice.spec import forms_tier2

            items = forms_tier2.contradictions(spec, use)
            print(json.dumps({"items": items}, indent=2, sort_keys=True))
            return 0
        if cmd == "draft":
            if generator is None:
                from lattice.llm.cli_client import ClaudeCliClient

                generator = ClaudeCliClient(model=args.model, effort="", full_text=True)
            rep = draft_forms(
                spec, trace, paths, rules=list(args.rule), out=args.out, generator=generator,
                model=args.model,
            )  # fmt: skip
            print(json.dumps(rep, indent=2))
            return 2 if rep["stopped"] else 0
        res = approve(
            paths, spec, trace, from_path=args.from_path, form_id=args.form,
            approver=args.approver, replace=args.replace, prompt=prompt,
        )  # fmt: skip
        print(json.dumps(res, indent=2) if args.json else f"wrote {res['written']} ({args.form})")
        return 0
    except FormsError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 -- any failure is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2


__all__ = ["NUMERIC", "main"]
