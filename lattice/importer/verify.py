"""The importer's differential: an original app against its Lattice import (spec
2026-09-27 importer §3.3, §4.4).

Generalised from ``bench/template_suite.py`` and ``tests/template/test_template_differential
.py``. :func:`verify` runs a request suite (:class:`Case`) against both apps, each case on
a fresh copy of the same seeded SQLite database, and classifies every case:

* ``equal`` -- same status, same (normalised) JSON body, same database effects;
* ``equal_under_rule`` -- the same, once the named representation rules are applied to
  both bodies (a rule is data: :class:`ValidationEnvelope`, :class:`ErrorEnvelope`,
  :class:`DatetimeWire`, or any object with ``name``/``description``/``canon``);
* ``deviation`` -- a declared :class:`Deviation`: the import's status and body are
  asserted exactly, its database effects are equal (or not) as declared, *and* the case
  really disagrees with the original;
* ``mismatch`` -- anything else, with a :class:`Diff` of status, body and DB effects.

The declarations are checked too: every rule must be needed by some case
(:attr:`VerifyResult.unneeded_rules`) and every deviation must name a case
(:attr:`VerifyResult.unknown_deviations`).

Records are normalised as in the template suite: values generated during a request (UUIDs
and datetimes absent from the seed database, JWTs, new password hashes) become
placeholders that keep their spelling -- ``<uuid:N:hex32>``, ``<datetime:N:space>`` -- so
two apps must also agree on how they write a value, not just on the value.

**Branch coverage** (review focus #3): ``branches`` maps a route name to the functions of
the original that implement it (the route function and its dependencies). Every
``raise HTTPException(...)`` in them is a :class:`RaiseSite`; while the original runs, a
``sys.monitoring`` tool records which cases reach which site (process-wide, so the
threads TestClient and the threadpool use are covered). A site that no case of its route
(``Case.route``; a case without a route counts for every route) reaches is an
*unverified branch* (:attr:`VerifyResult.unverified`): the suite cannot tell whether the
import reproduces it.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import textwrap
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

AppFactory = Callable[[Path], Any]
DbFactory = Callable[[str, Path], Path]
Login = Callable[[Any, str], str]
Side = Literal["original", "lattice"]
OutcomeKind = Literal["equal", "equal_under_rule", "deviation", "mismatch"]

# ------------------------------------------------------------------------------ cases --


@dataclass(frozen=True)
class Case:
    """One request. ``user`` is a handle the ``login`` callable turns into a bearer token;
    ``token`` is a raw bearer token (overrides ``user``). ``tables`` are snapshotted after
    the request. ``db`` names the seeded database variant (``db_factory(db, path)``);
    ``route`` names the route the case exercises (for branch coverage)."""

    name: str
    method: str
    path: str
    user: str | None = None
    token: str | None = None
    params: dict[str, Any] | None = None
    json: Any = None
    form: dict[str, str] | None = None
    tables: tuple[str, ...] = ()
    note: str = ""
    db: str = "seed"
    route: str | None = None


@dataclass
class CaseRecord:
    """What one app answered to one case: status, normalised body, normalised rows of
    the case's tables after the request."""

    name: str
    method: str
    path: str
    status: int
    body: Any
    db: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------- normalisation --

UUID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$")
JWT_RE = re.compile(r"^eyJ[\w-]*\.[\w-]+\.[\w-]+$")
DT_PLACEHOLDER_RE = re.compile(r"^<datetime:(\d+):[^>]*>$")


def uuid_spelling(s: str) -> str:
    """``dashed`` or ``hex32``, ``-upper`` if not lowercase."""
    return ("dashed" if "-" in s else "hex32") + ("" if s == s.lower() else "-upper")


def datetime_spelling(s: str) -> str:
    """The separator (``T`` / ``space``) then the zone (``Z``, ``+offset`` or nothing)."""
    sep = "space" if s[10] == " " else "T"
    if s.endswith("Z"):
        return sep + "Z"
    return sep + ("+offset" if re.search(r"[+-]\d{2}:?\d{2}$", s[19:]) else "")


