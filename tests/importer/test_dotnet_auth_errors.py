"""The answers of an ASP.NET authentication handler on a protected route (node F1): a 401 the
auth component raises for a request with no valid token is the route's ``unauthenticated`` error
and a 403 it raises for a caller without the role its ``forbidden`` one, so the anonymous and the
wrong-role R1 probes decide them (as they do for the Python front ends' dependencies). Any other
status the middleware raises (a rate limit) stays an ``other`` error, and other front ends are
unchanged."""

from __future__ import annotations

from lattice.importer.appmodel import AuthModel, RaiseSite, RepoModel, Route
from lattice.importer.emit_spec import _errors
from lattice.profile import ApiProfile


def route(auth: str, *raises: RaiseSite, path: str = "/x") -> Route:
    return Route(
        name="r", module="m", method="GET", path=path, params=(), response_model=None,
        response_class=None, status_code=200, dependencies=(), auth=auth, auth_reason="",
        raises=tuple(raises), calls=(), tables=(), owner_uses=(), body_models=(), line=1,
    )  # fmt: skip


def model(framework: str) -> RepoModel:
    return RepoModel(
        app_package="x", tables=(), models=(), dependencies=(), routes=(), natives=(),
        database_url=None, dialect=None, exception_handlers=(), custom_serialization=(),
        auth=AuthModel(users_table="User"), framework=framework,
    )  # fmt: skip


API = ApiProfile(tables={}, table_names={}, roles=frozenset({"Public", "Customer", "Admin"}))
RAISES = (
    RaiseSite(401, "bearer_required", 52, "middleware"),
    RaiseSite(403, None, 55, "middleware"),
    RaiseSite(429, "rate_limited", 70, "middleware"),
    RaiseSite(409, "taken", 12, "exception"),
)


def whens(framework: str, auth: str) -> list[tuple[int | None, str]]:
    m = model(framework)
    return [(e.status, e.when) for e in _errors(m, route(auth, *RAISES), API, None, ())]


def test_an_auth_handlers_401_and_403_on_a_protected_route_are_the_auth_answers() -> None:
    assert whens("aspnetcore", "Admin") == [
        (401, "unauthenticated"), (403, "forbidden"), (429, "other"), (409, "other"),
    ]  # fmt: skip


def test_a_public_route_keeps_every_error_plain() -> None:
    assert [w for _, w in whens("aspnetcore", "Public")] == ["other"] * 4


def test_other_front_ends_are_unchanged() -> None:
    assert [w for _, w in whens("gin", "Admin")] == ["other"] * 4
