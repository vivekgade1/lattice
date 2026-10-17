"""Tier 2 from forms (Spike H, D5): spec-level contradictions decided logically.

Two forms *contradict* when no app can pass both forms' generated checks. One decision
procedure per pair class, pure functions of ``(spec, forms)``; no model, app or Docker:

* **a** two bounds on one field (interval, finite-set and ``ne`` algebra; on INT ``gt a`` is
  ``ge a+1``), and a param bound whose accepted request stores a value the column's bound
  forbids;
* **b** two row limits on one ``(table, by, where)`` with different ``max``;
* **c** two ownership forms on one table and endpoint whose scope differs in who may act, or
  whose refused answers differ;
* **d** two transitions on one machine and trigger that disagree on a state; **d'** a
  transition edge the spec's ``Machine`` does not have;
* **e** two generated steps (or a generated step and a hand-written one) with canonically
  equal given, prefix and request and an incompatible expectation (D's D4a identity).

Every item carries a **witness** (a concrete value, state or request) and is emitted only if
a self-check on the witness agrees: *no witness, no claim*. The decision and the witness
are computed independently, so a wrong decision procedure cannot emit a false item.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lattice.spec import forms as F
from lattice.spec.impact_tier2 import canon_given, canon_request, incompatible
from lattice.spec.model import Await, Spec, Step, resolve_table
from lattice.typesys import Scalar


@dataclass(frozen=True)
class VSet:
    """The values a Cmp/In condition accepts: an interval (``lo``/``hi`` with inclusivity),
    or a finite set, minus the ``ne`` values."""

    lo: float | None = None
    lo_in: bool = True
    hi: float | None = None
    hi_in: bool = True
    fin: frozenset[Any] | None = None
    ne: frozenset[Any] = frozenset()
    integer: bool = False


def _num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def vset(cond: Mapping[str, Any], scalar: Scalar | None) -> VSet | None:
    """The accepted set of a value condition (None: not decided here, e.g. text ordering)."""
    node = cond["node"]
    if node == "In":
        return VSet(fin=frozenset(cond["values"]))
    if node != "Cmp":
        return None
    op, v = cond["op"], cond["value"]
    if op == "eq":
        return VSet(fin=frozenset([v]))
    if op == "ne":
        return VSet(ne=frozenset([v]))
    if not _num(v):
        return None
    integer = scalar == Scalar.INT
    if integer:
        if op == "gt":
            op, v = "ge", v + 1
        elif op == "lt":
            op, v = "le", v - 1
    if op == "ge":
        return VSet(lo=v, lo_in=True, integer=integer)
    if op == "gt":
        return VSet(lo=v, lo_in=False, integer=integer)
    if op == "le":
        return VSet(hi=v, hi_in=True, integer=integer)
    return VSet(hi=v, hi_in=False, integer=integer)


def lset(cond: Mapping[str, Any]) -> VSet:
    """A length condition as an integer interval."""
    n = cond["value"]
    if cond["op"] == "min_len":
        return VSet(lo=n, lo_in=True, integer=True)
    return VSet(lo=0, hi=n, hi_in=True, integer=True)


def _in_interval(v: Any, s: VSet) -> bool:
    if s.lo is not None:
        if not _num(v) or v < s.lo or (v == s.lo and not s.lo_in):
            return False
    if s.hi is not None:
        if not _num(v) or v > s.hi or (v == s.hi and not s.hi_in):
            return False
    return True


def _member(v: Any, s: VSet) -> bool:
    if s.fin is not None and v not in s.fin:
        return False
    return v not in s.ne and _in_interval(v, s)


def empty_intersection(a: VSet, b: VSet) -> bool:
    """Whether no value is in both sets (the decision procedure of class a)."""
    fin: frozenset[Any] | None = None
    for s in (a, b):
        if s.fin is not None:
            fin = s.fin if fin is None else fin & s.fin
    if fin is not None:
        return not any(_member(v, a) and _member(v, b) for v in fin)
    ne = a.ne | b.ne
    lo, lo_in, hi, hi_in = None, True, None, True
    for s in (a, b):
        if s.lo is not None and (lo is None or s.lo > lo or (s.lo == lo and not s.lo_in)):
            lo, lo_in = s.lo, s.lo_in
        if s.hi is not None and (hi is None or s.hi < hi or (s.hi == hi and not s.hi_in)):
            hi, hi_in = s.hi, s.hi_in
    if lo is None or hi is None:
        return False
    if lo > hi:
        return True
    if lo == hi:
        return not (lo_in and hi_in and lo not in ne)
    if (a.integer or b.integer) and lo_in and hi_in and hi - lo <= len(ne):
        # closed integer endpoints (gt/lt were tightened by `vset`): every integer excluded?
        return all(v in ne for v in range(int(lo), int(hi) + 1))
    return False


def _candidates(cond: Mapping[str, Any], scalar: Scalar | None) -> list[Any]:
    at, beyond = F.bound_values(cond, scalar)
    return [v for v in (at, beyond) if v is not None]


def _witness_value(
    ca: Mapping[str, Any], cb: Mapping[str, Any], sa: Scalar | None
) -> tuple[Any, str] | None:
    """A value one condition holds and the other fails (so the pair cannot both hold), found by
    evaluation, independent of the decision procedure. ``(value, 'a'|'b' holds)``."""
    for v in _candidates(ca, sa):
        if F.cond_holds(ca, v) and not F.cond_holds(cb, v):
            return v, "a"
    for v in _candidates(cb, sa):
        if F.cond_holds(cb, v) and not F.cond_holds(ca, v):
            return v, "b"
    return None


def _field_key(spec: Spec, form: Mapping[str, Any]) -> tuple[Any, ...] | None:
    fld = form["field"]
    if "column" in fld:
        t, c = fld["column"].split(".")
        found = resolve_table(spec.profile, t)
        return ("col", found[0] if found else t, c)
    ep = fld["param"]
    return (
        "param",
        ep.partition(" ")[0].upper(),
        F.norm_template(ep.partition(" ")[2]),
        fld["path"],
    )


def _scalar_of(spec: Spec, form: Mapping[str, Any]) -> Scalar | None:
    """The field's scalar: its column's, its ``stores`` column's, or its contract param's."""
    fld = form["field"]
    ref = fld.get("column") or form.get("stores")
    if ref:
        found = resolve_table(spec.profile, ref.split(".")[0])
        if found is not None:
            return dict(found[2].columns).get(ref.split(".")[1])
    if "param" in fld:
        method, _, tpl = fld["param"].partition(" ")
        src, _, name = fld["path"].partition(".")
        for c in spec.contracts:
            if c.method.upper() == method.upper() and F.norm_template(c.path) == F.norm_template(
                tpl
            ):
                for p in c.params:
                    if p.name == name and p.source == src:
                        return p.scalar
    return None


def _is_len(cond: Mapping[str, Any]) -> bool:
    return cond["node"] == "Len"


def _item(
    cls: str, fa: str, fb: str | None, rules: Sequence[str], witness: Any, why: str
) -> dict[str, Any]:
    forms = [fa] if fb is None else [fa, fb]
    return {
        "id": f"fc:{cls}:{':'.join(forms)}",
        "kind": "form_contradiction",
        "forms": forms,
        "rules": sorted(
            {r for r in rules}, key=lambda r: (int(r[1:]) if r[1:].isdigit() else 0, r)
        ),
        "class": cls,
        "witness": witness,
        "why": why,
        "claim": f"forms {' and '.join(forms)}: {why}",
        "status": "determined",
    }


# ---- class a ---------------------------------------------------------------------------------


def _class_a(spec: Spec, fa: F.Form, ra: str, fb: F.Form, rb: str) -> list[dict[str, Any]]:
    a, b = fa.form, fb.form
    ka, kb = _field_key(spec, a), _field_key(spec, b)
    out: list[dict[str, Any]] = []
    if ka == kb:
        ca, cb = a["cond"], b["cond"]
        if _is_len(ca) != _is_len(cb):
            return out
        scalar = _scalar_of(spec, a) or _scalar_of(spec, b)
        if _is_len(ca):
            sa, sb = lset(ca), lset(cb)
        else:
            va, vb = vset(ca, scalar), vset(cb, scalar)
            if va is None or vb is None:
                return out
            sa, sb = va, vb
        if empty_intersection(sa, sb):
            w = _witness_value(ca, cb, scalar)
            if w is not None:
                value, holder = w
                who = (fa.id, fb.id) if holder == "a" else (fb.id, fa.id)
                out.append(
                    _item(
                        "a",
                        fa.id,
                        fb.id,
                        [ra, rb],
                        {"value": value, "holds": who[0], "fails": who[1]},
                        f"no value satisfies both: {value!r} satisfies {who[0]} and fails {who[1]}",
                    )  # fmt: skip
                )
        return out
    return out


def _class_a_stores(spec: Spec, forms: Sequence[tuple[F.Form, str]]) -> list[dict[str, Any]]:
    """A param bound whose accepted request stores a value the column's bound forbids."""
    out: list[dict[str, Any]] = []
    cols = [
        (f, r) for f, r in forms
        if f.kind == "bound" and "column" in f.form["field"] and not _is_len(f.form["cond"])
    ]  # fmt: skip
    for p, rp in forms:
        if p.kind != "bound" or "param" not in p.form["field"] or not p.form.get("stores"):
            continue
        acc = (p.form.get("accepted") or {}).get("db")
        stores = p.form["stores"]
        sc = stores.split(".")[1]
        if not (isinstance(acc, dict) and sc in (acc.get("equals") or {})) or _is_len(
            p.form["cond"]
        ):
            continue
        t = (resolve_table(spec.profile, stores.split(".")[0]) or (stores.split(".")[0],))[0]
        for c, rc in cols:
            kc = _field_key(spec, c.form)
            if kc != ("col", t, sc):
                continue
            scalar = _scalar_of(spec, c.form)
            vp, vc = vset(p.form["cond"], scalar), vset(c.form["cond"], scalar)
            if vp is None or vc is None:
                continue
            if _subset_fails(vp, vc):
                for v in _candidates(p.form["cond"], scalar):
                    if F.cond_holds(p.form["cond"], v) and not F.cond_holds(c.form["cond"], v):
                        out.append(
                            _item(
                                "a",
                                p.id,
                                c.id,
                                [rp, rc],
                                {"value": v, "accepted_by": p.id, "forbidden_by": c.id},
                                f"{p.id} accepts {v!r} and stores it, {c.id} forbids it",
                            )  # fmt: skip
                        )
                        break
    return out