def dt_key(s: str) -> datetime:
    """The instant a datetime text names (naive values are taken as UTC)."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _dt_key_or_none(s: str) -> datetime | None:
    """:func:`dt_key`, or None for a datetime-shaped text naming no instant (an
    impossible date such as ``2026-02-30T00:00:00`` echoed back in a 422 body)."""
    try:
        return dt_key(s)
    except ValueError:
        return None


class Normalizer:
    """Knows the values of a seed database; :meth:`fresh` numbers one case's new values.

    ``hash_columns`` hold password hashes: ``<hash:seed>`` if unchanged, ``<hash:new>``."""

    def __init__(self, seed_db: Path, hash_columns: Sequence[str] = ("hashed_password",)):
        self.hash_columns = frozenset(hash_columns)
        self.known_uuids: set[uuid.UUID] = set()
        self.known_dts: set[datetime] = set()
        self.seed_hashes: set[str] = set()
        conn = sqlite3.connect(seed_db)
        try:
            tables = [
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ]
            for t in tables:
                cur = conn.execute(f'SELECT * FROM "{t}"')
                cols = [d[0] for d in cur.description]
                for row in cur:
                    for c, v in zip(cols, row, strict=True):
                        if not isinstance(v, str):
                            continue
                        if c in self.hash_columns:
                            self.seed_hashes.add(v)
                        elif UUID_RE.match(v):
                            self.known_uuids.add(uuid.UUID(v))
                        elif DT_RE.match(v) and (d := _dt_key_or_none(v)) is not None:
                            self.known_dts.add(d)
        finally:
            conn.close()

    def fresh(self) -> CaseNormalizer:
        return CaseNormalizer(self)


class CaseNormalizer:
    def __init__(self, base: Normalizer) -> None:
        self.base = base
        self.uuids: dict[uuid.UUID, int] = {}
        self.dts: dict[datetime, int] = {}

    def value(self, v: Any, key: str | None = None) -> Any:
        if isinstance(v, dict):
            return {k: self.value(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if not isinstance(v, str):
            return v
        if key in self.base.hash_columns:
            return "<hash:seed>" if v in self.base.seed_hashes else "<hash:new>"
        if UUID_RE.match(v):
            u = uuid.UUID(v)
            if u in self.base.known_uuids:
                return v
            n = self.uuids.setdefault(u, len(self.uuids) + 1)
            return f"<uuid:{n}:{uuid_spelling(v)}>"
        if DT_RE.match(v) and (d := _dt_key_or_none(v)) is not None:
            if d in self.base.known_dts:
                return v
            n = self.dts.setdefault(d, len(self.dts) + 1)
            return f"<datetime:{n}:{datetime_spelling(v)}>"
        if JWT_RE.match(v):
            return "<token>"
        return v


def snapshot_tables(db_path: Path, tables: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """Raw rows of ``tables`` (in ``rowid`` order) as stored by SQLite."""
    conn = sqlite3.connect(db_path)
    try:
        out: dict[str, list[dict[str, Any]]] = {}
        for t in tables:
            cur = conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')
            cols = [d[0] for d in cur.description]
            out[t] = [dict(zip(cols, row, strict=True)) for row in cur]
        return out
    finally:
        conn.close()


# ----------------------------------------------------------------------------- runner --


def form_login(
    path: str,
    credentials: Mapping[str, tuple[str, str]],
    *,
    username_field: str = "username",
    password_field: str = "password",
    token_field: str = "access_token",
) -> Login:
    """A ``login`` for an OAuth2 password form: handle -> (username, password)."""

    def login(client: Any, handle: str) -> str:
        username, password = credentials[handle]
        r = client.post(path, data={username_field: username, password_field: password})
        if r.status_code != 200:
            raise RuntimeError(f"login as {handle!r} failed: {r.status_code} {r.text}")
        return r.json()[token_field]

    return login


def _body(response: Any) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except json.JSONDecodeError:
        return response.text


def run_case(
    case: Case,
    app_factory: AppFactory,
    db_path: Path,
    norm: Normalizer,
    *,
    login: Login | None = None,
    prefix: str = "",
    tracer: BranchTracer | None = None,
) -> CaseRecord:
    """Issue ``case`` against ``app_factory(copy of db_path)``, logging in first when the
    case acts as a user; record status, body and the case's tables, normalised."""
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory(prefix="lattice-verify-") as tmp:
        case_db = Path(tmp) / "case.db"
        shutil.copyfile(db_path, case_db)
        app = app_factory(case_db)
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                headers = {}
                token = case.token
                if token is None and case.user is not None:
                    if login is None:
                        raise ValueError(f"case {case.name!r} acts as a user but no login")
                    token = login(client, case.user)
                if token is not None:
                    headers["Authorization"] = f"Bearer {token}"
                with tracer.case(case) if tracer else _nothing():
                    response = client.request(
                        case.method,
                        prefix + case.path,
                        params=case.params,
                        json=case.json,
                        data=case.form,
                        headers=headers,
                    )
        finally:
            engine = getattr(getattr(app, "state", None), "engine", None)
            if engine is not None:
                engine.dispose()
        raw_db = snapshot_tables(case_db, case.tables)
    n = norm.fresh()
    body = n.value(_body(response))
    db = {t: [n.value(row) for row in rows] for t, rows in raw_db.items()}
    return CaseRecord(case.name, case.method, case.path, response.status_code, body, db)


