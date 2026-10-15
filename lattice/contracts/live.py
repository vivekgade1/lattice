"""The live contract check (decision 11): replay the provider's own 2xx spec step for each import
on a provider app that the check starts itself.

The provider app is started by the check on a throwaway database (through
``start_target_harness``, in-process by default), never reached through a URL: a replay mutates
data. The database is a SQLite file, or, for a provider whose harness says ``mysql``
(:data:`LIVE_DATABASES`), a clone of its seed on one throwaway MySQL container that the check
starts itself (``lattice-live-mysql-*``, the image already present, never pulled), shared by the
replays of the run and removed at its end; a server the user named (``$LATTICE_MYSQL_URL``,
``database_url``) is dropped from the child's environment and never used. A link's ``live_url``
is refused (:class:`LiveError`, code ``not_loopback`` for a non-loopback host,
``live_url_unsupported`` for a loopback one) before anything is started, so no request is sent.
The provider's ``app``, ``db``, ``login``, ``src`` and ``env`` come from its ``studio.toml`` when
it has a usable one, else from the manifest's ``[[project]]`` keys
(:meth:`lattice.workspace.WsProject.settings`; ``env`` through
:meth:`lattice.workspace.WsProject.expanded_env`).

Per import: the first 2xx step for that endpoint (scenario then step order) in the provider's
current spec, made by ``as_user`` (anonymous for ``public``, any handle for a ``token`` import
without ``as_user``), replayed up to that step on a fresh seed (the scenario's ``given`` rows,
then its earlier steps without assertions; an ``Await`` is skipped, no service runs). The answer
is compared with the import, never with the pin:

* a status that is not in the import's ``status``: 401 or 403 where the import handles a 2xx give
  ``auth_changed``, anything else ``status_not_observed``;
* a 2xx answer without a relied response field: ``response_field_missing`` (an empty list answer
  shows no field names: it is listed under ``fields_unobservable``, never a finding).

An import of an endpoint the provider excludes from its contract (``excluded`` in the current
export) is not replayed: it is listed under ``not_replayed``, and is BROKEN in the static part.

Static gaps (claims the current export does not hold) that the live answer satisfies are listed
under ``verified_live``. Request fields are not checked (the replay sends the spec step's body,
not the consumer's): the result says ``not_checked: ["request_fields"]``.

Isolation. The replay runs in a child process (``python -m lattice.contracts.live``), because the
providers' packages are all named ``spec``, the app reads its environment and working directory,
and a long-lived caller must not keep a provider's modules. The child runs with a private
``TMPDIR`` that is removed afterwards, so neither the throwaway database nor any other temporary
file outlives the check. On a timeout the child gets SIGTERM and :data:`TERM_GRACE_S` to unwind;
after any child exit the parent removes only what the child's markers in that ``TMPDIR`` name (the
containers carrying this run's ``lattice.owner`` label, the app process groups it recorded).
The provider's code is executed (its spec module, the app and, through the spec's steps, whatever
the app does for them): use scratch clones, never a shared checkout.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from lattice.contracts.check import Import
    from lattice.workspace import Link, Workspace

LIVE_KINDS = ("status_not_observed", "auth_changed", "response_field_missing")
NOT_CHECKED = ["request_fields"]
TIMEOUT_S = 300.0
TERM_GRACE_S = 15.0  # SIGTERM's grace before the child's group is killed (a timeout)
LIVE_DATABASES = ("sqlite", "mysql")
CONTAINER_PREFIX = "lattice-live-"  # the throwaway server of a live run: lattice-live-mysql-<hex>
OWNER_ENV = "LATTICE_CONTAINER_OWNER"  # dbserver's label token: lattice.owner=<value>
OWNER_RE = re.compile(r"live-[0-9a-f]{16}")
SERVER_MARKER = "live-server.json"  # {"owner": ...}, written before `docker run`
PID_MARKER = "live-pids"  # one pid per line, appended when an app process was spawned
AUTH_REFUSALS = (401, 403)
EXCLUDED_REASON = "excluded by the provider (exclude)"

# the shapes of an answer body whose field names can be read
OBJECT, LIST, EMPTY_LIST, OTHER = "object", "list", "empty_list", "other"


class LiveError(Exception):
    """The live check cannot run (the link's ``live_url`` is refused, the provider app does not
    start, ...): ``code`` is a short machine code, the message says why. The link is an error and
    the exit code is 2."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- the parent: refuse, run the child, judge ------------------------------------------------


