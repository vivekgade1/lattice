"""R1 and a crash past the login check (node F5, ASP.NET Core only).

Removing a route's login requirement (``[AllowAnonymous]``) on an action that then reads the caller's identity does not answer 200: it answers
500, because there is no identity. The shared engine never decides on a 5xx (a crash can be before or past the check), so R1 stayed verified
and the removed requirement went unseen. On ASP.NET Core authorization is enforced by middleware before the endpoint runs: a request with no
credentials at all that gets a 5xx came from the endpoint, so it was not refused. ``run_probes(crash_admits=True)`` (set by the .NET import
and by ``lattice check`` for a .NET runtime) reads it so, for the anonymous caller only; every other call is unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from lattice.spec import Contract, ContractError
from lattice.spec.probes import Answer
from tests.spec import test_probes_soundness as S

GUARDED = Contract("GET", "/guarded", auth="Authenticated", errors=(ContractError(401),))


class Crashy(S.Toy):
    """``/guarded`` crashes (500) for a request with no Authorization header (``anonymous``) instead of answering 401, as an action that
    lost its login requirement and dereferences the caller does; or for a request carrying a bad token (``token``), as an authentication
    handler that throws would."""

    def request(self, method: str, path: str, **kw: Any) -> Answer:
        headers = dict(kw.get("headers") or {})
        if path == "/guarded" and "Authorization" not in headers and self.flags.get("anonymous"):
            return Answer(500, {}, {"message": "Object reference not set to an instance of an object"})
        if path == "/guarded" and "Authorization" in headers and self.flags.get("token") and self._user(headers) is None:
            return Answer(500, {}, {"message": "boom"})
        return super().request(method, path, **kw)


def run(flags: dict[str, bool], **kw: Any):
    @contextmanager
    def sessions(db):
        yield Crashy(db, **flags)

    return S.run_probes(S._spec(GUARDED), sessions, S.make_db, login=S.login, mint_token=S.mint, **kw)


def test_by_default_a_5xx_decides_nothing_and_r1_stays_verified():
    assert run({"anonymous": True}).items["R1 GET /guarded"].verdict == "verified"


def test_with_crash_admits_an_anonymous_5xx_refutes_the_login_requirement():
    v = run({"anonymous": True}, crash_admits=True).items["R1 GET /guarded"]
    assert v.verdict == "refuted" and "anonymous admitted" in v.reason, v.reason


def test_with_crash_admits_an_app_that_refuses_anonymous_stays_verified():
    assert run({}, crash_admits=True).items["R1 GET /guarded"].verdict == "verified"


def test_only_the_anonymous_caller_is_read_this_way():
    """A 5xx for a bad token may come from the authentication handler: it decides nothing."""
    assert run({"token": True}, crash_admits=True).items["R1 GET /guarded"].verdict == "verified"
