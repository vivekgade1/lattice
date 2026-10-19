"""Truth /5, code names, links in the truth replay, the global ledger and the layout rule of gRPC
round 5 (docs/results/2026-10-18-grpc-round5-prereg.md, section 2: H2, H3, H4, H5).

One module, used byte for byte by the contract check (``grpc_contract5.py``), the runner
(``grpc_probes5.py``) and, for the numbers it is handed, the scorer (``grpc_scorer5.py``):

* **H2** :func:`normalise_code`: one fixed 17-row table; anything else is a :class:`TruthError`
  (``unknown gRPC code <x> at <json path>``). :func:`load_truth` applies it to every code the chain
  reads and records ``codes_normalised``.
* **H3** :func:`replay_flow`: one truth flow on a fresh database, one process, ``links`` applied
  (canonical ``{from_step, from_field, to_step, to_field}`` in ``conventions.step_index_base``;
  deskd's ``{step, field, from_step, from_field}`` 1-based accepted as an alias), dotted ``expect``
  paths checked, ``link_unresolved`` rather than an empty field.
* **H4** :class:`Ledger`: one append-only, hash-chained JSONL file per round at a path fixed in the
  runner's bytes.
* **H5** :func:`check_layout`: the app directory itself, ``freeze.sha256`` at its root checked with
  ``sha256sum --check`` semantics, no symlink anywhere in the tree or at the app directory.

No model, no network, no write outside a scratch directory (the replay's database) and the ledger.
"""

from __future__ import annotations

# ruff: noqa: E501 -- docstrings and data lines
import contextlib
import copy
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

SCHEMA_5 = "lattice.heldout.truth/5"
SCHEMA_4 = "lattice.heldout.truth/4"
LEDGER_REL = "docs/results/data/2026-10-18-grpc-round5/ledger.jsonl"
GENESIS = "0" * 64
STATES = ("implemented", "unimplemented", "not_registered")
DDL_KINDS = ("go_literal", "go_embed", "harness", "migration_tool", "other")
MISSING = object()


class TruthError(Exception):
    """A truth document (or one code in it) the chain refuses."""


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- H2: the code-name table -------------------------------------------------------------------

# (number, google.rpc.Code UPPER_SNAKE name, grpc-go codes.Code String() name)
CODE_ROWS: tuple[tuple[int, str, str], ...] = (
    (0, "OK", "OK"),
    (1, "CANCELLED", "Canceled"),
    (2, "UNKNOWN", "Unknown"),
    (3, "INVALID_ARGUMENT", "InvalidArgument"),
    (4, "DEADLINE_EXCEEDED", "DeadlineExceeded"),
    (5, "NOT_FOUND", "NotFound"),
    (6, "ALREADY_EXISTS", "AlreadyExists"),
    (7, "PERMISSION_DENIED", "PermissionDenied"),
    (8, "RESOURCE_EXHAUSTED", "ResourceExhausted"),
    (9, "FAILED_PRECONDITION", "FailedPrecondition"),
    (10, "ABORTED", "Aborted"),
    (11, "OUT_OF_RANGE", "OutOfRange"),
    (12, "UNIMPLEMENTED", "Unimplemented"),
    (13, "INTERNAL", "Internal"),
    (14, "UNAVAILABLE", "Unavailable"),
    (15, "DATA_LOSS", "DataLoss"),
    (16, "UNAUTHENTICATED", "Unauthenticated"),
)
_BY_NUM = {n: up for n, up, _ in CODE_ROWS}
_BY_GO = {go: up for _, up, go in CODE_ROWS}
_BY_UPPER = {**{up: up for _, up, _ in CODE_ROWS}, "CANCELED": "CANCELLED"}  # the two spellings


def normalise_code(value: Any, at: str = "?") -> str:
    """The ``google.rpc.Code`` UPPER_SNAKE name of ``value``: an UPPER_SNAKE name (any case), a
    grpc-go ``String()`` name (exact case), ``CANCELED`` (American, maps to ``CANCELLED``), an integer
    0-16 or its decimal string. Anything else raises :class:`TruthError` naming ``at``."""
    name = None
    if isinstance(value, bool):
        name = None
    elif isinstance(value, int):
        name = _BY_NUM.get(value)
    elif isinstance(value, str):
        if value.isascii() and value.isdigit() and value == str(int(value)):
            name = _BY_NUM.get(int(value))
        elif value in _BY_GO:
            name = _BY_GO[value]
        else:
            name = _BY_UPPER.get(value.upper()) if value.isascii() else None
    if name is None:
        raise TruthError(f"unknown gRPC code {value!r} at {at}")
    return name


