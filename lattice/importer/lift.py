"""Lift orchestration (spec 2026-09-27 importer §3.2-§3.4, plan node I3): a repository ->
per-route lifts -> per-route verification -> Lattice programs per router file.

:func:`lift_repo` (no LLM, deterministic):

1. extracts the ``RepoModel`` and generates the profile (loaded from its source);
2. asks the lifters for every route (:func:`lattice.importer.lifters.lift_route`);
3. builds the request suite from the ``RepoModel`` (:mod:`lattice.importer.adapt`,
   independent of the lifts) and verifies each lifted route on its own: the route's
   Lattice program served by the Lattice runtime against the original app, on the
   route's cases, with the representation rules, the policy deviations the lift adopted
   (:mod:`lattice.importer.policy`) and the branch coverage of the route's and its auth
   dependencies' ``raise`` sites;
4. returns a :class:`LiftResult` per route: ``lifted-verified``, ``lifted-unverified``
   (the checker refused it, a case disagrees, a raise site went unexercised, or no
   suite could be built) or ``no-lifter`` (with the reason).

**The soundness gate:** :attr:`ImportResult.programs` holds only ``lifted-verified``
routes. Every other route is in :attr:`ImportResult.needs_llm` -- with its unverified
Lattice (if any) and the verification evidence, for the LLM rungs (I5) -- and is never
emitted as Lattice.

Verification logs the Lattice side in with a token minted by the original's own token
function (the profile's auth decodes it like a login's), so a route is verified without
the login route being part of its program. When no such function takes the users key
alone, the token is the one the original's own login route answers (on a scratch copy
of the seed database): the same credential, decoded by the import's auth.

Natives that read app configuration (the token decoder reading
``request.app.state.settings.secret_key``) get it from their NativeContext: the
generated profile declares the settings and where the original app keeps them
(``SETTINGS_FROM_APP``); their values are read once from an original app the factory
builds, and passed to the Lattice app at start-up -- exactly the configuration the
original runs with, never a guessed default.
"""

from __future__ import annotations

import dataclasses
import importlib
import sys
import time
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from fastapi.testclient import TestClient

from lattice.api.ir import EndpointPlan, lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.importer.adapt import SuiteInputs, adapt
from lattice.importer.extract import RepoModel, Route, _Index, extract, route_function
from lattice.importer.lifters import RouteLift, lift_route
from lattice.importer.lifters.base import RouteSource
from lattice.importer.policy import Policy, auth_policies, deviations_for, token_answers
from lattice.importer.profile_gen import ProfileResult, Unclear, generate_profile
from lattice.importer.suite import GeneratedSuite, generate_suite
from lattice.importer.verify import Deviation, Rule, ValidationEnvelope, VerifyResult, verify
from lattice.profile import Profile

LiftStatus = Literal["lifted-verified", "lifted-unverified", "no-lifter"]
HEADER = (
    "# lattice: v1\n"
    "# ruff: noqa: F821, F841 -- Lattice names are ambient; some bindings exist for their effect\n"
)
USER_DEP_KINDS = frozenset({"current_user", "active_user", "admin"})


@dataclass
class LiftResult:
    """One route's outcome. ``verify``: the differential (None when it did not run);
    ``deviations``: the policy deviations declared for its cases; ``reason``: why the
    route is not ``lifted-verified``."""

    key: str
    route: Route
    status: LiftStatus
    lift: RouteLift
    reason: str = ""
    verify: VerifyResult | None = None
    deviations: dict[str, Deviation] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()

    @property
    def router(self) -> str:
        return self.route.module.rsplit(".", 1)[-1]

    @property
    def needs_llm(self) -> bool:
        return self.status != "lifted-verified"

    @property
    def source(self) -> str | None:
        """The route's Lattice endpoint (emitted only when verified)."""
        return self.lift.source

    def tally(self) -> tuple[int, int, int, int] | None:
        return self.verify.tally() if self.verify is not None else None


@dataclass
class VerifyContext:
    """Everything verification of one route needs, kept so later rungs (the LLM loop,
    I5) verify their candidates exactly as the lifters' output was verified.
    ``suite`` is the lifted routes' suite; :meth:`full_suite` covers every route with a
    spec (the same seed plan: a route's cases do not depend on the other routes)."""

    root: Path
    model: RepoModel
    profile: Profile
    natives: dict[str, Callable[..., Any]]
    index: _Index
    inputs: SuiteInputs
    token_policies: tuple[Policy, ...]
    rules: tuple[Rule, ...]
    app_factory: Callable[[Path], Any]
    hypothesis_bodies: int = 5
    suite: GeneratedSuite | None = None
    _full: GeneratedSuite | None = field(default=None, repr=False)
    # the NativeContext settings' values (read from the original app), for build_app
    settings: dict[str, Any] = field(default_factory=dict)
    # tokens the original's login answered, per suite and user handle, with when (see
    # _login_tokens)
    _tokens: dict[tuple[int, str], tuple[str, float]] = field(default_factory=dict, repr=False)

    def full_suite(self) -> GeneratedSuite | None:
        if self._full is None and self.inputs.routes:
            self._full = _suite(list(self.inputs.routes.values()), self.inputs, self.profile,
                                self.hypothesis_bodies)  # fmt: skip
        return self._full

    def suite_for(self, key: str) -> GeneratedSuite | None:
        """A suite holding ``key``'s cases (None: the route has no spec)."""
        if key not in self.inputs.routes:
            return None
        if self.suite is not None and any(c.route == key for c in self.suite.cases):
            return self.suite
        return self.full_suite()


