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
the login route being part of its program.
"""

from __future__ import annotations

import importlib
import sys
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from lattice.api.ir import EndpointPlan, lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.importer.adapt import SuiteInputs, adapt
from lattice.importer.extract import RepoModel, Route, _Index, extract
from lattice.importer.lifters import RouteLift, lift_route
from lattice.importer.lifters.base import RouteSource
from lattice.importer.policy import Policy, auth_policies, deviations_for
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
class ImportResult:
    model: RepoModel
    profile: ProfileResult
    results: list[LiftResult]
    programs: dict[str, str]  # router (module's last part) -> Lattice source, verified routes only
    natives: dict[str, Callable[..., Any]]
    lattice_profile: Profile
    suite: GeneratedSuite | None = None

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
    generated = generate_profile(model)
    module = load_profile(generated, f"lattice_import_profile_{uuid.uuid4().hex}")
    profile: Profile = module.profile()
    api = profile.api
    if api is None:
        raise ValueError("the generated profile has no API settings")
    natives: dict[str, Callable[..., Any]] = dict(module.NATIVES)
    index = _Index(root, app_package)
    inputs = adapt(model, api, index, natives)
    token_policies = auth_policies(model, index, api)

    results: list[LiftResult] = []
    for route in model.routes:
        key = inputs.keys[(route.module, route.name)]
        if only is not None and key not in only:
            continue
        fn = index.modules[route.module].defs.get(route.name)
        if fn is None:
            continue
        lifted = lift_route(RouteSource(route, fn, index, api, model), model)  # type: ignore[arg-type]
        status: LiftStatus = "lifted-unverified" if lifted.ok else "no-lifter"
        results.append(LiftResult(key, route, status, lifted, reason=lifted.reason))

    lifted_results = [r for r in results if r.lift.ok]
    specs = [inputs.routes[r.key] for r in lifted_results if r.key in inputs.routes]
    suite = None
    if specs:
        suite = generate_suite(
            specs,
            inputs.tables,
            inputs.auth,
            hypothesis_bodies=hypothesis_bodies,
            insert_hook=inputs.insert_hook,
            uuid_storage=api.uuid_storage,
            datetime_storage=api.datetime_storage,
        )
    rules = tuple(rules) if rules is not None else (ValidationEnvelope(),)
    for r in lifted_results:
        _verify_route(r, model, profile, natives, inputs, suite, token_policies, rules, app_factory)

    programs = _emit(results, profile)
    return ImportResult(model, generated, results, programs, natives, profile, suite)


def _verify_route(
    r: LiftResult,
    model: RepoModel,
    profile: Profile,
    natives: dict[str, Callable[..., Any]],
    inputs: SuiteInputs,
    suite: GeneratedSuite | None,
    token_policies: Sequence[Policy],
    rules: Sequence[Rule],
    app_factory: Callable[[Path], Any],
) -> None:
    assert r.lift.source is not None and profile.api is not None
    plans, errors = check_program(program_source([r.lift.source], r.route.module), profile)
    if plans is None:
        r.reason, r.diagnostics = "the checker refuses the lifted program", errors
        return
    if suite is None or r.key not in inputs.routes:
        r.reason = f"no request suite: {inputs.skipped.get(r.key, 'unknown route')}"
        return
    cases = [c for c in suite.cases if c.route == r.key]
    policies = [*r.lift.policies, *(token_policies if r.route.auth != "Public" else ())]
    r.deviations = deviations_for(cases, policies, profile.api)
    mint = inputs.auth.mint_token

    def lattice_login(client: Any, handle: str) -> str:
        assert mint is not None
        return mint(suite.plan.user(handle).id)

    result = verify(
        app_factory,
        lambda path: build_app(plans, path, natives=natives),
        suite.db_factory,
        cases,
        rules,
        r.deviations,
        login=suite.login,
        lattice_login=lattice_login if mint is not None else None,
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
    "check_program",
    "lift_repo",
    "load_profile",
]