@contextmanager
def _nothing() -> Iterator[None]:
    yield


def build_dbs(db_factory: DbFactory, variants: Sequence[str], root: Path) -> dict[str, Path]:
    """Each database variant built once under ``root``."""
    return {v: Path(db_factory(v, root / f"{v}.db")) for v in dict.fromkeys(variants)}


def run_suite(
    app_factory: AppFactory,
    dbs: Mapping[str, Path],
    cases: Sequence[Case],
    *,
    login: Login | None = None,
    prefix: str = "",
    hash_columns: Sequence[str] = ("hashed_password",),
    tracer: BranchTracer | None = None,
) -> dict[str, CaseRecord]:
    """Every case on a fresh copy of its database variant; name -> record."""
    norms = {v: Normalizer(p, hash_columns) for v, p in dbs.items()}
    out: dict[str, CaseRecord] = {}
    for c in cases:
        if c.name in out:
            raise ValueError(f"duplicate case name {c.name!r}")
        out[c.name] = run_case(
            c, app_factory, dbs[c.db], norms[c.db], login=login, prefix=prefix, tracer=tracer
        )
    return out


# ------------------------------------------------------------------ representation rules --


class Rule(Protocol):
    """A representation rule: ``canon`` maps a body to the form both apps must agree on
    (unchanged when the rule does not apply)."""

    name: str
    description: str

    def canon(self, body: Any, status: int, side: Side) -> Any: ...


@dataclass(frozen=True)
class ValidationEnvelope:
    """Both answer ``status`` flagging the same fields (the same ``compare`` values, in
    order) in a ``{key: [...]}`` body; the wording of each entry may differ, but the
    import's entries must have exactly ``lattice_keys``."""

    name: str = "VALIDATION_ENVELOPE"
    description: str = (
        "Both answer 422 flagging the same fields (the same loc lists, in order) in a "
        '{"detail": [...]} body; each entry\'s wording differs: Lattice {"loc", "msg"}, '
        'pydantic {"type", "loc", "msg", "input", "ctx"}.'
    )
    status: int = 422
    key: str = "detail"
    compare: tuple[str, ...] = ("loc",)
    lattice_keys: frozenset[str] = frozenset({"loc", "msg"})

    def canon(self, body: Any, status: int, side: Side) -> Any:
        if status != self.status or not isinstance(body, dict) or set(body) != {self.key}:
            return body
        entries = body[self.key]
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            return body
        if side == "lattice" and not all(set(e) == self.lattice_keys for e in entries):
            return body
        if not all(all(k in e for k in self.compare) for e in entries):
            return body
        return {self.key: [{k: e[k] for k in self.compare} for e in entries]}


@dataclass(frozen=True)
class ErrorEnvelope:
    """An error (status >= 400) carries the same message at ``original`` in the original's
    body and at ``lattice`` in the import's; nothing else is compared."""

    original: tuple[str, ...] = ("detail",)
    lattice: tuple[str, ...] = ("detail",)
    name: str = "ERROR_ENVELOPE"
    description: str = "Errors carry the same message in a different envelope."

    def canon(self, body: Any, status: int, side: Side) -> Any:
        if status < 400:
            return body
        cur = body
        for k in self.original if side == "original" else self.lattice:
            if not isinstance(cur, dict) or k not in cur:
                return body
            cur = cur[k]
        return {"<error>": cur}


