"""Round 2, D1: ``reconcile_with`` answers each rpc independently of the state earlier calls leave
(RC1 a login immediately before every non-public call, RC2 forward and reverse passes, RC3 an
isolated rerun of what disagreed), on fakes that need no Go and on the sessions app (``SignOut``
revokes the caller's session, ``DeleteMe`` deletes the account) and the notes app (unchanged)."""

from __future__ import annotations

import contextlib
import dataclasses
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lattice.spec import grpc as g
from lattice.spec.grpc import BridgeError, reconcile, reconcile_with
from tests.grpc import oracle
from tests.grpc.conftest import needs_go

SVC = "s.v1.S"
PUBLIC = {"Login", "Health"}
ORDER = ["Login", "SignOut", "DeleteMe", "Secret", "Stub", "Health"]


class World:
    """One database + process of the fake server: users, live sessions, the calls made."""

    def __init__(self, methods: set[str] | None = None) -> None:
        self.users = {"ann", "ben"}
        self.sessions: dict[str, str] = {}
        self.methods = methods if methods is not None else set(ORDER)
        self.log: list[str] = []
        self.logins = 0
        self.minted = 0

    def call(self, method: str, token: str | None) -> str:
        self.log.append(method)
        if method not in PUBLIC and self.sessions.get(token or "") is None:
            return "UNAUTHENTICATED"  # the interceptor answers before the handler, served or not
        if method not in self.methods:
            return "UNIMPLEMENTED"
        user = self.sessions.get(token or "")
        if method == "SignOut":
            del self.sessions[token]
        elif method == "DeleteMe":
            self.users.discard(user)
            self.sessions = {t: u for t, u in self.sessions.items() if u != user}
        elif method == "Stub":
            return "UNIMPLEMENTED"
        elif method == "Login":
            return "INVALID_ARGUMENT"
        return "OK"


class Rpc:
    target = "127.0.0.1:1"

    def __init__(self, world: World, probes: list[str], streaming: set[str]) -> None:
        self.world, self.probes, self.streaming = world, probes, streaming
        self.bridge = SimpleNamespace(request=self.request)

    def call(self, service: str, method: str, message: Any, metadata: Any = None) -> Any:
        if method in self.streaming:
            raise BridgeError("streaming_not_supported", method)
        if method == "Boom":
            raise BridgeError("bridge_died", "gone")
        auth = (metadata or {}).get("authorization", "")
        token = auth.removeprefix("Bearer ") or None
        return SimpleNamespace(code=self.world.call(method, token))

    def request(self, payload: Any) -> dict[str, Any]:
        self.probes.append(payload["path"])
        return {"ok": True, "code": "UNIMPLEMENTED"}


class Harness:
    """make_app / build_db / runtime over fake worlds; counts what reconcile starts."""

    def __init__(self, methods: set[str] | None = None, streaming: set[str] | None = None) -> None:
        self.worlds: list[World] = []
        self.opened = 0
        self.probes: list[str] = []
        self.methods = methods
        self.streaming = streaming or set()
        harness = self

        class Auth:
            can_login = True

            def token(self, transport: Any, handle: str, login: Any = None) -> str:
                w = transport.world
                w.logins += 1
                if handle not in w.users:
                    raise RuntimeError(f"login as {handle!r} failed")
                w.minted += 1
                token = f"t{len(harness.worlds)}-{w.minted}"
                w.sessions[token] = handle
                return token

        class Runtime:
            auth = Auth()

            @contextlib.contextmanager
            def open(self, app: Any) -> Any:
                harness.opened += 1
                transport = SimpleNamespace(world=app.world)
                transport.rpc = lambda: Rpc(app.world, harness.probes, harness.streaming)
                yield transport

            def close(self, app: Any) -> None:
                app.closed = True

        self.runtime = Runtime()

    def build_db(self, path: Path) -> World:
        w = World(self.methods)
        self.worlds.append(w)
        return w

    @staticmethod
    def make_app(db: World) -> Any:
        return SimpleNamespace(world=db, closed=False)

    def model(self, names: list[str] | None = None) -> Any:
        routes = [
            SimpleNamespace(
                transport="grpc",
                rpc=SimpleNamespace(service=SVC, method=m),
                auth="Public" if m in PUBLIC else "Member",
            )
            for m in names or ORDER
        ]
        return SimpleNamespace(routes=routes)

    def run(self, names: list[str] | None = None, users: Any = None, **kw: Any) -> dict[str, Any]:
        return reconcile_with(
            self.model(names),
            make_app=self.make_app,
            build_db=self.build_db,
            runtime=self.runtime,
            users={"ann": ("ann", "x")} if users is None else users,
            **kw,
        )


def key(m: str) -> str:
    return f"{SVC}/{m}"


