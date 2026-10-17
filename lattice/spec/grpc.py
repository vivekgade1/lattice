"""gRPC in the spec runtime (Spike I, unary only).

* :func:`read_services` -- the services and rpcs of a spec's ``.proto`` files, read without
  Go (a small reader of ``service`` blocks: package, rpc names and the ``stream`` flags),
  for ``validate`` (S026, S028).
* :class:`Bridge` -- the pinned Go bridge (``lattice/grpcbridge``: grpc-go, protobuf-go and
  protocompile, a checked-in ``go.sum``), a long-lived process speaking JSON lines. It is
  built once per source hash, offline after the first ``go mod download`` (``-mod=readonly``),
  into ``$LATTICE_GRPCBRIDGE_CACHE`` (default ``~/.cache/lattice/grpcbridge``). It dials only
  loopback with insecure credentials and refuses streaming methods.
* :class:`GrpcTransport` and :class:`GrpcAnswer` -- one unary call: the request in Lattice's
  canonical message form ``lattice.grpcjson/1`` (protojson with proto field names,
  unpopulated fields emitted, enums by name, and -- the one deviation from protojson --
  64-bit integers within +/-2^53 as JSON numbers, not strings); the answer is the
  ``google.rpc.Code`` name, its number, the message and the response body. A fixed 10 s
  deadline per call, not checked behaviour.
* :func:`rpc_auth` (``AuthDriver.rpc``) -- logging in by an rpc; the token is presented as
  the metadata ``authorization: Bearer <token>``.
* :func:`reconcile` -- which extracted rpcs the server answers.

A failed bridge build is a :class:`BridgeBuildError` (a ``CheckError``: ``bridge_build_failed``),
never a silent skip.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lattice.importer.appmodel import RepoModel

BRIDGE_SRC = Path(__file__).resolve().parent.parent / "grpcbridge"
CACHE_KEEP = 3
CALL_TIMEOUT_S = 10.0  # the bridge's per-call deadline
BUILD_TIMEOUT_S = 600
MAX_SAFE = 2**53

# ---- reading a spec's protos (validate) --------------------------------------------------

_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_PACKAGE = re.compile(r"\bpackage\s+([A-Za-z_][\w.]*)\s*;")
_SERVICE = re.compile(r"\bservice\s+([A-Za-z_]\w*)\s*\{")
_RPC = re.compile(
    r"\brpc\s+([A-Za-z_]\w*)\s*\(\s*(stream\s+)?\.?([\w.]+)\s*\)\s*returns\s*\(\s*(stream\s+)?\.?([\w.]+)\s*\)"
)


def _block(text: str, start: int) -> str:
    """The text between the brace at ``start - 1`` and its match."""
    depth = 1
    i = start
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[start : i - 1]


def read_services(
    paths: Sequence[str], root: Path | str | None = None
) -> tuple[dict[str, dict[str, tuple[bool, bool]]], list[str]]:
    """``({service full name: {rpc: (client_streaming, server_streaming)}}, problems)`` of
    the ``.proto`` files ``paths`` (relative to ``root``, default the working directory).
    A file that cannot be read is a problem, not an exception."""
    base = Path(root) if root is not None else Path.cwd()
    services: dict[str, dict[str, tuple[bool, bool]]] = {}
    problems: list[str] = []
    for rel in paths:
        path = base / rel
        try:
            text = _COMMENTS.sub("", path.read_text())
        except OSError as err:
            problems.append(f"cannot read {rel}: {err.strerror or err}")
            continue
        m = _PACKAGE.search(text)
        package = m.group(1) if m else ""
        for sm in _SERVICE.finditer(text):
            full = f"{package}.{sm.group(1)}" if package else sm.group(1)
            rpcs = services.setdefault(full, {})
            for rm in _RPC.finditer(_block(text, sm.end())):
                rpcs[rm.group(1)] = (bool(rm.group(2)), bool(rm.group(4)))
    return services, problems


# ---- the bridge binary --------------------------------------------------------------------


class BridgeBuildError(RuntimeError):
    """The Go bridge could not be built (``bridge_build_failed``)."""


def _go_env() -> dict[str, str]:
    return {**os.environ, "GOFLAGS": "-mod=readonly", "CGO_ENABLED": "0"}


def source_hash() -> str:
    """The hash of the bridge's sources, ``go.sum`` and the toolchain (the cache key)."""
    h = hashlib.sha256()
    for p in sorted(BRIDGE_SRC.rglob("*")):
        if p.is_file() and not p.name.endswith("_test.go"):
            h.update(p.relative_to(BRIDGE_SRC).as_posix().encode() + b"\0")
            h.update(p.read_bytes() + b"\0")
    go = shutil.which("go")
    if go is not None:
        proc = subprocess.run(  # noqa: S603
            [go, "version"], capture_output=True, text=True, check=False, env=_go_env()
        )
        h.update(proc.stdout.strip().encode())
    return h.hexdigest()


