"""The Django + Django REST framework front end (Phase 2 plan §2.2, W2-A).

Tables come from the live model registry (:mod:`.django_orm`); routes from the live URLconf
(``get_resolver()``, walked through every ``include``), each view read from its live class
(``callback.cls``, a viewset's ``callback.actions`` and ``initkwargs``) plus the package's
AST of the methods it runs. Django's settings and registry are process-global: one Django
project per process (``python -m lattice.importer.frontends.django_drf ROOT PACKAGE``
runs :func:`extract` in a fresh process and prints the model as JSON).

**Routes.** One :class:`Route` per (method, path): a viewset route per method of its
``actions`` (``Route.view`` ``module:Class.action``, named ``<basename>_<action>``), an
``APIView`` per HTTP method it defines (``module:Class.get``). Paths are ``/``-rooted with
``{name}`` parameters (a ``pk`` takes the queryset model's key type). Left out, each listed
as an :class:`Excluded` with its reason: DRF's format-suffix duplicates (``/snippets.json``,
``{format}``; folded into the route they repeat), the ``DefaultRouter`` API root
(``APIRootView``), Django admin, DRF's ``api-auth`` login pages, static files, schema and
docs views, and plain Django function views (their methods are not declared).

**Guards** (``Route.guards``) from the view's ``permission_classes`` (an ``@action``'s own
override; else the class's, which defaults to the settings' ``DEFAULT_PERMISSION_CLASSES``):

- ``IsAuthenticated`` -> ``authenticated``; ``IsAuthenticatedOrReadOnly`` -> the same for
  unsafe methods; ``IsAdminUser`` -> ``authenticated`` + ``admin``; ``AllowAny`` -> none;
- a package class: its ``has_permission`` read for ``request.user.is_authenticated``
  (``authenticated``), ``is_staff`` / ``is_superuser`` (``admin``) or another
  ``request.user.<attr>`` (``role``, roles ``(attr,)``); its ``has_object_permission`` for
  ``obj.<col> == request.user`` (``object_owner``, and a ``compare`` owner use) -- only on
  routes that fetch one object through ``get_object``; a ``SAFE_METHODS`` test exempts
  reads; anything else is ``custom``.

The refusal of an anonymous caller is 401 when the view's first authentication class sends
a ``WWW-Authenticate`` header (Token, Basic, JWT), else 403 (Session first): DRF's rule. A
refused authenticated caller gets 403 with the class's ``message``. ``Route.auth`` is
``Public`` (no guard), ``Customer`` (authenticated / owner), ``Admin`` or ``unclear``.

**Owner evidence** (``OwnerUse``): ``compare`` from ``get_queryset``'s
``.filter(<fk>=request.user)`` on every route reading the queryset (not ``create``), and
from an owner object permission; ``assign`` from ``perform_create`` /
``perform_update``'s ``serializer.save(<fk>=request.user)`` (and a handler's own).

**Error sites** (``RaiseSite``, kind ``drf`` or ``return``) in the package methods a route
runs (handler, ``get_queryset``, ``get_object``, ``perform_*``, the body serializer's
``validate*`` / ``create`` / ``update``, the permission classes, and the ``self.`` helpers
they call): ``raise NotFound/PermissionDenied/ValidationError/...``,
``get_object_or_404(...)``, ``return Response(..., status=4xx)``. DRF's own sites are added
with line 0: the 404 of the generic ``get_object`` (detail ``No <Model> matches the given
query.``) and the 400 of a validated body.

**Bodies and parameters.** A write route's serializer (``get_serializer_class()`` with the
route's action) becomes a :class:`DataModel` of its writable fields (types, ``required``,
``allow_null``, length and value bounds, ``choices``, ``write_only``) and the body
parameter; query parameters come from ``request.query_params.get(...)`` reads (``int(...)``
around one: Int), the pagination class and the filter backends.

**AuthModel.** The scheme of the authenticated routes' (most common) authentication classes,
header schemes before the session: ``TokenAuthentication`` (``token``, the login route an
``ObtainAuthToken`` view, JSON, answering ``token``; minter ``Token``), simplejwt's
``JWTAuthentication`` (``bearer``; a ``TokenObtain*`` view answering ``access``; minter
``AccessToken.for_user``), ``BasicAuthentication`` (``basic``, no login route) or
``SessionAuthentication`` (``session``). The users model's ``USERNAME_FIELD``, ``password``,
``is_active``, ``is_staff`` columns; ``make_password`` hashes; and DRF's answers to an
unauthenticated, bad-token, unknown and inactive caller.

**Runtime** (:meth:`DjangoFrontEnd.runtime`): the WSGI application through
:class:`~lattice.importer.transport.WsgiTransport`, the AuthModel's scheme, dispose =
``connections.close_all()``, the seed written through the Django models, DRF's error
sites, no OpenAPI. A login with credentials: ``AuthDriver.from_model(model.auth, creds)``;
a token for a user key without logging in: :func:`minter`.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lattice.importer.appmodel import (
    AuthModel,
    DataModel,
    Excluded,
    FieldSpec,
    Guard,
    OwnerUse,
    RaiseSite,
    RepoModel,
    Route,
    RouteParam,
)
from lattice.importer.frontends import django_orm, register
from lattice.importer.frontends.pyindex import _choices_type, _Index, _single_assignment
from lattice.importer.transport import status_of

if TYPE_CHECKING:
    from lattice.importer.graph import GraphHints
    from lattice.importer.transport import AppRuntime

# DRF's (and Django's) exceptions an API view answers with a status
DRF_EXCEPTIONS = {
    "ValidationError": 400,
    "ParseError": 400,
    "AuthenticationFailed": 401,
    "NotAuthenticated": 401,
    "PermissionDenied": 403,
    "NotFound": 404,
    "Http404": 404,
    "MethodNotAllowed": 405,
    "NotAcceptable": 406,
    "UnsupportedMediaType": 415,
    "Throttled": 429,
}
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
NOT_AUTHENTICATED = "Authentication credentials were not provided."
FORBIDDEN = "You do not have permission to perform this action."
GENERIC_ACTIONS = ("list", "retrieve", "create", "update", "partial_update", "destroy")
OBJECT_ACTIONS = frozenset({"retrieve", "update", "partial_update", "destroy"})
QUERYSET_ACTIONS = OBJECT_ACTIONS | {"list"}
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH"})
ACTION_STATUS = {"create": 201, "destroy": 204}
PERFORM_HOOKS = {
    "create": ["perform_create"],
    "update": ["perform_update"],
    "partial_update": ["perform_update"],
    "destroy": ["perform_destroy"],
}
ADMIN_ATTRS = frozenset({"is_staff", "is_superuser", "is_admin"})
GETTEXT = frozenset({"_", "gettext", "gettext_lazy", "ugettext", "ugettext_lazy", "pgettext"})
FILTER_CALLS = frozenset({"filter", "get", "exclude", "get_object_or_404"})
SERIALIZER_HOOKS = ("validate", "create", "update")
FUNCTION_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
# a related manager's writing methods: ``<x>.<m2m>.<one of them>(...)`` writes its link
# table (pivot plan 2026-10-05, A.2)
M2M_WRITES = frozenset({"add", "remove", "set", "clear", "create", "get_or_create",
                        "update_or_create", "bulk_create"})  # fmt: skip
# methods a builtin container (or str) also has: not followed into a model's method
BUILTIN_METHODS = frozenset(
    n for t in (dict, list, set, str, tuple, bytes) for n in dir(t) if not n.startswith("_")
)
FRAMEWORK_VIEW_MODULES = (
    ("django.contrib.admin", "Django admin"),
    ("django.contrib.auth.views", "DRF browsable-API session login page (api-auth)"),
    ("django.views.static", "static files"),
    ("django.contrib.staticfiles", "static files"),
    ("rest_framework.schemas", "API schema view"),
    ("rest_framework.documentation", "API docs view"),
    ("drf_spectacular", "API schema / docs view"),
    ("drf_yasg", "API schema / docs view"),
)
TOKEN_MINTER = "rest_framework.authtoken.models:Token"
JWT_MINTER = "rest_framework_simplejwt.tokens:AccessToken.for_user"
HASHER = "django.contrib.auth.hashers:make_password"
# DRF's answers to a caller its authentication class refuses: (bad token, unknown, inactive)
AUTH_ANSWERS = {
    "token": ("Invalid token.", None, "User inactive or deleted."),
    "basic": ("Invalid username/password.", None, "User inactive or deleted."),
    "jwt": ("Given token not valid for any token type", "User not found", "User is inactive"),
}


# ------------------------------------------------------------------------ error sites --


def _detail(expr: ast.expr | None) -> str | None:
    """A literal detail: ``"x"`` or ``_("x")`` (a gettext call)."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if (
        isinstance(expr, ast.Call)
        and _name(expr.func) in GETTEXT
        and expr.args
        and isinstance(expr.args[0], ast.Constant)
        and isinstance(expr.args[0].value, str)
    ):
        return expr.args[0].value
    return None