@dataclass
class ImportResult:
    model: RepoModel
    profile: ProfileResult
    results: list[LiftResult]
    programs: dict[str, str]  # router (module's last part) -> Lattice source, verified routes only
    natives: dict[str, Callable[..., Any]]
    lattice_profile: Profile
    suite: GeneratedSuite | None = None
    context: VerifyContext | None = None

    @property
    def unclear(self) -> list[Unclear]:
        return self.profile.unclear

    @property
    def verified(self) -> list[LiftResult]:
        return [r for r in self.results if r.status == "lifted-verified"]

    @property
    def needs_llm(self) -> list[LiftResult]:
        return [r for r in self.results if r.needs_llm]

    def result(self, key: str) -> LiftResult:
        return next(r for r in self.results if r.key == key)

    def program(self) -> str:
        """Every emitted router file as one program (each header once)."""
        parts = [p.removeprefix(HEADER).strip("\n") for p in self.programs.values()]
        return HEADER + "\n\n\n".join(parts) + "\n"


def load_profile(result: ProfileResult, name: str) -> ModuleType:
    """The generated profile module (its ``profile()`` and ``NATIVES``)."""
    module = ModuleType(name)
    exec(compile(result.source, f"<{name}>", "exec"), module.__dict__)  # noqa: S102 -- our own generated source
    return module


def program_source(sources: Sequence[str], origin: str) -> str:
    comment = f"# Lifted from {origin} by lattice.importer (deterministic lifters, verified).\n"
    return HEADER + comment + "\n\n\n".join(s.strip("\n") for s in sources) + "\n"


def check_program(
    source: str, profile: Profile
) -> tuple[tuple[EndpointPlan, ...] | None, tuple[str, ...]]:
    """The lowered plans, or None and the error diagnostics."""
    checked = check_source(source, profile)
    errors = tuple(d.render() for d in checked.diagnostics if d.severity is Severity.ERROR)
    if checked.program is None or errors:
        return None, errors or ("the program was not accepted",)
    return lower_api(checked.program), ()


def _functions(model: RepoModel, route: Route) -> list[Callable[..., Any]]:
    """The original's functions behind a route: the route and its user dependencies."""
    deps = {d.name: d for d in model.dependencies}
    names: list[str] = []
    todo = list(route.dependencies)
    while todo:
        name = todo.pop(0)
        d = deps.get(name)
        if d is None or d.kind not in USER_DEP_KINDS or name in names:
            continue
        names.append(name)
        todo += list(d.depends_on)
    out = [getattr(importlib.import_module(route.module), route.name)]
    for name in sorted(names, key=lambda n: deps[n].kind != "current_user"):
        out.append(getattr(importlib.import_module(deps[name].module), name))
    return out