def cache_dir() -> Path:
    """``$LATTICE_GRPCBRIDGE_CACHE``, else ``~/.cache/lattice/grpcbridge``."""
    if env := os.environ.get("LATTICE_GRPCBRIDGE_CACHE"):
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "lattice" / "grpcbridge"


def build_bridge() -> Path:
    """The bridge binary, built with ``go build -trimpath -buildvcs=false -mod=readonly``
    once per source hash."""
    out = cache_dir() / source_hash()[:24] / "grpcbridge"
    if out.is_file():
        return out
    go = shutil.which("go")
    if go is None:
        raise BridgeBuildError("bridge_build_failed: the Go toolchain is not on PATH")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".grpcbridge-{os.getpid()}")
    try:
        proc = subprocess.run(  # noqa: S603
            [go, "build", "-trimpath", "-buildvcs=false", "-mod=readonly", "-o", str(tmp), "."],
            cwd=BRIDGE_SRC,
            capture_output=True,
            text=True,
            check=False,
            env=_go_env(),
            timeout=BUILD_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as err:
        tmp.unlink(missing_ok=True)
        raise BridgeBuildError("bridge_build_failed: go build timed out") from err
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise BridgeBuildError(f"bridge_build_failed: go build failed:\n{proc.stderr.strip()}")
    os.replace(tmp, out)
    builds = sorted(
        (p for p in cache_dir().iterdir() if p.is_dir() and (p / "grpcbridge").is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in builds[CACHE_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)
    return out


_BRIDGES: set[Bridge] = set()
_BRIDGES_LOCK = threading.Lock()


def live_bridges() -> list[Bridge]:
    """The bridge processes started and not yet stopped (empty after every check)."""
    with _BRIDGES_LOCK:
        return [b for b in _BRIDGES if b.running]


class BridgeError(RuntimeError):
    """The bridge refused a request (``kind``: non_loopback, streaming_not_supported,
    proto_compile, unknown_method, bad_message, dial...)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


class Bridge:
    """One bridge process (JSON lines on stdin / stdout)."""

    def __init__(self, binary: Path | None = None) -> None:
        self._binary = binary
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self.last_line = ""  # the bytes of the last answer (canonical JSON: byte-comparable)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> Bridge:
        with self._lock:
            if self._proc is None:
                binary = self._binary or build_bridge()
                self._proc = subprocess.Popen(  # noqa: S603 -- the project's own binary
                    [str(binary)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
                with _BRIDGES_LOCK:
                    _BRIDGES.add(self)
        return self

    def request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """One request and its answer (``ok`` false: a :class:`BridgeError`)."""
        self.start()
        with self._lock:
            proc = self._proc
            assert proc is not None and proc.stdin is not None and proc.stdout is not None
            try:
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
            except (BrokenPipeError, OSError) as err:
                raise BridgeError("bridge_died", str(err)) from err
        if not line:
            raise BridgeError("bridge_died", "the bridge closed its output")
        self.last_line = line
        answer = json.loads(line, parse_int=int)
        if not answer.get("ok"):
            raise BridgeError(answer.get("error", "bridge"), answer.get("message", ""))
        return answer  # type: ignore[no-any-return]

    def describe(
        self, files: Sequence[str | Path], proto_paths: Sequence[str | Path]
    ) -> dict[str, Any]:
        """Compile the protos (protocompile) and answer their contract (``grpc``) and the
        descriptor set (``descriptor_set``, base64)."""
        return self.request(
            {
                "op": "describe",
                "files": [str(f) for f in files],
                "proto_paths": [str(p) for p in proto_paths],
            }
        )

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is not None:
            if proc.poll() is None:
                with contextlib.suppress(Exception):
                    assert proc.stdin is not None
                    proc.stdin.write('{"op":"quit"}\n')
                    proc.stdin.flush()
                try:
                    proc.wait(2)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.killpg(proc.pid, 9)
                    proc.wait()
            for stream in (proc.stdin, proc.stdout):
                with contextlib.suppress(Exception):
                    if stream is not None:
                        stream.close()
        with _BRIDGES_LOCK:
            _BRIDGES.discard(self)

    def __enter__(self) -> Bridge:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


# ---- calls ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class GrpcAnswer:
    """What a unary call answered: ``code`` (a ``google.rpc.Code`` name), ``code_num``,
    the status ``message`` and the response ``body`` (``lattice.grpcjson/1``; None for an
    error)."""

    code: str
    code_num: int
    message: str
    body: Any = None

    @property
    def ok(self) -> bool:
        return self.code == "OK"

    def as_body(self) -> Any:
        """The value ``expect_message`` is compared with: the response, or
        ``{"code", "message"}`` for an error."""
        return self.body if self.ok else {"code": self.code, "message": self.message}


@dataclass(eq=False)
class GrpcTransport:
    """Unary calls to one server (``127.0.0.1:<port>``) through one bridge. The protos are
    described once (``protos`` relative to ``root``)."""

    target: str
    protos: Sequence[str] = ()
    root: Path | str | None = None
    proto_paths: Sequence[str] = ()
    bridge: Bridge = field(default_factory=Bridge)
    _described: bool = field(default=False, init=False, repr=False)
    calls: int = field(default=0, init=False)

    def describe(self) -> None:
        if self._described:
            return
        base = Path(self.root) if self.root is not None else Path.cwd()
        files = [str((base / p).resolve()) for p in self.protos]
        roots = [str((base / p).resolve()) for p in self.proto_paths] or [str(base.resolve())]
        self.bridge.describe(files, [str(base.resolve()), *roots])
        self._described = True

    def call(
        self,
        service: str,
        method: str,
        message: Any = None,
        metadata: Mapping[str, str] | None = None,
    ) -> GrpcAnswer:
        self.describe()
        self.calls += 1
        answer = self.bridge.request(
            {
                "op": "call",
                "target": self.target,
                "service": service,
                "method": method,
                "message": {} if message is None else message,
                "metadata": dict(metadata or {}),
            }
        )
        return GrpcAnswer(
            answer["code"], int(answer["code_num"]), answer["message"], answer["body"]
        )

    def health(self) -> bool:
        """``grpc.health.v1.Health/Check`` answers ``SERVING``."""
        answer = self.bridge.request({"op": "health", "target": self.target})
        return bool(answer.get("serving"))

    def close(self) -> None:
        self.bridge.stop()


def bearer(token: str | None, metadata: Mapping[str, str] | None = None) -> dict[str, str]:
    """The request metadata carrying ``token`` (``authorization: Bearer <token>``)."""
    out = dict(metadata or {})
    if token is not None:
        out["authorization"] = f"Bearer {token}"
    return out


def rpc_login(
    service: str,
    method: str,
    credentials: Mapping[str, tuple[str, str]],
    *,
    username_field: str = "name",
    password_field: str = "password",
    token_field: str = "token",
) -> Any:
    """A ``Login`` (``(client, handle) -> token``) calling the rpc ``service``/``method``
    with the handle's credentials; ``client`` is the transport's client (it carries the rpc
    transport: :class:`~lattice.spec.process.ProcessTransport`)."""

    def login(client: Any, handle: str) -> str:
        username, password = credentials[handle]
        reach = getattr(client, "_lattice_rpc", None)
        if reach is None:
            raise RuntimeError("the app's transport has no gRPC client")
        answer = reach().call(service, method, {username_field: username, password_field: password})
        if not answer.ok:
            raise RuntimeError(f"login as {handle!r} failed: {answer.code} {answer.message}")
        value = answer.body
        for key in token_field.split("."):
            value = value[key]
        return str(value)

    return login


def reconcile(root: Path, model: RepoModel, *, timeout: float = 30.0) -> dict[str, Any]:
    """Which extracted rpcs the server answers: the app's binary (the harness of ``studio.toml``
    ``[app]``) on a throwaway SQLite file, each registered rpc called with an empty message -- as
    the harness's first user (``USERS``) for an rpc that is not public -- and an rpc the proto
    does not declare. The harness is read from ``root`` and :func:`reconcile_with` does the work;
    the result maps ``<Service>/<Method>`` to ``{code, answered}`` (``answered``: the code is not
    UNIMPLEMENTED); no port, no timing."""
    from lattice.importer.frontends.go_gin import GoExtractError, _harness_attr, _studio_app

    cfg = _studio_app(root)
    if "app" not in cfg or "db" not in cfg:
        raise GoExtractError(f"{root}/studio.toml names no [app] app / db harness")
    make_app = _harness_attr(root, cfg["app"])
    build_db = _harness_attr(root, cfg["db"])
    runtime = _harness_attr(root, cfg["runtime"]) if "runtime" in cfg else None
    try:
        users = dict(_harness_attr(root, cfg["app"].split(":")[0] + ":USERS"))
    except (AttributeError, ImportError, TypeError, ValueError):
        users = {}
    return reconcile_with(
        model, make_app=make_app, build_db=build_db, runtime=runtime, users=users, timeout=timeout
    )


def _call_fresh(
    rpc: Any,
    transport: Any,
    runtime: Any,
    first_user: str | None,
    login: Any,
    service: str,
    method: str,
    public: bool,
) -> dict[str, Any]:
    """One rpc, called with a token obtained by a login made immediately before it (RC1): a
    failed login is ``login_failed`` (no code, not answered), never an answer."""
    token = None
    if not public and first_user is not None and (runtime.auth.can_login or login is not None):
        try:
            token = runtime.auth.token(transport, first_user, login)
        except Exception:  # noqa: BLE001 -- any failure to log in is the reason, not an answer
            return {"code": None, "answered": None, "reason": "login_failed"}
    try:
        answer = rpc.call(service, method, {}, bearer(token))
    except BridgeError as err:
        if err.kind != "streaming_not_supported":
            raise
        return {"code": None, "answered": None, "reason": f"bridge:{err.kind}"}
    return {"code": answer.code, "answered": answer.code != "UNIMPLEMENTED"}


def _with_protos(app: Any, protos: Sequence[str], proto_paths: Sequence[str]) -> Any:
    """``app`` with the contract's protos named, when it names none (a harness that leaves them
    unset: the rpc transport needs them to describe the contract)."""
    import dataclasses

    if not protos or getattr(app, "protos", ()) or not dataclasses.is_dataclass(app):
        return app
    names = {f.name for f in dataclasses.fields(app)}
    if not {"protos", "proto_paths"} <= names:
        return app
    return dataclasses.replace(app, protos=tuple(protos), proto_paths=tuple(proto_paths))


def reconcile_with(
    model: RepoModel,
    *,
    make_app: Any,
    build_db: Any,
    runtime: Any,
    users: Mapping[str, Any],
    protos: Sequence[str] = (),
    proto_paths: Sequence[str] = (),
    login: Any = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """:func:`reconcile` over a harness given as values -- no ``studio.toml`` -- and independent
    of the state an earlier call leaves (round 2, D1). RC1: every rpc that is not public is
    called with a token from a login made immediately before it (the first of ``users``). RC2:
    the rpcs are called twice on one process, forward (sorted by ``<Service>/<Method>``) and
    reverse. RC3: an rpc whose answer differs between the passes (or that no pass could log in for)
    is called again alone -- a fresh
    database, a fresh process, a fresh login -- and that answer (``isolated: true``) stands. The
    result maps ``<Service>/<Method>`` to ``{code, answered}`` in the contract's order, with
    ``reason`` for a failed login, then ``(absent)``; ``order: {disagreed: [...]}`` and
    ``auth_refused: [...]`` (a non-public rpc a fresh token did not get past the interceptor
    of) appear only when not empty. ``protos`` / ``proto_paths`` name the contract for an app
    that does not (absolute, or relative to the working directory)."""
    import tempfile

    first_user = next(iter(users), None)
    ordered: list[tuple[str, str, str, bool]] = []
    for r in model.routes:
        if r.transport == "grpc" and r.rpc is not None:
            ordered.append(
                (f"{r.rpc.service}/{r.rpc.method}", r.rpc.service, r.rpc.method, r.auth == "Public")
            )
    by_key = {k: (s, m, pub) for k, s, m, pub in ordered}

    def sig(res: Mapping[str, Any]) -> tuple[Any, Any]:
        return res.get("code"), res.get("reason")

    forward: dict[str, dict[str, Any]] = {}
    reverse: dict[str, dict[str, Any]] = {}
    absent: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="lattice-grpc-reconcile-") as tmp:
        db = build_db(Path(tmp) / "reconcile.db")
        app = _with_protos(make_app(db), protos, proto_paths)
        try:
            with runtime.open(app) as transport:
                rpc = transport.rpc()
                for key in sorted(by_key):
                    s, m, pub = by_key[key]
                    forward[key] = _call_fresh(
                        rpc, transport, runtime, first_user, login, s, m, pub
                    )
                for key in sorted(by_key, reverse=True):
                    s, m, pub = by_key[key]
                    reverse[key] = _call_fresh(
                        rpc, transport, runtime, first_user, login, s, m, pub
                    )
                # a method the proto does not declare: the server must refuse it
                probe = rpc.bridge.request(
                    {
                        "op": "probe",
                        "target": rpc.target,
                        "path": "/lattice.reconcile.v1.Absent/NoSuchRpc",
                    }
                )
                absent = {"code": probe["code"], "answered": probe["code"] != "UNIMPLEMENTED"}
        finally:
            runtime.close(app)
    # a pass that could not even log in answers nothing: it is no agreement either
    disagreed = sorted(
        k
        for k in by_key
        if sig(forward[k]) != sig(reverse[k]) or forward[k].get("reason") == "login_failed"
    )
    final: dict[str, dict[str, Any]] = {k: forward[k] for k in by_key}
    for key in disagreed:
        s, m, pub = by_key[key]
        with tempfile.TemporaryDirectory(prefix="lattice-grpc-reconcile-") as tmp:
            db = build_db(Path(tmp) / "isolated.db")
            app = _with_protos(make_app(db), protos, proto_paths)
            try:
                with runtime.open(app) as transport:
                    alone = _call_fresh(
                        transport.rpc(), transport, runtime, first_user, login, s, m, pub
                    )
            finally:
                runtime.close(app)
        final[key] = {**alone, "isolated": True}
    out: dict[str, Any] = {k: final[k] for k, _, _, _ in ordered}
    out["(absent)"] = absent
    refused = sorted(
        k
        for k, (_, _, pub) in by_key.items()
        if not pub and final[k].get("code") == "UNAUTHENTICATED"
    )
    if disagreed:
        out["order"] = {"disagreed": disagreed}
    if refused:
        out["auth_refused"] = refused
    return out


# ---- rpc flows: the live run (Spike I, D6) ---------------------------------------------------


def _scenario_of(flow_id: str, steps: Sequence[Mapping[str, Any]]) -> Any:
    """The recorded steps of a flow as a layer-D scenario of ``Rpc`` steps."""
    from lattice.spec.model import Rpc, Scenario

    return Scenario(
        f"flow {flow_id}",
        steps=tuple(
            Rpc(s["service"], s["method"], s["user"], s["message"], expect_code=s["code"])
            for s in steps
        ),
    )


def run_rpc_flows(
    extract: Mapping[str, Any],
    spec: Any,
    make_app: Any,
    build_db: Any,
    *,
    runtime: Any,
    root: Path | str | None = None,
    check_runtime: Any = None,
    login: Any = None,
) -> list[dict[str, Any]]:
    """Recover the flows of ``extract["grpc"]``, run each on a throwaway database against the
    app's own binary, and verify what ran (see ``lattice.importer.workflows``, D6 and round 2's
    D2-D4). ``spec`` supplies the users (the first two are A and B), the profile and ``protos``
    the replay uses. Every flow is answered with its steps recorded as concrete ``Rpc`` steps --
    each with its ``role`` (``step`` or ``lookup``) and ``bound`` (which earlier step and response
    field fed each request field) -- and a status: ``verified`` (every step answered as planned,
    and the recorded scenario passes layer D on a fresh database), ``unverified`` (the replay
    disagreed), ``failed`` (a step answered otherwise than planned, or the flow could not run) or
    ``unbound`` (a request field names a row by a natural key and nothing supplies it: the flow
    was not run). Schema ``lattice.rpc-flows/2``."""
    import tempfile

    from lattice.importer import workflows as wf
    from lattice.spec.check import check

    views = wf.rpc_views(extract)
    by_key = {v.key: v for v in views}
    enums = wf.rpc_enums(extract)
    handles = list(spec.users)
    if not handles:
        return []
    who = {"A": handles[0], "B": handles[1] if len(handles) > 1 else None}
    plans = wf.static_rpc_plans(views)
    results: list[dict[str, Any]] = []

    def run_steps(steps: Sequence[Any]) -> tuple[list[dict[str, Any]], str | None]:
        """Execute ``steps`` (``RpcPlanStep``s) on a fresh database; the recorded steps and the
        reason the run stopped early (None: ran to the end)."""
        with tempfile.TemporaryDirectory(prefix="lattice-rpcflow-") as tmp:
            db = build_db(Path(tmp) / "flow.db")
            app = make_app(db)
            recorded: list[dict[str, Any]] = []
            ids: dict[int, tuple[Any, str]] = {}
            bodies: dict[int, Any] = {}
            try:
                with runtime.open(app) as t:
                    rpc = t.rpc(spec.protos)
                    tokens: dict[str, str] = {}
                    for n, st in enumerate(steps, 1):
                        view = by_key[st.key]
                        handle = who.get(st.caller) if st.caller else None
                        if st.caller and handle is None:
                            return recorded, f"step {n} needs a second user"
                        token = None
                        if handle is not None:
                            if handle not in tokens:
                                tokens[handle] = runtime.auth.token(t, handle, login)
                            token = tokens[handle]
                        bound = {f: ids[k][0] for f, k in st.binds.items() if k in ids}
                        record = {
                            f: {"step": k, "from_field": ids[k][1]}
                            for f, k in st.binds.items()
                            if k in ids
                        }
                        for f, (k, col) in st.nat.items():
                            value = wf.natural_value(
                                bodies.get(k), col, lists_first=steps[k - 1].role == "lookup"
                            )
                            if value is None:
                                return recorded, f"step {n}: no {col} from step {k} for {f}"
                            bound[f] = value
                            record[f] = {"step": k, "from_field": col}
                        msg = wf.rpc_message(view, bound, enums)
                        if st.same_as is not None and st.same_as <= len(recorded):
                            before = recorded[st.same_as - 1]["message"]
                            msg = {
                                f["name"]: before[f["name"]]
                                for f in view.inputs
                                if f["name"] in before
                            }
                            record = {}
                        answer = rpc.call(view.service, view.method, msg, bearer(token))
                        recorded.append(
                            {
                                "rpc": view.key,
                                "service": view.service,
                                "method": view.method,
                                "user": handle,
                                "message": msg,
                                "code": answer.code,
                                "role": st.role,
                                "bound": record,
                            }
                        )
                        if answer.ok:
                            bodies[n] = answer.body
                            got = wf.id_source(answer.body)
                            if got is not None:
                                ids[n] = got
            except Exception as err:  # noqa: BLE001 -- a flow that cannot run is reported
                return recorded, f"{type(err).__name__}: {err}"
            finally:
                runtime.close(app)
            return recorded, None

    def settle(plan: Any, recorded: list[dict[str, Any]], stop: str | None) -> dict[str, Any]:
        flow: dict[str, Any] = {
            "id": plan.id,
            "kind": plan.kind,
            "why": plan.why,
            "steps": recorded,
        }
        if plan.unbound:
            flow.update(status="unbound", reason=plan.unbound, steps=[])
            return flow
        if stop is not None or len(recorded) != len(plan.steps):
            flow.update(status="failed", reason=stop or "the flow stopped early")
            return flow
        for n, (st, rec) in enumerate(zip(plan.steps, recorded, strict=True), 1):
            if not wf.rpc_step_ok(plan.kind, st.intent, rec["code"]):
                flow.update(status="failed", reason=f"step {n} ({st.key}) answered {rec['code']}")
                return flow
        scenario = _scenario_of(plan.id, recorded)
        from dataclasses import replace

        replay_spec = replace(spec, scenarios=(scenario,))
        try:
            report = check(
                replay_spec, make_app, build_db, layers="D", runtime=runtime, login=login
            )
        except Exception as err:  # noqa: BLE001
            flow.update(status="unverified", reason=f"the replay could not run: {err}")
            return flow
        if report.findings:
            flow.update(
                status="unverified",
                reason="the replay disagreed: "
                + "; ".join(str(f.details.get("problems")) for f in report.findings),
            )
            return flow
        flow.update(status="verified", reason=None)
        return flow

    for plan in plans:
        if plan.unbound:
            results.append(settle(plan, [], None))
            continue
        recorded, stop = run_steps(plan.steps)
        results.append(settle(plan, recorded, stop))

    # lifecycles: chains of the writers of a created row, found by running them
    def chains(
        writers: list[Any], field_of: Mapping[str, str], first: list[Any], at: int
    ) -> list[list[Any]]:
        found: list[list[Any]] = []

        def work(chain: list[Any]) -> int:
            return sum(s.role == "step" for s in chain)

        def extend(chain: list[Any], used: frozenset[str]) -> None:
            grew = False
            if work(chain) < wf.LIFECYCLE_MAX:
                for w in writers:
                    if w.key in used:
                        continue
                    step = wf.RpcPlanStep(w.key, wf._caller(w, "A"), {field_of[w.key]: at})
                    grown = wf.expand_natural(wf.RpcPlan("lifecycle", [*chain, step], ""), views)
                    if grown.unbound:
                        continue
                    steps = grown.steps
                    recorded, stop = run_steps(steps)
                    if (
                        stop is None
                        and len(recorded) == len(steps)
                        and recorded[-1]["code"] == "OK"
                    ):
                        grew = True
                        extend(steps, used | {w.key})
            if not grew and work(chain) >= 3:
                found.append(chain)

        extend(first, frozenset())
        found.sort(key=lambda c: (-len(c), [s.key for s in c]))
        return found

    for table, creator, writers in wf.rpc_lifecycle_creators(views):
        field_of = {c: f for t, p, c, f in wf.rpc_edges(views) if t == table and p == creator.key}
        start = wf.expand_natural(
            wf.RpcPlan(
                "lifecycle", [wf.RpcPlanStep(creator.key, wf._caller(creator, "A"), {})], ""
            ),
            views,
        )
        if start.unbound:
            results.append(
                settle(
                    wf.RpcPlan("lifecycle", start.steps, f"{table}: {creator.key}", start.unbound),
                    [],
                    None,
                )
            )
            continue
        at = next(i for i, s in enumerate(start.steps, 1) if s.key == creator.key)
        for chain in chains(writers, field_of, start.steps, at)[:4]:
            plan = wf.RpcPlan(
                "lifecycle",
                chain,
                f"{table}: "
                + " -> ".join(s.key.rsplit("/", 1)[-1] for s in chain if s.role == "step"),
            )
            recorded, stop = run_steps(chain)
            results.append(settle(plan, recorded, stop))
    results.sort(key=lambda f: (f["kind"], f["id"]))
    return results