def _name(expr: ast.expr | None) -> str | None:
    if isinstance(expr, ast.Attribute):
        return expr.attr
    if isinstance(expr, ast.Name):
        return expr.id
    return None


def error_site_kind(node: ast.AST) -> tuple[int | None, str | None, str] | None:
    """A DRF view's error site: ``raise <DRF exception>(detail)`` (kind ``drf``),
    ``get_object_or_404(...)`` (404, ``drf``), ``return Response(..., status=4xx)``
    (``return``) -> (status, detail when literal, kind)."""
    if isinstance(node, ast.Raise) and node.exc is not None:
        exc = node.exc
        func = exc.func if isinstance(exc, ast.Call) else exc
        status = DRF_EXCEPTIONS.get(_name(func) or "")
        if status is None:
            return None
        detail = None
        if isinstance(exc, ast.Call):
            kw = {k.arg: k.value for k in exc.keywords if k.arg}
            detail = _detail(kw.get("detail", exc.args[0] if exc.args else None))
        return status, detail, "drf"
    if isinstance(node, ast.Call) and _name(node.func) == "get_object_or_404":
        return 404, None, "drf"
    if (
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Call)
        and _name(node.value.func) == "Response"
    ):
        kw = {k.arg: k.value for k in node.value.keywords if k.arg}
        status = status_of(
            kw.get("status", node.value.args[1] if len(node.value.args) > 1 else None)
        )
        if status is not None and status >= 400:
            return status, None, "return"
    return None


def drf_error_site(node: ast.AST) -> tuple[int | None, str | None] | None:
    """The runtime's error-site predicate (branch coverage): :func:`error_site_kind`."""
    found = error_site_kind(node)
    return None if found is None else (found[0], found[1])


def is_drf_error_class(qual: str) -> bool:
    """A resolved exception class answering an error: DRF's exceptions, Django's
    ``Http404`` / ``PermissionDenied``."""
    return qual.startswith("rest_framework.exceptions:") or qual.rsplit(":", 1)[-1] in (
        "Http404",
        "PermissionDenied",
    )


# ------------------------------------------------------------------------------ paths --

_REGEX_GROUP = re.compile(r"\(\?P<(\w+)>([^)]*)\)")
_ROUTE_PARAM = re.compile(r"<(?:(\w+):)?(\w+)>")


def _pattern_piece(pattern: Any) -> tuple[str, dict[str, str | None]]:
    """A URL pattern's text as a path piece with ``{name}`` parameters, and each
    parameter's type (None: not known from the pattern)."""
    from django.urls.resolvers import RegexPattern, RoutePattern

    params: dict[str, str | None] = {}
    if isinstance(pattern, RoutePattern):
        text = str(pattern)

        def route_param(m: re.Match[str]) -> str:
            from lattice.importer.formats import of_converter

            conv = pattern.converters.get(m.group(2))
            kind = type(conv).__name__ if conv is not None else ""
            typ = {"IntConverter": "Int", "UUIDConverter": "Uuid"}.get(kind, "Str")
            fmt = of_converter(conv)
            # a converter's format rides on the type ("Str/slug"): _path_params splits it
            params[m.group(2)] = f"{typ}/{fmt}" if fmt else typ
            return "{" + m.group(2) + "}"

        return _ROUTE_PARAM.sub(route_param, text), params
    if isinstance(pattern, RegexPattern):
        text = pattern.regex.pattern
        text = text.removeprefix("^").removesuffix("$").removesuffix("\\Z")

        def regex_param(m: re.Match[str]) -> str:
            params[m.group(1)] = "Int" if m.group(2) in (r"\d+", "[0-9]+") else "Str"
            return "{" + m.group(1) + "}"

        text = _REGEX_GROUP.sub(regex_param, text)
        text = text.replace("/?", "").replace("\\", "")
        return text, params
    return str(pattern), params


# -------------------------------------------------------------------------- extraction --


@dataclass
class _View:
    """One URL pattern's DRF view."""

    path: str
    params: dict[str, str | None]
    cls: Any
    actions: dict[str, str] | None
    initkwargs: dict[str, Any]