def lift_repo(
    repo_root: str | Path,
    app_package: str,
    app_factory: Callable[[Path], Any],
    *,
    only: Collection[str] | None = None,
    rules: Sequence[Rule] | None = None,
    hypothesis_bodies: int = 5,
) -> ImportResult:
    """Import the repository with the deterministic lifters only (see the module
    docstring). ``app_factory(db_path)`` builds the original app on a SQLite file;
    ``only`` limits the routes (by key: the route name, module-qualified when repeated)."""
    root = Path(repo_root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    model = extract(root, app_package)
    index = _Index(root, app_package)
    try:
        answers = token_answers(model, index)
    except Exception:  # noqa: BLE001 -- an unfamiliar auth shape: the default answers
        answers = None
    generated = generate_profile(model, answers)
    module = load_profile(generated, f"lattice_import_profile_{uuid.uuid4().hex}")
    profile: Profile = module.profile()
    api = profile.api
    if api is None:
        raise ValueError("the generated profile has no API settings")
    natives: dict[str, Callable[..., Any]] = dict(module.NATIVES)
    settings = app_settings(module, app_factory)
    inputs = adapt(model, api, index, natives, settings)
    try:
        token_policies = auth_policies(model, index, api)
    except Exception:  # noqa: BLE001 -- an unfamiliar auth shape: no policy (cases then fail)
        token_policies = ()

    results: list[LiftResult] = []
    for route in model.routes:
        key = inputs.keys[(route.module, route.name)]
        if only is not None and key not in only:
            continue
        fn = route_function(index, route)
        if fn is None or route.enclosing is not None:
            where = f"inside {route.enclosing}()" if route.enclosing else "where it cannot be found"
            lifted = RouteLift(route.name, (), reason=f"the route is defined {where}: not lifted")
            results.append(LiftResult(key, route, "no-lifter", lifted, reason=lifted.reason))
            continue
        try:
            lifted = lift_route(RouteSource(route, fn, index, api, model), model)  # type: ignore[arg-type]
        except Exception as err:  # noqa: BLE001 -- never crash on unfamiliar code
            why = f"a lifter failed: {type(err).__name__}: {err}"
            lifted = RouteLift(route.name, (), reason=why)
        status: LiftStatus = "lifted-unverified" if lifted.ok else "no-lifter"
        results.append(LiftResult(key, route, status, lifted, reason=lifted.reason))

    lifted_results = [r for r in results if r.lift.ok]
    specs = [inputs.routes[r.key] for r in lifted_results if r.key in inputs.routes]
    suite = None
    if specs:
        try:
            suite = _suite(specs, inputs, profile, hypothesis_bodies)
        except Exception as err:  # noqa: BLE001 -- the routes are then unverifiable, not a crash
            why = f"the suite generator failed: {type(err).__name__}: {err}"
            for r in lifted_results:
                if r.key in inputs.routes:
                    inputs.skipped[r.key] = why
                    del inputs.routes[r.key]
    rules = tuple(rules) if rules is not None else (ValidationEnvelope(),)
    ctx = VerifyContext(root, model, profile, natives, index, inputs, tuple(token_policies),
                        rules, app_factory, hypothesis_bodies, suite,
                        settings=settings)  # fmt: skip
    for r in lifted_results:
        try:
            _verify_route(r, ctx, suite)
        except Exception as err:  # noqa: BLE001 -- unverified with the reason, never a crash
            r.status, r.verify = "lifted-unverified", None
            r.reason = f"verification failed: {type(err).__name__}: {err}"

    programs = _emit(results, profile)
    return ImportResult(model, generated, results, programs, natives, profile, suite, ctx)


def app_settings(module: ModuleType, app_factory: Callable[[Path], Any]) -> dict[str, Any]:
    """The values of the generated profile's NativeContext settings, read from an
    original app built by ``app_factory`` (``SETTINGS_FROM_APP``); {} when the profile
    declares none or the app cannot be built (the routes needing them then fail to
    start, with the reason)."""
    if not getattr(module, "SETTINGS_FROM_APP", None):
        return {}
    import tempfile

    with tempfile.TemporaryDirectory(prefix="lattice-settings-") as tmp:
        try:
            app = app_factory(Path(tmp) / "settings.db")
            values = dict(module.settings_from_app(app))
        except Exception:  # noqa: BLE001 -- an unfamiliar factory: no values, not a crash
            return {}
        engine = getattr(getattr(app, "state", None), "engine", None)
        if engine is not None:
            engine.dispose()
    return values


# how long a token the original's login answered is reused: well under any sensible
# token lifetime (the held-out apps' is 30 minutes), which a long import outlives
LOGIN_TOKEN_REUSE_SECONDS = 300.0
_clock = time.monotonic


def _login_tokens(ctx: VerifyContext, suite: GeneratedSuite) -> Callable[[Any, str], str]:
    """A Lattice-side login: the token the original's own login route answers for the
    handle, on a scratch copy of the seed database (reused for
    :data:`LOGIN_TOKEN_REUSE_SECONDS`, then fetched again: it may expire)."""
    import tempfile

    login = suite.login
    assert login is not None

    def lattice_login(client: Any, handle: str) -> str:
        key = (id(suite), handle)
        cached = ctx._tokens.get(key)
        if cached is None or _clock() - cached[1] > LOGIN_TOKEN_REUSE_SECONDS:
            with tempfile.TemporaryDirectory(prefix="lattice-login-") as tmp:
                db = Path(suite.db_factory("seed", Path(tmp) / "login.db"))
                app = ctx.app_factory(db)
                try:
                    with TestClient(app) as original:
                        cached = (login(original, handle), _clock())
                        ctx._tokens[key] = cached
                finally:
                    engine = getattr(getattr(app, "state", None), "engine", None)
                    if engine is not None:
                        engine.dispose()
        return cached[0]

    return lattice_login


def _suite(
    specs: Sequence[Any], inputs: SuiteInputs, profile: Profile, hypothesis_bodies: int
) -> GeneratedSuite:
    api = profile.api
    assert api is not None
    return generate_suite(
        specs,
        inputs.tables,
        inputs.auth,
        hypothesis_bodies=hypothesis_bodies,
        insert_hook=inputs.insert_hook,
        uuid_storage=api.uuid_storage,
        datetime_storage=api.datetime_storage,
    )


def verify_candidate(
    ctx: VerifyContext,
    base: LiftResult,
    source: str,
    *,
    profile: Profile | None = None,
    natives: dict[str, Callable[..., Any]] | None = None,
) -> LiftResult:
    """Verify ``source`` (one Lattice endpoint, no header) as ``base``'s route, exactly as
    a lifter's output is verified (checker, the route's cases with its policies, branch
    coverage). ``profile``/``natives`` override the context's (a carve-out adds natives).
    Returns a new :class:`LiftResult`: ``lifted-verified`` or ``lifted-unverified`` with
    the evidence. The policies are those of the lifter fragments that matched the route
    (and the auth policies), never ones the candidate claims."""
    policies = tuple(p for f in base.lift.fragments for p in f.policies)
    lift = dataclasses.replace(base.lift, source=source, policies=policies)
    r = LiftResult(base.key, base.route, "lifted-unverified", lift)
    _verify_route(r, ctx, ctx.suite_for(base.key), profile=profile, natives=natives)
    return r


def _verify_route(
    r: LiftResult,
    ctx: VerifyContext,
    suite: GeneratedSuite | None,
    *,
    profile: Profile | None = None,
    natives: dict[str, Callable[..., Any]] | None = None,
) -> None:
    model, inputs, rules, app_factory = ctx.model, ctx.inputs, ctx.rules, ctx.app_factory
    profile = profile or ctx.profile
    natives = natives if natives is not None else ctx.natives
    token_policies = ctx.token_policies
    assert r.lift.source is not None and profile.api is not None
    plans, errors = check_program(program_source([r.lift.source], r.route.module), profile)
    if plans is None:
        r.reason, r.diagnostics = "the checker refuses the lifted program", errors
        return
    if suite is None or r.key not in inputs.routes:
        r.reason = f"no request suite: {inputs.skipped.get(r.key, 'unknown route')}"
        return
    cases = [c for c in suite.cases if c.route == r.key]
    if not cases:
        r.reason = "no request suite: the suite has no case for the route"
        return
    policies = [*r.lift.policies, *(token_policies if r.route.auth != "Public" else ())]
    r.deviations = deviations_for(cases, policies, profile.api)
    mint = inputs.auth.mint_token

    def lattice_login(client: Any, handle: str) -> str:
        assert mint is not None
        return mint(suite.plan.user(handle).id)

    login_as = lattice_login if mint is not None else None
    if login_as is None and suite.login is not None:
        login_as = _login_tokens(ctx, suite)  # the original's own token
    settings = ctx.settings
    result = verify(
        app_factory,
        lambda path: build_app(plans, path, natives=natives, settings=settings),
        suite.db_factory,
        cases,
        rules,
        r.deviations,
        login=suite.login,
        lattice_login=login_as,
        # the app's own password-hash column (a new salted hash differs on each side)
        hash_columns=(inputs.auth.password_column,),
        branches={r.key: _functions(model, r.route)},
    )
    r.verify = result
    problems = [f"{n}: {o.reason}" for n, o in result.mismatches.items()]
    problems += [f"deviation for no case: {d}" for d in result.unknown_deviations]
    problems += [f"unverified branch: {s.render()}" for _, s in result.unverified]
    if problems:
        r.reason = "verification failed: " + "; ".join(problems)
        return
    r.status, r.reason = "lifted-verified", ""


def _emit(results: list[LiftResult], profile: Profile) -> dict[str, str]:
    """The verified routes, one program per router file (the joined file is checked
    again; a file the checker refuses sends its routes back to unverified)."""
    by_router: dict[str, list[LiftResult]] = {}
    for r in results:
        if r.status == "lifted-verified":
            by_router.setdefault(r.router, []).append(r)
    programs: dict[str, str] = {}
    for router, rs in by_router.items():
        source = program_source([r.lift.source or "" for r in rs], rs[0].route.module)
        plans, errors = check_program(source, profile)
        if plans is None:
            for r in rs:
                r.status, r.reason, r.diagnostics = (
                    "lifted-unverified",
                    "the joined router program is refused",
                    errors,
                )
            continue
        programs[router] = source
    return programs


__all__ = [
    "HEADER",
    "ImportResult",
    "LiftResult",
    "LiftStatus",
    "VerifyContext",
    "check_program",
    "lift_repo",
    "load_profile",
    "program_source",
    "verify_candidate",
]
