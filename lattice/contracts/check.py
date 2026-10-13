"""The consumer side of a contract: the import file, the lock, ``pin`` and ``check``.

The consumer's directory holds ``lattice.imports.toml`` (what it relies on)::

    [[import]]
    provider = "billing-api"               # a project of the workspace
    endpoint = "POST /invoices/{id}/pay"   # METHOD /path ({name} and ids are templated to {})
    status = [200, 409]                    # non-empty list of statuses it handles
    auth = "token"                         # "public" or "token"
    as_user = "eve"                        # optional, token only: a seed handle of the provider
    request_fields = ["amount"]            # default []
    response_fields = ["id", "status"]     # default []

and, once pinned, ``lattice.contracts.lock.json``: for each provider the export that was pinned
(:mod:`lattice.contracts.export`), its ``spec_ref`` and version. ``check`` compares the imports
with the provider's *current* export ``E`` and the *pinned* export ``P``, claim by claim (the
endpoint, each status, each request and response field, the auth):

* held by ``E``: ok;
* not held by ``E`` but held by ``P``: **BROKEN** (``endpoint_missing``, ``status_not_observed``,
  ``request_field_missing``, ``response_field_missing``, ``auth_changed``);
* held by neither: a **GAP**, a reliance the provider's spec never showed (``*_unobserved``);
* every item of the diff ``P -> E`` that is not folded into a BROKEN is **CHANGED**, tagged
  ``relied`` when some import names its endpoint.

Findings sort by (class, endpoint, kind, item). ``version_changed`` and the gaps are
informational. ``check`` exits 0 when no BROKEN and no CHANGED, 1 otherwise, 2 when it cannot
run (``no_pin``, ``bad_pin``, ``bad_imports``, ``export_failed``, a manifest error...); ``pin``
exits 0 when it writes, 1 when it refuses (a BROKEN finding, unless ``force``), 2 when it
cannot run. Only links with ``contract = "static"`` or ``"live"`` are looked at.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lattice.contracts.export import (
    DEFAULT_SPEC,
    ExportFailed,
    compute_export,
    parse_endpoint,
    spec_hash,
)
from lattice.workspace import ERROR, Link, Problem, Workspace

LOCK_FILE = "lattice.contracts.lock.json"
IMPORTS_FILE = "lattice.imports.toml"
LOCK_FORMAT = 1

BROKEN, CHANGED, GAP = "BROKEN", "CHANGED", "GAP"
AUTHS = ("public", "token")
CLAIM_KINDS = ("endpoint", "status", "request_field", "response_field", "auth")
BROKEN_KINDS = {
    "endpoint": "endpoint_missing",
    "status": "status_not_observed",
    "request_field": "request_field_missing",
    "response_field": "response_field_missing",
    "auth": "auth_changed",
}
GAP_KINDS = {k: f"{k}_unobserved" for k in CLAIM_KINDS}
_IMPORT_KEYS = ("provider", "endpoint", "status", "auth", "as_user", "request_fields",
                "response_fields")  # fmt: skip
_AUTH_RANK = {"public": 0, "unknown": 1, "token": 2}
_STATE_RANK = {"granted": 0, "none": 1, "refused": 2}


class ContractError(Exception):
    """A check or pin that cannot run: ``code`` (``no_imports``, ``bad_imports``, ``no_pin``,
    ``bad_pin``, ``export_failed``, ...) and a message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- the import file -------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Import:
    """One ``[[import]]`` table, validated; ``endpoint`` is already a normalised key."""

    provider: str
    endpoint: str
    status: tuple[int, ...]
    auth: str
    as_user: str | None = None
    request_fields: tuple[str, ...] = ()
    response_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"provider": self.provider, "endpoint": self.endpoint,
                               "status": list(self.status), "auth": self.auth}  # fmt: skip
        if self.as_user is not None:
            out["as_user"] = self.as_user
        out["request_fields"] = list(self.request_fields)
        out["response_fields"] = list(self.response_fields)
        return out