class _Extractor:
    def __init__(self, root: Path, package: str) -> None:
        self.root = root
        self.package = package
        self.index = _Index(root, package)
        self.excluded: list[Excluded] = []
        self.models: dict[str, DataModel] = {}
        self.data_names: dict[Any, str] = {}
        self.route_cls: dict[str, Any] = {}  # route name -> its view class
        self.route_auths: dict[str, list[Any]] = {}  # route name -> authentication classes

    # -- the URLconf --

    def views(self) -> list[_View]:
        from django.urls import URLResolver, get_resolver

        out: list[_View] = []

        def walk(resolver: Any, prefix: str, params: dict[str, str | None]) -> None:
            for p in resolver.url_patterns:
                piece, more = _pattern_piece(p.pattern)
                path, all_params = prefix + piece, {**params, **more}
                if isinstance(p, URLResolver):
                    if p.namespace == "admin" or p.app_name == "admin":
                        n = _count_patterns(p)
                        self._exclude(
                            f"* /{path}", f"Django admin ({n} URL patterns): framework route"
                        )
                        continue
                    walk(p, path, all_params)
                    continue
                view = self._view(p, "/" + path, all_params)
                if view is not None:
                    out.append(view)

        walk(get_resolver(), "", {})
        return out

    def _exclude(self, name: str, reason: str) -> None:
        self.excluded.append(Excluded("route", name, reason))

    def _view(self, p: Any, path: str, params: dict[str, str | None]) -> _View | None:
        from rest_framework.routers import APIRootView

        callback = p.callback
        cls = getattr(callback, "cls", None)
        actions = getattr(callback, "actions", None)
        module = getattr(cls or callback, "__module__", "") or ""
        methods = ",".join(m.upper() for m in actions) if actions else self._method_list(cls)
        for prefix, what in FRAMEWORK_VIEW_MODULES:
            if module.startswith(prefix):
                self._exclude(f"* {path}", f"{what}: framework route")
                return None
        if cls is None:
            self._exclude(
                f"* {path}",
                "a Django function view that is not DRF's: its methods are not declared",
            )
            return None
        if isinstance(cls, type) and issubclass(cls, APIRootView):
            self._exclude(
                f"{methods} {path}",
                "DRF DefaultRouter API root view (APIRootView): framework route",
            )
            return None
        if "format" in params:
            base = path.replace(".{format}", "/").replace("{format}", "").replace("//", "/")
            self._exclude(
                f"{methods} {path}", f"DRF format-suffix duplicate of {base} (folded into it)"
            )
            return None
        return _View(
            path,
            params,
            cls,
            dict(actions) if actions else None,
            dict(getattr(callback, "initkwargs", {}) or {}),
        )

    @staticmethod
    def _method_list(cls: Any) -> str:
        if cls is None:
            return "*"
        return ",".join(m.upper() for m in cls.http_method_names
                        if m not in ("options", "head") and hasattr(cls, m))  # fmt: skip

    # -- per route --

    def routes(self, views: list[_View], tables: dict[Any, str]) -> list[Route]:
        self.tables = tables
        out: list[Route] = []
        names: Counter[str] = Counter()
        for v in views:
            if v.actions:
                items = [(m.upper(), a) for m, a in v.actions.items()]
            else:
                items = [(m.upper(), None) for m in v.cls.http_method_names
                         if m not in ("options", "head") and hasattr(v.cls, m)]  # fmt: skip
            for method, action in items:
                route = self._route(v, method, action)
                names[route.name] += 1
                if names[route.name] > 1:
                    route = _renamed(route, f"{route.name}_{names[route.name]}")
                out.append(route)
                self.route_cls[route.name] = v.cls
                self.route_auths[route.name] = _auth_classes(v)
        return out

    def _route(self, v: _View, method: str, viewset_action: str | None) -> Route:
        cls = v.cls
        handler_name = viewset_action or method.lower()
        handler = getattr(cls, handler_name, None)
        own_handler = self._own(cls, handler_name)
        action = viewset_action or self._generic_action(handler, own_handler)
        basename = v.initkwargs.get("basename")
        if viewset_action:
            name = f"{basename or _snake(cls.__name__)}_{viewset_action}".replace("-", "_")
        else:
            name = f"{_snake(cls.__name__)}_{method.lower()}"
        qs_model = _queryset_model(cls)
        serializer = self._serializer(v, viewset_action or action, method)
        ser_model = _serializer_model(serializer)
        perms = list(v.initkwargs.get("permission_classes", getattr(cls, "permission_classes", ())))
        perms = self._live_permissions(v, viewset_action or action, method, perms)
        fns = self._functions(cls, own_handler, action, method, serializer, perms)
        # the models the package code the route runs reads or writes (Model.objects,
        # get_object_or_404, a model serializer, related managers, module helpers): a
        # plain ViewSet / APIView method names its model nowhere else
        orm, link_tables, unread, rel_tables = self._orm(fns)
        model = qs_model or ser_model or (orm[0] if orm else None)
        get_object = self._own(cls, "get_object")
        if own_handler is not None:
            # a handler of its own fetches through get_object (or super()'s action), or
            # checks the object permissions on a row it fetched itself
            fetched = _calls_self(own_handler[1], "get_object") or (
                action in OBJECT_ACTIONS and _calls(own_handler[1], action)
            )
            fetches = fetched or _calls_self(own_handler[1], "check_object_permissions")
        else:
            fetched = fetches = action in OBJECT_ACTIONS
        if get_object is not None:  # an override: what it does
            object_checked = fetches and _calls(
                get_object[1], "check_object_permissions", "get_object"
            )
            implicit_404 = fetched and _calls(get_object[1], "get_object_or_404", "get_object")
        else:
            object_checked, implicit_404 = fetches, fetched
        uses_queryset = action in QUERYSET_ACTIONS or (
            own_handler is not None
            and _calls_self(own_handler[1], "get_object", "get_queryset", "filter_queryset")
        )
        if get_object is not None and not _calls(get_object[1], "get_queryset", "get_object"):
            uses_queryset = action == "list" or (
                own_handler is not None
                and _calls_self(own_handler[1], "get_queryset", "filter_queryset")
            )
        auths = _auth_classes(v)
        unauth = _unauthenticated_status(auths)
        guards: list[Guard] = []
        owner: list[OwnerUse] = []
        line = self._attr_line(cls, "permission_classes")
        for perm in perms:
            g, o = self._permission(perm, method, object_checked, unauth, model, line)
            guards += g
            owner += o
        if uses_queryset:
            owner += self._queryset_owner(cls, model)
        if action == "create":
            owner += self._save_owner(cls, "perform_create", ser_model or model)
        elif action in ("update", "partial_update"):
            owner += self._save_owner(cls, "perform_update", ser_model or model)
        if own_handler is not None:
            owner += self._calls_owner(own_handler, ser_model or model)
        params = self._path_params(v, cls, qs_model or model)
        params += self._query_params(cls, own_handler, action)
        body_models: tuple[str, ...] = ()
        if method in WRITE_METHODS and serializer is not None:
            data = self._data_model(serializer)
            params.append(RouteParam("data", data, data, "body"))
            body_models = (data,)
        raises = self._raises(fns)
        if implicit_404 and model is not None:
            raises.append(
                RaiseSite(404, f"No {model._meta.object_name} matches the given query.", 0, "drf")
            )
        validated = action in ("create", "update", "partial_update") or any(
            _calls(f, "is_valid") for _, f in fns
        )
        if validated and serializer is not None and method in WRITE_METHODS:
            raises.append(RaiseSite(400, None, 0, "drf"))
        tables = [self.tables[m] for m in (qs_model, ser_model) if m in self.tables]
        if method in WRITE_METHODS and serializer is not None and ser_model is not None:
            written = self._link_tables(serializer, ser_model)
            tables += written
            rel_tables += written
        tables += [self.tables[m] for m in orm if m in self.tables] + link_tables
        returns = None
        out_model = (
            _serializer_model(self._serializer(v, viewset_action or action, "GET")) or ser_model
        )
        if action != "destroy" and out_model in self.tables:
            returns = self.tables[out_model]
        status = ACTION_STATUS.get(action or "", 200)
        if own_handler is not None:
            status = _handler_status(own_handler[1]) or status
        auth, reason = _route_auth(guards, perms, auths)
        return Route(
            name=name,
            module=cls.__module__,
            method=method,
            path=v.path,
            params=tuple(params),
            response_model=returns if action != "list" else None,
            response_class=None,
            status_code=status,
            dependencies=(),
            auth=auth,
            auth_reason=reason,
            raises=tuple(dict.fromkeys(raises)),
            calls=(),
            tables=tuple(dict.fromkeys(tables)),
            owner_uses=tuple(dict.fromkeys(owner)),
            body_models=body_models,
            line=own_handler[1].lineno if own_handler is not None else self._class_line(cls),
            returns=returns,
            guards=tuple(guards),
            view=f"{cls.__module__}:{cls.__qualname__}.{handler_name}",
            unread=unread,
            rel_tables=tuple(dict.fromkeys(rel_tables)),
        )

    def _live_permissions(self, v: _View, action: str | None, method: str,
                          default: list[Any]) -> list[Any]:  # fmt: skip
        """The permission classes a view's own ``get_permissions`` gives for ``action``
        (one adding ``IsAdminOrOwner`` for ``destroy`` only), evaluated on the live view;
        ``default`` when it has none or it needs more than the action and the method."""
        if self._own(v.cls, "get_permissions") is None:
            return default
        from types import SimpleNamespace

        try:
            view = v.cls(**v.initkwargs)
            view.action = action
            view.action_map = v.actions or {}
            view.request = SimpleNamespace(method=method, user=None, query_params={}, data={})
            view.args, view.kwargs, view.format_kwarg = (), {}, None
            found = [type(p) for p in view.get_permissions()]
        except Exception:  # noqa: BLE001 -- a get_permissions needing a real request
            return default
        return found or default

    def _orm(
        self, fns: list[tuple[str, ast.FunctionDef]]
    ) -> tuple[list[Any], list[str], str | None, list[str]]:
        """The models the functions (and the package's module functions they call,
        followed) read or write -- ``get_object_or_404(M)`` targets first, then
        ``M.objects``, ``M(...)``, a model serializer ``S(...)`` (its ``Meta.model``), a
        related manager (``topic.tags.add``, ``topic.comments.all``, also held in a local:
        ``tags = topic.tags; tags.add(t)``) -- the link tables of many-to-many managers
        they touch, why the evidence is incomplete (a call of package code that is not
        followed: a method of a package class; a relationship write the front end cannot
        name: ``getattr(obj, name).add(...)``), and the tables written through
        relationships (pivot plan 2026-10-05, A.2: a related manager's ``add`` /
        ``remove`` / ``set`` / ``clear`` / ``create``..., a model serializer setting a
        many-to-many field). A method call on a row (``post.star(user)``) is followed
        into every package model defining a method so named."""
        from rest_framework import serializers as drf_serializers

        model_serializer = drf_serializers.ModelSerializer
        base_serializer = drf_serializers.BaseSerializer

        first: list[Any] = []
        found: list[Any] = []
        links: list[str] = []
        unread: list[str] = []
        rels: list[str] = []
        accessors = self._accessors()
        seen: set[int] = set()
        todo = list(fns)
        while todo:
            mod, fn = todo.pop(0)
            if id(fn) in seen:
                continue
            seen.add(id(fn))
            g = vars(sys.modules[mod]) if mod in sys.modules else {}
            # locals holding a related manager: ``tags = topic.tags``
            aliases = {
                node.targets[0].id: node.value.attr
                for node in ast.walk(fn)
                if isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Attribute) and node.value.attr in accessors
            }  # fmt: skip
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and node.attr == "objects":
                    m = _live(node.value, g)
                    if m in self.tables:
                        found.append(m)
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if _name(f) in ("get_object_or_404", "get_list_or_404") and node.args:
                    m = _live(node.args[0], g)
                    m = getattr(m, "model", m)  # a queryset or manager names its model
                    if m in self.tables:
                        first.append(m)
                # a related manager: <x>.<accessor>.<method>(...), or a local holding one
                accessor = None
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute):
                    accessor = f.value.attr
                elif (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                      and f.value.id in aliases):  # fmt: skip
                    accessor = aliases[f.value.id]
                for m, through in accessors.get(accessor or "", ()):
                    found.append(m)
                    if through is not None and through in self.tables:
                        links.append(self.tables[through])
                        if isinstance(f, ast.Attribute) and f.attr in M2M_WRITES:
                            rels.append(self.tables[through])
                if (isinstance(f, ast.Attribute) and f.attr in M2M_WRITES
                        and isinstance(f.value, ast.Call) and _name(f.value.func) == "getattr"
                        and len(f.value.args) >= 2
                        and not isinstance(f.value.args[1], ast.Constant)):  # fmt: skip
                    unread.append(f"{ast.unparse(f)}(...) writes a relationship the front "
                                  f"end cannot name ({mod}:{node.lineno})")  # fmt: skip
                target = _live(f, g)
                if target is None and isinstance(f, ast.Attribute) and accessor is None \
                        and f.attr not in BUILTIN_METHODS:  # fmt: skip
                    # a method of a row (``post.star(user)``): every package model's
                    # method so named
                    for model in self.tables:
                        own = self._own(model, f.attr)
                        if own is not None:
                            todo.append(own)
                if target in self.tables:
                    found.append(target)
                elif isinstance(target, type) and issubclass(target, model_serializer):
                    m = getattr(getattr(target, "Meta", None), "model", None)
                    if m in self.tables:
                        found.append(m)
                        if any(k.arg == "data" for k in node.keywords):
                            written = self._link_tables(target, m)
                            links += written
                            rels += written
                elif isinstance(target, type) and issubclass(target, base_serializer):
                    # a package serializer: its own create / update / save run on .save()
                    for hook in ("create", "update", "save"):
                        own = self._own(target, hook)
                        if own is not None:
                            todo.append(own)
                elif callable(target) and django_orm.in_package(
                    getattr(target, "__module__", "") or "", self.package
                ):
                    qual = f"{target.__module__}:{getattr(target, '__qualname__', '')}"
                    hit = self.index.node(qual) if inspect.isfunction(target) else None
                    if hit is not None and isinstance(hit[1], FUNCTION_DEFS):
                        todo.append(hit)  # type: ignore[arg-type]
                    elif not isinstance(target, type) or not _known_class(target):
                        unread.append(f"calls {qual}, which the front end does not follow")
        models = list(dict.fromkeys([*first, *found]))
        reason = "; ".join(dict.fromkeys(unread)) or None
        return models, list(dict.fromkeys(links)), reason, list(dict.fromkeys(rels))

    def _accessors(self) -> dict[str, list[tuple[Any, Any]]]:
        """Related-manager names -> (the model it reaches, the many-to-many link model
        or None), over every model: forward and reverse many-to-many, reverse foreign
        keys."""
        if getattr(self, "_accessor_map", None) is not None:
            return self._accessor_map
        out: dict[str, list[tuple[Any, Any]]] = {}
        for model in self.tables:
            for f in model._meta.get_fields():
                if f.many_to_many and not f.auto_created:
                    out.setdefault(f.name, []).append((f.related_model, f.remote_field.through))
                elif f.auto_created and f.is_relation and (f.many_to_many or f.one_to_many):
                    name = f.get_accessor_name()
                    through = getattr(f, "through", None) if f.many_to_many else None
                    if through is None and f.many_to_many:
                        through = f.remote_field.through
                    if name:
                        out.setdefault(name, []).append((f.related_model, through))
        self._accessor_map = out
        return out

    # -- the package's code of a view --

    def _class_node(self, klass: Any) -> tuple[str, ast.ClassDef] | None:
        if not django_orm.in_package(klass.__module__, self.package):
            return None
        m = self.index.modules.get(klass.__module__)
        node = m.defs.get(klass.__name__) if m is not None else None
        return (klass.__module__, node) if isinstance(node, ast.ClassDef) else None

    def _own(self, cls: Any, name: str) -> tuple[str, ast.FunctionDef] | None:
        """The package's definition of ``cls.name`` (by the MRO), None when it comes
        from a library."""
        for klass in getattr(cls, "__mro__", ()):
            if name not in vars(klass):
                continue
            found = self._class_node(klass)
            if found is None:
                return None
            for stmt in found[1].body:
                if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and stmt.name == name:
                    return found[0], stmt  # type: ignore[return-value]
            return None
        return None

    def _attr_line(self, cls: Any, attr: str) -> int:
        for klass in getattr(cls, "__mro__", ()):
            if attr not in vars(klass):
                continue
            found = self._class_node(klass)
            if found is None:
                return 0
            for stmt in found[1].body:
                if isinstance(stmt, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == attr for t in stmt.targets
                ):
                    return stmt.lineno
            return 0
        return 0

    def _class_line(self, cls: Any) -> int:
        found = self._class_node(cls)
        return found[1].lineno if found is not None else 0

    @staticmethod
    def _generic_action(handler: Any, own: tuple[str, ast.FunctionDef] | None) -> str | None:
        """The generic mixin action a library handler calls (``self.retrieve(...)``)."""
        if own is not None or handler is None:
            return None
        try:
            source = inspect.getsource(handler)
        except (OSError, TypeError):
            return None
        found = re.search(r"self\.(" + "|".join(GENERIC_ACTIONS) + r")\(", source)
        return found.group(1) if found else None

    def _serializer(self, v: _View, action: str | None, method: str) -> Any:
        cls = v.cls
        try:
            view = cls(**v.initkwargs)
            view.action = action
            view.action_map = v.actions or {}
            view.request = None
            view.format_kwarg = None
            view.kwargs = {}
            serializer = view.get_serializer_class()
        except Exception:  # noqa: BLE001 -- a get_serializer_class needing a request
            serializer = getattr(cls, "serializer_class", None)
        return serializer if isinstance(serializer, type) else None

    def _functions(
        self,
        cls: Any,
        own_handler: Any,
        action: str | None,
        method: str,
        serializer: Any,
        perms: list[Any],
    ) -> list[tuple[str, ast.FunctionDef]]:
        """The package functions a route runs (see the module docstring), each once."""
        names: list[str] = []
        if action in QUERYSET_ACTIONS or own_handler is not None:
            names += ["get_queryset", "filter_queryset", "get_object"]
        names += PERFORM_HOOKS.get(action or "", [])
        names.append("get_serializer_class")
        out: list[tuple[str, ast.FunctionDef]] = [own_handler] if own_handler else []
        out += [f for f in (self._own(cls, n) for n in names) if f is not None]
        if serializer is not None and method in WRITE_METHODS:
            hooks = [
                n for n in dir(serializer) if n in SERIALIZER_HOOKS or n.startswith("validate_")
            ]
            out += [f for f in (self._own(serializer, n) for n in hooks) if f is not None]
        for perm in perms:
            if isinstance(perm, type):
                out += [f for f in (self._own(perm, n) for n in
                        ("has_permission", "has_object_permission")) if f is not None]  # fmt: skip
        seen: dict[int, tuple[str, ast.FunctionDef]] = {}
        todo = list(out)
        while todo:
            item = todo.pop(0)
            if id(item[1]) in seen:
                continue
            seen[id(item[1])] = item
            for helper in _self_calls(item[1]):
                found = self._own(cls, helper)
                if found is not None:
                    todo.append(found)
        return list(seen.values())

    def _raises(self, fns: list[tuple[str, ast.FunctionDef]]) -> list[RaiseSite]:
        out = []
        for _, fn in fns:
            for node in ast.walk(fn):
                found = error_site_kind(node)
                if found is None:
                    continue
                status, detail, kind = found
                if detail is None and isinstance(node, ast.Raise):
                    detail = _named_detail(fn, node)
                out.append(RaiseSite(status, detail, node.lineno, kind))  # type: ignore[attr-defined]
        return sorted(out, key=lambda s: s.line)

    # -- guards and owner evidence --

    def _permission(
        self, perm: Any, method: str, object_checked: bool, unauth: int, model: Any, line: int
    ) -> tuple[list[Guard], list[OwnerUse]]:
        from rest_framework import permissions as p

        if not isinstance(perm, type):  # composed (A | B, A & B, ~A): not read
            return [Guard("custom", repr(perm), 403, FORBIDDEN, line=line)], []
        source = f"{perm.__module__}:{perm.__qualname__}"
        message = getattr(perm, "message", None)
        detail = message if isinstance(message, str) else FORBIDDEN
        authenticated = Guard("authenticated", source, unauth, NOT_AUTHENTICATED, line=line)
        guards: list[Guard] = []
        owner: list[OwnerUse] = []
        has = perm.has_permission
        if has is p.IsAuthenticated.has_permission:
            guards.append(authenticated)
        elif has is p.IsAuthenticatedOrReadOnly.has_permission:
            if method not in SAFE_METHODS:
                guards.append(authenticated)
        elif has is p.IsAdminUser.has_permission:
            guards += [authenticated, Guard("admin", source, 403, detail, line=line)]
        elif has in (p.AllowAny.has_permission, p.BasePermission.has_permission):
            pass
        else:
            own = self._own(perm, "has_permission")
            if own is None:
                guards.append(Guard("custom", source, 403, detail, line=line))
            elif not (method in SAFE_METHODS and _mentions(own[1], "SAFE_METHODS")):
                guards += _read_has_permission(own[1], source, unauth, detail)
        obj = perm.has_object_permission
        if object_checked and obj is not p.BasePermission.has_object_permission:
            own = self._own(perm, "has_object_permission")
            if own is None:
                guards.append(Guard("custom", source, 403, detail, line=line))
            elif not (method in SAFE_METHODS and _mentions(own[1], "SAFE_METHODS")):
                cols = _object_owner_columns(own[1])
                columns = [c for c in (self._column(model, a) for a, _ in cols) if c]
                if columns:
                    # `request.user.is_staff or obj.user == request.user`: the admin
                    # bypasses the owner check (owner-or-admin)
                    bypass = ("Admin",) if set(_user_attrs(own[1])) & ADMIN_ATTRS else ()
                    guards.append(Guard("object_owner", source, 403, detail, bypass,
                                        own[1].lineno))  # fmt: skip
                    owner += [OwnerUse(self.tables[model], c, "compare", ln)
                              for c, (_, ln) in zip(columns, cols, strict=False)]  # fmt: skip
                else:
                    guards.append(Guard("custom", source, 403, detail, line=own[1].lineno))
        return guards, owner

    def _column(self, model: Any, attr: str) -> str | None:
        """The users foreign key column ``attr`` names on ``model`` (``owner`` /
        ``owner_id`` -> ``owner_id``), None when it is not one."""
        if model is None or model not in self.tables:
            return None
        users = django_orm.users_model()
        for f in model._meta.concrete_fields:
            if attr in (f.name, f.attname) and f.is_relation and f.related_model is users:
                return f.column
        return None

    def _queryset_owner(self, cls: Any, model: Any) -> list[OwnerUse]:
        out = []
        for name in ("get_queryset", "filter_queryset"):
            own = self._own(cls, name)
            if own is not None:
                out += self._filters(own[1], model, "compare")
        return out

    def _save_owner(self, cls: Any, hook: str, model: Any) -> list[OwnerUse]:
        own = self._own(cls, hook)
        return self._saves(own[1], model) if own is not None else []

    def _calls_owner(self, own: tuple[str, ast.FunctionDef], model: Any) -> list[OwnerUse]:
        scope = vars(sys.modules[own[0]]) if own[0] in sys.modules else {}
        return self._filters(own[1], model, "compare", scope) + self._saves(own[1], model, scope)

    def _filters(self, fn: ast.AST, model: Any, how: str,
                 scope: dict[str, Any] | None = None) -> list[OwnerUse]:  # fmt: skip
        """``.filter(<fk>=request.user)`` and the like, on the call's own model when it
        names one (``Comment.objects.filter``, ``get_object_or_404(Comment, ...)``),
        else on ``model``."""
        out = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and _name(node.func) in FILTER_CALLS:
                target = _call_model(node, scope or {}, self.tables) or model
                for kw in node.keywords:
                    if kw.arg and _is_caller(kw.value):
                        col = self._column(target, kw.arg.split("__", 1)[0])
                        if col is not None:
                            out.append(OwnerUse(self.tables[target], col, how, node.lineno))
        return out

    def _saves(self, fn: ast.AST, model: Any,
               scope: dict[str, Any] | None = None) -> list[OwnerUse]:  # fmt: skip
        """``.save(<fk>=request.user)`` / ``.create(...)``, on the call's own model when
        it names one (``Comment.objects.create``), else on ``model``."""
        out = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and _name(node.func) in ("save", "create"):
                target = _call_model(node, scope or {}, self.tables) or model
                for kw in node.keywords:
                    if kw.arg and _is_caller(kw.value):
                        col = self._column(target, kw.arg)
                        if col is not None:
                            out.append(OwnerUse(self.tables[target], col, "assign", node.lineno))
        return out

    # -- parameters and bodies --

    def _path_params(self, v: _View, cls: Any, model: Any) -> list[RouteParam]:
        from lattice.importer.formats import of_django_field

        lookup = {getattr(cls, "lookup_url_kwarg", None) or getattr(cls, "lookup_field", "pk")}
        out = []
        for name, spelled in v.params.items():
            typ, _, fmt = (spelled or "").partition("/")  # "Str/slug": a converter's format
            column = None
            if model is not None and name in lookup | {"pk"}:
                field_name = getattr(cls, "lookup_field", "pk") or "pk"
                try:
                    field = (
                        model._meta.pk if field_name == "pk" else model._meta.get_field(field_name)
                    )
                    typ = django_orm.field_type(field) or typ
                    if field is not model._meta.pk:
                        column = field.column  # rows looked up by another column
                        fmt = of_django_field(field) or fmt
                except Exception:  # noqa: BLE001 -- a lookup that is not a field
                    pass
            out.append(RouteParam(name, typ or "str", typ or None, "path", lookup=column,
                                  format=fmt or None))  # fmt: skip
        return out

    def _query_params(self, cls: Any, own_handler: Any, action: str | None) -> list[RouteParam]:
        found: dict[str, RouteParam] = {}
        fns = [own_handler[1]] if own_handler else []
        if action == "list":
            fns += [
                f[1] for f in (self._own(cls, n) for n in ("get_queryset", "filter_queryset")) if f
            ]
        for fn in fns:
            for p in _query_reads(fn):
                found.setdefault(p.name, p)
        if action == "list":
            for p in _framework_query_params(cls):
                found.setdefault(p.name, p)
        return list(found.values())

    def _data_model(self, serializer: Any) -> str:
        """The serializer's writable fields as a DataModel (its name)."""
        if serializer in self.data_names:
            return self.data_names[serializer]
        name = serializer.__name__
        if name in self.models:
            name = f"{serializer.__module__.rsplit('.', 1)[-1]}_{name}"
        self.data_names[serializer] = name
        try:
            fields = serializer().fields
        except Exception:  # noqa: BLE001 -- a serializer needing context to build fields
            fields = {}
        specs = tuple(self._ser_field(n, f) for n, f in fields.items() if not f.read_only)
        bases = tuple(
            f"{b.__module__}:{b.__qualname__}"
            for b in serializer.__bases__
            if django_orm.in_package(b.__module__, self.package)
        )
        self.models[name] = DataModel(name, serializer.__module__, specs, bases)
        return name

    def _ser_field(self, name: str, f: Any) -> FieldSpec:
        from rest_framework import fields as F
        from rest_framework import relations as R
        from rest_framework import serializers as S

        typ, item = _ser_type(f), None
        choices: tuple[Any, ...] = ()
        if isinstance(f, R.ManyRelatedField):
            typ, item = "List", _ser_type(f.child_relation)
        elif isinstance(f, S.ListSerializer):
            typ, item = "List", self._data_model(type(f.child))
        elif isinstance(f, S.BaseSerializer):
            typ = self._data_model(type(f))
        elif isinstance(f, F.ListField):
            typ, item = "List", _ser_type(f.child)
        if isinstance(f, F.ChoiceField):
            read = _choices_type(list(f.choices))
            typ, choices = read.type, read.choices
        bounds: dict[str, Any] = {}
        for attr, key in (("min_length", "min_length"), ("max_length", "max_length"),
                          ("min_value", "ge"), ("max_value", "le")):  # fmt: skip
            value = getattr(f, attr, None)
            if isinstance(value, int | float) and not isinstance(value, bool):
                bounds[key] = value
            elif value is not None and key in ("ge", "le"):
                bounds[key] = float(value)
        if isinstance(f, F.DecimalField) and f.max_digits is not None:
            places = f.decimal_places or 0
            top = round(10 ** (f.max_digits - places) - 10**-places, places)
            bounds.setdefault("le", top)
            bounds.setdefault("ge", -top)
        default = None if f.default is F.empty else repr(f.default)
        from lattice.importer.formats import of_drf_field

        return FieldSpec(
            name=name,
            annotation=type(f).__name__,
            type=typ,
            item=item,
            nullable=bool(getattr(f, "allow_null", False)),
            required=bool(f.required),
            default=default,
            choices=choices,
            write_only=bool(f.write_only),
            # a slug, a URL, an IP address, an email: the values it takes
            format=None if choices or item else of_drf_field(f),
            **bounds,
        )

    def _link_tables(self, serializer: Any, model: Any) -> list[str]:
        """The link tables of the many-to-many fields a write serializer sets."""
        try:
            fields = serializer().fields
        except Exception:  # noqa: BLE001
            return []
        out = []
        for name, f in fields.items():
            if f.read_only:
                continue
            source = getattr(f, "source", name) or name
            try:
                mf = model._meta.get_field(source)
            except Exception:  # noqa: BLE001 -- not a model field
                continue
            if mf.many_to_many and not mf.auto_created:
                through = mf.remote_field.through
                if through in self.tables:
                    out.append(self.tables[through])
        return out


