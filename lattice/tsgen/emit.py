"""EndpointPlan -> api.ts and hooks.ts (spec 2026-09-29 ui-a §3)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from lattice.api.ir import EndpointPlan, ErrorSpec, WriteStep
from lattice.catalog import API_ERRORS, LIST_KIND
from lattice.targets import TS_REFUSAL
from lattice.tsgen.regex import Untranslatable, fullmatch_source
from lattice.tsgen.specs import BoundError, plan_specs, ts_checks
from lattice.typesys import Schema

SAFE = 2**53 - 1
KIND = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
        409: "conflict", 422: "validation"}  # fmt: skip
SUCCESS = {"One": 200, "Page": 200, "Created": 201, "NoContent": 204, LIST_KIND: 200}
TS_SCALAR = {"Str": "string", "Bool": "boolean", "Float": "number", "Int": "bigint",
             "Uuid": "string", "Date": "string", "DateTime": "string"}  # fmt: skip


class TsGenRefusal(Exception):
    """L893: the TypeScript client cannot be generated for this plan."""

    code = TS_REFUSAL

    def __init__(self, plan: EndpointPlan, message: str) -> None:
        super().__init__(f"{plan.span.line}:{plan.span.col}: error {TS_REFUSAL}: {message}")


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def pascal(name: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in name.split("_"))


# Names a generated binding may not have: ECMAScript reserved words plus the strict-mode and
# module restrictions (api.ts is a module); a type alias may not have TypeScript's
# predefined type names either.
RESERVED = frozenset(
    "break case catch class const continue debugger default delete do else enum export extends"
    " false finally for function if import in instanceof new null return super switch this"
    " throw true try typeof var void while with implements interface let package private"
    " protected public static yield await eval arguments".split()
)
RESERVED_TYPES = RESERVED | frozenset(
    "any unknown never object string number boolean bigint symbol undefined".split()
)
# Names api.ts itself declares (values, then types).
FIXED_VALUES = ("rt", "ENDPOINTS")
FIXED_TYPES = ("rt", "CallOpts", "FieldError", "Result", "Unexpected", "ValidationError")
# Names hooks.ts imports as values; each endpoint adds its hook `use<Pascal>`.
FIXED_HOOKS = ("useMutation", "useQuery", "api", "canonicalKey")


# A generated name (binding, type, or property of a generated type or object) is an ASCII
# JavaScript identifier: `_1x` would give the function `1x`; non-ASCII is refused rather than
# reasoned about (Python and JS identifier sets and case mappings differ). `__proto__` is
# never a name: as a key of an object literal (ENDPOINTS) or a property the runtime sets
# (a decoded row) it sets the prototype instead.
JS_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def check_identifier(plan: EndpointPlan, name: str, what: str) -> None:
    if not JS_IDENTIFIER.fullmatch(name) or name == "__proto__":
        raise TsGenRefusal(plan, f"{what} '{name}' is not a usable JavaScript identifier")


def check_names(plans: Sequence[EndpointPlan]) -> None:
    """L893 when a plan's generated names are not valid, distinct TypeScript names."""
    values: dict[str, str] = dict.fromkeys(FIXED_VALUES, "api.ts")
    types: dict[str, str] = dict.fromkeys(FIXED_TYPES, "api.ts")

    def claim(table: dict[str, str], name: str, owner: str, plan: EndpointPlan) -> None:
        check_identifier(plan, name, f"'{owner}' generates the name")
        if name in (RESERVED_TYPES if table is types else RESERVED):
            raise TsGenRefusal(plan, f"'{owner}' generates the reserved name '{name}'")
        if name in table and table[name] != owner:
            raise TsGenRefusal(plan, f"'{owner}' generates '{name}', as '{table[name]}' does")
        table[name] = owner

    hooks: dict[str, str] = dict.fromkeys(FIXED_HOOKS, "hooks.ts")
    names: set[str] = set()
    for plan in plans:
        if plan.name in names:
            raise TsGenRefusal(plan, f"two endpoints are named '{plan.name}'")
        names.add(plan.name)
        check_identifier(plan, plan.name, "the endpoint name")  # the ENDPOINTS key
        for spec in plan.params:
            if spec.source != "user":
                check_identifier(plan, spec.name, "the parameter")
        for column, _scalar in plan.schema.columns:
            check_identifier(plan, column, "the response column")
        if plan.success == "Page":
            for _key, field in page_shape(plan):
                check_identifier(plan, field, "the page field")
    for plan in plans:
        if plan.response is not None:
            claim(types, plan.response, f"model {plan.response}", plan)
    for plan in plans:
        owner = f"endpoint {plan.name}"
        for value in (plan.name.upper(), f"validate{pascal(plan.name)}", camel(plan.name)):
            if value in values and values[value] == owner:  # one plan, two roles
                raise TsGenRefusal(plan, f"'{owner}' generates '{value}' twice")
            claim(values, value, owner, plan)
        for suffix in ("Params", "Response", "Error"):
            claim(types, pascal(plan.name) + suffix, owner, plan)
        claim(hooks, f"use{pascal(plan.name)}", owner, plan)


