"""The .NET (ASP.NET Core + EF Core) importer front end
(``lattice.importer.frontends.dotnet_aspnet``): F-DETECT, PB-LAZY, the dnextract/1 document mapped
to the app model, the runtime and the login fields. The extractor itself is tested in
``tests/dncheck``. The rows that run ``dncheck`` skip without the .NET SDK."""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lattice.importer import frontends

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "dncheck" / "fixtures"
HAS_DOTNET = shutil.which("dotnet") is not None
WEB = '<Project Sdk="Microsoft.NET.Sdk.Web"><ItemGroup>{items}</ItemGroup></Project>'
EF = '<PackageReference Include="Microsoft.EntityFrameworkCore.Sqlite" Version="10.0.13" />'


def project(path: Path, name: str, body: str = "", sdk: str = "Microsoft.NET.Sdk.Web") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    file = path / f"{name}.csproj"
    file.write_text(f'<Project Sdk="{sdk}"><ItemGroup>{body}</ItemGroup></Project>')
    return file


def dotnet():
    frontends._load_dotnet()
    return frontends.select("aspnetcore")


def test_detect_web_project_with_ef_core(tmp_path):
    project(tmp_path, "App", EF)
    assert dotnet().detect(tmp_path, ".")


@pytest.mark.parametrize(
    "package",
    [
        '<PackageReference Include="Pomelo.EntityFrameworkCore.MySql" Version="9.0.0" />',
        '<PackageReference Version="9.0.0" Include="MySql.EntityFrameworkCore" />',
    ],
)
def test_detect_either_mysql_provider(tmp_path, package):
    project(tmp_path, "App", package)
    assert dotnet().detect(tmp_path, ".")


def test_detect_web_by_framework_reference_or_aspnet_package(tmp_path):
    project(
        tmp_path / "a",
        "A",
        '<FrameworkReference Include="Microsoft.AspNetCore.App" />' + EF,
        "Microsoft.NET.Sdk",
    )
    project(
        tmp_path / "b",
        "B",
        '<PackageReference Include="Microsoft.AspNetCore.OpenApi" />' + EF,
        "Microsoft.NET.Sdk",
    )
    assert dotnet().detect(tmp_path, "a") and dotnet().detect(tmp_path, "b")


def test_detect_ef_core_through_project_reference_or_central_packages(tmp_path):
    lib = project(tmp_path / "lib", "Data", EF, "Microsoft.NET.Sdk")
    project(tmp_path / "web", "Web", '<ProjectReference Include="..\\lib\\Data.csproj" />')
    assert lib.is_file() and dotnet().detect(tmp_path, "web")
    project(tmp_path / "cpm" / "src", "Cpm")
    assert not dotnet().detect(tmp_path / "cpm", "src")
    (tmp_path / "cpm" / "Directory.Packages.props").write_text(
        "<Project><ItemGroup>"
        + EF.replace("PackageReference", "PackageVersion")
        + "</ItemGroup></Project>"
    )
    assert dotnet().detect(tmp_path / "cpm", "src")


def test_detect_declines_with_a_note_for_a_web_project_without_ef_core(tmp_path):
    project(tmp_path, "App", '<PackageReference Include="Dapper" Version="2.1.35" />')
    assert not dotnet().detect(tmp_path, ".")
    with pytest.raises(LookupError, match=r"without EF Core"):
        frontends.select(None, tmp_path, ".")


def test_detect_declines_with_a_note_naming_two_projects(tmp_path):
    project(tmp_path, "A", EF)
    project(tmp_path, "B", EF)
    with pytest.raises(LookupError, match=r"A\.csproj, B\.csproj"):
        frontends.select(None, tmp_path, ".")


def test_detect_is_silent_for_a_library_and_for_an_empty_directory(tmp_path):
    project(tmp_path / "lib", "Lib", EF, "Microsoft.NET.Sdk")
    (tmp_path / "empty").mkdir()
    for package in ("lib", "empty", "absent"):
        assert not dotnet().detect(tmp_path, package)
    with pytest.raises(LookupError) as err:
        frontends.select(None, tmp_path, "empty")
    assert str(err.value) == f"no front end recognises 'empty' under {tmp_path}"


def test_notes_do_not_leak_between_selections(tmp_path):
    project(tmp_path / "a", "A", '<PackageReference Include="Dapper" />')
    (tmp_path / "b").mkdir()
    with pytest.raises(LookupError, match="without EF Core"):
        frontends.select(None, tmp_path, "a")
    with pytest.raises(LookupError) as err:
        frontends.select(None, tmp_path, "b")
    assert "EF Core" not in str(err.value)


@pytest.mark.skipif(not HAS_DOTNET, reason="needs the .NET SDK")
def test_select_detects_the_parity_fixture():
    assert frontends.select(None, FIXTURE, "dnparity1").name == "aspnetcore"


def test_go_and_python_answers_are_unchanged():
    assert frontends.select(None, REPO / "tests" / "spec", "gotickets").name == "gin"
    assert frontends.select(None, REPO, "bench.import_repos.snippets").name == "django"
    assert frontends.select(None, REPO, "bench.import_repos.microblog").name == "flask"