# ------------------------------------------------------------------- AST helpers --


def _auth_classes(v: _View) -> list[Any]:
    """The view's authentication classes (an ``@action``'s own, else the class's, which
    defaults to the settings' ``DEFAULT_AUTHENTICATION_CLASSES``)."""
    return list(v.initkwargs.get("authentication_classes",
                                 getattr(v.cls, "authentication_classes", ())))  # fmt: skip


def _named_detail(fn: ast.AST, node: ast.Raise) -> str | None:
    """``msg = _("x"); raise ValidationError(msg)``: the literal of a local name the
    function assigns once."""
    exc = node.exc
    if not isinstance(exc, ast.Call):
        return None
    kw = {k.arg: k.value for k in exc.keywords if k.arg}
    arg = kw.get("detail", exc.args[0] if exc.args else None)
    if not isinstance(arg, ast.Name):
        return None
    return _detail(_single_assignment(fn, arg.id))


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _renamed(route: Route, name: str) -> Route:
    from dataclasses import replace

    return replace(route, name=name)


def _count_patterns(resolver: Any) -> int:
    from django.urls import URLResolver

    return sum(
        _count_patterns(p) if isinstance(p, URLResolver) else 1 for p in resolver.url_patterns
    )


def _is_caller(expr: ast.expr) -> bool:
    """``request.user`` / ``self.request.user`` (or its ``.id`` / ``.pk``)."""
    if isinstance(expr, ast.Attribute) and expr.attr in ("id", "pk"):
        expr = expr.value
    if not (isinstance(expr, ast.Attribute) and expr.attr == "user"):
        return False
    base = expr.value
    if isinstance(base, ast.Name) and base.id == "request":
        return True
    return (
        isinstance(base, ast.Attribute)
        and base.attr == "request"
        and isinstance(base.value, ast.Name)
        and base.value.id == "self"
    )


