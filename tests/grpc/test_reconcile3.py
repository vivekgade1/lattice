"""Round 3, D3: ``reconcile_with`` calls every unary rpc of the contract it is given, not only the
model's routes (D3.1), adds ``route: false`` to what is not a route and nothing else (D3.2), and
leaves counting to the library: the runner has no call of its own in the scored path (D3.3).

The fakes are round 2's (``tests/grpc/test_reconcile2.py``): a world of users and sessions, a
runtime that opens it, a transport whose ``rpc()`` is described here by a contract."""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.grpc.test_reconcile2 import ORDER, SVC, Harness, key

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parent.parent / "dogfood" / "grpc_probes3.py"


class ContractHarness(Harness):
    """Round 2's harness whose transports describe ``contract`` (``(method, streaming)``)."""

    def __init__(self, contract: list[tuple[str, bool]], **kw: Any) -> None:
        super().__init__(**kw)
        self.contract = contract
        self.described: list[Any] = []
        opened = self.runtime.open
        described = self

        @contextlib.contextmanager
        def open_with_contract(app: Any) -> Any:
            with opened(app) as transport:
                plain = transport.rpc

                def rpc() -> Any:
                    r = plain()
                    r.protos, r.root, r.proto_paths = ("s.proto",), None, ()
                    r.bridge.describe = lambda files, roots: (
                        described.described.append((files, roots))
                        or {
                            "grpc": {
                                "services": [
                                    {
                                        "name": SVC,
                                        "rpcs": [
                                            {
                                                "name": m,
                                                "client_streaming": False,
                                                "server_streaming": s,
                                            }
                                            for m, s in described.contract
                                        ],
                                    }
                                ]
                            }
                        }
                    )
                    return r

                transport.rpc = rpc
                yield transport

        self.runtime.open = open_with_contract  # type: ignore[method-assign]


ALL = [(m, False) for m in ORDER]


def test_d3_1_a_contract_rpc_that_is_not_a_route_is_called() -> None:
    h = ContractHarness(ALL, methods={"Secret", "Health", "Login"})
    out = h.run(["Login", "Secret", "Health"])
    # SignOut, DeleteMe and Stub are declared, not routes of the model
    for m in ("SignOut", "DeleteMe", "Stub"):
        assert out[key(m)]["code"] == "UNIMPLEMENTED" and out[key(m)]["route"] is False
    assert [k for k in out if k.startswith(SVC)][:3] == [key("Login"), key("Secret"), key("Health")]
    assert list(out)[-1] == "(absent)"


def test_d3_2_a_route_has_no_route_key_and_a_non_route_has_only_that_one_added() -> None:
    h = ContractHarness(ALL, methods={"Secret", "Health", "Login"})
    out = h.run(["Login", "Secret", "Health"])
    assert all(set(out[key(m)]) == {"code", "answered"} for m in ("Login", "Secret", "Health"))
    assert set(out[key("Stub")]) == {"code", "answered", "route"}


def test_d3_1_a_non_route_gets_its_own_fresh_login_each_call() -> None:
    h = ContractHarness([("Login", False), ("Secret", False), ("Ghost", False)], methods={"Secret"})
    h.run(["Login"])
    # Secret and Ghost are not public: one login per call, two passes
    assert h.worlds[0].logins == 4 and h.worlds[0].minted == 4


def test_d3_1_the_calls_are_sorted_by_service_and_method_then_reversed() -> None:
    h = ContractHarness([("Zed", False), ("Alpha", False), ("Login", False)], methods={"Login"})
    h.run(["Login"])
    assert h.worlds[0].log == ["Alpha", "Login", "Zed", "Zed", "Login", "Alpha"]


def test_d3_1_a_streaming_contract_rpc_is_not_called_or_listed() -> None:
    h = ContractHarness([("Login", False), ("Watch", True)], methods={"Login"}, streaming={"Watch"})
    out = h.run(["Login"])
    assert key("Watch") not in out and "Watch" not in h.worlds[0].log


def test_d3_1_an_order_dependent_non_route_is_settled_isolated() -> None:
    """DeleteMe deletes the account the later logins need: it and what follows disagree between
    the passes and are re-run alone, route or not."""
    h = ContractHarness(ALL, methods=set(ORDER))
    out = h.run(["Login", "Health"])
    disagreed = out["order"]["disagreed"]
    assert key("DeleteMe") in disagreed and key("Secret") in disagreed
    assert all(out[k]["isolated"] is True and out[k]["route"] is False for k in disagreed)
    assert out[key("Secret")]["code"] == "OK" and out[key("Stub")]["code"] == "UNIMPLEMENTED"


def test_d3_1_a_refused_non_route_is_listed_in_auth_refused() -> None:
    h = ContractHarness([("Login", False), ("Secret", False)], methods={"Login"})
    h.build_db = lambda path: SimpleNamespace(  # type: ignore[method-assign]
        users={"ann"}, sessions={}, log=[], logins=0, minted=0, call=lambda m, t: "UNAUTHENTICATED"
    )
    out = h.run(["Login"], users={})
    assert out["auth_refused"] == [key("Secret")]


def test_d3_2_without_a_described_contract_the_output_is_round_twos() -> None:
    h = Harness(methods={"Secret", "Health", "Login"})
    out = h.run(["Login", "Secret", "Health"])
    assert list(out) == [key("Login"), key("Secret"), key("Health"), "(absent)"]
    assert all(set(v) == {"code", "answered"} for v in out.values())


def test_d3_1_the_contract_is_described_from_the_transports_protos() -> None:
    h = ContractHarness(ALL, methods={"Login"})
    h.run(["Login"])
    cwd = str(Path.cwd().resolve())
    assert h.described[0] == ([f"{cwd}/s.proto"], [cwd, cwd])  # files, include roots


def test_d3_1_a_transport_that_names_no_protos_has_no_contract() -> None:
    h = Harness(methods={"Login"})
    out = h.run(["Login"])
    assert list(out) == [key("Login"), "(absent)"]


def test_d3_3_the_runner_has_no_call_of_its_own_in_the_scored_path() -> None:
    src = RUNNER.read_text()
    body = src[src.index("def rec_row") : src.index("def flows_row")]
    assert "_call_fresh" not in body and "Bridge(" not in body and ".call(" not in body
    assert re.search(r"supplementary_calls\W+0", body)


def test_the_scored_row_fails_when_the_count_is_not_zero() -> None:
    import sys

    sys.path.insert(0, str(RUNNER.parent))
    import grpc_scorer3 as sc

    row = sc.score_rec(
        {"a.v1.A/X": "implemented"},
        {"a.v1.A/X": {"code": "OK"}, "(absent)": {"code": "UNIMPLEMENTED"}},
        supplementary_calls=1,
    )
    assert row["pass"] is False and row["reason"] == "supplementary_calls"
    assert json.dumps(row)  # plain data