def _refuse_url(link: Link) -> None:
    """A ``live_url`` is never a target: refuse it, naming the guardrail that decides the code."""
    if link.live_url is None:
        return
    from lattice.security.target import UnsafeTargetError, ensure_safe_target

    try:
        host = urlparse(link.live_url).hostname or ""
    except ValueError:
        host = ""
    try:
        ensure_safe_target(host)
    except UnsafeTargetError as e:
        raise LiveError("not_loopback", f"{link.source} -> {link.target}: live_url "
                        f"{link.live_url!r} is refused ({e})") from None  # fmt: skip
    raise LiveError(
        "live_url_unsupported",
        f"{link.source} -> {link.target}: live_url {link.live_url!r} is loopback but a live "
        "check never targets a URL (a replay mutates data): remove live_url; the check starts "
        "the provider app itself on a throwaway database",
    )


def _who(imp: Import) -> str:
    return "public" if imp.auth == "public" else (f"as_user:{imp.as_user}" if imp.as_user
                                                   else "token")  # fmt: skip


def _finding(imp: Import, kind: str, item: str, detail: str) -> dict[str, Any]:
    return {"class": "BROKEN", "provider": imp.provider, "endpoint": imp.endpoint, "kind": kind,
            "item": item, "detail": detail}  # fmt: skip