def _live(expr: ast.expr | None, scope: dict[str, Any]) -> Any:
    """The live object a name or a dotted name of modules and classes denotes in a
    module's globals (``Topic``, ``models.Topic``); None otherwise."""
    import builtins

    if isinstance(expr, ast.Name):
        return scope.get(expr.id, getattr(builtins, expr.id, None))
    if isinstance(expr, ast.Attribute):
        base = _live(expr.value, scope)
        if base is None or not (isinstance(base, type) or inspect.ismodule(base)):
            return None
        try:
            return inspect.getattr_static(base, expr.attr, None) and getattr(base, expr.attr)
        except Exception:  # noqa: BLE001 -- a descriptor needing an instance
            return None
    return None


def _call_model(node: ast.Call, scope: dict[str, Any], tables: dict[Any, str]) -> Any:
    """The model a call names itself: ``M.objects.<...>.<method>(...)`` or
    ``get_object_or_404(M, ...)``; None when it names none (``serializer.save``)."""
    if _name(node.func) in ("get_object_or_404", "get_list_or_404") and node.args:
        m = _live(node.args[0], scope)
        m = getattr(m, "model", m)
        return m if m in tables else None
    f = node.func
    while isinstance(f, ast.Attribute):
        if f.attr == "objects":
            m = _live(f.value, scope)
            return m if m in tables else None
        f = f.value
    return None