# ---- fakes: RC1, RC2, RC3, D1.5, D1.6 ---------------------------------------------------------


def test_rc1_every_non_public_call_is_preceded_by_its_own_login() -> None:
    h = Harness(methods={"Secret", "Stub", "Health", "Login"})
    h.run(["Login", "Secret", "Stub", "Health"])
    # two passes x two non-public rpcs, one login each, on the one process
    assert h.worlds[0].logins == 4 and h.worlds[0].minted == 4
    assert len(h.worlds) == 1  # no disagreement: no isolated run


def test_rc1_a_revoked_session_does_not_reach_the_next_rpc() -> None:
    """SignOut sorts first and revokes its token: with one shared token every later rpc would
    answer UNAUTHENTICATED (round 1's defect)."""
    h = Harness(methods={"Aout", "Bsecret", "Health"})
    out = h.run(["Aout", "Bsecret", "Cstub", "Health"])
    assert out[key("Bsecret")] == {"code": "OK", "answered": True}
    assert out[key("Cstub")] == {"code": "UNIMPLEMENTED", "answered": False}  # Stub is unserved


def test_rc1_a_failed_login_is_not_an_answer() -> None:
    h = Harness()
    out = h.run(users={"nobody": ("n", "x")})
    for m in ("SignOut", "DeleteMe", "Secret", "Stub"):
        r = out[key(m)]
        assert r["code"] is None and r["answered"] is None and r["reason"] == "login_failed"
        assert r["isolated"] is True  # no pass could log in: re-run alone, and failing there too
    assert "auth_refused" not in out  # never an UNAUTHENTICATED answer in disguise
    assert out[key("Health")] == {"code": "OK", "answered": True}


def test_rc1_a_public_rpc_is_called_without_a_login() -> None:
    h = Harness(methods={"Health", "Login"})
    h.run(["Login", "Health"])
    assert h.worlds[0].logins == 0


def test_rc2_forward_is_sorted_and_reverse_is_its_reverse() -> None:
    h = Harness(methods={"B", "A", "Health", "Login", "C"})
    h.run(["C", "A", "Health", "B", "Login"])
    assert h.worlds[0].log == ["A", "B", "C", "Health", "Login", "Login", "Health", "C", "B", "A"]


def test_rc3_a_disagreement_is_rerun_alone_on_a_fresh_database_and_process() -> None:
    h = Harness()
    out = h.run()
    truth = {
        "Login": "INVALID_ARGUMENT",
        "SignOut": "OK",
        "DeleteMe": "OK",
        "Secret": "OK",
        "Stub": "UNIMPLEMENTED",
        "Health": "OK",
    }
    assert {m: out[key(m)]["code"] for m in ORDER} == truth
    disagreed = out["order"]["disagreed"]
    assert disagreed == [key(m) for m in ("DeleteMe", "Secret", "SignOut", "Stub")]
    # one database and one process for the two passes, one fresh pair per isolated rerun
    assert len(h.worlds) == 1 + len(disagreed) and h.opened == 1 + len(disagreed)
    for w in h.worlds[1:]:
        assert w.users == ({"ann", "ben"} - {"ann"} if w.log == ["DeleteMe"] else {"ann", "ben"})
        assert len([c for c in w.log]) == 1  # that rpc alone
    assert all(out[k].get("isolated") is True for k in disagreed)
    assert not any("isolated" in out[key(m)] for m in ("Login", "Health"))


def test_rc3_agreeing_passes_add_no_key_and_keep_round_ones_shape() -> None:
    h = Harness(methods={"Secret", "Health", "Login"})
    out = h.run(["Login", "Secret", "Health"])
    assert list(out) == [key("Login"), key("Secret"), key("Health"), "(absent)"]
    assert all(set(v) == {"code", "answered"} for v in out.values())


def test_d1_5_the_output_keeps_the_contracts_order_then_absent() -> None:
    h = Harness()
    out = h.run(["Health", "Secret", "Login"])
    assert list(out)[:3] == [key("Health"), key("Secret"), key("Login")]
    assert list(out)[3] == "(absent)"


def test_d1_6_a_refusal_with_a_fresh_token_is_listed() -> None:
    class Strict(World):
        def call(self, method: str, token: str | None) -> str:
            return "UNAUTHENTICATED" if method == "Secret" else super().call(method, token)

    h = Harness(methods={"Secret", "Health", "Login"})
    h.build_db = lambda path: h.worlds.append(Strict(h.methods)) or h.worlds[-1]  # type: ignore[method-assign, func-returns-value]
    out = h.run(["Login", "Secret", "Health"])
    assert out["auth_refused"] == [key("Secret")]
    assert out[key("Secret")]["answered"] is True  # still not UNIMPLEMENTED, but listed