def _str_list(item: dict[str, Any], key: str, label: str, problems: list[str]) -> tuple[str, ...]:
    val = item.get(key, [])
    if not isinstance(val, list) or not all(isinstance(v, str) and v for v in val):
        problems.append(f"{label}: {key} must be a list of non-empty strings")
        return ()
    return tuple(dict.fromkeys(val))


def parse_imports(path: Path | str) -> list[Import]:
    """The imports of the file at `path`. Raises :class:`ContractError`: ``no_imports`` when the
    file does not exist, ``bad_imports`` for anything else wrong (unknown keys, bad types, a bad
    endpoint, not TOML); the message lists every problem."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ContractError("no_imports", f"{path} does not exist: it holds the consumer's "
                            "[[import]] tables (what it relies on)") from None  # fmt: skip
    except (OSError, UnicodeDecodeError) as e:
        raise ContractError("bad_imports", f"cannot read {path}: {e}") from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ContractError("bad_imports", f"{path} is not valid TOML: {e}") from None
    problems: list[str] = []
    for key in sorted(data):
        if key != "import":
            problems.append(f"unknown top-level key {key!r} (only [[import]] tables)")
    raw = data.get("import", [])
    if not isinstance(raw, list):
        problems.append("[[import]] must be an array of tables")
        raw = []
    out: list[Import] = []
    for i, item in enumerate(raw, 1):
        label = f"import #{i}"
        if not isinstance(item, dict):
            problems.append(f"{label} is not a table")
            continue
        for key in sorted(item):
            if key not in _IMPORT_KEYS:
                problems.append(f"{label}: unknown key {key!r}")
        provider = item.get("provider")
        if not isinstance(provider, str) or not provider:
            problems.append(f"{label}: provider must be a project name")
            provider = ""
        endpoint = ""
        try:
            endpoint = parse_endpoint(item.get("endpoint"))
        except ValueError as e:
            problems.append(f"{label}: {e}")
        status = item.get("status")
        if (
            not isinstance(status, list)
            or not status
            or not all(isinstance(s, int) and not isinstance(s, bool) and 100 <= s <= 599
                       for s in status)
        ):  # fmt: skip
            problems.append(f"{label}: status must be a non-empty list of HTTP statuses")
            status = []
        auth = item.get("auth")
        if auth not in AUTHS:
            problems.append(f"{label}: auth must be one of {', '.join(AUTHS)}")
            auth = "token"
        as_user = item.get("as_user")
        if as_user is not None:
            if not isinstance(as_user, str) or not as_user:
                problems.append(f"{label}: as_user must be a handle (a string)")
                as_user = None
            elif auth != "token":
                problems.append(f'{label}: as_user needs auth = "token"')
        req = _str_list(item, "request_fields", label, problems)
        resp = _str_list(item, "response_fields", label, problems)
        out.append(Import(provider, endpoint, tuple(dict.fromkeys(status)), auth, as_user,
                          req, resp))  # fmt: skip
    if not raw and not problems:
        problems.append("no [[import]] tables")
    if problems:
        raise ContractError("bad_imports", f"{path}: " + "; ".join(problems))
    return out


# ---- the lock --------------------------------------------------------------------------------


def version_of(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The flat version record of a provider entry: spec_ref, spec_hash, git_ref, dirty."""
    v = entry["version"]
    return {"spec_ref": entry["spec_ref"], "spec_hash": v["spec_hash"], "git_ref": v["git_ref"],
            "dirty": v["dirty"]}  # fmt: skip


def lock_text(lock: Mapping[str, Any]) -> str:
    """The lock file's text: sorted keys, one-space indent, ASCII, a final newline (no
    timestamps and no absolute paths, so equal pins are byte-identical)."""
    return json.dumps(lock, sort_keys=True, indent=1, ensure_ascii=True) + "\n"


