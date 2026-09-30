"""EndpointPlan -> api.ts (spec 2026-09-29 ui-a §3). hooks_ts is added by Task 7."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence

from lattice.api.ir import EndpointPlan, ErrorSpec, WriteStep
from lattice.catalog import API_ERRORS, LIST_KIND
from lattice.targets import TS_REFUSAL
from lattice.tsgen.regex import Untranslatable, fullmatch_source
from lattice.tsgen.specs import plan_specs
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


def check_names(plans: Sequence[EndpointPlan]) -> None:
    """L893 when a plan's generated names are not valid, distinct TypeScript names."""
    values: dict[str, str] = dict.fromkeys(FIXED_VALUES, "api.ts")
    types: dict[str, str] = dict.fromkeys(FIXED_TYPES, "api.ts")

    def claim(table: dict[str, str], name: str, owner: str, plan: EndpointPlan) -> None:
        if name in (RESERVED_TYPES if table is types else RESERVED):
            raise TsGenRefusal(plan, f"'{owner}' generates the reserved name '{name}'")
        if name in table and table[name] != owner:
            raise TsGenRefusal(plan, f"'{owner}' generates '{name}', as '{table[name]}' does")
        table[name] = owner

    names: set[str] = set()
    for plan in plans:
        if plan.name in names:
            raise TsGenRefusal(plan, f"two endpoints are named '{plan.name}'")
        names.add(plan.name)
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


def js_number(plan: EndpointPlan, v: float) -> str:
    if not math.isfinite(v):
        raise TsGenRefusal(plan, f"the bound {v!r} is not finite")
    return repr(float(v)) if isinstance(v, float) else str(v)


def ts_value(tagged: dict[str, object]) -> str:
    if tagged["t"] == "int":
        return f'{{ t: "int", v: {tagged["v"]}n }}'
    if tagged["t"] == "float":
        return f'{{ t: "float", v: {tagged["v"]} }}'
    return json.dumps(tagged)


def ts_spec(d: dict[str, object], plan: EndpointPlan) -> str:
    parts = [
        f"name: {json.dumps(d['name'])}",
        f'source: "{d["source"]}"',
        f'scalar: "{d["scalar"]}"',
    ]
    for key in ("ge", "le"):
        if key in d:
            parts.append(f"{key}: {js_number(plan, d[key])}")  # type: ignore[arg-type]
    for key in ("min_len", "max_len"):
        if key in d:
            parts.append(f"{key}: {d[key]}")
    if "one_of" in d:
        parts.append(f"one_of: {json.dumps(d['one_of'])}")
    if "default" in d:
        parts.append(f"default: {ts_value(d['default'])}")  # type: ignore[arg-type]
    for key in ("optional", "nullable", "email"):
        if d.get(key):
            parts.append(f"{key}: true")
    if "pattern" in d:
        try:
            js = fullmatch_source(d["pattern"])  # type: ignore[arg-type]
        except Untranslatable as err:
            raise TsGenRefusal(plan, f"pattern {d['pattern']!r}: {err}") from None
        parts.append(f"pattern: {{ py: {json.dumps(d['pattern'])}, js: {json.dumps(js)} }}")
    return "{ " + ", ".join(parts) + " }"


def int_is_number(d: dict[str, object]) -> bool:
    ge, le = d.get("ge"), d.get("le")
    return (
        isinstance(ge, int | float) and isinstance(le, int | float) and -SAFE <= ge and le <= SAFE
    )


def param_field(d: dict[str, object]) -> str:
    t = "number" if d["scalar"] == "Int" and int_is_number(d) else TS_SCALAR[d["scalar"]]  # type: ignore[index]
    name = d["name"]
    if "default" not in d:
        return f"  {name}: {t};"
    nullable = d["default"] == {"t": "none"} or bool(d.get("nullable"))
    return f"  {name}?: {t}{' | null' if nullable else ''};"


def row_type(schema: Schema) -> str:
    fields = [
        f"  {n}: {TS_SCALAR[s.value]}{' | null' if n in schema.nullable else ''};"
        for n, s in schema.columns
    ]
    return "{\n" + "\n".join(fields) + "\n}"


def cols_literal(schema: Schema) -> str:
    cols = (f'[{json.dumps(n)}, "{s.value}", {"true" if n in schema.nullable else "false"}]'
            for n, s in schema.columns)  # fmt: skip
    return "[" + ", ".join(cols) + "]"


def page_shape(plan: EndpointPlan) -> list[tuple[str, str]]:
    total = plan.page is not None and plan.page.total
    return [(k, f) for k, f in plan.page_shape if f and (k != "total" or total)]


def _response(plan: EndpointPlan, row: str) -> tuple[str, str]:
    """(the response type, the decode function)."""
    if plan.success not in SUCCESS:
        raise TsGenRefusal(plan, f"success kind {plan.success!r}")
    if plan.success == "NoContent":
        return "void", "() => undefined"
    cols = cols_literal(plan.schema)
    if plan.success == "Page":
        shape = page_shape(plan)
        fields = [f"{f}: {row}[]" if k == "items" else f"{f}: number" for k, f in shape]
        shape_ts = "[" + ", ".join(f"[{json.dumps(k)}, {json.dumps(f)}]" for k, f in shape) + "]"
        return "{ " + "; ".join(fields) + " }", f"(v) => rt.decodePage(v, {cols}, {shape_ts})"
    if plan.success == LIST_KIND:
        return f"{row}[]", f"(v) => rt.decodeList(v, {cols})"
    return row, f"(v) => rt.decodeRow(v, {cols})"


def _endpoint(plan: EndpointPlan, models: dict[str, str]) -> str:
    specs = plan_specs(plan)
    name, fn = pascal(plan.name), camel(plan.name)
    if plan.response is not None:
        models.setdefault(plan.response, row_type(plan.schema))
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
    fields = "\n".join(param_field(d) for d in specs)
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


def hooks_ts(plans: Sequence[EndpointPlan]) -> str:
    return "// hooks.ts: generated by Task 7\nexport {};\n"