def _subset_fails(p: VSet, c: VSet) -> bool:
    """Whether P has a value outside C (conservative: True only when decided)."""
    if p.fin is not None:
        return any(not _member(v, c) and _member(v, p) for v in p.fin)
    if c.fin is not None:
        return True
    if c.lo is not None and (
        p.lo is None or p.lo < c.lo or (p.lo == c.lo and p.lo_in and not c.lo_in)
    ):
        return True
    if c.hi is not None and (
        p.hi is None or p.hi > c.hi or (p.hi == c.hi and p.hi_in and not c.hi_in)
    ):
        return True
    return bool(c.ne and not p.ne)


# ---- class b ---------------------------------------------------------------------------------


def _class_b(spec: Spec, fa: F.Form, ra: str, fb: F.Form, rb: str) -> list[dict[str, Any]]:
    a, b = fa.form, fb.form
    ta, tb = resolve_table(spec.profile, a["table"]), resolve_table(spec.profile, b["table"])
    if ta is None or tb is None or ta[0] != tb[0]:
        return []
    if sorted(a["by"]) != sorted(b["by"]) or F.canon(a.get("where") or {}) != F.canon(
        b.get("where") or {}
    ):
        return []
    if a["max"] == b["max"]:
        return []
    small, large = sorted((a["max"], b["max"]))
    # the witness: a state with `small` counted rows; the smaller limit refuses one more row,
    # the larger accepts it (small <= large - 1)
    if not (small >= small and small <= large - 1):
        return []
    return [
        _item(
            "b",
            fa.id,
            fb.id,
            [ra, rb],
            {"counted_rows": small},
            f"with {small} counted rows the limit {small} refuses and the limit {large} accepts",
        )  # fmt: skip
    ]