def read_lock(path: Path | str, consumer: str) -> dict[str, Any] | None:
    """The lock at `path` (None when the file does not exist). Raises ``bad_pin`` when it is
    not a lock of this consumer, or when an entry's ``spec_hash`` does not match its own
    embedded export."""
    path = Path(path)
    if not path.is_file():
        return None

    def bad(why: str) -> ContractError:
        return ContractError("bad_pin", f"{path}: {why}; delete it and run "
                             f"`lattice contracts pin {consumer}`")  # fmt: skip

    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise bad(f"not readable JSON ({e})") from None
    if not isinstance(lock, dict) or lock.get("format") != LOCK_FORMAT:
        raise bad(f"not a format {LOCK_FORMAT} lock")
    if lock.get("consumer") != consumer:
        raise bad(f"pinned for consumer {lock.get('consumer')!r}, not {consumer!r}")
    providers = lock.get("providers")
    if not isinstance(providers, dict):
        raise bad("no providers table")
    for name, entry in providers.items():
        try:
            export, version = entry["export"], entry["version"]
            ok = (
                isinstance(entry["spec_ref"], str)
                and isinstance(export, dict)
                and isinstance(export["endpoints"], dict)
                and isinstance(version["spec_hash"], str)
            )
            matches = ok and spec_hash(export) == version["spec_hash"]
        except (KeyError, TypeError):
            raise bad(f"the entry of {name!r} is malformed") from None
        if not ok:
            raise bad(f"the entry of {name!r} is malformed")
        if not matches:
            raise bad(f"the spec_hash of {name!r} does not match its embedded export "
                      f"(recorded {version['spec_hash'][:12]}, computed "
                      f"{spec_hash(export)[:12]})")  # fmt: skip
        version.setdefault("git_ref", None)
        version.setdefault("dirty", False)
    return lock


def write_lock(path: Path | str, lock: Mapping[str, Any]) -> None:
    """Write the lock (via a temporary file, so a failure leaves the old one)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(lock_text(lock), encoding="utf-8")
    os.replace(tmp, path)


# ---- claims, findings, diff ------------------------------------------------------------------


def claims_of(imp: Import) -> list[tuple[str, str]]:
    """What an import relies on, as ``(kind, item)`` claims (kinds: :data:`CLAIM_KINDS`): the
    endpoint, each status, each request and response field, and the auth (``public``,
    ``token`` or ``as_user:HANDLE``)."""
    who = "public" if imp.auth == "public" else (f"as_user:{imp.as_user}" if imp.as_user
                                                else "token")  # fmt: skip
    return [
        ("endpoint", ""),
        *(("status", str(s)) for s in imp.status),
        *(("request_field", f) for f in imp.request_fields),
        *(("response_field", f) for f in imp.response_fields),
        ("auth", who),
    ]


def holds(export: Mapping[str, Any], imp: Import, kind: str, item: str) -> bool:
    """Whether `export` supports one claim of `imp` (see :func:`claims_of`)."""
    ep = export["endpoints"].get(imp.endpoint)
    if ep is None:
        return False
    if kind == "endpoint":
        return True
    if kind == "status":
        return int(item) in ep["statuses"]
    if kind == "request_field":
        return item in ep["request_fields"] or item in ep["query_fields"]
    if kind == "response_field":
        return item in ep["response_fields"]
    if kind == "auth":
        if item == "public":
            return bool(ep["auth"] == "public")
        if item.startswith("as_user:"):
            return item[len("as_user:") :] in ep["granted"]
        return bool(ep["granted"])
    return False


def finding(cls: str, provider: str, endpoint: str, kind: str, item: str = "",
            **extra: Any) -> dict[str, Any]:  # fmt: skip
    """A finding: ``class``, ``provider``, ``endpoint``, ``kind``, ``item`` and the extras."""
    return {"class": cls, "provider": provider, "endpoint": endpoint, "kind": kind,
            "item": item, **extra}  # fmt: skip


def _sort_key(f: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (f["class"], f["endpoint"], f["kind"], f["item"])


def _detail(kind: str, item: str, now: Mapping[str, Any] | None) -> str:
    if now is None:
        return "the endpoint is not in the provider's spec"
    if kind == "status":
        return f"the spec now shows statuses {now['statuses']}"
    if kind == "request_field":
        return f"the spec now shows request fields {sorted({*now['request_fields'], *now['query_fields']})}"  # noqa: E501
    if kind == "response_field":
        return f"the spec now shows response fields {now['response_fields']}"
    if item == "public":
        return f"the endpoint's auth is now {now['auth']}"
    return f"granted handles are now {now['granted']}"


def evaluate(
    imports: Sequence[Import], current: Mapping[str, Any], pinned: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(broken, gaps)`` of the imports against the current export and the pinned one. When the
    endpoint itself is missing from the current export only the endpoint claim is reported."""
    broken: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    gaps: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for imp in imports:
        now = current["endpoints"].get(imp.endpoint)
        for kind, item in claims_of(imp):
            if holds(current, imp, kind, item):
                continue
            if kind != "endpoint" and now is None:
                continue  # the endpoint claim says it
            if holds(pinned, imp, kind, item):
                f = finding(BROKEN, imp.provider, imp.endpoint, BROKEN_KINDS[kind], item,
                            detail=_detail(kind, item, now))  # fmt: skip
                broken[_sort_key(f)] = f
            else:
                f = finding(GAP, imp.provider, imp.endpoint, GAP_KINDS[kind], item,
                            detail="the provider's spec never shows it")  # fmt: skip
                gaps[_sort_key(f)] = f
    return (sorted(broken.values(), key=_sort_key), sorted(gaps.values(), key=_sort_key))