def _walk_codes(raw: Any, visit: Callable[[Mapping[str, Any] | list[Any], Any, str], None]) -> None:
    """Calls ``visit(container, key, json_path)`` on every code the chain reads (tolerant of a
    malformed document: a shape it does not recognise is skipped, the schema check names it)."""
    if not isinstance(raw, Mapping):
        return
    rpcs = raw.get("rpcs")
    items = (
        rpcs.items()
        if isinstance(rpcs, Mapping)
        else enumerate(rpcs if isinstance(rpcs, list) else [])
    )
    for i, r in items:
        if not isinstance(r, dict):
            continue
        base = f"$.rpcs[{i}]" if isinstance(i, int) else f"$.rpcs[{i!r}]"
        for key in ("codes", "interceptor_codes"):
            if isinstance(r.get(key), list):
                for j in range(len(r[key])):
                    visit(r[key], j, f"{base}.{key}[{j}]")
        if isinstance(r.get("code_sites"), list):
            for j, site in enumerate(r["code_sites"]):
                if isinstance(site, dict) and "code" in site:
                    visit(site, "code", f"{base}.code_sites[{j}].code")
    flows = raw.get("flows")
    for fi, f in enumerate(flows if isinstance(flows, list) else []):
        for si, st in enumerate(f.get("steps") or [] if isinstance(f, dict) else []):
            if not isinstance(st, dict):
                continue
            if isinstance(st.get("expect"), dict) and "code" in st["expect"]:
                visit(st["expect"], "code", f"$.flows[{fi}].steps[{si}].expect.code")
            if "expect_code" in st:
                visit(st, "expect_code", f"$.flows[{fi}].steps[{si}].expect_code")


def normalise_codes_in(raw: Any) -> tuple[Any, int]:
    """A copy of the truth document with every code normalised, and how many codes changed spelling.
    Raises :class:`TruthError` on the first code H2 refuses."""
    out = copy.deepcopy(raw)
    changed = [0]

    def visit(container: Any, key: Any, path: str) -> None:
        new = normalise_code(container[key], path)
        changed[0] += new != container[key]
        container[key] = new

    _walk_codes(out, visit)
    return out, changed[0]


# ---- the truth document -------------------------------------------------------------------------