# ---- class c ---------------------------------------------------------------------------------


def _allowed(scope: str, caller: str, write: bool) -> bool:
    form, _col = _scope(scope)
    if caller == "admin":
        return form in ("owner_or_admin", "any")
    # a signed-in non-owner
    if form == "any":
        return True
    if form == "owner_or_public":
        return not write
    return False


def _scope(scope: str) -> tuple[str | None, str | None]:
    from lattice.spec.model import scope_parts

    return scope_parts(scope)


def _class_c(spec: Spec, fa: F.Form, ra: str, fb: F.Form, rb: str) -> list[dict[str, Any]]:
    a, b = fa.form, fb.form
    ta, tb = resolve_table(spec.profile, a["table"]), resolve_table(spec.profile, b["table"])
    if ta is None or tb is None or ta[0] != tb[0]:
        return []

    def key(ep: str) -> str:
        m, _, p = ep.strip().partition(" ")
        return f"{m.upper()} {F.norm_template(p)}"

    shared = sorted({key(e) for e in a["endpoints"]} & {key(e) for e in b["endpoints"]})
    if not shared:
        return []
    out: list[dict[str, Any]] = []
    if a["scope"] != b["scope"]:
        for ep in shared:
            write = ep.partition(" ")[0] not in F.READ_METHODS
            for caller in ("other", "admin"):
                if _allowed(a["scope"], caller, write) != _allowed(b["scope"], caller, write):
                    out.append(
                        _item(
                            "c",
                            fa.id,
                            fb.id,
                            [ra, rb],
                            {
                                "endpoint": ep,
                                "caller": caller,
                                "write": write,
                                fa.id: _allowed(a["scope"], caller, write),
                                fb.id: _allowed(b["scope"], caller, write),
                            },
                            f"on {ep} a {caller} caller may act under {a['scope']!r} but not "
                            f"{b['scope']!r}"
                            if _allowed(a["scope"], caller, write)
                            else f"on {ep} a {caller} caller may act under {b['scope']!r} but not "
                            f"{a['scope']!r}",
                        )  # fmt: skip
                    )
                    return out
    ga, gb = F.generate(spec, [fa]), F.generate(spec, [fb])
    sa = {s.name.split(":", 2)[2]: s for s in ga.scenarios}
    sb = {s.name.split(":", 2)[2]: s for s in gb.scenarios}
    for ep in shared:
        slug_a = next((F._slug(e) for e in a["endpoints"] if key(e) == ep), "")  # noqa: SLF001
        slug_b = next((F._slug(e) for e in b["endpoints"] if key(e) == ep), "")  # noqa: SLF001
        for case in ("other", "anon"):
            x, y = sa.get(f"{case}_{slug_a}"), sb.get(f"{case}_{slug_b}")
            if x is None or y is None:
                continue
            sx, sy = x.steps[0], y.steps[0]
            assert isinstance(sx, Step) and isinstance(sy, Step)
            if sx.expect_status != sy.expect_status:
                out.append(
                    _item(
                        "c",
                        fa.id,
                        fb.id,
                        [ra, rb],
                        {
                            "endpoint": ep,
                            "caller": case,
                            fa.id: sx.expect_status,
                            fb.id: sy.expect_status,
                        },
                        f"{case} on {ep} is refused with {sx.expect_status} by {fa.id} and "
                        f"{sy.expect_status} by {fb.id}",
                    )  # fmt: skip
                )
                return out
    return out


