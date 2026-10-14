"""The export of a provider project: the facts its spec shows about its HTTP API.

Derived from the loaded :class:`lattice.spec.Spec`, never hand-authored. Only ``Step``s are read
(``Await``s are ignored); the facts are *observed*, a lower bound, because a spec states what its
scenarios exercise, not what the app can do. Per endpoint (``METHOD /templated/path``):

``statuses``        every observed status
``request_fields``  union of ``Step.json`` keys over the 2xx steps
``query_fields``    union of ``Step.params`` keys (and ``?a=b`` names) over the 2xx steps
``response_fields`` union over the 2xx steps of the ``expect_body`` keys (a list: its items' keys)
``body``            observed shapes over the 2xx steps with a body: ``object`` and/or ``list``
``auth``            ``public`` (an anonymous step got a 2xx), ``token`` (anonymous steps exist and
                    every one got 401), else ``unknown``
``granted``         the handles with at least one 2xx
``refused``         the handles with a 403 and no 2xx

An endpoint with no 2xx step is left out (a 405 probe, for instance). Top level: ``format``,
``basis`` (``observed``) and ``error_body`` (``profile.api.error_body``, or null).

A provider can declare routes that are not part of its contract (test harness and internal
triggers) with ``exclude`` in the workspace manifest. The filter is applied here, once, before
the hash: :func:`apply_exclude` drops the listed endpoints from ``endpoints`` and adds
``excluded`` (the sorted, de-duplicated normalised keys, matched or not), only when the list is
not empty, so an export without exclusion has exactly the keys above. ``excluded`` is part of
the hashed export. The spec itself is not filtered (a live replay still sends an excluded
endpoint as an earlier step of a scenario). The result of :func:`build_export` carries
``exclude_unmatched`` beside ``export`` (not hashed): the declared keys that the export does not
hold, only when the list is not empty.

``spec_hash`` is the sha256 of the canonical JSON of the export (sorted keys, ``(",", ":")``
separators, ASCII); the version is ``{spec_hash, git_ref, dirty}`` (``git_ref``: the provider's
``git rev-parse HEAD``, or null; ``dirty``: tracked files differ from HEAD). The export is
computed in a subprocess (:func:`compute_export`) because both projects name their package
``spec``: nothing of the provider's stays imported in the caller.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

FORMAT = 1
DEFAULT_SPEC = "spec:SPEC"
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

_INT = re.compile(r"[0-9]+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_NAMED = re.compile(r"\{[^{}]*\}")
_METHOD = re.compile(r"[A-Za-z]+")


class ExportFailed(Exception):
    """The provider's spec could not be loaded (or its export could not be computed)."""


# ---- path templates --------------------------------------------------------------------------