def kind_of(status: int) -> str:
    return KIND.get(status, f"status_{status}")


def error_statuses(plan: EndpointPlan) -> list[int]:
    """Every error status the server can answer for this plan (not 5xx)."""
    out = {422}
    if plan.success in ("One", "Created"):
        out.add(404)  # respond_one: no row
    if plan.auth != "Public":
        out |= {401, 403}
        if plan.active_column is not None:
            out.add(plan.inactive_status)
        for answer in (plan.token_invalid, plan.token_unknown):
            if answer is not None:
                out.add(answer[0])
    out |= {API_ERRORS[e] for e in plan.errors if e in API_ERRORS}
    if any(isinstance(s, WriteStep) and s.kind in ("insert", "update") for s in plan.steps):
        out.add(409)  # serve: any UNIQUE violation not declared by unique_or= (UNIQUE_CONFLICT)
    for step in plan.steps:
        for attr in ("error", "conflict", "missing"):
            spec = getattr(step, attr, None)
            if isinstance(spec, ErrorSpec):
                out.add(spec.status)
    return sorted(out)


def js_bound(tagged: dict[str, object]) -> str:
    """A bound (specs.tag_bound) as a JS literal: an int bound a bigint (exact), a float
    bound its repr (the shortest text that reads back as the same double, in JS too)."""
    return f"{tagged['v']}n" if tagged["t"] == "int" else str(tagged["v"])


def ts_value(tagged: dict[str, object]) -> str:
    if tagged["t"] == "int":
        return f'{{ t: "int", v: {tagged["v"]}n }}'
    if tagged["t"] == "float":
        return f'{{ t: "float", v: {tagged["v"]} }}'
    return json.dumps(tagged)


def ts_spec(d: dict[str, object], plan: EndpointPlan) -> str:
    """The runtime's ParamSpec: each constraint with its message (specs.ts_checks)."""
    try:
        checks = ts_checks(d)
    except BoundError as err:
        raise TsGenRefusal(plan, f"parameter '{d['name']}': {err}") from None
    parts = [
        f"name: {json.dumps(d['name'])}",
        f'source: "{d["source"]}"',
        f'scalar: "{d["scalar"]}"',
    ]

    def check(key: str, v: str) -> None:
        parts.append(f"{key}: {{ v: {v}, msg: {json.dumps(checks[key]['msg'])} }}")

    for key in ("ge", "le"):
        if key in d:
            check(key, js_bound(checks[key]["v"]))  # type: ignore[arg-type]
    for key in ("min_len", "max_len", "one_of"):
        if key in d:
            check(key, json.dumps(d[key]))
    if "default" in d:
        parts.append(f"default: {ts_value(d['default'])}")  # type: ignore[arg-type]
    for key in ("optional", "nullable"):
        if d.get(key):
            parts.append(f"{key}: true")
    if "pattern" in d:
        try:
            js = fullmatch_source(d["pattern"])  # type: ignore[arg-type]
        except Untranslatable as err:
            raise TsGenRefusal(plan, f"pattern {d['pattern']!r}: {err}") from None
        check("pattern", json.dumps(js))
    if d.get("email"):
        check("email", json.dumps(checks["email"]["v"]))
    return "{ " + ", ".join(parts) + " }"


def int_is_number(d: dict[str, object]) -> bool:
    ge, le = d.get("ge"), d.get("le")
    return (
        isinstance(ge, int | float) and isinstance(le, int | float) and -SAFE <= ge and le <= SAFE
    )


def param_field(d: dict[str, object], json_body: bool) -> str:
    """A Params field. `| null` only where null reaches the server as the value it means:
    a `T | None = None` parameter (null is left out of a query or form, and the server's
    default is None), and an UNSET parameter that accepts null only in a JSON body (null sets
    NULL there; a query or form cannot carry null, and leaving it out would mean "not
    sent", so it is not offered)."""
    t = "number" if d["scalar"] == "Int" and int_is_number(d) else TS_SCALAR[d["scalar"]]  # type: ignore[index]
    name = d["name"]
    if "default" not in d:
        return f"  {name}: {t};"
    null_sets = bool(d.get("nullable")) and d["source"] == "body" and json_body
    nullable = d["default"] == {"t": "none"} or null_sets
    return f"  {name}?: {t}{' | null' if nullable else ''};"