# ---- class d ---------------------------------------------------------------------------------


def _machine_key(spec: Spec, form: Mapping[str, Any]) -> tuple[str, str]:
    t, c = form["machine"].split(".")
    found = resolve_table(spec.profile, t)
    return (found[0] if found else t, c)


def _trigger_key(form: Mapping[str, Any]) -> str:
    m, _, p = form["trigger"].strip().partition(" ")
    return f"{m.upper()} {F.norm_template(p)}"


def _expect_for(form: Mapping[str, Any], state: str) -> dict[str, Any]:
    t, c = form["machine"].split(".")
    if state in form["from"]:
        status = form["accepted"]["status"]
        final = form["to"] if form["to"] is not None else state
    else:
        status, final = form["refused"]["status"], state
    return {"status": status, "body": None, "db": {t: {"equals": {c: final}}}}


def _class_d(spec: Spec, fa: F.Form, ra: str, fb: F.Form, rb: str) -> list[dict[str, Any]]:
    a, b = fa.form, fb.form
    if _machine_key(spec, a) != _machine_key(spec, b) or _trigger_key(a) != _trigger_key(b):
        return []
    machine = next(
        (m for m in spec.machines
         if (resolve_table(spec.profile, m.table) or ("",))[0] == _machine_key(spec, a)[0]
         and m.column == _machine_key(spec, a)[1]),
        None,
    )  # fmt: skip
    if machine is None:
        return []
    for s in sorted(set(machine.states())):
        if s not in a["from"] and s not in b["from"]:
            continue
        ea, eb = _expect_for(a, s), _expect_for(b, s)
        why = incompatible(ea, eb, spec)
        if why is not None:
            return [
                _item(
                    "d",
                    fa.id,
                    fb.id,
                    [ra, rb],
                    {"state": s, fa.id: ea["status"], fb.id: eb["status"], "incompatible": why},
                    f"in state {s!r} {fa.id} and {fb.id} expect incompatible answers ({why})",
                )  # fmt: skip
            ]
    return []


def _class_d_prime(spec: Spec, f: F.Form, rule: str) -> list[dict[str, Any]]:
    form = f.form
    machine = next(
        (m for m in spec.machines
         if (resolve_table(spec.profile, m.table) or ("",))[0] == _machine_key(spec, form)[0]
         and m.column == _machine_key(spec, form)[1]),
        None,
    )  # fmt: skip
    if machine is None or form["to"] is None:
        return []
    edges = set(machine.edges)
    for s in sorted(form["from"]):
        if (s, form["to"]) not in edges:
            return [
                _item(
                    "d'",
                    f.id,
                    None,
                    [rule],
                    {"edge": [s, form["to"]]},
                    f"{f.id} moves {s!r} to {form['to']!r}; the machine has no such edge",
                )  # fmt: skip
            ]
    return []