@pytest.mark.parametrize("imports", ["select('fastapi')", "select('django')", "select('gin')"])
def test_a_python_or_go_import_never_loads_the_dotnet_front_end(imports):
    code = textwrap.dedent(f"""
        import sys
        sys.dont_write_bytecode = True
        from lattice.importer.frontends import select
        import lattice.importer.extract
        {imports}
        assert "lattice.importer.frontends.dotnet_aspnet" not in sys.modules
        select("aspnetcore")
        assert "lattice.importer.frontends.dotnet_aspnet" in sys.modules
    """)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={"PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr


# ---- the mapping, on the parity fixture's extract (needs the SDK) ----------------------------

needs_dotnet = pytest.mark.skipif(not HAS_DOTNET, reason="needs the .NET SDK")


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    import os

    from lattice import dncheck

    os.environ.setdefault("LATTICE_DNCHECK_CACHE", str(tmp_path_factory.mktemp("dncheck-cache")))
    return dncheck.extract_json(FIXTURE / "dnparity1", workflow=True, policy=True)


@needs_dotnet
def test_model_from_extract_counts_and_vocabulary(doc):
    from lattice.importer.frontends.dotnet_aspnet import model_from_extract

    model = model_from_extract(doc, "dnparity1")
    assert (model.framework, model.orm) == ("aspnetcore", "efcore")
    assert len(model.routes) == len(doc["routes"]) == 39
    assert len(model.tables) == len(doc["tables"]) and len(model.models) == len(doc["models"])
    assert {r.auth for r in model.routes} <= {"Public", "Customer", "Admin"}
    assert "Customer" in {r.auth for r in model.routes}
    entities = {t.name for t in model.tables}
    assert all(set(r.tables) <= entities for r in model.routes)
    assert model.dialect == "mysql"


@needs_dotnet
def test_enum_choices_reach_the_columns(doc):
    from lattice.importer.frontends.dotnet_aspnet import model_from_extract

    note = next(t for t in model_from_extract(doc).tables if t.name == "Note")
    assert next(c for c in note.columns if c.name == "Status").choices == (
        "Draft", "Published", "Archived",
    )  # fmt: skip


@needs_dotnet
def test_dialect_comes_from_the_providers_or_the_studio_database(doc):
    from lattice.importer.frontends.dotnet_aspnet import model_from_extract

    both = {**doc, "dialect": "", "dotnet": {**doc["dotnet"], "providers": ["mysql", "sqlite"]}}
    assert model_from_extract(both).dialect == "sqlite"
    assert model_from_extract(both, database="mysql").dialect == "mysql"
    only = {**doc, "dialect": "", "dotnet": {**doc["dotnet"], "providers": ["mysql"]}}
    assert model_from_extract(only).dialect is None


def test_a_foreign_schema_is_refused():
    from lattice.importer.frontends.dotnet_aspnet import DotnetFrontEndError, model_from_extract

    with pytest.raises(DotnetFrontEndError, match="unexpected extractor schema"):
        model_from_extract({"schema": "lattice.goextract/1"})


@needs_dotnet
def test_extract_keeps_the_json_and_the_sidecar(tmp_path):
    fe = dotnet()
    with pytest.raises(Exception, match="nothing has been extracted"):
        type(fe)().sidecar()
    model = fe.extract(FIXTURE, "dnparity1")
    assert len(model.routes) == 39 and fe.last_extract["schema"] == "lattice.dnextract/1"
    assert fe.sidecar().endswith("}\n")


def test_login_fields_are_the_required_body_fields_beyond_the_credentials():
    from lattice.importer.frontends.dotnet_aspnet import login_fields

    def p(name, type_="Str", required=True, source="body"):
        return {"name": name, "type": type_, "required": required, "source": source}

    data = {
        "auth": {"login_path": "/login", "username_field": "email", "password_field": "password"},
        "routes": [
            {"method": "POST", "path_template": "/login", "params": [
                p("email"), p("password"), p("installation_id", "Uuid"), p("label", required=False),
                p("note"), p("q", source="query")]},
            {"method": "POST", "path_template": "/other", "params": [p("zzz")]},
        ],
    }  # fmt: skip
    assert login_fields(data) == {
        "installation_id": "00000000-0000-4000-8000-000000000001",
        "note": "lattice-import",
    }
    assert login_fields({"routes": []}) == {}


def test_the_login_client_adds_the_fields_to_the_login_post_only():
    from lattice.importer.frontends.dotnet_aspnet import _LoginClient

    class Inner:
        def __init__(self):
            self.sent = []

        def post(self, url, **kw):
            self.sent.append((url, kw))

        def get(self, url):
            return url

    inner = Inner()
    client = _LoginClient(inner, "/login", {"installation_id": "x"})
    client.post("/login", json={"email": "a"})
    client.post("/other", json={"email": "a"})
    assert inner.sent == [
        ("/login", {"json": {"installation_id": "x", "email": "a"}}),
        ("/other", {"json": {"email": "a"}}),
    ]
    assert client.get("/h") == "/h"


# ---- runtime and reconcile ---------------------------------------------------------------------


def test_runtime_without_a_harness_is_the_dotnet_process_runtime(tmp_path):
    rt = dotnet().runtime(tmp_path, ".")
    assert rt.framework == "dotnet" and rt.one_server


def test_runtime_with_a_harness_is_the_harness_runtime(tmp_path):
    (tmp_path / "dn_rt_harness.py").write_text(
        "from lattice.spec.process import process_runtime\n"
        "RUNTIME = process_runtime(framework='dotnet-harness')\n"
    )
    (tmp_path / "studio.toml").write_text('[app]\nruntime = "dn_rt_harness:RUNTIME"\n')
    assert dotnet().runtime(tmp_path, ".").framework == "dotnet-harness"


def test_runtime_wraps_the_transports_when_the_login_takes_more_fields(tmp_path, monkeypatch):
    from lattice.importer.frontends.dotnet_aspnet import AspNetFrontEnd, _LoginTransport

    fe = AspNetFrontEnd()
    fe.last_extract = {
        "auth": {"login_path": "/login", "username_field": "u", "password_field": "p"},
        "routes": [{"method": "POST", "path_template": "/login", "params": [
            {"name": "u", "type": "Str", "required": True, "source": "body"},
            {"name": "device", "type": "Uuid", "required": True, "source": "body"}]}],
    }  # fmt: skip

    class Stub:
        client = object()

    from lattice.importer.transport import AppRuntime

    monkeypatch.setattr(
        "lattice.spec.process.process_runtime",
        lambda **kw: AppRuntime(framework="dotnet", transport=lambda app, **kw: Stub()),
    )
    transport = fe.runtime(tmp_path, ".").transport(None)
    assert isinstance(transport, _LoginTransport) and transport.client._fields == {
        "device": "00000000-0000-4000-8000-000000000001"
    }
    fe.last_extract = None
    assert not isinstance(fe.runtime(tmp_path, ".").transport(None), _LoginTransport)


def test_reconcile_needs_the_harness(tmp_path):
    from lattice.importer.frontends.dotnet_aspnet import DotnetFrontEndError

    with pytest.raises(DotnetFrontEndError, match="names no"):
        dotnet().reconcile(tmp_path, None)


# ---- the users table's enum role column (node F1) ---------------------------------------------


def _with_role_column(doc, storage="string", values=(0, 1)):
    import copy

    out = copy.deepcopy(doc)
    out["dotnet"]["auth"]["role_column"] = {
        "column": "role",
        "members": [{"name": "Member", "value": values[0]}, {"name": "Admin", "value": values[1]}],
        "storage": storage,
    }
    return out


@needs_dotnet
@pytest.mark.parametrize(
    ("storage", "spelled"),
    [("string", ("Member", "Admin")), (None, ("Member", "Admin")), ("int", ("0", "1"))],
)
def test_a_role_column_becomes_the_role_scheme(doc, storage, spelled):
    from lattice.importer.appmodel import RoleScheme
    from lattice.importer.frontends.dotnet_aspnet import model_from_extract

    model = model_from_extract(_with_role_column(doc, storage), "dnparity1")
    assert model.role_scheme == RoleScheme(
        "role", ((spelled[0], spelled[0], "Member"), (spelled[1], spelled[1], "Admin")),
        "Admin", None, None,
    )  # fmt: skip
    assert model.auth.role_column == "role"
    # any signed-in caller is `Authenticated` once the roles are the scheme's, not `Customer`
    assert {r.auth for r in model.routes} <= {"Public", "Authenticated", "Admin"}
    assert "Authenticated" in {r.auth for r in model.routes}


@needs_dotnet
def test_a_regular_expression_is_the_regex_format_of_the_param_and_the_model_field(doc):
    import copy

    from lattice.importer.frontends.dotnet_aspnet import model_from_extract

    out = copy.deepcopy(doc)
    route = next(r for r in out["routes"] if r["params"])
    route["params"][0]["pattern"] = "^[a-z]+$"
    field = out["models"][0]["fields"][0]
    field["pattern"] = "^[0-9]+$"
    model = model_from_extract(out, "dnparity1")
    got = next(r for r in model.routes if (r.method, r.path) == (route["method"], route["path_template"]))
    assert got.params[0].format == "regex:^[a-z]+$"
    assert model.models[0].fields[0].format == "regex:^[0-9]+$"
    plain = model_from_extract(doc, "dnparity1")
    assert all(p.format is None for r in plain.routes for p in r.params)
    assert all(f.format is None for m in plain.models for f in m.fields)


@needs_dotnet
def test_without_a_role_column_the_model_is_unchanged(doc):
    from lattice.importer.frontends.dotnet_aspnet import model_from_extract

    model = model_from_extract(doc, "dnparity1")
    assert model.role_scheme is None and model.auth.role_column is None
    assert "Customer" in {r.auth for r in model.routes}