def _is_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def schema_errors(raw: Any, *, strict: bool = True) -> list[str]:
    """Every problem of the document against Appendix B (``strict``: truth /5 as written;
    otherwise truth /4 and the dev corpora's looser forms are read). The first is what the
    contract check prints."""
    if not isinstance(raw, Mapping):
        return ["the document is not a JSON object"]
    errs: list[str] = []
    schema = raw.get("schema")
    if strict and schema != SCHEMA_5:
        errs.append(f'schema must be "{SCHEMA_5}" (got {schema!r})')
    elif not strict and schema not in (SCHEMA_5, SCHEMA_4):
        errs.append(f'schema must be "{SCHEMA_5}" or "{SCHEMA_4}" (got {schema!r})')
    conv = raw.get("conventions")
    base = conv.get("step_index_base") if isinstance(conv, Mapping) else None
    if base not in (0, 1) or isinstance(base, bool):
        errs.append("conventions.step_index_base is required and must be 0 or 1")
    rpcs = raw.get("rpcs")
    names: set[str] = set()
    if not isinstance(rpcs, list) or not rpcs:
        errs.append("rpcs must be a non-empty list")
        rpcs = []
    for i, r in enumerate(rpcs):
        at = f"rpcs[{i}]"
        if not isinstance(r, Mapping):
            errs.append(f"{at} is not an object")
            continue
        for key in ("rpc", "service", "method"):
            if not _is_str(r.get(key)):
                errs.append(f"{at}.{key} must be a non-empty string")
        if _is_str(r.get("rpc")):
            names.add(r["rpc"])
            if "/" not in r["rpc"] or (
                _is_str(r.get("service"))
                and _is_str(r.get("method"))
                and r["rpc"] != f"{r['service']}/{r['method']}"
            ):
                errs.append(f"{at}.rpc must be <service>/<method>")
        ok_states = (*STATES, "streaming") if not strict else STATES
        if r.get("state") not in ok_states:
            errs.append(f"{at}.state must be one of {'|'.join(ok_states)} (got {r.get('state')!r})")
        if not isinstance(r.get("streaming"), bool):
            errs.append(f"{at}.streaming must be true or false")
        if not _is_str(r.get("auth")):
            errs.append(f"{at}.auth must be Public, Member or a role name")
        if not isinstance(r.get("codes"), list):
            errs.append(f"{at}.codes must be a list")
        for key in ("code_sites", "interceptor_codes", "tables", "writes"):
            if key in r and not isinstance(r[key], list):
                errs.append(f"{at}.{key} must be a list")
    login = raw.get("login")
    if not isinstance(login, Mapping) or any(
        not _is_str(login.get(k))
        for k in ("service", "method", "username_field", "password_field", "token_field")
    ):
        errs.append("login must have service, method, username_field, password_field, token_field")
    users = raw.get("users")
    handles: set[str] = set()
    if not isinstance(users, list) or not users:
        errs.append("users must be a non-empty list of {handle, ...}")
    else:
        for i, u in enumerate(users):
            if not isinstance(u, Mapping) or not _is_str(u.get("handle")):
                errs.append(f"users[{i}] needs a handle")
            else:
                handles.add(u["handle"])
    tables = raw.get("tables")
    if not isinstance(tables, list) or not tables:
        errs.append("tables must be a non-empty list")
        tables = []
    for i, t in enumerate(tables):
        if not isinstance(t, Mapping) or not _is_str(t.get("name")):
            errs.append(f"tables[{i}] needs a name")
            continue
        if strict and t.get("ddl") not in DDL_KINDS:
            errs.append(
                f"tables[{i}].ddl must be one of {'|'.join(DDL_KINDS)} (got {t.get('ddl')!r})"
            )
        for key in ("read_by", "written_by"):
            if key in t and not isinstance(t[key], list):
                errs.append(f"tables[{i}].{key} must be a list")
    flows = raw.get("flows")
    if not isinstance(flows, list) or not flows:
        errs.append("flows must be a non-empty list")
        flows = []
    seen: set[str] = set()
    for fi, f in enumerate(flows):
        at = f"flows[{fi}]"
        if not isinstance(f, Mapping) or not _is_str(f.get("name")):
            errs.append(f"{at} needs a name")
            continue
        if f["name"] in seen:
            errs.append(f"{at}: duplicate flow name {f['name']!r}")
        seen.add(f["name"])
        steps = f.get("steps")
        if not isinstance(steps, list) or not steps:
            errs.append(f"{at}.steps must be a non-empty list")
            continue
        for si, st in enumerate(steps):
            sat = f"{at}.steps[{si}]"
            if not isinstance(st, Mapping):
                errs.append(f"{sat} is not an object")
                continue
            if not _is_str(st.get("rpc")) or "/" not in st["rpc"]:
                errs.append(f"{sat}.rpc must be <service>/<method>")
            elif names and st["rpc"] not in names:
                errs.append(f"{sat}.rpc {st['rpc']!r} is not in rpcs")
            if "as" not in st or not (st["as"] is None or _is_str(st["as"])):
                errs.append(f"{sat}.as must be a handle or null")
            elif handles and st["as"] is not None and st["as"] not in handles:
                errs.append(f"{sat}.as {st['as']!r} is not in users")
            if not isinstance(st.get("request"), Mapping):
                errs.append(f"{sat}.request must be an object")
            ex = st.get("expect")
            if not isinstance(ex, Mapping) or "code" not in ex:
                errs.append(f"{sat}.expect must be an object with a code")
        links = f.get("links", [])
        if not isinstance(links, list):
            errs.append(f"{at}.links must be a list")
            continue
        for li, ln in enumerate(links):
            lat = f"{at}.links[{li}]"
            if not isinstance(ln, Mapping):
                errs.append(f"{lat} is not an object")
                continue
            if strict or "to_step" in ln:
                if any(k not in ln for k in ("from_step", "from_field", "to_step", "to_field")):
                    errs.append(f"{lat} needs from_step, from_field, to_step, to_field")
                    continue
            elif any(k not in ln for k in ("step", "field", "from_step", "from_field")):
                errs.append(f"{lat} needs from_step, from_field, to_step, to_field")
                continue
            if base in (0, 1) and isinstance(steps, list):
                try:
                    _canonical_link(ln, base, len(steps))
                except TruthError as err:
                    errs.append(f"{lat}: {err}")
    return errs