def _known_class(cls: type) -> bool:
    """A package class whose construction reads or writes no table by itself: a
    serializer, permission, pagination, exception, response or view."""
    from rest_framework.pagination import BasePagination
    from rest_framework.permissions import BasePermission
    from rest_framework.response import Response
    from rest_framework.serializers import BaseSerializer
    from rest_framework.views import APIView

    return issubclass(cls, (BaseSerializer, BasePermission, BasePagination, BaseException,
                            Response, APIView))  # fmt: skip


def _self_calls(fn: ast.AST) -> list[str]:
    return [
        n.func.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
    ]


def _calls_self(fn: ast.AST, *names: str) -> bool:
    return any(n in names for n in _self_calls(fn))


def _calls(fn: ast.AST, *names: str) -> bool:
    """``fn`` calls any of ``names`` (as a function, a method or ``super().name``)."""
    return any(isinstance(n, ast.Call) and _name(n.func) in names for n in ast.walk(fn))


def _mentions(fn: ast.AST, name: str) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id == name)
        or (isinstance(n, ast.Attribute) and n.attr == name)
        for n in ast.walk(fn)
    )


def _user_attrs(fn: ast.AST) -> list[str]:
    """The attributes read off ``request.user`` in ``fn``."""
    return [
        n.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Attribute) and _is_caller(n.value) and n.attr not in ("id", "pk")
    ]


def _read_has_permission(fn: ast.FunctionDef, source: str, unauth: int, detail: str) -> list[Guard]:
    attrs = _user_attrs(fn)
    out = []
    if "is_authenticated" in attrs or any(a in ADMIN_ATTRS for a in attrs):
        out.append(Guard("authenticated", source, unauth, NOT_AUTHENTICATED, line=fn.lineno))
    if any(a in ADMIN_ATTRS for a in attrs):
        out.append(Guard("admin", source, 403, detail, line=fn.lineno))
    roles = tuple(
        dict.fromkeys(a for a in attrs if a not in ADMIN_ATTRS and a != "is_authenticated")
    )
    if roles:
        out.append(Guard("role", source, 403, detail, roles, fn.lineno))
    if not out:
        out.append(Guard("custom", source, 403, detail, line=fn.lineno))
    return out