# Every response column may be null: the lowered plan's response schema does not carry
# nullability through select/derive/joins (its `nullable` is the checker's L420/L421 dataset
# metadata, empty after a projection), and a stored or derived value can be NULL (found by
# the L3 gate: realapps read_item answers "description": null with an empty `nullable`).
# Promising non-null would make a valid answer a decode error.


def row_type(schema: Schema) -> str:
    fields = [f"  {n}: {TS_SCALAR[s.value]} | null;" for n, s in schema.columns]
    return "{\n" + "\n".join(fields) + "\n}"


def cols_literal(schema: Schema) -> str:
    cols = (f'[{json.dumps(n)}, "{s.value}", true]' for n, s in schema.columns)
    return "[" + ", ".join(cols) + "]"


def page_shape(plan: EndpointPlan) -> list[tuple[str, str]]:
    total = plan.page is not None and plan.page.total
    return [(k, f) for k, f in plan.page_shape if f and (k != "total" or total)]


def page_num(plan: EndpointPlan, key: str) -> str:
    """How a Page envelope number decodes (rt.PageNum). `total` is a row count; `limit` and
    `offset` echo page()'s argument, a literal or a parameter's value: a number when it fits
    in +-(2^53 - 1) (a parameter: by its declared bounds, as for Int parameters), else a
    bigint (found by the L3 gate: `offset: Int(ge=0)` echoes 2^63 - 1)."""
    if key == "total":
        return "count"
    arg = getattr(plan.page, key) if plan.page is not None else None
    if isinstance(arg, int):
        return "number" if abs(arg) <= SAFE else "bigint"
    spec = next((d for d in plan_specs(plan) if d["name"] == arg), None)
    ok = spec is not None and spec["scalar"] == "Int" and int_is_number(spec)
    return "number" if ok else "bigint"


def _response(plan: EndpointPlan, row: str) -> tuple[str, str]:
    """(the response type, the decode function)."""
    if plan.success not in SUCCESS:
        raise TsGenRefusal(plan, f"success kind {plan.success!r}")
    if plan.success == "NoContent":
        return "void", "() => undefined"
    cols = cols_literal(plan.schema)
    if plan.success == "Page":
        shape = page_shape(plan)
        modes = {k: page_num(plan, k) for k, _f in shape if k != "items"}
        ts_num = {k: "bigint" if m == "bigint" else "number" for k, m in modes.items()}
        fields = [f"{f}: {row}[]" if k == "items" else f"{f}: {ts_num[k]}" for k, f in shape]
        shape_ts = "[" + ", ".join(
            f"[{json.dumps(k)}, {json.dumps(f)}]" if k == "items"
            else f"[{json.dumps(k)}, {json.dumps(f)}, {json.dumps(modes[k])}]"
            for k, f in shape
        ) + "]"  # fmt: skip
        return "{ " + "; ".join(fields) + " }", f"(v) => rt.decodePage(v, {cols}, {shape_ts})"
    if plan.success == LIST_KIND:
        return f"{row}[]", f"(v) => rt.decodeList(v, {cols})"
    return row, f"(v) => rt.decodeRow(v, {cols})"