def _canonical_link(ln: Mapping[str, Any], base: int, n_steps: int) -> dict[str, Any]:
    """One link as ``{from_step, from_field, to_step, to_field}`` with 0-based indices. The alias
    form ``{step, field, from_step, from_field}`` is 1-based whatever the convention says."""
    if "to_step" in ln:
        frm, to, ffield, tfield, b = (
            ln["from_step"],
            ln["to_step"],
            ln["from_field"],
            ln["to_field"],
            base,
        )
    else:
        frm, to, ffield, tfield, b = ln["from_step"], ln["step"], ln["from_field"], ln["field"], 1
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (frm, to)):
        raise TruthError("link steps must be integers")
    frm, to = frm - b, to - b
    if not (0 <= frm < to < n_steps):
        raise TruthError(
            f"link from step {frm + b} to step {to + b} is outside the flow or not forward"
        )
    if not _is_str(ffield) or not _is_str(tfield):
        raise TruthError("link fields must be non-empty dotted paths")
    return {"from_step": frm, "from_field": ffield, "to_step": to, "to_field": tfield}


def canonical_links(
    links: Sequence[Mapping[str, Any]], base: int, n_steps: int
) -> list[dict[str, Any]]:
    return [_canonical_link(ln, base, n_steps) for ln in links]