@dataclass(frozen=True)
class DatetimeWire:
    """Datetimes compare as instants: a placeholder's spelling is dropped and a known
    (seed) datetime text becomes its UTC instant."""

    name: str = "DATETIME_WIRE"
    description: str = "Datetimes are the same instants, spelled differently on the wire."

    def canon(self, body: Any, status: int, side: Side) -> Any:
        if isinstance(body, dict):
            return {k: self.canon(v, status, side) for k, v in body.items()}
        if isinstance(body, list):
            return [self.canon(v, status, side) for v in body]
        if isinstance(body, str):
            m = DT_PLACEHOLDER_RE.match(body)
            if m:
                return f"<datetime:{m.group(1)}>"
            if DT_RE.match(body) and (d := _dt_key_or_none(body)) is not None:
                return d.isoformat()
        return body


@dataclass(frozen=True)
class Diff:
    """What differs: ``(original, lattice)`` pairs; None where the two agree."""

    status: tuple[int, int] | None = None
    body: tuple[Any, Any] | None = None
    db: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = field(default_factory=dict)

    def render(self) -> str:
        lines = []
        if self.status:
            lines.append(f"status: {self.status[0]} != {self.status[1]}")
        if self.body:
            lines.append(f"body: {self.body[0]!r} != {self.body[1]!r}")
        for t, (a, b) in self.db.items():
            lines.append(f"db {t}: {a!r} != {b!r}")
        return "\n".join(lines)


class Mismatch(AssertionError):
    def __init__(self, message: str, diff: Diff) -> None:
        super().__init__(message + ("\n" + diff.render() if diff.render() else ""))
        self.diff = diff


def diff_records(original: CaseRecord, lattice: CaseRecord) -> Diff:
    return Diff(
        status=None if original.status == lattice.status else (original.status, lattice.status),
        body=None if original.body == lattice.body else (original.body, lattice.body),
        db={
            t: (original.db.get(t, []), lattice.db.get(t, []))
            for t in dict.fromkeys([*original.db, *lattice.db])
            if original.db.get(t) != lattice.db.get(t)
        },
    )


def agreement(
    original: CaseRecord, lattice: CaseRecord, rules: Sequence[Rule] = ()
) -> tuple[str, ...]:
    """The rules under which ``lattice`` reproduces ``original`` (empty: exactly).

    Status and database effects must be identical; the bodies are compared raw, then
    after each rule in turn (a rule counts only if it changed a body). A :class:`Mismatch`
    (an AssertionError) when they still differ."""
    diff = diff_records(original, lattice)
    if diff.status:
        raise Mismatch("status differs", diff)
    if diff.db:
        raise Mismatch("database effects differ", diff)
    a, b = original.body, lattice.body
    used: list[str] = []
    for rule in rules:
        if a == b:
            break
        a2, b2 = (
            rule.canon(a, original.status, "original"),
            rule.canon(b, lattice.status, "lattice"),
        )
        if (a2, b2) != (a, b):
            used.append(rule.name)
            a, b = a2, b2
    if a != b:
        raise Mismatch("body differs", diff)
    return tuple(used)


# ---------------------------------------------------------------------------- deviations --


@dataclass(frozen=True)
class Deviation:
    """An intentional difference (``policy``) or a known language gap (``gap``): what the
    import answers instead of the original.

    ``accept_original``: either answer is acceptable -- the original's (the case is then
    ``equal``/``equal_under_rule`` as usual) or the declared one (``deviation``); without
    it the import must answer as declared and agreeing is a mismatch."""

    id: str
    kind: str
    reason: str
    status: int
    body: Any
    db_same: bool = True
    accept_original: bool = False


# ------------------------------------------------------------------------ branch coverage --


@dataclass(frozen=True)
class RaiseSite:
    """A ``raise HTTPException(...)`` in an original function."""

    function: str
    file: str
    line: int
    status: int | None
    detail: str | None

    def render(self) -> str:
        return f"{self.function} ({Path(self.file).name}:{self.line}) -> {self.status}"


def _status_of(node: ast.expr | None) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Attribute):  # status.HTTP_403_FORBIDDEN
        m = re.match(r"HTTP_(\d{3})_", node.attr)
        return int(m.group(1)) if m else None
    return None


def _is_http_exception(node: ast.expr) -> bool:
    name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
    return isinstance(name, str) and name.endswith("HTTPException")