def _endpoint(plan: EndpointPlan, models: dict[str, str]) -> str:
    specs = plan_specs(plan)
    name, fn = pascal(plan.name), camel(plan.name)
    if plan.response is not None:
        row_ts = models.setdefault(plan.response, row_type(plan.schema))
        if row_ts != row_type(plan.schema):
            raise TsGenRefusal(
                plan, f"model {plan.response} has another schema in an earlier endpoint"
            )
        row = plan.response
    else:
        row = row_type(plan.schema)
    resp, decode = _response(plan, row)
    statuses = error_statuses(plan)
    variants = (
        ["rt.ValidationError"]
        + [
            f'{{ kind: "{kind_of(s)}"; status: {s}; detail: string | null }}'
            for s in statuses
            if s != 422
        ]
        + ["rt.Unexpected"]
    )
    errors = ", ".join(f'{s}: "{kind_of(s)}"' for s in statuses)
    number_ints = [d["name"] for d in specs if d["scalar"] == "Int" and int_is_number(d)]
    fields = "\n".join(param_field(d, plan.body != "form") for d in specs)
    return f"""
export type {name}Params = {{
{fields}
}};
export type {name}Response = {resp};
export type {name}Error =
  | {"\n  | ".join(variants)};
const {plan.name.upper()}: rt.Endpoint = {{
  method: {json.dumps(plan.method)},
  path: {json.dumps(plan.path)},
  params: [{", ".join(ts_spec(d, plan) for d in specs)}],
  form: {"true" if plan.body == "form" else "false"},
  success: {SUCCESS[plan.success]},
  errors: {{ {errors} }},
  numberInts: {json.dumps(number_ints)},
  decode: {decode},
}};
export function validate{name}(p: {name}Params): rt.FieldError[] {{
  return rt.validate({plan.name.upper()}, p);
}}
export function {fn}(
  p: {name}Params,
  o: rt.CallOpts,
): Promise<rt.Result<{name}Response, {name}Error>> {{
  return rt.call({plan.name.upper()}, p, o) as Promise<rt.Result<{name}Response, {name}Error>>;
}}
"""


HEADER = """// Generated by lattice.tsgen from a checked Lattice program. Do not edit.
// Request validation here is proven identical to the server's (spec 2026-09-29 ui-a §5).
// Compile with allowImportingTsExtensions (imports end in .ts) and lib ES2024 or later
// (String.prototype.isWellFormed / toWellFormed, Object.hasOwn, AbortSignal.any).
// Every call returns a Result and never throws: errors are values (their `kind`).
import * as rt from "./lattice-rt.ts";
export type { CallOpts, FieldError, Result, Unexpected, ValidationError } from "./lattice-rt.ts";
"""


def api_ts(plans: Sequence[EndpointPlan]) -> str:
    check_names(plans)
    models: dict[str, str] = {}
    bodies = [_endpoint(p, models) for p in plans]
    model_text = "".join(f"\nexport type {m} = {t};\n" for m, t in models.items())
    registry = ", ".join(f"{json.dumps(p.name)}: {p.name.upper()}" for p in plans)
    endpoints = f"\nexport const ENDPOINTS: Record<string, rt.Endpoint> = {{ {registry} }};\n"
    return HEADER + model_text + "".join(bodies) + endpoints


HOOKS_HEADER = """// Generated by lattice.tsgen. React hooks over api.ts (TanStack Query 5).
// Compile with allowImportingTsExtensions and lib ES2024 or later (see api.ts).
// They never throw: `error` is the endpoint's typed error union, so TanStack's `retry` never
// fires, not even on an Unexpected (network, 5xx): retry by calling `refetch` yourself.
// Query keys are [endpoint name, params]: they do not include the base URL or the token. Use
// one QueryClient per account and base URL, or add them to the key yourself.
import { useMutation, useQuery } from "@tanstack/react-query";
import * as api from "./api.ts";
import { canonicalKey, type CallOpts } from "./lattice-rt.ts";
"""


def _query_hook(plan: EndpointPlan) -> str:
    n, fn = pascal(plan.name), camel(plan.name)
    return f"""
export function use{n}(p: api.{n}Params, o: CallOpts) {{
  const q = useQuery({{
    queryKey: [{json.dumps(plan.name)}, canonicalKey(p)],
    queryFn: ({{ signal }}) =>
      api.{fn}(p, {{ ...o, signal: o.signal ? AbortSignal.any([o.signal, signal]) : signal }}),
  }});
  const r = q.data;
  return {{
    data: r?.ok ? r.value : undefined,
    error: r !== undefined && !r.ok ? r.error : null,
    isPending: q.isPending,
    refetch: q.refetch,
  }};
}}
"""


def _mutation_hook(plan: EndpointPlan) -> str:
    n, fn = pascal(plan.name), camel(plan.name)
    return f"""
export function use{n}(o: CallOpts) {{
  const m = useMutation({{ mutationFn: (p: api.{n}Params) => api.{fn}(p, o) }});
  const r = m.data;
  return {{
    mutate: m.mutate,
    mutateAsync: m.mutateAsync,
    data: r?.ok ? r.value : undefined,
    error: r !== undefined && !r.ok ? r.error : null,
    isPending: m.isPending,
    reset: m.reset,
  }};
}}
"""


def hooks_ts(plans: Sequence[EndpointPlan]) -> str:
    check_names(plans)
    body = "".join(_query_hook(p) if p.method == "GET" else _mutation_hook(p) for p in plans)
    return HOOKS_HEADER + body
