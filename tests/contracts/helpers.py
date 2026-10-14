"""Helpers for the contracts tests: tiny synthetic provider projects, a workspace and a consumer
(an imports file), all under ``tmp_path``. The provider's ``spec.py`` is real: the export is
computed from it in a subprocess, exactly as for a real project."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from lattice.contracts import check as ck
from lattice.contracts import export as ex
from lattice.workspace import MANIFEST

SPEC_PY = """\
from lattice.profile import ApiProfile, Profile
from lattice.spec import Await, Scenario, Spec, Step

DATA = {data!r}


def build():
    scenarios = []
    for name, steps in DATA:
        built = []
        for s in steps:
            if "service" in s:
                built.append(Await(**s))
            else:
                built.append(Step(**s))
        scenarios.append(Scenario(name, steps=tuple(built)))
    return tuple(scenarios)


# a comment that a test may change without changing any fact
SPEC = Spec(
    Profile(
        schemas={{}},
        api=ApiProfile(
            tables={{}}, table_names={{}}, roles=frozenset({{"Public"}}), error_body={error_body!r}
        ),
    ),
    scenarios=build(),
    users={{"ann": ("ann@x.io", "pw"), "bob": ("bob@x.io", "pw"), "eve": ("eve@x.io", "pw")}},
)
"""


def st(method: str, path: str, user: str | None = None, status: int = 200, **kw: Any) -> dict:
    """One step as the keyword dict ``Step(**d)`` takes (``json``, ``params``, ``expect_body``)."""
    return {"method": method, "path": path, "user": user, "expect_status": status, **kw}


# a small API: a public login, token-gated jobs, a role-gated admin route
BASE: list[tuple[str, list[dict]]] = [
    (
        "login",
        [
            st(
                "POST",
                "/auth/login",
                None,
                200,
                json={"email": "a", "password": "b"},
                expect_body={"access_token": "x", "token_type": "bearer"},
            ),
            st("POST", "/auth/login", None, 401, json={"email": "a", "password": "no"}),
        ],
    ),
    (
        "jobs",
        [
            st("GET", "/jobs", None, 401),
            st(
                "GET",
                "/jobs",
                "ann",
                200,
                params={"limit": 5},
                expect_body=[{"id": 1, "name": "nightly"}],
            ),
            st("GET", "/jobs/7", "ann", 200, expect_body={"id": 7, "name": "nightly"}),
            st("GET", "/jobs/8", "ann", 404),
            st("POST", "/jobs/7/runs", "ann", 201, expect_body={"id": 1, "status": "queued"}),
            st("POST", "/jobs/7/runs", "bob", 403),
            st("GET", "/users/me", "ann", 200, expect_body={"id": 1}),
        ],
    ),
]


def copy_data(data: list[tuple[str, list[dict]]]) -> list[tuple[str, list[dict]]]:
    return json.loads(json.dumps(data))  # plain data: a deep copy


def write_spec(project: Path, data: list[tuple[str, list[dict]]], error_body: str = "fastapi",
               comment: str = "") -> None:  # fmt: skip
    text = SPEC_PY.format(data=[(n, list(s)) for n, s in data], error_body=error_body)
    project.mkdir(parents=True, exist_ok=True)
    (project / "spec.py").write_text(text + comment)


def git(project: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t",
                        *args], capture_output=True, text=True, check=True)  # fmt: skip
    return r.stdout


def git_init(project: Path) -> None:
    git(project, "init", "-q")
    git(project, "add", "-A")
    git(project, "commit", "-qm", "init")


def imports_text(*imports: dict[str, Any]) -> str:
    """A ``lattice.imports.toml`` from dicts (provider defaults to ``prov``)."""
    out = []
    for imp in imports:
        imp = {"provider": "prov", **imp}
        lines = ["[[import]]"]
        for k, v in imp.items():
            lines.append(f"{k} = {json.dumps(v)}")
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


# what the tests' consumer relies on: every claim is held by BASE
IMPORTS = [
    {"endpoint": "POST /auth/login", "status": [200], "auth": "public",
     "request_fields": ["email", "password"], "response_fields": ["access_token"]},
    {"endpoint": "GET /jobs", "status": [200], "auth": "token", "as_user": "ann",
     "response_fields": ["id", "name"]},
    {"endpoint": "GET /jobs/{id}", "status": [200, 404], "auth": "token",
     "response_fields": ["name"]},
    {"endpoint": "POST /jobs/7/runs", "status": [201], "auth": "token", "as_user": "ann",
     "response_fields": ["id", "status"]},
]  # fmt: skip


class World:
    """A workspace ``w`` with a provider ``prov`` (spec ``spec:SPEC``) and a consumer ``cons``
    that links to it with ``contract = "static"``."""

    def __init__(self, root: Path, data: list[tuple[str, list[dict]]] | None = None,
                 imports: list[dict] | None = None, contract: str | None = "static",
                 repo: bool = False) -> None:  # fmt: skip
        self.root = root
        self.provider = root / "prov"
        self.consumer = root / "cons"
        write_spec(self.provider, data if data is not None else BASE)
        if repo:
            git_init(self.provider)
        self.consumer.mkdir()
        (self.consumer / ck.IMPORTS_FILE).write_text(
            imports_text(*(IMPORTS if imports is None else imports))
        )
        self.contract = contract
        self.write_manifest()

    def write_manifest(self, extra_link: str = "", extra_project: str = "") -> None:
        """The manifest; `extra_project` is raw TOML lines for the provider (``exclude = ...``)."""
        link = f'contract = "{self.contract}"\n' if self.contract else ""
        (self.root / MANIFEST).write_text(
            '[workspace]\nname = "w"\n\n'
            '[[project]]\nname = "prov"\npath = "prov"\nrole = "provider"\nspec = "spec:SPEC"\n'
            f"{extra_project}\n"
            '[[project]]\nname = "cons"\npath = "cons"\nrole = "consumer"\n\n'
            f'[[link]]\nfrom = "cons"\nto = "prov"\nkind = "uses"\n{link}{extra_link}'
        )

    @property
    def lock(self) -> Path:
        return self.consumer / ck.LOCK_FILE

    def edit(self, data: list[tuple[str, list[dict]]], **kw: Any) -> None:
        write_spec(self.provider, data, **kw)

    def run(self, *argv: str) -> tuple[int, dict]:
        """``lattice contracts ARGV --json`` in-process: ``(exit code, parsed JSON)``."""
        import contextlib
        import io

        from lattice.contracts.__main__ import main

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main([*argv, "--workspace", str(self.root), "--json"])
        return rc, json.loads(buf.getvalue())

    def pin(self, *flags: str) -> tuple[int, dict]:
        return self.run("pin", "cons", *flags)

    def check(self, *flags: str) -> tuple[int, dict]:
        return self.run("check", "cons", *flags)


def kinds(link: dict, cls: str | None = None) -> list[tuple[str, str, str]]:
    """``(kind, endpoint, item)`` of a link's findings (of one class if given)."""
    return [(f["kind"], f["endpoint"], f["item"]) for f in link["findings"]
            if cls is None or f["class"] == cls]  # fmt: skip


def export_of(project: Path, ref: str = "spec:SPEC") -> dict:
    return ex.compute_export(project, ref)


def make_spec(data: list[tuple[str, list[dict]]], error_body: str = "fastapi") -> Any:
    """The ``Spec`` of `data` built in this process (for the pure export functions)."""
    from lattice.profile import ApiProfile, Profile
    from lattice.spec import Await, Scenario, Spec, Step

    scenarios = []
    for name, steps in data:
        built = [Await(**s) if "service" in s else Step(**s) for s in steps]
        scenarios.append(Scenario(name, steps=tuple(built)))
    api = ApiProfile(tables={}, table_names={}, roles=frozenset({"Public"}), error_body=error_body)
    return Spec(Profile(schemas={}, api=api), scenarios=tuple(scenarios))