def raise_sites(func: Callable[..., Any]) -> list[RaiseSite]:
    """Every ``raise HTTPException(...)`` in ``func``'s own body, in source order."""
    func = inspect.unwrap(func)
    code = func.__code__
    lines, first = inspect.getsourcelines(func)
    tree = ast.parse(textwrap.dedent("".join(lines)))
    sites = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        call = node.exc
        if not _is_http_exception(call.func):
            continue
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        status = _status_of(kw.get("status_code", call.args[0] if call.args else None))
        det = kw.get("detail", call.args[1] if len(call.args) > 1 else None)
        detail = det.value if isinstance(det, ast.Constant) and isinstance(det.value, str) else None
        sites.append(
            RaiseSite(func.__qualname__, code.co_filename, first + node.lineno - 1, status, detail)
        )
    return sorted(sites, key=lambda s: s.line)


class BranchTracer:
    """Records which case reaches which raise site, via a ``sys.monitoring`` tool
    (process-wide: every thread is traced)."""

    def __init__(self, functions: Sequence[Callable[..., Any]]) -> None:
        self.codes: dict[Any, dict[int, RaiseSite]] = {}
        for f in functions:
            code = inspect.unwrap(f).__code__
            for s in raise_sites(f):
                self.codes.setdefault(code, {})[s.line] = s
        self.hits: dict[RaiseSite, list[str]] = {
            s: [] for by_line in self.codes.values() for s in by_line.values()
        }
        self._current: str | None = None
        self._lock = threading.Lock()
        self._tool: int | None = None

    @property
    def sites(self) -> tuple[RaiseSite, ...]:
        return tuple(self.hits)

    def _on_line(self, code: Any, line: int) -> Any:
        site = self.codes.get(code, {}).get(line)
        if site is None:
            return sys.monitoring.DISABLE
        with self._lock:
            if self._current is not None and self._current not in self.hits[site]:
                self.hits[site].append(self._current)
        return None

    @contextmanager
    def active(self) -> Iterator[BranchTracer]:
        mon = sys.monitoring
        tool = next((i for i in range(6) if mon.get_tool(i) is None), None)
        if tool is None:
            raise RuntimeError("no free sys.monitoring tool id")
        mon.use_tool_id(tool, "lattice-verify")
        self._tool = tool
        try:
            mon.register_callback(tool, mon.events.LINE, self._on_line)
            for code in self.codes:
                mon.set_local_events(tool, code, mon.events.LINE)
            yield self
        finally:
            for code in self.codes:
                mon.set_local_events(tool, code, 0)
            mon.register_callback(tool, mon.events.LINE, None)
            mon.free_tool_id(tool)
            mon.restart_events()
            self._tool = None

    @contextmanager
    def case(self, case: Case) -> Iterator[None]:
        with self._lock:
            self._current = case.name
        try:
            yield
        finally:
            with self._lock:
                self._current = None


# ------------------------------------------------------------------------------- verify --


@dataclass(frozen=True)
class CaseOutcome:
    name: str
    kind: OutcomeKind
    rules: tuple[str, ...] = ()
    deviation: str | None = None
    reason: str = ""
    diff: Diff | None = None


@dataclass
class VerifyResult:
    """Per-case outcomes, the records of both apps, and the declaration and branch
    checks. ``ok``: no mismatch, every rule needed, every deviation names a case, no
    unverified branch."""

    outcomes: dict[str, CaseOutcome]
    original: dict[str, CaseRecord]
    lattice: dict[str, CaseRecord]
    rules: tuple[str, ...]
    unknown_deviations: tuple[str, ...]
    hits: dict[RaiseSite, tuple[str, ...]]
    unverified: tuple[tuple[str, RaiseSite], ...]

    @property
    def rules_used(self) -> frozenset[str]:
        return frozenset(r for o in self.outcomes.values() for r in o.rules)

    @property
    def unneeded_rules(self) -> tuple[str, ...]:
        return tuple(r for r in self.rules if r not in self.rules_used)

    @property
    def mismatches(self) -> dict[str, CaseOutcome]:
        return {n: o for n, o in self.outcomes.items() if o.kind == "mismatch"}

    def tally(self, names: Sequence[str] | None = None) -> tuple[int, int, int, int]:
        """(equal, equal_under_rule, deviation, mismatch) over ``names`` (default all)."""
        kinds = [self.outcomes[n].kind for n in (names if names is not None else self.outcomes)]
        order = ("equal", "equal_under_rule", "deviation", "mismatch")
        return tuple(kinds.count(k) for k in order)  # type: ignore[return-value]

    @property
    def ok(self) -> bool:
        return not (
            self.mismatches or self.unneeded_rules or self.unknown_deviations or self.unverified
        )

    def render(self) -> str:
        lines = [f"tally (equal, under rule, deviation, mismatch): {self.tally()}"]
        for n, o in self.mismatches.items():
            lines.append(f"MISMATCH {n}: {o.reason}")
            if o.diff:
                lines.extend("    " + x for x in o.diff.render().splitlines())
        lines += [f"unneeded rule: {r}" for r in self.unneeded_rules]
        lines += [f"deviation names no case: {d}" for d in self.unknown_deviations]
        lines += [f"unverified branch: {r}: {s.render()}" for r, s in self.unverified]
        return "\n".join(lines)