# ---- class e ---------------------------------------------------------------------------------


def _steps_of(sc: Any) -> list[tuple[str, tuple[str, ...], Step, int]]:
    out = []
    prefix: list[str] = []
    for k, st in enumerate(sc.steps, 1):
        if isinstance(st, Await):
            break
        req = canon_request(st.method, st.path, st.user, st.json, st.params)
        out.append((canon_given(sc.given), tuple(prefix), st, k))
        prefix.append(req)
    return out


def _class_e(spec: Spec, forms: Sequence[tuple[F.Form, str]]) -> list[dict[str, Any]]:
    gens = {f.id: (F.generate(spec, [f]), r) for f, r in forms}
    entries: list[tuple[tuple[str, tuple[str, ...], str], str, str, Step, str]] = []
    for fid, (gen, rule) in gens.items():
        for sc in gen.scenarios:
            for given, prefix, st, _k in _steps_of(sc):
                req = canon_request(st.method, st.path, st.user, st.json, st.params)
                entries.append(((given, prefix, req), fid, rule, st, sc.name))
    hand: list[tuple[tuple[str, tuple[str, ...], str], str, Step]] = []
    for sc in spec.scenarios:
        if sc.name.startswith(F.RESERVED_PREFIX):
            continue
        for given, prefix, st, _k in _steps_of(sc):
            req = canon_request(st.method, st.path, st.user, st.json, st.params)
            hand.append(((given, prefix, req), sc.name, st))

    def expect(st: Step) -> dict[str, Any]:
        return {
            "status": st.expect_status,
            "body": st.expect_body,
            "db": dict(st.expect_db) if st.expect_db else None,
        }

    out: list[dict[str, Any]] = []
    for i, (ka, fa, ra, sa, na) in enumerate(entries):
        for kb, fb, rb, sb, nb in entries[i + 1 :]:
            if fa == fb or ka != kb:
                continue
            why = incompatible(expect(sa), expect(sb), spec)
            if why:
                x, y = sorted((fa, fb), key=lambda s: (F._fnum(s), s))  # noqa: SLF001
                out.append(
                    _item(
                        "e",
                        x,
                        y,
                        [ra, rb],
                        {"request": [sa.method, sa.path], "cases": [na, nb], "incompatible": why},
                        f"{na} and {nb} send the same request after the same state and expect "
                        f"incompatible answers ({why})",
                    )  # fmt: skip
                )
        for kh, name, sh in hand:
            if ka != kh:
                continue
            why = incompatible(expect(sa), expect(sh), spec)
            if why:
                out.append(
                    _item(
                        "e",
                        fa,
                        f"scenario:{name}",
                        [ra],
                        {
                            "request": [sa.method, sa.path],
                            "cases": [na, f"scenario:{name}"],
                            "incompatible": why,
                        },
                        f"{na} and the hand-written scenario {name!r} send the same request "
                        f"after the same state and expect incompatible answers ({why})",
                    )  # fmt: skip
                )
    return out


# ---- entry point -----------------------------------------------------------------------------


def contradictions(spec: Spec, forms: Sequence[F.Form | F.Resolved]) -> list[dict[str, Any]]:
    """The contradictions among ``forms`` (those that may generate), and between a form and a
    hand-written scenario, sorted by id. Pure."""
    items: list[tuple[F.Form, str]] = []
    for x in forms:
        items.append((x.form, x.rule) if isinstance(x, F.Resolved) else (x, x.rule))
    items.sort(key=lambda p: (F._fnum(p[0].id), p[0].id))  # noqa: SLF001
    found: dict[str, dict[str, Any]] = {}

    def add(new: Sequence[dict[str, Any]]) -> None:
        for it in new:
            found.setdefault(it["id"], it)

    for (fa, ra), (fb, rb) in itertools.combinations(items, 2):
        if fa.kind == fb.kind == "bound":
            add(_class_a(spec, fa, ra, fb, rb))
        elif fa.kind == fb.kind == "row_limit":
            add(_class_b(spec, fa, ra, fb, rb))
        elif fa.kind == fb.kind == "ownership":
            add(_class_c(spec, fa, ra, fb, rb))
        elif fa.kind == fb.kind == "transition":
            add(_class_d(spec, fa, ra, fb, rb))
    add(_class_a_stores(spec, items))
    for f, r in items:
        if f.kind == "transition":
            add(_class_d_prime(spec, f, r))
    add(_class_e(spec, items))
    return [found[k] for k in sorted(found)]