def _state(ep: Mapping[str, Any], handle: str) -> str:
    if handle in ep["granted"]:
        return "granted"
    return "refused" if handle in ep["refused"] else "none"


def diff_exports(pinned: Mapping[str, Any], current: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The items of the diff ``pinned -> current`` over the whole export: dicts with ``kind``
    (``endpoint_added``, ``endpoint_removed``, ``status_added``, ``status_removed``,
    ``field_added``, ``field_removed`` (with ``where``), ``auth_tightened``, ``auth_loosened``,
    ``body_shape_changed``, ``error_body_changed``), ``endpoint`` (``*`` for the whole API),
    ``item`` and ``detail``. Sorted by (endpoint, kind, item)."""
    items: list[dict[str, Any]] = []

    def add(kind: str, endpoint: str, item: str, detail: str, **extra: Any) -> None:
        items.append({"kind": kind, "endpoint": endpoint, "item": item, "detail": detail,
                      **extra})  # fmt: skip

    p_eps, c_eps = pinned["endpoints"], current["endpoints"]
    for key in sorted(set(c_eps) - set(p_eps)):
        add("endpoint_added", key, "", "a new endpoint in the provider's spec")
    for key in sorted(set(p_eps) - set(c_eps)):
        add("endpoint_removed", key, "", "no longer in the provider's spec")
    for key in sorted(set(p_eps) & set(c_eps)):
        old, new = p_eps[key], c_eps[key]
        for s in sorted(set(new["statuses"]) - set(old["statuses"])):
            add("status_added", key, str(s), f"status {s} is now observed")
        for s in sorted(set(old["statuses"]) - set(new["statuses"])):
            add("status_removed", key, str(s), f"status {s} is no longer observed")
        for where, name in (("request", "request_fields"), ("query", "query_fields"),
                            ("response", "response_fields")):  # fmt: skip
            for f in sorted(set(new[name]) - set(old[name])):
                add("field_added", key, f, f"{where} field {f} is now shown", where=where)
            for f in sorted(set(old[name]) - set(new[name])):
                add("field_removed", key, f, f"{where} field {f} is no longer shown", where=where)
        if old["auth"] != new["auth"]:
            tighter = _AUTH_RANK[new["auth"]] > _AUTH_RANK[old["auth"]]
            add("auth_tightened" if tighter else "auth_loosened", key, "auth",
                f"{old['auth']} -> {new['auth']}")  # fmt: skip
        handles = {*old["granted"], *old["refused"], *new["granted"], *new["refused"]}
        for h in sorted(handles):
            a, b = _state(old, h), _state(new, h)
            if a != b:
                tighter = _STATE_RANK[b] > _STATE_RANK[a]
                add("auth_tightened" if tighter else "auth_loosened", key, f"as_user:{h}",
                    f"{h}: {a} -> {b}")  # fmt: skip
        if old["body"] != new["body"]:
            add("body_shape_changed", key, "body", f"{old['body']} -> {new['body']}")
    if pinned.get("error_body") != current.get("error_body"):
        add("error_body_changed", "*", "error_body",
            f"{pinned.get('error_body')} -> {current.get('error_body')}")  # fmt: skip
    return sorted(items, key=lambda i: (i["endpoint"], i["kind"], i["item"], i.get("where", "")))


def _folded(broken: Sequence[Mapping[str, Any]], diff: Sequence[Mapping[str, Any]]) -> set[int]:
    """Indexes of the diff items that cause a BROKEN finding (they are not repeated as CHANGED)."""
    out: set[int] = set()
    for b in broken:
        ep, kind, item = b["endpoint"], b["kind"], b["item"]
        for i, d in enumerate(diff):
            if d["endpoint"] != ep:
                continue
            dk, di, where = d["kind"], d["item"], d.get("where")
            if (
                (kind == "endpoint_missing" and dk == "endpoint_removed")
                or (kind == "status_not_observed" and dk == "status_removed" and di == item)
                or (kind == "request_field_missing" and dk == "field_removed" and di == item
                    and where in ("request", "query"))
                or (kind == "response_field_missing" and dk == "field_removed" and di == item
                    and where == "response")
                or (kind == "auth_changed" and dk == "auth_tightened"
                    and (di == item or (item == "public" and di == "auth")
                         or (item == "token" and di.startswith("as_user:"))))
            ):  # fmt: skip
                out.add(i)
    return out


def changed_of(pinned: Mapping[str, Any], current: Mapping[str, Any],
               broken: Sequence[Mapping[str, Any]],
               imports: Sequence[Import]) -> list[dict[str, Any]]:  # fmt: skip
    """The CHANGED findings: the diff items not folded into a BROKEN, each ``relied`` when some
    import names its endpoint (or, for ``*``, when there is any import)."""
    diff = diff_exports(pinned, current)
    skip = _folded(broken, diff)
    named = {imp.endpoint for imp in imports}
    provider = imports[0].provider if imports else ""
    out = []
    for i, d in enumerate(diff):
        if i in skip:
            continue
        extra = {"where": d["where"]} if "where" in d else {}
        out.append(finding(CHANGED, provider, d["endpoint"], d["kind"], d["item"],
                           relied=bool(named) if d["endpoint"] == "*" else d["endpoint"] in named,
                           detail=d["detail"], **extra))  # fmt: skip
    return sorted(out, key=_sort_key)


# ---- running ---------------------------------------------------------------------------------


class Report:
    """The result of :func:`run_check` or :func:`run_pin`: ``links`` (one dict per link),
    ``problems`` and the exit code (2 over 1 over 0)."""

    def __init__(self, kind: str, workspace: str, consumer: str | None) -> None:
        self.kind = kind
        self.workspace = workspace
        self.consumer = consumer
        self.links: list[dict[str, Any]] = []
        self.problems: list[Problem] = []
        self.extra: dict[str, Any] = {}
        self.elapsed_s = 0.0

    @property
    def exit_code(self) -> int:
        if any(p.severity == ERROR for p in self.problems) or any(
            lk["status"] == "error" for lk in self.links
        ):
            return 2
        if any(lk["status"] in ("broken", "changed", "refused") for lk in self.links):
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"workspace": self.workspace, "consumer": self.consumer,
                               "checked": len(self.links), "links": self.links,
                               "problems": [p.to_dict() for p in self.problems]}  # fmt: skip
        out.update(self.extra)
        out["elapsed_s"] = round(self.elapsed_s, 3)
        return out


class _Session:
    """What one run shares between its links: exports (each provider is loaded once), imports
    and locks (read once per consumer)."""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self._exports: dict[tuple[str, str], dict[str, Any] | ExportFailed] = {}

    def export(self, name: str) -> dict[str, Any]:
        project = self.ws.project(name)
        if project is None:
            raise ContractError("unknown_project", f"{name} is not a project of the workspace")
        ref = project.spec or DEFAULT_SPEC
        key = (str(project.path), ref)
        if key not in self._exports:
            try:
                self._exports[key] = compute_export(project.path, ref)
            except ExportFailed as e:
                self._exports[key] = e
        got = self._exports[key]
        if isinstance(got, ExportFailed):
            raise ContractError("export_failed", f"{name}: {got}")
        return got

    def imports(self, link: Link) -> list[Import]:
        consumer = self.ws.project(link.source)
        assert consumer is not None
        everything = parse_imports(consumer.path / IMPORTS_FILE)
        known = set(self.ws.names())
        for imp in everything:
            if imp.provider not in known:
                raise ContractError("bad_imports", f"{consumer.path / IMPORTS_FILE}: provider "
                                    f"{imp.provider!r} is not a project of the workspace "
                                    f"(have {', '.join(sorted(known))})")  # fmt: skip
        return [i for i in everything if i.provider == link.target]

    def lock(self, consumer: str) -> tuple[Path, dict[str, Any] | None]:
        path = self.ws.project(consumer).path / LOCK_FILE  # type: ignore[union-attr]
        return path, read_lock(path, consumer)


def _manifest_problems(ws: Workspace, report: Report) -> bool:
    """Record the manifest's problems; True when it has an error (nothing can run)."""
    report.problems = list(ws.problems)
    return bool(ws.errors)


def _links_of(ws: Workspace, consumer: str | None, report: Report) -> list[Link] | None:
    if consumer is not None and ws.project(consumer) is None:
        have = ", ".join(ws.names()) or "none"
        msg = f"no project {consumer!r} in the workspace (have {have})"
        report.problems.append(Problem("unknown_project", msg))
        return None
    return [lk for lk in ws.links if lk.contract and (consumer is None or lk.source == consumer)]


def _error_link(link: Link, err: ContractError) -> dict[str, Any]:
    return {"consumer": link.source, "provider": link.target, "contract": link.contract,
            "status": "error", "error": {"code": err.code, "message": err.message},
            "imports": 0, "pinned": None, "current": None, "version_changed": None,
            "spec_hash_changed": None, "spec_ref_changed": None, "findings": [], "gaps": [],
            "live": None}  # fmt: skip


def _run_live(
    ws: Workspace,
    link: Link,
    imports: Sequence[Import],
    current: Mapping[str, Any],
    static_only: bool,
    clone_env: Mapping[str, str] | None,
) -> dict[str, Any]:
    """The ``live`` entry of a live link. Raises :class:`ContractError` when it cannot run."""
    if static_only:
        return {"status": "not_run", "reason": "static-only"}
    from lattice.contracts import live

    try:
        res = live.run_live(ws, link, imports, current, clone_env=clone_env)
    except NotImplementedError:
        return {"status": "not_built", "reason": "live check not built"}
    except live.LiveError as e:
        raise ContractError(e.code, e.message) from None
    out = dict(res)
    out.setdefault("status", "ok")
    out["findings"] = sorted(out.get("findings", []), key=_sort_key)
    out.setdefault("verified_live", [])
    return out


def _check_link(s: _Session, link: Link, static_only: bool,
                clone_env: Mapping[str, str] | None) -> dict[str, Any]:  # fmt: skip
    cur = s.export(link.target)
    imports = s.imports(link)
    lock_path, lock = s.lock(link.source)
    entry = (lock or {}).get("providers", {}).get(link.target)
    if entry is None:
        why = "has no lock" if lock is None else f"has no pin for {link.target}"
        raise ContractError("no_pin", f"{link.source} {why} ({lock_path}): run "
                            f"`lattice contracts pin {link.source}`")  # fmt: skip
    pinned_v = version_of(entry)
    cur_v = version_of(cur)
    broken, gaps = evaluate(imports, cur["export"], entry["export"])
    changed = changed_of(entry["export"], cur["export"], broken, imports)
    live = None
    live_findings: list[dict[str, Any]] = []
    if link.contract == "live":
        live = _run_live(s.ws, link, imports, cur["export"], static_only, clone_env)
        live_findings = list(live.get("findings", []))
    all_findings = sorted([*broken, *changed], key=_sort_key)
    if any(f["class"] == BROKEN for f in [*all_findings, *live_findings]):
        status = "broken"
    elif all_findings or live_findings:
        status = "changed"
    else:
        status = "ok"
    return {
        "consumer": link.source, "provider": link.target, "contract": link.contract,
        "status": status, "error": None, "imports": len(imports),
        "pinned": pinned_v, "current": cur_v,
        "version_changed": (pinned_v["git_ref"], pinned_v["dirty"])
        != (cur_v["git_ref"], cur_v["dirty"]),
        "spec_hash_changed": pinned_v["spec_hash"] != cur_v["spec_hash"],
        "spec_ref_changed": pinned_v["spec_ref"] != cur_v["spec_ref"],
        "findings": all_findings, "gaps": gaps, "live": live,
    }  # fmt: skip


def run_check(ws: Workspace, consumer: str | None = None, *, static_only: bool = False,
              clone_env: Mapping[str, str] | None = None) -> Report:  # fmt: skip
    """Check the opted-in links (of `consumer`, or of every project): see the module docstring.
    Never raises on content; errors are in ``Report.problems`` and the links' ``error``."""
    t0 = time.monotonic()
    report = Report("check", ws.name, consumer)
    if _manifest_problems(ws, report):
        return report
    links = _links_of(ws, consumer, report)
    session = _Session(ws)
    for lk in links or []:
        try:
            report.links.append(_check_link(session, lk, static_only, clone_env))
        except ContractError as e:
            report.links.append(_error_link(lk, e))
    report.elapsed_s = time.monotonic() - t0
    return report


def run_pin(ws: Workspace, consumer: str, *, force: bool = False) -> Report:
    """Pin the current exports of `consumer`'s opted-in providers into its lock. Refuses (exit 1,
    nothing written) while a link has a BROKEN finding against the existing pin, unless `force`;
    with nothing opted in or an error, nothing is written (exit 2)."""
    t0 = time.monotonic()
    report = Report("pin", ws.name, consumer)
    if _manifest_problems(ws, report):
        return report
    links = _links_of(ws, consumer, report)
    if links is None:
        return report
    session = _Session(ws)
    if not links:
        msg = (
            f"{consumer} has no link with a contract key "
            '(set contract = "static" on a [[link]] to opt in)'
        )
        report.problems.append(Problem("no_links", msg))
        return report
    lock_path = ws.project(consumer).path / LOCK_FILE  # type: ignore[union-attr]
    new_entries: dict[str, Any] = {}
    existing: dict[str, Any] = {}
    for lk in links:
        try:
            cur = session.export(lk.target)
            imports = session.imports(lk)
            _path, lock = session.lock(consumer)
            existing = (lock or {}).get("providers", {})
        except ContractError as e:
            report.links.append(_error_link(lk, e))
            continue
        old = existing.get(lk.target)
        broken: list[dict[str, Any]] = []
        if old is not None:
            broken, _gaps = evaluate(imports, cur["export"], old["export"])
        row = {
            "consumer": consumer, "provider": lk.target, "contract": lk.contract,
            "status": "refused" if broken and not force else "pinned", "error": None,
            "imports": len(imports), "replaced": old is not None,
            "forced": bool(broken and force), "broken": broken,
            "current": version_of(cur),
            "endpoints": len(cur["export"]["endpoints"]),
        }  # fmt: skip
        report.links.append(row)
        if row["status"] == "pinned":
            new_entries[lk.target] = {"spec_ref": cur["spec_ref"], "version": cur["version"],
                                      "export": cur["export"]}  # fmt: skip
    if report.exit_code == 0 and new_entries:
        providers = {**existing, **new_entries}
        write_lock(lock_path, {"format": LOCK_FORMAT, "consumer": consumer,
                               "providers": providers})  # fmt: skip
        report.extra["lock"] = str(lock_path)
    report.extra["written"] = report.exit_code == 0 and bool(new_entries)
    report.elapsed_s = time.monotonic() - t0
    return report