def classify(
    original: CaseRecord,
    lattice: CaseRecord,
    rules: Sequence[Rule],
    deviation: Deviation | None,
) -> CaseOutcome:
    name = original.name
    try:
        used = agreement(original, lattice, rules)
    except Mismatch as m:
        if deviation is None:
            return CaseOutcome(name, "mismatch", reason=str(m).splitlines()[0], diff=m.diff)
        if (lattice.status, lattice.body) != (deviation.status, deviation.body):
            return CaseOutcome(
                name,
                "mismatch",
                deviation=deviation.id,
                reason=f"deviation {deviation.id!r}: the import does not answer as declared "
                f"({deviation.status}, {deviation.body!r})",
                diff=m.diff,
            )
        if (lattice.db == original.db) is not deviation.db_same:
            return CaseOutcome(
                name,
                "mismatch",
                deviation=deviation.id,
                reason=f"deviation {deviation.id!r}: database effects are not as declared "
                f"(db_same={deviation.db_same})",
                diff=m.diff,
            )
        return CaseOutcome(name, "deviation", deviation=deviation.id, reason=deviation.reason)
    if deviation is not None and not deviation.accept_original:
        return CaseOutcome(
            name,
            "mismatch",
            rules=used,
            deviation=deviation.id,
            reason=f"deviation {deviation.id!r} declared but the import agrees",
        )
    return CaseOutcome(name, "equal_under_rule" if used else "equal", rules=used)


def verify(
    original_app_factory: AppFactory,
    lattice_app_factory: AppFactory,
    db_factory: DbFactory,
    cases: Sequence[Case],
    rules: Sequence[Rule],
    deviations: Mapping[str, Deviation],
    *,
    login: Login | None = None,
    prefix: str = "",
    hash_columns: Sequence[str] = ("hashed_password",),
    branches: Mapping[str, Sequence[Callable[..., Any]]] | None = None,
    lattice_login: Login | None = None,
) -> VerifyResult:
    """Run ``cases`` against both apps on identical seeded databases and classify each.

    ``db_factory(variant, path)`` writes the database variant a case names (``Case.db``)
    at ``path``; each variant is built once and both apps get fresh copies of the same
    file. ``login(client, handle)`` returns a bearer token (``lattice_login`` for the
    import, default the same). ``deviations`` maps case names to :class:`Deviation`.
    ``branches`` maps route names to the original's functions whose raise sites must each
    be reached by a case of that route."""
    names = [c.name for c in cases]
    if len(set(names)) != len(names):
        raise ValueError("case names must be unique")
    functions = list(dict.fromkeys(f for fs in (branches or {}).values() for f in fs))
    tracer = BranchTracer(functions)
    with tempfile.TemporaryDirectory(prefix="lattice-verify-dbs-") as tmp:
        dbs = build_dbs(db_factory, [c.db for c in cases], Path(tmp))
        common = {"prefix": prefix, "hash_columns": hash_columns}
        with tracer.active():
            original = run_suite(
                original_app_factory, dbs, cases, login=login, tracer=tracer, **common
            )
        lattice = run_suite(lattice_app_factory, dbs, cases, login=lattice_login or login, **common)
    outcomes = {n: classify(original[n], lattice[n], rules, deviations.get(n)) for n in names}
    route_of = {c.name: c.route for c in cases}
    unverified: list[tuple[str, RaiseSite]] = []
    for route, fs in (branches or {}).items():
        for f in fs:
            for s in raise_sites(f):
                if not any(route_of[n] in (route, None) for n in tracer.hits[s]):
                    unverified.append((route, s))
    return VerifyResult(
        outcomes=outcomes,
        original=original,
        lattice=lattice,
        rules=tuple(r.name for r in rules),
        unknown_deviations=tuple(d for d in deviations if d not in outcomes),
        hits={s: tuple(ns) for s, ns in tracer.hits.items()},
        unverified=tuple(unverified),
    )