def scorer_links(links: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Canonical (0-based) links in the flows scorer's form: ``{step, field, from_step, from_field}``,
    1-based."""
    return [
        {
            "step": x["to_step"] + 1,
            "field": x["to_field"],
            "from_step": x["from_step"] + 1,
            "from_field": x["from_field"],
        }
        for x in links
    ]


def load_truth(path: Path, *, strict: bool = True) -> dict[str, Any]:
    """``truth.json`` read under Appendix B and H2. ``strict`` (default): truth /5 as written.
    Not strict (the dev corpora: toolshed's /4): /4 or /5, the loose forms accepted. Raises
    :class:`TruthError` on the first problem. Returns the form the round's scorers read: ``rpcs``
    ``{full: {state, codes (a set, OK excluded), auth, auth_raw, effects, ...}}``, ``tables`` (a set
    of lower-case names), ``flows`` (``{name, steps: [{rpc, as, message, expect_code, expect}]}``),
    ``links`` (per flow, the scorer's 1-based form), ``flow_links`` (per flow, canonical 0-based)."""
    raw = json.loads(Path(path).read_text())
    errs = schema_errors(raw, strict=strict)
    if errs:
        raise TruthError(errs[0])
    doc, normalised = normalise_codes_in(raw)
    base = doc["conventions"]["step_index_base"]
    rpcs: dict[str, dict[str, Any]] = {}
    effects: dict[str, str] = {}
    for r in doc["rpcs"]:
        key = r["rpc"]
        rpcs[key] = {
            "state": "streaming" if r["streaming"] else r["state"],
            "streaming": r["streaming"],
            "codes": {c for c in r["codes"] if c != "OK"},
            "interceptor_codes": [c for c in r.get("interceptor_codes", []) if c != "OK"],
            "auth": "Public"
            if str(r["auth"]).lower() == "public"
            else ("Member" if str(r["auth"]).lower() == "member" else str(r["auth"])),
            "auth_raw": str(r["auth"]),
            "tables": list(r.get("tables", [])),
            "writes": list(r.get("writes", [])),
        }
        effects[key] = str(r.get("effects", "none") or "none")
    by_rpc: dict[str, set[str]] = {k: set() for k in rpcs}
    for t in doc["tables"]:
        for k in (*t.get("read_by", []), *t.get("written_by", [])):
            by_rpc.setdefault(k, set()).add(str(t["name"]).lower())
    flows: list[dict[str, Any]] = []
    flow_links: dict[str, list[dict[str, Any]]] = {}
    for f in doc["flows"]:
        steps = []
        for st in f["steps"]:
            ex = dict(st["expect"])
            code = ex.pop("code")
            steps.append(
                {
                    "rpc": st["rpc"],
                    "as": st["as"],
                    "message": st["request"],
                    "expect_code": code,
                    "expect": ex,
                }
            )
        flows.append({"name": f["name"], "steps": steps})
        flow_links[f["name"]] = canonical_links(f.get("links", []), base, len(steps))
    return {
        "schema": doc["schema"],
        "step_index_base": base,
        "codes_normalised": normalised,
        "rpcs": rpcs,
        "effects": effects,
        "tables": {str(t["name"]).lower() for t in doc["tables"]},
        "tables_detail": {
            str(t["name"]).lower(): {
                "ddl": t.get("ddl"),
                "read_by": t.get("read_by", []),
                "written_by": t.get("written_by", []),
            }
            for t in doc["tables"]
        },
        "tables_by_rpc": {k: sorted(v) for k, v in by_rpc.items()},
        "flows": flows,
        "links": {n: scorer_links(v) for n, v in flow_links.items()},
        "flow_links": flow_links,
        "login": doc["login"],
        "users": doc["users"],
        "keys": sorted(doc),
    }


# ---- H3: dotted paths, links, the replay -----------------------------------------------------------


def get_path(obj: Any, path: str) -> Any:
    """``obj`` at the dotted ``path`` (numeric segments index lists; a final ``#`` is a list
    length), or :data:`MISSING`."""
    parts = path.split(".")
    cur = obj
    for n, seg in enumerate(parts):
        if seg == "#" and n == len(parts) - 1:
            return len(cur) if isinstance(cur, (list, dict)) else MISSING
        if isinstance(cur, list) and seg.lstrip("-").isdigit():
            i = int(seg)
            cur = cur[i] if -len(cur) <= i < len(cur) else MISSING
        elif isinstance(cur, Mapping) and seg in cur:
            cur = cur[seg]
        else:
            return MISSING
        if cur is MISSING:
            return MISSING
    return cur


def set_path(obj: dict[str, Any], path: str, value: Any) -> None:
    """Sets the dotted ``path`` in ``obj``, creating objects (a numeric segment indexes an existing
    list)."""
    parts = path.split(".")
    cur: Any = obj
    for seg in parts[:-1]:
        if isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
            continue
        if not isinstance(cur.get(seg), (dict, list)):
            cur[seg] = {}
        cur = cur[seg]
    last = parts[-1]
    if isinstance(cur, list) and last.isdigit() and int(last) < len(cur):
        cur[int(last)] = value
    else:
        cur[last] = value


def values_equal(got: Any, want: Any) -> bool:
    """JSON equality, with an int64 accepted as a JSON number or its decimal string."""
    if isinstance(got, bool) or isinstance(want, bool):
        return isinstance(got, bool) and isinstance(want, bool) and got == want
    if isinstance(got, int) and isinstance(want, str) and re.fullmatch(r"-?\d+", want):
        return got == int(want)
    if isinstance(want, int) and isinstance(got, str) and re.fullmatch(r"-?\d+", got):
        return want == int(got)
    if isinstance(got, list) and isinstance(want, list):
        return len(got) == len(want) and all(
            values_equal(a, b) for a, b in zip(got, want, strict=True)
        )
    if isinstance(got, Mapping) and isinstance(want, Mapping):
        return set(got) == set(want) and all(values_equal(got[k], want[k]) for k in got)
    return bool(got == want)


def _unresolved(value: Any) -> bool:
    return value is MISSING or value is None or value == "" or value == [] or value == {}


@dataclass
class FlowResult:
    """One replayed flow: ``ok``, or the first failing step (0-based ``step``) and its reason
    class: ``code_mismatch``, ``expect_mismatch``, ``link_unresolved``, ``login_failed``,
    ``call_error`` or ``server_error``. ``steps``: the concrete steps (links substituted)."""

    name: str
    ok: bool
    step: int | None = None
    reason: str | None = None
    code: str | None = None
    expected: str | None = None
    detail: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)

    def label(self, base: int = 0) -> str:
        """``ok`` or ``step <i>: <reason class> (<code> vs <expected>)`` (``i`` in the truth's base)."""
        if self.ok:
            return "ok"
        i = (self.step or 0) + base
        return f"step {i}: {self.reason} ({self.code or '-'} vs {self.expected or '-'})"

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "step": self.step,
            "reason": self.reason,
            "code": self.code,
            "expected": self.expected,
            "detail": self.detail,
        }


class SqliteSource:
    """A fresh SQLite file in a fresh temporary directory per flow."""

    @contextlib.contextmanager
    def fresh(self) -> Iterator[Any]:
        with tempfile.TemporaryDirectory(prefix="truth5-") as tmp:
            yield Path(tmp) / "replay.db"


class ServerSource:
    """A fresh, empty database per flow on a database server (MySQL for PM-SG): ``build_db``
    receives its URL, as the checker's ``db_factory`` does."""

    def __init__(self, server: Any) -> None:
        self.server = server

    @contextlib.contextmanager
    def fresh(self) -> Iterator[Any]:
        name = f"t5_{secrets.token_hex(4)}"
        url = self.server.create(name)
        try:
            yield url
        finally:
            with contextlib.suppress(Exception):
                self.server.drop(name)


def to_rpc_steps(result: FlowResult) -> list[Any]:
    """The recorded concrete steps as library ``Rpc`` steps (for layer D)."""
    from lattice.spec import Rpc

    return [
        Rpc(s["service"], s["method"], s["user"], s["message"], expect_code=s["expect_code"])
        for s in result.steps
    ]


def replay_flow(
    flow: Mapping[str, Any],
    links: Sequence[Mapping[str, Any]],
    *,
    make_app: Callable[[Any], Any],
    build_db: Callable[[Any], Any],
    runtime: Any,
    source: Any = None,
) -> FlowResult:
    """Replays one truth flow on a fresh database built through the harness (``build_db`` answering
    ``None`` means the path it was given, ``check()``'s convention), one process, through the library's
    ``GrpcTransport``. ``links``: canonical 0-based (:func:`canonical_links`). The same function is
    used by the contract check, the runner and the scorer's inputs."""
    name = str(flow.get("name"))
    try:
        with (source or SqliteSource()).fresh() as target:
            ref = build_db(target)
            app = make_app(target if ref is None else ref)
            try:
                with runtime.open(app) as transport:
                    return _run_flow(name, flow, links, transport, runtime)
            finally:
                runtime.close(app)
    except Exception as err:  # noqa: BLE001 -- a server that does not start is a reason, not a crash
        return FlowResult(
            name, False, 0, "server_error", detail=f"{type(err).__name__}: {str(err)[:300]}"
        )


def _run_flow(
    name: str,
    flow: Mapping[str, Any],
    links: Sequence[Mapping[str, Any]],
    transport: Any,
    runtime: Any,
) -> FlowResult:
    from lattice.spec.grpc import bearer

    rpc = transport.rpc()
    tokens: dict[str, str] = {}
    bodies: list[Any] = []
    recorded: list[dict[str, Any]] = []
    for i, st in enumerate(flow["steps"]):
        want = normalise_code(
            st.get("expect_code", (st.get("expect") or {}).get("code", "OK")),
            f"flow {name} step {i}",
        )
        service, _, method = str(st["rpc"]).partition("/")
        msg = copy.deepcopy(st.get("message") or {})
        for ln in links:
            if ln["to_step"] != i:
                continue
            value = (
                get_path(bodies[ln["from_step"]], ln["from_field"])
                if ln["from_step"] < len(bodies)
                else MISSING
            )
            if _unresolved(value):
                return FlowResult(
                    name,
                    False,
                    i,
                    "link_unresolved",
                    expected=want,
                    detail=f"no value at step {ln['from_step']} field {ln['from_field']}",
                    steps=recorded,
                )
            set_path(msg, ln["to_field"], value)
        handle = st.get("as")
        token = None
        if handle is not None:
            try:
                if handle not in tokens:
                    tokens[handle] = runtime.auth.token(transport, handle, None)
                token = tokens[handle]
            except Exception as err:  # noqa: BLE001
                return FlowResult(
                    name,
                    False,
                    i,
                    "login_failed",
                    expected=want,
                    detail=f"{type(err).__name__}: {str(err)[:300]}",
                    steps=recorded,
                )
        try:
            answer = rpc.call(service, method, msg, bearer(token))
        except Exception as err:  # noqa: BLE001 -- a bridge or transport error (streaming, a dead server)
            return FlowResult(
                name,
                False,
                i,
                "call_error",
                expected=want,
                detail=f"{type(err).__name__}: {str(err)[:300]}",
                steps=recorded,
            )
        recorded.append(
            {
                "service": service,
                "method": method,
                "user": handle,
                "message": msg,
                "expect_code": want,
            }
        )
        bodies.append(answer.body)
        if answer.code != want:
            return FlowResult(
                name, False, i, "code_mismatch", answer.code, want, answer.message[:200], recorded
            )
        shown = answer.as_body()
        for path, expect in (st.get("expect") or {}).items():
            if path == "code":
                continue
            got = get_path(shown, path)
            if got is MISSING or not values_equal(got, expect):
                return FlowResult(
                    name,
                    False,
                    i,
                    "expect_mismatch",
                    answer.code,
                    want,
                    f"{path}: got {None if got is MISSING else got!r}, want {expect!r}",
                    recorded,
                )
    return FlowResult(name, True, steps=recorded)


# ---- H4: the global ledger -------------------------------------------------------------------------

LEDGER_FIELDS = (
    "seq",
    "utc",
    "arm",
    "runner_sha256",
    "scorer_sha256",
    "truth5_sha256",
    "target_freeze_sha256",
    "out",
    "status",
    "prev_sha256",
)
LEDGER_STATUSES = ("refused_preflight", "started", "crashed_before_metric", "scored")


def real_ledger_path(runner_file: str | Path) -> Path:
    """The round's ledger, resolved from the runner's own location (``<repo>/dogfood/<runner>``);
    no flag moves it."""
    return Path(runner_file).resolve().parent.parent / LEDGER_REL


def verify_chain(lines: Sequence[bytes | str]) -> list[str]:
    """The problems of a ledger given as its lines (empty: the chain verifies). Pure."""
    problems: list[str] = []
    prev = GENESIS
    for n, raw in enumerate(lines, 1):
        ln = raw if isinstance(raw, bytes) else raw.encode()
        try:
            e = json.loads(ln)
        except ValueError:
            return [*problems, f"line {n}: not JSON"]
        if not isinstance(e, dict) or any(k not in e for k in LEDGER_FIELDS):
            problems.append(f"line {n}: fields differ from the ledger schema")
            prev = sha256_of(ln)
            continue
        if e["seq"] != n:
            problems.append(f"line {n}: seq {e['seq']} (expected {n})")
        if e["prev_sha256"] != prev:
            problems.append(f"line {n}: prev_sha256 does not match the previous line")
        if e["status"] not in LEDGER_STATUSES:
            problems.append(f"line {n}: status {e['status']!r}")
        prev = sha256_of(ln)
    return problems


def runs_of(entries: Sequence[Mapping[str, Any]], arm: str, target: str) -> list[dict[str, Any]]:
    """The ``started`` lines of ``(arm, target)``, each with ``closed``: ``scored``,
    ``crashed_before_metric`` or ``open``. Pure."""
    out: list[dict[str, Any]] = []
    for e in entries:
        if e["arm"] != arm or e["target_freeze_sha256"] != target:
            continue
        if e["status"] == "started":
            out.append({**e, "closed": "open"})
        elif (
            e["status"] in ("scored", "crashed_before_metric")
            and out
            and out[-1]["closed"] == "open"
        ):
            out[-1]["closed"] = e["status"]
    return out


def next_run(runs: Sequence[Mapping[str, Any]]) -> tuple[int, dict[str, Any], str]:
    """``(index, prior, refusal)`` for the run that would start after ``runs`` (a third run, and a
    re-run after a scored run, are refused; a re-run after a crash carries the crashed run's runner
    hash as ``prior``). Pure."""
    if len(runs) >= 2:
        return (
            len(runs) + 1,
            {},
            "a third run is refused: one re-run is permitted, after a crash before any metric",
        )
    if runs and runs[-1]["closed"] == "scored":
        return (
            2,
            {},
            "a re-run is permitted only after a crash before any metric; the first run scored",
        )
    prior = {"crashed_before_metric": True, "runner": runs[-1]["runner_sha256"]} if runs else {}
    return len(runs) + 1, prior, ""


class Ledger:
    """One append-only JSONL file; every line carries ``prev_sha256`` = the sha256 of the previous
    line's bytes (the first: 64 zeros). The run index of a target (``arm`` + ``target_freeze_sha256``)
    is 1 + the earlier ``started`` lines of it, whatever ``out`` or runner hash."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def raw_lines(self) -> list[bytes]:
        if not self.path.is_file():
            return []
        return [ln for ln in self.path.read_bytes().split(b"\n") if ln.strip()]

    def entries(self) -> list[dict[str, Any]]:
        return [json.loads(ln) for ln in self.raw_lines()]

    def verify(self) -> list[str]:
        """The chain's problems (empty: it verifies)."""
        return verify_chain(self.raw_lines())

    def tail_sha256(self) -> str:
        lines = self.raw_lines()
        return sha256_of(lines[-1]) if lines else GENESIS

    def append(
        self,
        *,
        arm: str,
        status: str,
        runner_sha256: str = "",
        scorer_sha256: str = "",
        truth5_sha256: str = "",
        target_freeze_sha256: str = "",
        out: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Appends one line (after verifying the chain: a broken chain is never extended)."""
        problems = self.verify()
        if problems:
            raise TruthError(f"ledger chain broken: {problems[0]}")
        if status not in LEDGER_STATUSES:
            raise TruthError(f"ledger status {status!r}")
        entry = {
            "seq": len(self.raw_lines()) + 1,
            "utc": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "arm": arm,
            "runner_sha256": runner_sha256,
            "scorer_sha256": scorer_sha256,
            "truth5_sha256": truth5_sha256,
            "target_freeze_sha256": target_freeze_sha256,
            "out": out,
            "status": status,
            "prev_sha256": self.tail_sha256(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as fh:
            fh.write(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode() + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry

    def runs(self, arm: str, target: str) -> list[dict[str, Any]]:
        """The ``started`` lines of ``(arm, target)``, each with ``closed``: ``scored``,
        ``crashed_before_metric`` or ``open`` (the process died before it could write)."""
        return runs_of(self.entries(), arm, target)

    def run_index(self, arm: str, target: str) -> int:
        """1 + the earlier ``started`` lines of this target."""
        return 1 + len(self.runs(arm, target))

    def may_start(self, arm: str, target: str) -> tuple[int, dict[str, Any], str]:
        """``(index, prior, refusal)``: the next run's index, what the previous run was (a re-run: the
        crashed run's runner hash), and a non-empty refusal when no run is permitted (a third run, or
        a re-run after a scored one)."""
        return next_run(self.runs(arm, target))


# ---- H5: the layout rule ---------------------------------------------------------------------------

_SHA_LINE = re.compile(r"^([0-9a-f]{64}) [ *](.+)$")


def check_freeze(listing: Path, base: Path) -> list[str]:
    """``sha256sum --check`` semantics: every line ``<64 hex>  <relative path>``; a path that is
    absolute or climbs out, a missing file and a changed file are problems."""
    problems: list[str] = []
    for n, line in enumerate(Path(listing).read_text().splitlines(), 1):
        if not line.strip():
            continue
        m = _SHA_LINE.match(line)
        if m is None:
            problems.append(f"{Path(listing).name} line {n}: improperly formatted")
            continue
        digest, rel = m.groups()
        if rel.startswith("/") or ".." in Path(rel).parts:
            problems.append(f"{rel}: path is not relative to the app")
            continue
        f = Path(base) / rel
        if not f.is_file():
            problems.append(f"missing {rel}")
        elif sha256_of(f.read_bytes()) != digest:
            problems.append(f"changed {rel}")
    return problems


def check_layout(app: Path) -> list[str]:
    """The refusals of a preflight (each is ``refused_preflight``, no run consumed): the app is not a
    directory or is given as a symlink, any symlink inside the tree, a missing ``harness.py``,
    ``studio.toml``, ``truth.json`` or ``freeze.sha256`` at its root, a freeze that does not check."""
    app = Path(app)
    if app.is_symlink():
        return [f"the app directory {app} is a symlink"]
    if not app.is_dir():
        return [f"{app} is not a directory"]
    problems: list[str] = []
    for dirpath, dirnames, filenames in os.walk(app):
        for n in (*dirnames, *filenames):
            p = Path(dirpath) / n
            if p.is_symlink():
                problems.append(f"symlink in the app tree: {p.relative_to(app)}")
    for needed in ("harness.py", "studio.toml", "truth.json", "freeze.sha256"):
        if not (app / needed).is_file():
            problems.append(f"missing {needed} at the app root")
    if (app / "freeze.sha256").is_file():
        problems += [f"freeze: {x}" for x in check_freeze(app / "freeze.sha256", app)]
    return problems


def app_freeze_sha256(app: Path) -> str:
    f = Path(app) / "freeze.sha256"
    return sha256_of(f.read_bytes()) if f.is_file() else ""