def template_path(path: str) -> str:
    """The path with every id segment replaced by ``{}``: an all-digit segment, a UUID, or a
    ``{name}`` parameter (names are labels only and are not exported). Literal segments such as
    ``me`` stay. A query string and a trailing slash are not part of the template."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    segs = path.split("/")
    for i, seg in enumerate(segs):
        if _INT.fullmatch(seg) or _UUID.fullmatch(seg) or _NAMED.fullmatch(seg):
            segs[i] = "{}"
    return "/".join(segs)


def endpoint_key(method: str, path: str) -> str:
    """``METHOD /templated/path`` (method upper-cased)."""
    return f"{method.upper()} {template_path(path)}"


def parse_endpoint(text: object) -> str:
    """The endpoint key of an import's ``endpoint`` text (``"POST /jobs/{id}/runs"``), normalised
    like the export's. Raises ``ValueError`` (with the reason) for a malformed endpoint: no method
    or path, a method that is not an HTTP method, a path that does not start with ``/``, a query
    string, whitespace or an empty segment."""
    if not isinstance(text, str):
        raise ValueError(f"endpoint must be a string, not {type(text).__name__}")
    parts = text.split()
    if len(parts) != 2:
        raise ValueError(f"endpoint {text!r} must be 'METHOD /path'")
    method, path = parts
    if not _METHOD.fullmatch(method) or method.upper() not in HTTP_METHODS:
        raise ValueError(f"endpoint {text!r}: {method!r} is not one of {', '.join(HTTP_METHODS)}")
    if not path.startswith("/"):
        raise ValueError(f"endpoint {text!r}: the path must start with '/'")
    if "?" in path or "#" in path:
        raise ValueError(f"endpoint {text!r}: a query string is not part of an endpoint")
    body = path[:-1] if len(path) > 1 and path.endswith("/") else path
    if body != "/" and any(seg == "" for seg in body.split("/")[1:]):
        raise ValueError(f"endpoint {text!r}: empty path segment")
    return endpoint_key(method, path)


def check_exclude(raw: object) -> tuple[tuple[str, ...], list[str]]:
    """``(keys, reasons)`` of an ``exclude`` value: the normalised endpoint keys in declared order
    (:func:`parse_endpoint`, the import rule) and every reason it is not valid (a value that is
    not a list of ``METHOD /path`` strings, a bad entry, a key named twice). Valid when `reasons`
    is empty; ``[]`` is valid and excludes nothing."""
    if not isinstance(raw, (list, tuple)):
        return (), ["exclude must be a list of 'METHOD /path' strings"]
    keys: list[str] = []
    reasons: list[str] = []
    for i, entry in enumerate(raw, 1):
        try:
            keys.append(parse_endpoint(entry))
        except ValueError as e:
            reasons.append(f"exclude entry #{i}: {e}")
    for key in dict.fromkeys(k for k in keys if keys.count(k) > 1):
        reasons.append(f"exclude names {key} twice")
    return tuple(keys), reasons


# ---- the export ------------------------------------------------------------------------------


def _fields(body: Any) -> tuple[str | None, set[str]]:
    """``(shape, keys)`` of an expected body: ``object`` and its keys, ``list`` and the keys of
    its dict items, or ``(None, set())``."""
    if isinstance(body, dict):
        return "object", {str(k) for k in body}
    if isinstance(body, (list, tuple)):
        keys: set[str] = set()
        for item in body:
            if isinstance(item, dict):
                keys.update(str(k) for k in item)
        return "list", keys
    return None, set()


@dataclasses.dataclass
class _Facts:
    """What the steps of one endpoint template show (see the module docstring)."""

    statuses: set[int] = dataclasses.field(default_factory=set)
    request: set[str] = dataclasses.field(default_factory=set)
    query: set[str] = dataclasses.field(default_factory=set)
    response: set[str] = dataclasses.field(default_factory=set)
    body: set[str] = dataclasses.field(default_factory=set)
    anonymous: list[int] = dataclasses.field(default_factory=list)  # anonymous steps' statuses
    granted: set[str] = dataclasses.field(default_factory=set)
    forbidden: set[str] = dataclasses.field(default_factory=set)  # handles with a 403
    ok: bool = False  # some 2xx step

    def auth(self) -> str:
        if any(200 <= s < 300 for s in self.anonymous):
            return "public"
        if self.anonymous and all(s == 401 for s in self.anonymous):
            return "token"
        return "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "auth": self.auth(),
            "body": sorted(self.body),
            "granted": sorted(self.granted),
            "query_fields": sorted(self.query),
            "refused": sorted(self.forbidden - self.granted),
            "request_fields": sorted(self.request),
            "response_fields": sorted(self.response),
            "statuses": sorted(self.statuses),
        }


def export_spec(spec: Any) -> dict[str, Any]:
    """The export (the dict that ``spec_hash`` is computed over) of a loaded ``Spec``."""
    from lattice.spec import Step

    acc: dict[str, _Facts] = {}
    for scenario in spec.scenarios:
        for st in scenario.steps:
            if not isinstance(st, Step):  # an Await
                continue
            facts = acc.setdefault(endpoint_key(st.method, st.path), _Facts())
            status = int(st.expect_status)
            facts.statuses.add(status)
            if st.user is None:
                facts.anonymous.append(status)
            elif status == 403:
                facts.forbidden.add(str(st.user))
            if not 200 <= status < 300:
                continue
            facts.ok = True
            if st.user is not None:
                facts.granted.add(str(st.user))
            if isinstance(st.json, dict):
                facts.request.update(str(k) for k in st.json)
            if "?" in st.path:
                pairs = st.path.split("?", 1)[1].split("#", 1)[0].split("&")
                facts.query.update(q.split("=", 1)[0] for q in pairs if q)
            if st.params:
                facts.query.update(str(k) for k in st.params)
            shape, keys = _fields(st.expect_body)
            if shape:
                facts.body.add(shape)
            facts.response.update(keys)
    api = getattr(getattr(spec, "profile", None), "api", None)
    error_body = getattr(api, "error_body", None)
    return {
        "basis": "observed",
        "endpoints": {k: acc[k].to_dict() for k in sorted(acc) if acc[k].ok},
        "error_body": error_body if isinstance(error_body, str) else None,
        "format": FORMAT,
    }


def apply_exclude(export: dict[str, Any], exclude: Sequence[str] = ()
                  ) -> tuple[dict[str, Any], list[str]]:  # fmt: skip
    """``(export, unmatched)``: `export` without the endpoints named by `exclude` (normalised
    keys) and with ``excluded``, or `export` itself for an empty `exclude`. `unmatched` lists the
    keys the export does not hold (sorted)."""
    keys = sorted(set(exclude))
    if not keys:
        return export, []
    held = export["endpoints"]
    out = dict(export)
    out["endpoints"] = {k: v for k, v in held.items() if k not in keys}
    out["excluded"] = keys
    return out, [k for k in keys if k not in held]


def canonical(obj: Any) -> str:
    """Canonical JSON: sorted keys, ``(",", ":")`` separators, ASCII."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def spec_hash(export: Mapping[str, Any]) -> str:
    """The sha256 (hex) of the canonical JSON of `export`."""
    return hashlib.sha256(canonical(export).encode("ascii")).hexdigest()


