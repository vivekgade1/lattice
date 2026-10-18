"""Round 4, D6: ``AuthDriver.rpc`` / ``rpc_login`` read the username and password fields from the
login request message when the harness names none: the one string field containing ``pass`` and its one
other string field (or the one named username, user, name, login, email, handle, first in that order).
No single answer is ``login_fields_ambiguous`` (a failed login), never a guess; an explicit field wins."""

from __future__ import annotations

# ruff: noqa: E501 -- docstrings and data lines
import hashlib
from dataclasses import dataclass
from typing import Any

import pytest

from lattice.importer.transport import AuthDriver
from lattice.spec.grpc import GrpcAnswer, GrpcTransport, rpc_login
from tests.grpc import oracle

L4 = oracle.FIXTURES / "login4"
TRUTH_SHA = "72f0096862e9a051e90c879b36a28c73ba0d8b8c2f33c67653607b2e2e70f5de"
SERVICE = "login4.v1.Auth"
CREDS = {"ada": ("ada", "pw")}


@dataclass(eq=False)
class Recording(GrpcTransport):
    """The real transport (the bridge describes the protos) with the call itself recorded."""

    sent: Any = None

    def call(
        self, service: str, method: str, message: Any = None, metadata: Any = None
    ) -> GrpcAnswer:
        self.sent = (service, method, message)
        return GrpcAnswer("OK", 0, "", {"token": "t0ken"})


class Client:
    def __init__(self) -> None:
        self.rpc = Recording(target="127.0.0.1:1", protos=["proto/login4/v1/login4.proto"], root=L4)

    def _lattice_rpc(self) -> Recording:
        return self.rpc


@pytest.fixture(scope="module")
def truth() -> Any:
    return oracle.load(L4 / "truth.json")


@pytest.fixture()
def client() -> Any:
    c = Client()
    yield c
    c.rpc.close()


def test_the_truth_was_hashed_before_the_library_changed() -> None:
    assert hashlib.sha256((L4 / "truth.json").read_bytes()).hexdigest() == TRUTH_SHA


@pytest.mark.parametrize("method", ["LoginUsername", "LoginName", "LoginEmail", "LoginTenant"])
def test_the_fields_are_read_from_the_login_message(client: Any, truth: Any, method: str) -> None:
    login = rpc_login(SERVICE, method, CREDS)
    assert login(client, "ada") == "t0ken"
    assert client.rpc.sent == (SERVICE, method, truth["discovered"][method])


def test_a_non_string_field_is_not_a_credential(client: Any) -> None:
    rpc_login(SERVICE, "LoginTenant", CREDS)(client, "ada")
    assert set(client.rpc.sent[2]) == {"user", "password"}  # tenant and attempt are not sent


@pytest.mark.parametrize("method", ["LoginAmbiguous", "LoginTwoPass"])
def test_no_single_answer_is_login_fields_ambiguous(client: Any, truth: Any, method: str) -> None:
    assert method in truth["ambiguous"]
    with pytest.raises(RuntimeError, match="login_fields_ambiguous") as err:
        rpc_login(SERVICE, method, CREDS)(client, "ada")
    assert method in str(err.value) and client.rpc.sent is None  # no call was made


def test_an_explicit_field_wins_over_discovery(client: Any, truth: Any) -> None:
    e = truth["explicit"]
    rpc_login(SERVICE, e["method"], CREDS, username_field=e["username_field"])(client, "ada")
    assert client.rpc.sent == (SERVICE, e["method"], e["sent"])


def test_both_fields_explicit_never_describe(client: Any) -> None:
    rpc_login(SERVICE, "LoginAmbiguous", CREDS, username_field="a", password_field="b")(
        client, "ada"
    )
    assert client.rpc.sent == (SERVICE, "LoginAmbiguous", {"a": "ada", "b": "pw"})


def test_one_explicit_field_leaves_the_other_to_discovery(client: Any) -> None:
    rpc_login(SERVICE, "LoginAmbiguous", CREDS, password_field="b")(client, "ada")
    assert client.rpc.sent[2] == {"a": "ada", "b": "pw"}  # b is named, a is the one other


def test_the_driver_defaults_are_discovery_not_name_and_password() -> None:
    drv = AuthDriver.rpc(SERVICE, "LoginEmail", CREDS)
    assert drv.login is not None and drv.scheme == "bearer"
    import inspect

    sig = inspect.signature(AuthDriver.rpc.__func__)  # type: ignore[attr-defined]
    assert sig.parameters["username_field"].default is None
    assert sig.parameters["password_field"].default is None
    assert sig.parameters["token_field"].default == "token"


def test_the_driver_login_reads_the_message_too(client: Any) -> None:
    drv = AuthDriver.rpc(SERVICE, "LoginEmail", CREDS)
    assert drv.login(client, "ada") == "t0ken"
    assert client.rpc.sent[2] == {"email": "ada", "passcode": "pw"}


def test_the_credentials_are_keyword_only_after_the_credentials() -> None:
    with pytest.raises(TypeError):
        AuthDriver.rpc(SERVICE, "LoginName", CREDS, "name")  # type: ignore[misc]


def test_a_rpc_the_proto_does_not_declare_is_ambiguous_not_a_crash(client: Any) -> None:
    with pytest.raises(RuntimeError, match="login_fields_ambiguous"):
        rpc_login(SERVICE, "NoSuchLogin", CREDS)(client, "ada")