def _object_owner_columns(fn: ast.FunctionDef) -> list[tuple[str, int]]:
    """``obj.<attr> == request.user`` (either side; ``.id`` / ``.pk`` on both allowed) in
    a ``has_object_permission``: (attr, line) each."""
    args = [a.arg for a in fn.args.args]
    obj = args[3] if len(args) > 3 else "obj"
    out = []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq | ast.Is)):  # fmt: skip
            continue
        for a, b in ((node.left, node.comparators[0]), (node.comparators[0], node.left)):
            if not _is_caller(b):
                continue
            if (
                isinstance(a, ast.Attribute)
                and a.attr in ("id", "pk")
                and isinstance(a.value, ast.Attribute)
            ):
                a = a.value
            if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name) and a.value.id == obj:
                out.append((a.attr, node.lineno))
    return out


def _query_reads(fn: ast.AST) -> list[RouteParam]:
    """``<x>.query_params.get("n"[, default])`` / ``<x>.GET.get(...)`` /
    ``<x>.query_params["n"]`` reads (Int when passed straight to ``int(...)``)."""
    ints: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _name(node.func) == "int" and node.args:
            ints.add(id(node.args[0]))
    out = []
    for node in ast.walk(fn):
        name = default = None
        required = False
        if (
            isinstance(node, ast.Call)
            and _name(node.func) == "get"
            and isinstance(node.func, ast.Attribute)
            and _name(node.func.value) in ("query_params", "GET")
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            name = node.args[0].value
            if len(node.args) > 1:
                default = ast.unparse(node.args[1])
        elif (
            isinstance(node, ast.Subscript)
            and _name(node.value) in ("query_params", "GET")
            and isinstance(node.slice, ast.Constant)
        ):
            name, required = node.slice.value, True
        if isinstance(name, str):
            typ = "Int" if id(node) in ints else "Str"
            out.append(RouteParam(name, typ.lower(), typ, "query", default, required))
    return out


def _framework_query_params(cls: Any) -> list[RouteParam]:
    """A list route's query parameters from its pagination class and filter backends."""
    from rest_framework import filters, pagination

    out: list[RouteParam] = []
    pager = getattr(cls, "pagination_class", None)
    if isinstance(pager, type):
        if issubclass(pager, pagination.PageNumberPagination):
            out.append(
                RouteParam(pager.page_query_param, "int", "Int", "query", required=False, ge=1)
            )
            if pager.page_size_query_param:
                out.append(
                    RouteParam(
                        pager.page_size_query_param, "int", "Int", "query", required=False, ge=1
                    )
                )
        elif issubclass(pager, pagination.LimitOffsetPagination):
            out.append(
                RouteParam(pager.limit_query_param, "int", "Int", "query", required=False, ge=0)
            )
            out.append(
                RouteParam(pager.offset_query_param, "int", "Int", "query", required=False, ge=0)
            )
        elif issubclass(pager, pagination.CursorPagination):
            out.append(RouteParam(pager.cursor_query_param, "str", "Str", "query", required=False))
    for backend in getattr(cls, "filter_backends", ()):
        if isinstance(backend, type) and issubclass(backend, filters.SearchFilter):
            if getattr(cls, "search_fields", None):
                out.append(RouteParam(backend.search_param, "str", "Str", "query", required=False))
        elif isinstance(backend, type) and issubclass(backend, filters.OrderingFilter):
            out.append(RouteParam(backend.ordering_param, "str", "Str", "query", required=False))
        elif getattr(backend, "__name__", "") == "DjangoFilterBackend":
            model = _queryset_model(cls)
            for name in getattr(cls, "filterset_fields", None) or ():
                typ = None
                if model is not None:
                    try:
                        typ = django_orm.field_type(model._meta.get_field(name))
                    except Exception:  # noqa: BLE001
                        typ = None
                out.append(
                    RouteParam(name, (typ or "str").lower(), typ or "Str", "query", required=False)
                )
    return out


def _handler_status(fn: ast.AST) -> int | None:
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Call)
            and _name(node.value.func) == "Response"
        ):
            kw = {k.arg: k.value for k in node.value.keywords if k.arg}
            status = status_of(kw.get("status"))
            if status is not None and status < 400:
                return status
    return None


def _ser_type(f: Any) -> str | None:
    from rest_framework import fields as F
    from rest_framework import relations as R

    if isinstance(f, R.PrimaryKeyRelatedField):
        qs = getattr(f, "queryset", None)
        model = getattr(qs, "model", None)
        return django_orm.field_type(model._meta.pk) if model is not None else "Int"
    if isinstance(f, R.RelatedField):
        return "Str"
    order = (
        (F.EmailField, "Email"), (F.UUIDField, "Uuid"), (F.FileField, None),
        (F.CharField, "Str"), (F.BooleanField, "Bool"), (F.IntegerField, "Int"),
        (F.FloatField, "Float"), (F.DecimalField, "Float"), (F.DateTimeField, "DateTime"),
        (F.DateField, "Date"),
    )  # fmt: skip
    for klass, typ in order:
        if isinstance(f, klass):
            return typ
    return None


def _queryset_model(cls: Any) -> Any:
    qs = getattr(cls, "queryset", None)
    return getattr(qs, "model", None)


def _serializer_model(serializer: Any) -> Any:
    meta = getattr(serializer, "Meta", None)
    return getattr(meta, "model", None)


def _unauthenticated_status(auths: list[Any]) -> int:
    """DRF's status for an unauthenticated caller: 401 when the first authentication
    class answers a ``WWW-Authenticate`` header, else 403."""
    if not auths:
        return 403
    try:
        header = auths[0]().authenticate_header(None)
    except Exception:  # noqa: BLE001 -- a header needing the request: assume one
        return 401
    return 401 if header else 403


def _route_auth(guards: list[Guard], perms: list[Any], auths: list[Any]) -> tuple[str, str]:
    kinds = {g.kind for g in guards}
    names = ", ".join(getattr(p, "__name__", repr(p)) for p in perms) or "none"
    reason = (f"permission_classes: {names}; authentication_classes: "
              f"{', '.join(a.__name__ for a in auths) or 'none'}")  # fmt: skip
    if "custom" in kinds or "role" in kinds:
        return "unclear", reason + " (a permission class the front end cannot read)"
    if "admin" in kinds:
        return "Admin", reason
    if kinds & {"authenticated", "object_owner"}:
        return "Customer", reason
    return "Public", reason


# ------------------------------------------------------------------------- the AuthModel --


def _auth_kind(cls: Any) -> str | None:
    """``token`` / ``jwt`` / ``basic`` / ``session`` for DRF's and simplejwt's classes."""
    from rest_framework import authentication as A

    if not isinstance(cls, type):
        return None
    if issubclass(cls, A.TokenAuthentication):
        return "token"
    if issubclass(cls, A.BasicAuthentication):
        return "basic"
    if issubclass(cls, A.SessionAuthentication):
        return "session"
    if any(k.__name__ == "JWTAuthentication" and k.__module__.startswith("rest_framework_simplejwt")
           for k in cls.__mro__):  # fmt: skip
        return "jwt"
    return None


def _primary_auths(routes: list[Route], views: dict[str, list[Any]]) -> list[Any]:
    from rest_framework.settings import api_settings

    lists = Counter(tuple(views[r.name]) for r in routes if r.guards and r.name in views)
    if lists:
        return list(lists.most_common(1)[0][0])
    return list(api_settings.DEFAULT_AUTHENTICATION_CLASSES)


