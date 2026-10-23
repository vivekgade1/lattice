"""The role check's own answer on an ASP.NET app (node F6). Node F2 made the service layer's
typed-exception refusals visible, so a route restricted to a role also states a 403 of its own
(``ForbiddenException`` in the service, no literal detail on the wire contract). The verifier then
could not tell a Member's 403 from that rule's, and the wrong-role caller went undecided. The
authentication handler's ``HandleForbiddenAsync`` writes one literal detail for every caller without
the role: the importer names it as the profile's ``forbidden_detail``, so a 403 with that very detail
on a row that does not exist (no owner to explain it, another caller getting past it) is the role's
refusal, and a 403 of another detail stays what it was, undecided. No SDK needed."""

from __future__ import annotations

from lattice.importer.frontends.dotnet_aspnet import role_scheme
from lattice.profile import ApiProfile
from lattice.spec.model import Contract, ContractError
from lattice.spec.probes import Answer, _refused

ROLE = {"column": "role", "storage": "string",
        "members": [{"name": "Member", "value": 0}, {"name": "Admin", "value": 1}]}  # fmt: skip
FORBIDDEN = "this action requires the admin role"


def middleware(status, detail, where="Outlay.Auth.BearerTokenHandler.HandleForbiddenAsync"):
    return {"status": status, "code": None, "detail": detail, "kind": "middleware", "in": where}


def extract(*routes):
    return {"dotnet": {"auth": {"role_column": ROLE, "admin_role": "Admin"}}, "routes": list(routes)}


def route(auth, *raises):
    return {"method": "POST", "path_template": "/r/{id}/approve", "auth": auth, "raises": list(raises)}


SERVICE_403 = {"status": 403, "code": None, "kind": "exception", "sentinel": "ForbiddenException",
               "detail": "a reviewer cannot decide their own expense report"}  # fmt: skip


def test_the_auth_handlers_forbidden_detail_is_the_role_schemes():
    data = extract(
        route("Admin", middleware(401, "a valid bearer token is required", "H.HandleChallengeAsync"),
              middleware(403, FORBIDDEN), middleware(403, None, "H.WriteProblem"), SERVICE_403),
        route("Public", middleware(403, "a public route has no role check")),
    )  # fmt: skip
    scheme = role_scheme(data)
    assert (scheme.status, scheme.detail) == (403, FORBIDDEN)


def test_a_service_403_or_two_different_handler_answers_name_no_forbidden_detail():
    assert role_scheme(extract(route("Admin", SERVICE_403))).detail is None
    two = extract(route("Admin", middleware(403, FORBIDDEN)),
                  route("Admin", middleware(403, "forbidden for another reason")))  # fmt: skip
    assert role_scheme(two).detail is None


def answer(detail):
    return Answer(403, body={"title": "Forbidden", "status": 403, "detail": detail})


def api(scheme):
    return ApiProfile(tables={}, table_names={}, roles=frozenset({"Public", "Member", "Admin"}),
                      forbidden_detail=scheme.detail, error_body="fastapi")  # fmt: skip


def test_a_members_403_on_a_missing_row_is_the_role_refusal_when_it_carries_the_handlers_detail():
    # the route states the service's 403 with no detail on the wire contract (F2's extract)
    contract = Contract("POST", "/r/{id}/approve", auth="Admin",
                        errors=(ContractError(403, when="forbidden"), ContractError(403)))  # fmt: skip
    profile = api(role_scheme(extract(route("Admin", middleware(403, FORBIDDEN), SERVICE_403))))
    # a missing row: no owner to refuse, the caller owns it as far as the probe can tell
    assert _refused(answer(FORBIDDEN), contract, profile, True) is True
    # the same status with the service rule's detail: the route's own 403, past the role check --
    # an admission, never the role's refusal
    assert _refused(answer(SERVICE_403["detail"]), contract, profile, True) is False
    # on a row that is not the caller's own an owner check answers alike: undecided
    assert _refused(answer(FORBIDDEN), contract, profile, False) is None


def test_without_the_handlers_detail_the_members_403_stays_undecided():
    contract = Contract("POST", "/r/{id}/approve", auth="Admin",
                        errors=(ContractError(403, when="forbidden"), ContractError(403)))  # fmt: skip
    profile = api(role_scheme(extract(route("Admin", SERVICE_403))))
    assert _refused(answer(FORBIDDEN), contract, profile, True) is None