def load_spec(project: Path, ref: str) -> Any:
    """The ``Spec`` that ``module:attr`` names, loaded as ``python -m lattice.spec check`` does:
    the project first on ``sys.path``, a stale ``spec`` package dropped from ``sys.modules``, a
    function returning a Spec called. Meant for a fresh process (:func:`compute_export`)."""
    from lattice.spec.__main__ import load

    sys.path.insert(0, str(project))
    for name in [m for m in sys.modules if m == "spec" or m.startswith("spec.")]:
        del sys.modules[name]
    spec = load(ref)
    if callable(spec) and not hasattr(spec, "scenarios"):
        spec = spec()
    return spec


def git_version(project: Path) -> tuple[str | None, bool]:
    """``(git_ref, dirty)``: HEAD of the repository holding `project`, or None; dirty when a
    tracked file differs from HEAD (untracked files do not count)."""

    def git(*args: str) -> str | None:
        try:
            r = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True,
                               timeout=30, check=False)  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    head = git("rev-parse", "HEAD")
    if head is None or not head.strip():
        return None, False
    status = git("status", "--porcelain", "--untracked-files=no")
    return head.strip(), bool(status and status.strip())


def build_export(project: Path, ref: str, exclude: Sequence[str] = ()) -> dict[str, Any]:
    """``{"spec_ref", "version", "export"}`` for the project, computed in this process (the
    subprocess entry of :func:`compute_export`; call that one instead). `exclude` (endpoint
    keys, see :func:`apply_exclude`) is applied before the hash; with a non-empty `exclude` the
    result also has ``exclude_unmatched``. Raises ``ValueError`` for an invalid `exclude`."""
    keys, reasons = check_exclude(list(exclude))
    if reasons:
        raise ValueError("; ".join(reasons))
    export, unmatched = apply_exclude(export_spec(load_spec(project, ref)), keys)
    git_ref, dirty = git_version(project)
    out: dict[str, Any] = {
        "export": export,
        "spec_ref": ref,
        "version": {"dirty": dirty, "git_ref": git_ref, "spec_hash": spec_hash(export)},
    }
    if export.get("excluded"):
        out["exclude_unmatched"] = unmatched
    return out


def compute_export(project: Path | str, ref: str | None = None,
                   exclude: Sequence[str] = ()) -> dict[str, Any]:  # fmt: skip
    """:func:`build_export` in a subprocess (``python -m lattice.contracts export``), so the
    provider's modules never meet the caller's. `exclude` is passed as ``--exclude`` flags.
    Raises :class:`ExportFailed`."""
    project = Path(project).resolve()
    ref = ref or DEFAULT_SPEC
    if not project.is_dir():
        raise ExportFailed(f"{project} is not a directory")
    import lattice

    env = dict(os.environ)
    root = str(Path(lattice.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join([root, *([env["PYTHONPATH"]] if env.get("PYTHONPATH")
                                                 else [])])  # fmt: skip
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [sys.executable, "-P", "-m", "lattice.contracts", "export", "--project", str(project),
           "--spec", ref]  # fmt: skip
    for key in exclude:
        cmd += ["--exclude", key]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=project, env=env,
                           timeout=600, check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError) as e:
        raise ExportFailed(f"cannot run the export of {project}: {e}") from None
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["no message"])[-1]
        tail = tail.removeprefix("lattice contracts: ")
        raise ExportFailed(f"cannot load {ref} from {project}: {tail}")
    try:
        out = json.loads(r.stdout)
        assert isinstance(out["export"], dict) and isinstance(out["version"], dict)
    except (ValueError, KeyError, AssertionError, TypeError):
        raise ExportFailed(f"the export of {project} is not valid JSON") from None
    return out