def judge(imp: Import, outcome: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The BROKEN findings of one replayed import: `outcome` is ``{"answered": status, "shape":
    ..., "fields": [names]}``. Judged against the import only."""
    status = int(outcome["answered"])
    out: list[dict[str, Any]] = []
    handled = ", ".join(str(s) for s in imp.status)
    if status not in imp.status:
        wants_success = any(200 <= s < 300 for s in imp.status)
        if status in AUTH_REFUSALS and wants_success:
            out.append(_finding(imp, "auth_changed", _who(imp), f"the live answer was {status} "
                                f"where the import handles {handled}"))  # fmt: skip
        else:
            wanted = sorted(s for s in imp.status if 200 <= s < 300) or sorted(imp.status)
            out.append(_finding(imp, "status_not_observed", str(wanted[0]),
                                f"the live answer was {status}; the import handles "
                                f"{handled}"))  # fmt: skip
    if 200 <= status < 300 and outcome["shape"] != EMPTY_LIST:
        have = set(outcome["fields"])
        for f in imp.response_fields:
            if f not in have:
                out.append(_finding(imp, "response_field_missing", f,
                                    f"the live answer has fields {sorted(have)}"))  # fmt: skip
    return out


def _verified(imp: Import, outcome: Mapping[str, Any], current: Mapping[str, Any]
              ) -> list[dict[str, Any]]:  # fmt: skip
    """The static gaps of `imp` (claims the current export does not hold) that the live answer
    satisfies: the answered status, a relied response field that is in a 2xx answer, the auth of
    a 2xx answer. Request fields are not checked live."""
    from lattice.contracts.check import GAP_KINDS, claims_of, holds

    status = int(outcome["answered"])
    ok = 200 <= status < 300
    have = set(outcome["fields"])
    out = []
    for kind, item in claims_of(imp):
        if kind in ("endpoint", "request_field") or holds(current, imp, kind, item):
            continue
        satisfied = (
            (kind == "status" and int(item) == status)
            or (kind == "response_field" and ok and item in have)
            or (kind == "auth" and ok)
        )
        if satisfied:
            out.append({"provider": imp.provider, "endpoint": imp.endpoint,
                        "kind": GAP_KINDS[kind], "item": item})  # fmt: skip
    return out


def _stop_child(proc: subprocess.Popen[str]) -> None:
    """End the child's process group after a timeout: SIGTERM (the child unwinds: its app, its
    clone and its container go), then SIGKILL after :data:`TERM_GRACE_S`."""
    with contextlib.suppress(OSError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.communicate(timeout=TERM_GRACE_S)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()


def _sweep(tmp: Path, owner: str) -> None:
    """After a child's exit, whatever its markers in `tmp` name and is still alive: the app
    process groups it recorded, then the containers labelled with this run's `owner`. No marker,
    nothing is looked at and no ``docker`` is called."""
    pids = tmp / PID_MARKER
    if pids.is_file():
        for pid in sorted({int(w) for w in pids.read_text().split() if w.isdigit()}):
            with contextlib.suppress(OSError):  # gone, or not ours to signal
                if os.getpgid(pid) == pid:  # the app's own session leader
                    os.killpg(pid, signal.SIGKILL)
    marker = tmp / SERVER_MARKER
    if not marker.is_file() or not OWNER_RE.fullmatch(owner):
        return
    try:
        named = json.loads(marker.read_text(encoding="utf-8")).get("owner")
    except (OSError, ValueError, AttributeError):
        return
    if named != owner:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        listed = subprocess.run(  # noqa: S603
            ["docker", "ps", "-aq", "--filter", f"label=lattice.owner={owner}"],  # noqa: S607
            capture_output=True, text=True, timeout=60, check=False)  # fmt: skip
        ids = listed.stdout.split()
        if ids:
            subprocess.run(  # noqa: S603
                ["docker", "rm", "-f", "-v", *ids],  # noqa: S607
                capture_output=True, text=True, timeout=120, check=False)  # fmt: skip


def _spawn(job: Mapping[str, Any], project: Path, env: Mapping[str, str], tmp: Path
           ) -> dict[str, Any]:  # fmt: skip
    """Run the child on `job`; its result dict, or a :class:`LiveError`."""
    job_file, out_file = tmp / "job.json", tmp / "out.json"
    job_file.write_text(json.dumps(job), encoding="utf-8")
    cmd = [sys.executable, "-P", "-m", "lattice.contracts.live", "--job", str(job_file),
           "--out", str(out_file)]  # fmt: skip
    try:
        proc = subprocess.Popen(cmd, cwd=project, env=dict(env), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)  # fmt: skip
    except OSError as e:
        raise LiveError("live_failed", f"cannot run the live check of {project}: {e}") from None
    try:
        _stdout, stderr = proc.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _stop_child(proc)
        raise LiveError("live_timeout", f"the live check of {project} took more than "
                        f"{TIMEOUT_S:g} s") from None  # fmt: skip
    finally:
        with contextlib.suppress(OSError):  # a grandchild the app left behind
            os.killpg(proc.pid, signal.SIGKILL)
    try:
        got = json.loads(out_file.read_text(encoding="utf-8"))
        assert isinstance(got, dict)
    except (OSError, ValueError, AssertionError):
        tail = (stderr.strip().splitlines() or ["no message"])[-1]
        raise LiveError("live_failed", f"the live check of {project} produced no result "
                        f"({tail})") from None  # fmt: skip
    if not got.get("ok"):
        raise LiveError(str(got.get("code", "live_failed")), str(got.get("message", "")))
    return got


def run_live(
    workspace: Workspace,
    link: Link,
    imports: Sequence[Import],
    current_export: Mapping[str, Any],
    *,
    clone_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Replay the imports of `link` (``link.source`` -> ``link.target``, the imports already
    filtered to that provider) against the provider app and return::

        {
          "status": "ok",
          "replayed": 7,                      # imports that had a 2xx step and were replayed
          "not_replayed": [                   # sorted by endpoint
            {"endpoint": "GET /x", "reason": "no 2xx step for as_user eve"},
          ],
          "replays": [                        # sorted by (endpoint, auth), one per replayed import
            {"endpoint": "GET /x", "auth": "as_user:eve", "scenario": "me", "step": 2,
             "answered": 200},            # "step" is 1-based; "awaits_skipped" only when > 0
          ],
          "findings": [                       # BROKEN only, sorted by (endpoint, kind, item)
            {"class": "BROKEN", "provider": "scheduler", "endpoint": "POST /a/{}/runs",
             "kind": "status_not_observed",   # | "auth_changed" | "response_field_missing"
             "item": "201",                   # the import's (lowest 2xx) status, "public" /
                                              # "token" / "as_user:eve", or the missing field
             "detail": "the live answer was 200; the import handles 201"},
          ],
          "verified_live": [                  # static GAPs the live answer satisfied
            {"provider": "scheduler", "endpoint": "POST /auth/login",
             "kind": "response_field_unobserved", "item": "access_token"},
          ],
          "fields_unobservable": [            # 2xx answers that were an empty list
            {"endpoint": "GET /x", "fields": ["id"], "reason": "the live answer is an empty list"},
          ],
          "not_checked": ["request_fields"],
          "database": {"engine": "mysql", "image": "mysql:8.4", "throwaway": True,
                       "ignored": ["LATTICE_MYSQL_URL", "database_url"]},  # server dialects only
        }

    ``database`` is present only when the provider's harness uses a server dialect (never for
    SQLite, whose result is exactly as before): ``ignored`` lists, sorted, which of the user's
    own server settings (``$LATTICE_MYSQL_URL``, the harness's ``database_url``) were set and
    were not used.

    `current_export` is the provider's current export (the ``export`` dict of
    :func:`lattice.contracts.export.compute_export`), from which the static gaps follow
    (:func:`lattice.contracts.check.claims_of` and :func:`~lattice.contracts.check.holds`). An
    import of an endpoint in its ``excluded`` is not sent to the child: it is listed in
    ``not_replayed`` with the reason :data:`EXCLUDED_REASON`, and ``replayed`` counts the rest
    (the exclusion governs the contract, not the app: the spec's scenarios still send an excluded
    endpoint as an earlier step).
    `clone_env` is extra environment for the provider app (it wins over the manifest's ``env``).
    Raises :class:`LiveError`: ``not_loopback`` / ``live_url_unsupported`` (a ``live_url``),
    ``no_harness`` (the provider has no usable app/db), ``provider_missing``, ``spec_failed``,
    ``provider_start_failed``, ``unsupported_database`` (a database not in
    :data:`LIVE_DATABASES`), ``driver_missing``, ``docker_unavailable``, ``docker_image_missing``,
    ``database_start_failed`` (a server dialect), ``live_timeout``, ``live_failed``.
    """
    _refuse_url(link)
    project = workspace.project(link.target)
    if project is None:
        raise LiveError("provider_missing", f"{link.target} is not a project of the workspace")
    if not project.exists:
        raise LiveError("provider_missing", f"{link.target}: {project.path} is not a directory")
    from lattice.contracts.export import DEFAULT_SPEC

    excluded = set(current_export.get("excluded", ()))
    replayable = [imp for imp in imports if imp.endpoint not in excluded]
    root = str(Path(__file__).resolve().parent.parent.parent)
    env = {**os.environ, **project.expanded_env(), **dict(clone_env or {})}
    env["PYTHONPATH"] = os.pathsep.join(
        [root, *([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])]
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    owner = f"live-{secrets.token_hex(8)}"
    env[OWNER_ENV] = owner  # a throwaway container of this run carries lattice.owner=<owner>
    tmp = Path(tempfile.mkdtemp(prefix="lattice-live-"))
    env["TMPDIR"] = str(tmp)
    try:
        job = {
            "project": str(project.path),
            "spec": project.spec or DEFAULT_SPEC,
            "settings": {
                k: v for k, v in project.settings().items() if k not in ("env", "exclude")
            },
            "imports": [imp.to_dict() for imp in replayable],
        }
        got = _spawn(job, project.path, env, tmp)
    finally:
        _sweep(tmp, owner)
        shutil.rmtree(tmp, ignore_errors=True)

    findings: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    verified: dict[tuple[str, str, str], dict[str, Any]] = {}
    unobservable: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    not_replayed: list[dict[str, str]] = [
        {"endpoint": imp.endpoint, "reason": EXCLUDED_REASON}
        for imp in imports
        if imp.endpoint in excluded
    ]
    replays: list[dict[str, Any]] = []
    for imp, res in zip(replayable, got["outcomes"], strict=True):
        if "reason" in res:
            not_replayed.append({"endpoint": imp.endpoint, "reason": res["reason"]})
            continue
        replay = {"endpoint": imp.endpoint, "auth": _who(imp), "scenario": res["scenario"],
                  "step": res["step"], "answered": res["answered"]}  # fmt: skip
        if res.get("awaits_skipped"):
            replay["awaits_skipped"] = res["awaits_skipped"]
        replays.append(replay)
        for f in judge(imp, res):
            findings[(f["endpoint"], f["kind"], f["item"], f["class"])] = f
        for v in _verified(imp, res, current_export):
            verified[(v["endpoint"], v["kind"], v["item"])] = v
        if 200 <= res["answered"] < 300 and res["shape"] == EMPTY_LIST and imp.response_fields:
            key = (imp.endpoint, tuple(imp.response_fields))
            unobservable[key] = {"endpoint": imp.endpoint, "fields": list(imp.response_fields),
                                 "reason": "the live answer is an empty list"}  # fmt: skip
    out: dict[str, Any] = {
        "status": "ok",
        "replayed": len(replays),
        "not_replayed": sorted(
            {(n["endpoint"], n["reason"]): n for n in not_replayed}.values(),
            key=lambda n: (n["endpoint"], n["reason"]),
        ),
        "replays": sorted(
            replays, key=lambda r: (r["endpoint"], r["auth"], r["scenario"], r["step"])
        ),  # fmt: skip
        "findings": sorted(
            findings.values(), key=lambda f: (f["endpoint"], f["kind"], f["item"])
        ),  # fmt: skip
        "verified_live": sorted(
            verified.values(), key=lambda v: (v["endpoint"], v["kind"], v["item"])
        ),  # fmt: skip
        "fields_unobservable": sorted(
            unobservable.values(), key=lambda u: (u["endpoint"], u["fields"])
        ),  # fmt: skip
        "not_checked": list(NOT_CHECKED),
    }
    if got.get("database") is not None:  # a server dialect: the throwaway server's description
        out["database"] = got["database"]
    return out


# ---- the child: load the provider, start its app, replay -------------------------------------


class _Fatal(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def select_step(spec: Any, endpoint: str, auth: str, as_user: str | None
                ) -> tuple[Any, int] | str:  # fmt: skip
    """``(scenario, index)`` of the first request step of `endpoint` that expects a 2xx and is
    made by the import's caller (anonymous for ``public``; `as_user`, else any handle, for
    ``token``), in scenario then step order; else the reason there is none."""
    from lattice.contracts.export import endpoint_key
    from lattice.spec import Step

    seen = False
    for scenario in spec.scenarios:
        for i, st in enumerate(scenario.steps):
            if not isinstance(st, Step) or endpoint_key(st.method, st.path) != endpoint:
                continue
            seen = True
            if not 200 <= int(st.expect_status) < 300:
                continue
            if auth == "public":
                match = st.user is None
            elif as_user is not None:
                match = st.user == as_user
            else:
                match = st.user is not None
            if match:
                return scenario, i
    who = "public" if auth == "public" else (f"as_user {as_user}" if as_user else "any handle")
    if not seen:
        return "the endpoint is not in the provider's current spec"
    return f"no 2xx step for {who}"


def _harness(project: Path, settings: Mapping[str, Any]) -> Any:
    """The provider's :class:`lattice.perf.measure.Harness`: its ``studio.toml`` when usable
    (read exactly as ``lattice check`` does), else the manifest's keys."""
    from lattice.perf.measure import Harness, MeasureError, harness_from_project
    from lattice.spec.__main__ import load
    from lattice.spec.check import CheckError
    from lattice.spec.process import process_runtime, returns_process_app

    problem = ""
    if (project / "studio.toml").is_file():
        try:
            return harness_from_project(project)
        except MeasureError as e:
            problem = str(e)
            if not (settings.get("app") and settings.get("db")):
                raise _Fatal("no_harness", f"{project.name}: {e}") from None
    if not (settings.get("app") and settings.get("db")):
        raise _Fatal("no_harness", f"{project.name} sets no app and db for the live check "
                     "(a [[project]] app = \"module:factory\" and db = \"module:build_db\", "
                     "or [app] in its studio.toml)")  # fmt: skip
    for p in (project, project / str(settings["src"])) if settings.get("src") else (project,):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    try:
        app_factory = load(str(settings["app"]))
        db_factory = load(str(settings["db"]))
        login = load(str(settings["login"])) if settings.get("login") else None
        runtime = process_runtime() if returns_process_app(app_factory) else None
    except (CheckError, ImportError, AttributeError) as e:
        raise _Fatal("no_harness", f"{project.name}: the app harness does not load: {e}"
                     + (f" (studio.toml: {problem})" if problem else "")) from None  # fmt: skip
    return Harness(app_factory, db_factory, runtime, login, "sqlite", None, project)


def _fields_of(body: Any) -> tuple[str, list[str]]:
    """``(shape, names)`` of an answer body: an object's keys, a list's dict items' keys (an
    empty list shows none)."""
    if isinstance(body, dict):
        return OBJECT, sorted(str(k) for k in body)
    if isinstance(body, list):
        if not body:
            return EMPTY_LIST, []
        names: set[str] = set()
        for item in body:
            if isinstance(item, dict):
                names.update(str(k) for k in item)
        return LIST, sorted(names)
    return OTHER, []


def _replay_one(harness: Any, spec: Any, scenario: Any, index: int,
                databases: Any = None) -> dict[str, Any]:  # fmt: skip
    """One replay on a fresh seeded throwaway database: ``given``, the earlier steps (answers
    ignored), then the target step. A result with ``reason`` when it could not be sent.
    `databases`: the run's shared server's ``make_db`` (a server dialect): the replay's database
    is a clone of its seed, dropped afterwards; ``None``: a SQLite file."""
    from lattice.security.schema import AttackRequest
    from lattice.security.target import start_target_harness
    from lattice.spec import Step
    from lattice.spec.check import _insert_given

    given_error: list[str] = []

    def seed(db: Any) -> None:
        if scenario.given:
            try:
                _insert_given(spec, db, scenario.given)
            except Exception as e:  # noqa: BLE001 -- reported as not replayed
                given_error.append(f"{type(e).__name__}: {e}")

    def request(st: Step) -> Any:
        return target.request(AttackRequest(
            st.method, st.path, as_user=st.user, json=st.json,
            params=dict(st.params) if st.params else None))  # fmt: skip

    extra = {} if databases is None else {"databases": databases}
    skipped = 0
    with start_target_harness(harness, seed, **extra) as target:
        if given_error:
            return {"reason": f"the scenario's given rows were refused ({given_error[0]})"}
        for st in scenario.steps[:index]:
            if not isinstance(st, Step):
                skipped += 1  # an Await: no service runs in a live replay
                continue
            with contextlib.suppress(Exception):  # sent without assertions
                request(st)
        step = scenario.steps[index]
        try:
            ans = request(step)
        except Exception as e:  # noqa: BLE001 -- e.g. the login of the step's user failed
            who = f"as {step.user}" if step.user else "anonymously"
            return {"reason": f"the request {who} could not be sent ({type(e).__name__}: {e})"}
    shape, names = _fields_of(ans.body)
    out = {"scenario": scenario.name, "step": index + 1, "answered": int(ans.status),
           "shape": shape, "fields": names}  # fmt: skip
    if skipped:
        out["awaits_skipped"] = skipped
    return out


def _missing_requirements(requires: Sequence[str]) -> list[str]:
    """The pip requirements (``name`` or ``name>=x.y``) that the running interpreter lacks."""
    from importlib import metadata

    def numbers(text: str) -> tuple[int, ...]:
        return tuple(int(n) for n in re.findall(r"\d+", text)[:4])

    missing = []
    for req in requires:
        m = re.match(r"\s*([A-Za-z0-9_.-]+)\s*(?:(>=|==)\s*([0-9][0-9A-Za-z.]*))?", req)
        if m is None:
            continue
        name, op, want = m.groups()
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(req.strip())
            continue
        if want and (
            numbers(have) < numbers(want) if op == ">=" else numbers(have) != numbers(want)
        ):
            missing.append(req.strip())
    return missing


def _image_present(image: str) -> bool:
    """Whether the Docker image is already local (``docker image inspect``): never pulled."""
    try:
        out = subprocess.run(  # noqa: S603
            ["docker", "image", "inspect", image],  # noqa: S607
            capture_output=True, text=True, timeout=30, check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


class _ServerRun:
    """The one throwaway server of a live run on a server dialect (L2-L5): opened at the first
    replay that is sent (a run that replays nothing starts nothing), shared by every replay,
    which gets a clone of the harness's seed; closed (clones, then the container) at the end.

    The user's own servers are never used: every ``LATTICE_<DIALECT>_URL`` is removed from this
    process's environment (the provider app inherits it) and the harness's ``database_url`` is
    not read."""

    def __init__(self, project: str, harness: Any, engine: str, tmp: Path) -> None:
        from lattice.spec import engines
        from lattice.spec.db import SERVER_DIALECTS

        self.project, self.harness, self.engine, self.tmp = project, harness, engine, tmp
        self.desc = engines.engine(engine)
        var = f"LATTICE_{engine.upper()}_URL"
        given = {var: os.environ.get(var), "database_url": getattr(harness, "database_url", None)}
        self.ignored = sorted(k for k, v in given.items() if v)
        for name in {*SERVER_DIALECTS, *engines.REGISTRY.names()}:
            os.environ.pop(f"LATTICE_{name.upper()}_URL", None)
        self._dbs: Any = None
        self._server: Any = None

    def describe(self) -> dict[str, Any]:
        return {"engine": self.engine, "image": self.desc.server.image, "throwaway": True,
                "ignored": self.ignored}  # fmt: skip

    def databases(self) -> Any:
        """The shared ``make_db`` (opening the server first, at its first use)."""
        if self._dbs is None:
            self._open()
        return self._dbs

    def _open(self) -> None:
        from lattice.spec import dbserver

        image = self.desc.server.image
        missing = _missing_requirements(self.desc.requires)
        if missing:
            raise _Fatal("driver_missing", f"{self.project}: the live check on {self.engine} "
                         f"needs {', '.join(missing)} in {sys.executable} (the Python that runs "
                         "it): install lattice[db]")  # fmt: skip
        if not dbserver.docker_available():
            raise _Fatal("docker_unavailable", f"{self.project}: the live check on "
                         f"{self.engine} starts a throwaway {image} container and Docker is "
                         "not available (no docker CLI, or its daemon does not "
                         "answer)")  # fmt: skip
        if not _image_present(image):
            raise _Fatal("docker_image_missing", f"{self.project}: the image {image} is not "
                         "present locally and the live check never pulls: run "
                         f"`docker pull {image}`")  # fmt: skip
        owner = os.environ.get(OWNER_ENV, "")
        (self.tmp / SERVER_MARKER).write_text(json.dumps({"owner": owner}), encoding="utf-8")
        try:
            server, owned = dbserver.server_for(self.engine, None, name_prefix=CONTAINER_PREFIX)
        except Exception as e:  # noqa: BLE001 -- docker run / wait_ready / a driver error
            raise _Fatal("database_start_failed", f"{self.project}: the throwaway {self.engine} "
                         f"server did not start ({type(e).__name__}: {e})") from None  # fmt: skip
        if not owned:  # never a server somebody else named
            server.stop()
            raise _Fatal("database_start_failed", f"{self.project}: refusing a {self.engine} "
                         "server that the check did not start")  # fmt: skip
        self._server = server
        self._dbs = dbserver.ServerDatabases(server, self.harness.db_factory)

    def close(self) -> None:
        dbs, server = self._dbs, self._server
        self._dbs = self._server = None
        if dbs is not None:
            with contextlib.suppress(Exception):
                dbs.close()
        if server is not None:
            with contextlib.suppress(Exception):
                server.stop()


def _on_term(signum: int, _frame: Any) -> None:
    sys.exit(128 + signum)  # unwind: `with`/`finally` stop the app, drop the clone, rm the server


def _record_app_pids(path: Path) -> None:
    """Append the pid of every process app this child spawns to `path` (the parent kills what is
    still alive after the child's exit)."""
    from lattice.spec import process

    spawn = process.ProcessApp._spawn  # noqa: SLF001

    def recording(self: Any) -> None:
        spawn(self)
        if self.pid is not None:
            with path.open("a", encoding="utf-8") as f:
                f.write(f"{self.pid}\n")

    process.ProcessApp._spawn = recording  # type: ignore[method-assign]  # noqa: SLF001


def _child(job: Mapping[str, Any]) -> dict[str, Any]:
    project = Path(job["project"]).resolve()
    settings = job["settings"]
    os.chdir(project)
    from lattice.contracts.export import load_spec
    from lattice.security.target import UnsafeTargetError

    try:
        spec = load_spec(project, job["spec"])
    except Exception as e:  # noqa: BLE001
        tail = f"{type(e).__name__}: {e}"
        raise _Fatal("spec_failed", f"cannot load {job['spec']} from {project}: {tail}") from None
    harness = _harness(project, settings)
    engine = getattr(harness, "database", "sqlite")
    if engine not in LIVE_DATABASES:
        raise _Fatal("unsupported_database", f"{project.name}: the live check runs on a "
                     f"throwaway database of {', '.join(LIVE_DATABASES)}, not "
                     f"{engine}")  # fmt: skip
    server = None if engine == "sqlite" else _ServerRun(
        project.name, harness, engine, Path(tempfile.gettempdir()))  # fmt: skip
    outcomes: list[dict[str, Any]] = []
    cache: dict[tuple[str, int], dict[str, Any]] = {}
    try:
        for imp in job["imports"]:
            picked = select_step(spec, imp["endpoint"], imp["auth"], imp.get("as_user"))
            if isinstance(picked, str):
                outcomes.append({"reason": picked})
                continue
            scenario, index = picked
            key = (scenario.name, index)
            if key not in cache:  # imports on one step share its replay (a fresh seed, run once)
                dbs = None if server is None else server.databases()
                try:
                    cache[key] = _replay_one(harness, spec, scenario, index, dbs)
                except UnsafeTargetError as e:
                    raise _Fatal("not_loopback", str(e)) from None
                except Exception as e:  # noqa: BLE001 -- the app did not start
                    raise _Fatal("provider_start_failed", f"{project.name}: the app did not "
                                 f"start ({type(e).__name__}: {e})") from None  # fmt: skip
            outcomes.append(cache[key])
    finally:
        if server is not None:
            server.close()
    result: dict[str, Any] = {"outcomes": outcomes}
    if server is not None:
        result["database"] = server.describe()
    return result


def _main(argv: Sequence[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m lattice.contracts.live")
    ap.add_argument("--job", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(list(argv))
    job = json.loads(Path(a.job).read_text(encoding="utf-8"))
    signal.signal(signal.SIGTERM, _on_term)
    _record_app_pids(Path(tempfile.gettempdir()) / PID_MARKER)
    try:
        result: dict[str, Any] = {"ok": True, **_child(job)}
    except _Fatal as e:
        result = {"ok": False, "code": e.code, "message": e.message}
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "code": "live_failed", "message": f"{type(e).__name__}: {e}"}
    Path(a.out).write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