def test_without_a_user_a_non_public_rpc_is_called_bare_and_listed() -> None:
    h = Harness(methods={"Secret", "Health", "Login"})
    out = h.run(["Login", "Secret", "Health"], users={})
    assert out[key("Secret")]["code"] == "UNAUTHENTICATED"
    assert out["auth_refused"] == [key("Secret")]


def test_the_absent_rpc_is_probed_once() -> None:
    h = Harness(methods={"Health"})
    out = h.run(["Health"])
    assert out["(absent)"] == {"code": "UNIMPLEMENTED", "answered": False}
    assert h.probes == ["/lattice.reconcile.v1.Absent/NoSuchRpc"]


def test_a_streaming_rpc_is_recorded_other_bridge_errors_raise() -> None:
    h = Harness(methods={"Health"}, streaming={"Watch"})
    out = h.run(["Health", "Watch"])
    assert out[key("Watch")] == {
        "code": None,
        "answered": None,
        "reason": "bridge:streaming_not_supported",
    }
    h2 = Harness(methods={"Health"})
    with pytest.raises(BridgeError):
        h2.run(["Health", "Boom"])


def test_protos_are_named_only_for_an_app_that_names_none() -> None:
    @dataclasses.dataclass(frozen=True)
    class App:
        protos: tuple[str, ...] = ()
        proto_paths: tuple[str, ...] = ()

    named = g._with_protos(App(), ["/p/a.proto"], ["/p"])
    assert named.protos == ("/p/a.proto",) and named.proto_paths == ("/p",)
    own = App(protos=("own.proto",))
    assert g._with_protos(own, ["/p/a.proto"], ["/p"]) is own
    plain = SimpleNamespace(protos=())
    assert g._with_protos(plain, ["/p/a.proto"], ["/p"]) is plain  # not a dataclass: untouched
    assert g._with_protos(App(), [], []) == App()


def test_the_api_reconcile_wraps_reconcile_with() -> None:
    assert list(inspect.signature(reconcile).parameters) == ["root", "model", "timeout"]
    params = inspect.signature(reconcile_with).parameters
    assert list(params) == [
        *("model", "make_app", "build_db", "runtime", "users"),
        *("protos", "proto_paths", "login", "timeout"),
    ]
    assert all(p.kind is p.KEYWORD_ONLY for n, p in params.items() if n != "model")


# ---- the sessions app (Go, pure-Go SQLite) -------------------------------------------------------


@pytest.fixture(scope="module")
def sessions_truth() -> Any:
    return oracle.load(oracle.FIXTURES / "sessions" / "truth.json")


@pytest.fixture(scope="module")
def sessions_binary() -> Any:
    from tests.grpc.fixtures.sessions import harness

    return harness.binary()


@needs_go
def test_sessions_reconcile_equals_the_truth_alone(
    front_end: Any, sessions_binary: Any, sessions_truth: Any
) -> None:
    model = front_end.extract(oracle.FIXTURES, "sessions")
    got = front_end.reconcile(oracle.FIXTURES / "sessions", model)
    for k, want in sessions_truth["rpcs"].items():
        assert got[k]["code"] == want["alone"], (k, got[k])
        assert got[k]["answered"] == (want["alone"] != "UNIMPLEMENTED")
        assert "reason" not in got[k]
    assert got["(absent)"]["code"] == sessions_truth["absent"]
    # DeleteMe deletes the account the next logins need: the passes disagree, RC3 settles it
    assert got["order"]["disagreed"]
    assert all(got[k]["isolated"] for k in got["order"]["disagreed"])
    assert "auth_refused" not in got


@needs_go
def test_sessions_reconcile_is_the_same_twice(front_end: Any, sessions_binary: Any) -> None:
    model = front_end.extract(oracle.FIXTURES, "sessions")
    a = front_end.reconcile(oracle.FIXTURES / "sessions", model)
    b = front_end.reconcile(oracle.FIXTURES / "sessions", model)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@needs_go
def test_notes_reconcile_has_round_ones_shape(front_end: Any, notes_binary: Any) -> None:
    model = front_end.extract(oracle.FIXTURES, "notes")
    got = front_end.reconcile(oracle.NOTES, model)
    assert set(got) == {f"{r.rpc.service}/{r.rpc.method}" for r in model.routes} | {"(absent)"}
    assert all(set(v) == {"code", "answered"} for v in got.values())


@needs_go
def test_nothing_is_left_after_reconcile(front_end: Any, sessions_binary: Any) -> None:
    from lattice.spec.grpc import live_bridges
    from lattice.spec.process import live_processes

    model = front_end.extract(oracle.FIXTURES, "sessions")
    front_end.reconcile(oracle.FIXTURES / "sessions", model)
    assert live_processes() == [] and live_bridges() == []