def auth_model(routes: list[Route], route_auths: dict[str, list[Any]],
               views: dict[str, Any], models: dict[str, DataModel]) -> AuthModel:  # fmt: skip
    """The app's AuthModel (see the module docstring)."""
    from django.conf import settings

    users = django_orm.users_model()
    names = {f.name: f for f in users._meta.concrete_fields}
    auths = _primary_auths(routes, route_auths)
    kinds = [k for k in (_auth_kind(a) for a in auths) if k is not None]
    kind = next((k for k in kinds if k != "session"), kinds[0] if kinds else None)
    status = _unauthenticated_status(auths)
    common: dict[str, Any] = dict(
        users_table=users.__name__,
        email_column=names[users.USERNAME_FIELD].column if users.USERNAME_FIELD in names else None,
        password_column="password" if "password" in names else None,
        active_column="is_active" if "is_active" in names else None,
        admin_column="is_staff"
        if "is_staff" in names
        else ("is_superuser" if "is_superuser" in names else None),
        hasher=HASHER,
        unauthenticated_answer=(status, NOT_AUTHENTICATED),
    )
    if kind in AUTH_ANSWERS:
        bad, unknown, inactive = AUTH_ANSWERS[kind]
        common["invalid_answer"] = (status, bad)
        common["unknown_answer"] = (status, unknown) if unknown else None
        backends = list(getattr(settings, "AUTHENTICATION_BACKENDS", ()))
        refuses_inactive = backends == ["django.contrib.auth.backends.ModelBackend"]
        # ModelBackend never authenticates an inactive user: Basic answers it as bad
        common["inactive_answer"] = (
            status,
            bad if kind == "basic" and refuses_inactive else inactive,
        )
    if kind == "token":
        from rest_framework.authtoken.views import ObtainAuthToken

        keyword = next(
            (getattr(a, "keyword", "Token") for a in auths if _auth_kind(a) == "token"), "Token"
        )
        login = _login_route(routes, views, lambda c: issubclass(c, ObtainAuthToken))
        fields = _login_fields(login, models)
        return AuthModel(scheme=keyword.lower(), minter=TOKEN_MINTER, token_path=("token",),
                         login_encoding="json", **fields, **common)  # fmt: skip
    if kind == "jwt":
        from rest_framework_simplejwt.settings import api_settings as jwt_settings

        header = (jwt_settings.AUTH_HEADER_TYPES or ("Bearer",))[0]
        login = _login_route(routes, views, lambda c: c.__name__.startswith("TokenObtain"))
        fields = _login_fields(login, models)
        return AuthModel(scheme=header.lower(), minter=JWT_MINTER, token_path=("access",),
                         login_encoding="json", **fields, **common)  # fmt: skip
    if kind == "basic":
        return AuthModel(scheme="basic", **common)
    if kind == "session":
        common["invalid_answer"] = common["unauthenticated_answer"]
        return AuthModel(scheme="session", cookie=settings.SESSION_COOKIE_NAME, **common)
    return AuthModel(**common)


def _login_route(routes: list[Route], views: dict[str, Any], test: Any) -> Route | None:
    return next(
        (
            r
            for r in routes
            if r.method == "POST" and isinstance(views.get(r.name), type) and test(views[r.name])
        ),
        None,
    )


def _login_fields(route: Route | None, models: dict[str, DataModel]) -> dict[str, Any]:
    if route is None:
        return {}
    body = next((p.type for p in route.params if p.source == "body"), None)
    fields = [f.name for f in models[body].fields] if body in models else []
    password = next((f for f in fields if "password" in f), "password")
    username = next((f for f in fields if f != password), "username")
    return {"login_path": route.path, "username_field": username, "password_field": password}


def minter(auth: AuthModel | None) -> Any:
    """A function minting a token for a users key without a request (None: the scheme
    has none). DRF ``Token``: a row of ``authtoken_token`` in the database the default
    connection points at (mint after the app is built on the case database); simplejwt:
    a signed access token, no database."""
    if auth is None:
        return None
    if auth.minter == TOKEN_MINTER:

        def mint_token(key: Any) -> str:
            from rest_framework.authtoken.models import Token

            return Token.objects.get_or_create(user_id=key)[0].key

        return mint_token
    if auth.minter == JWT_MINTER:

        def mint_jwt(key: Any) -> str:
            from rest_framework_simplejwt.tokens import AccessToken

            return str(AccessToken.for_user(django_orm.users_model()(pk=key)))

        return mint_jwt
    return None


# ---------------------------------------------------------------------- the front end --


def extract_report(root: Path, package: str) -> tuple[RepoModel, tuple[Excluded, ...]]:
    """The app model and what was excluded from it (Django is booted in this process)."""
    root = Path(root).resolve()
    django_orm.boot(root, package)
    ex = _Extractor(root, package)
    table_models, excluded_tables = django_orm.tables(package)
    from django.apps import apps

    by_table = {m._meta.db_table: m for m in apps.get_models(include_auto_created=True)}
    classes = {by_table[t.table_name]: t.name for t in table_models if t.table_name in by_table}
    views = ex.views()
    routes = ex.routes(views, classes)
    auth = auth_model(routes, ex.route_auths, ex.route_cls, ex.models)
    from rest_framework.settings import api_settings

    handler = api_settings.EXCEPTION_HANDLER
    handlers = ()
    if handler is not None and getattr(handler, "__module__", "") != "rest_framework.views":
        handlers = (f"{handler.__module__}:{handler.__qualname__}",)
    model = RepoModel(
        app_package=package,
        tables=table_models,
        models=tuple(ex.models.values()),
        dependencies=(),
        routes=tuple(routes),
        natives=(),
        database_url=None,
        dialect=django_orm.dialect(),
        exception_handlers=handlers,
        custom_serialization=(),
        framework="django",
        orm="django",
        auth=auth,
    )
    return model, (*excluded_tables, *ex.excluded)


def extract(root: Path, package: str) -> RepoModel:
    return extract_report(root, package)[0]


def _is_view_method(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if any(
        _name(d.func if isinstance(d, ast.Call) else d) in ("action", "api_view")
        for d in fn.decorator_list
    ):
        return True
    return fn.name in ("get", "post", "put", "patch", "delete", *GENERIC_ACTIONS) and bool(
        fn.args.args and fn.args.args[0].arg == "self"
    )


def _is_django_model(node: ast.ClassDef) -> bool:
    return any(_name(b) in ("Model", "AbstractUser", "AbstractBaseUser") for b in node.bases)


def _no_depends(expr: ast.expr) -> bool:
    return False


class DjangoFrontEnd:
    """The Django + DRF front end (``lattice.importer.frontends.FrontEnd``)."""

    name = "django"

    def __init__(self) -> None:
        self._models: dict[tuple[str, str], RepoModel] = {}

    @property
    def graph_hints(self) -> GraphHints:
        from lattice.importer.graph import GraphHints

        return GraphHints(is_route=_is_view_method, is_table=_is_django_model,
                          is_depends=_no_depends, overrides_attr=None, sql_modules=())  # fmt: skip

    def detect(self, root: Path, package: str) -> bool:
        """Some module of the package imports from ``django`` or ``rest_framework``."""
        index = _Index(Path(root), package)
        return any(
            q.split(":", 1)[0].split(".", 1)[0] in ("django", "rest_framework")
            for m in index.modules.values()
            for q in m.imports.values()
        )

    def extract(self, root: Path, package: str) -> RepoModel:
        key = (str(Path(root).resolve()), package)
        if key not in self._models:
            self._models[key] = extract(Path(root), package)
        return self._models[key]

    def excluded(self, root: Path, package: str) -> tuple[Excluded, ...]:
        """What :meth:`extract` leaves out of the model, with reasons."""
        return extract_report(Path(root), package)[1]

    def minter(self, auth: AuthModel | None) -> Any:
        return minter(auth)

    def runtime(self, root: Path, package: str) -> AppRuntime:
        from lattice.importer.transport import AppRuntime, AuthDriver, WsgiTransport

        auth = self.extract(root, package).auth
        session = auth is not None and auth.scheme == "session"
        driver = AuthDriver(
            scheme=auth.scheme if auth is not None else "bearer",
            cookie=auth.cookie if session else None,  # type: ignore[union-attr]
        )
        return AppRuntime(
            framework="django",
            transport=WsgiTransport,
            auth=driver,
            dispose=(django_orm.dispose,),
            seed_hook=django_orm.seed_hook,
            error_site=drf_error_site,
            error_class=is_drf_error_class,
            openapi=None,
        )


FRONT_END = register(DjangoFrontEnd())


def main(argv: list[str]) -> int:
    """``ROOT PACKAGE [--compact]``: the AppModel (plus ``excluded``) as JSON on stdout."""
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2:
        print("usage: python -m lattice.importer.frontends.django_drf ROOT PACKAGE [--compact]")
        return 2
    model, excluded = extract_report(Path(args[0]), args[1])
    out = {**model.to_dict(), "excluded": [{"what": e.what, "name": e.name, "reason": e.reason}
                                           for e in excluded]}  # fmt: skip
    print(json.dumps(out, default=str, indent=None if "--compact" in argv else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
