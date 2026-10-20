"""Small synthetic projects for the idioms the Arm 0 fixture does not carry (docs/results/2026-10-18-dotnet-prereg.md
N1-N11): each is written to a temporary directory, only read by the helper, never restored or built.
"""

# ruff: noqa: E501
from __future__ import annotations

import os
import shutil
import textwrap
from pathlib import Path
from typing import Any

import pytest

from lattice import dncheck

pytestmark = pytest.mark.skipif(shutil.which("dotnet") is None, reason="needs the .NET SDK")

WEB = """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup><TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable><ImplicitUsings>enable</ImplicitUsings></PropertyGroup>
  <ItemGroup><PackageReference Include="Microsoft.EntityFrameworkCore.Sqlite" Version="9.0.21" /></ItemGroup>
</Project>
"""


@pytest.fixture(scope="module", autouse=True)
def helper(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cache = tmp_path_factory.mktemp("dncheck-cache")
    old = os.environ.get("LATTICE_DNCHECK_CACHE")
    os.environ["LATTICE_DNCHECK_CACHE"] = str(cache)
    try:
        yield dncheck.build_dncheck()
    finally:
        if old is None:
            os.environ.pop("LATTICE_DNCHECK_CACHE", None)
        else:
            os.environ["LATTICE_DNCHECK_CACHE"] = old


def project(tmp: Path, files: dict[str, str], csproj: str = WEB, name: str = "app") -> Path:
    root = tmp / name
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.csproj").write_text(csproj)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))
    return root


def routes(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(r["method"], r["path"]): r for r in doc["routes"]}


DB = """
using Microsoft.EntityFrameworkCore;
public class Db : DbContext { public Db(DbContextOptions<Db> o) : base(o) {} public DbSet<Item> Items { get; set; } = null!; }
public class Item { public int Id { get; set; } public int OwnerId { get; set; } public string Name { get; set; } = ""; }
"""


def test_registration_idioms(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "Program.cs": """
            using Microsoft.AspNetCore.Authorization;
            var builder = WebApplication.CreateBuilder(args);
            builder.Services.AddAuthorization(o =>
            {
                o.AddPolicy("AdminOnly", p => p.RequireRole("Admin"));
                o.AddPolicy("Custom", p => p.AddRequirements(new MyReq()));
                o.FallbackPolicy = new AuthorizationPolicyBuilder().RequireAuthenticatedUser().Build();
            });
            var app = builder.Build();
            app.UsePathBase("/base");
            var items = app.MapGroup("/items");
            items.MapGet("/", () => Results.Ok()).AllowAnonymous();
            items.MapGet("/admin", () => Results.Ok()).RequireAuthorization("AdminOnly");
            items.MapGet("/custom", () => Results.Ok()).RequireAuthorization("Custom");
            items.MapGet("/missing", () => Results.Ok()).RequireAuthorization("Nope");
            items.MapPost("/", (ItemIn input) =>
            {
                if (input.Qty < 1 || input.Qty > 9) return Results.BadRequest();
                if (string.IsNullOrEmpty(input.Name) || input.Name.Length > 50) return Results.BadRequest();
                return Results.Ok();
            });
            Shared.Map(app.MapGroup("/a"));
            Shared.Map(app.MapGroup("/b"));
            for (int i = 0; i < 2; i++) app.MapGet("/loop" + i, () => "x");
            if (builder.Environment.IsDevelopment()) app.MapGet("/dev", () => "d"); else app.MapGet("/prod", () => "p");
            app.Run();
            public record ItemIn(string Name, int Qty);
            public class MyReq : IAuthorizationRequirement {}
            """,
            "Shared.cs": """
            public static class Shared
            {
                public static void Map(RouteGroupBuilder g) { g.MapGet("/ping", () => "pong"); g.MapGet("/{x:int:min(5)}", (int x) => x); }
            }
            """,
        },
    )
    doc = dncheck.extract_json(root)
    r = routes(doc)
    assert (
        r[("GET", "/base/items")]["auth"] == "Public"
    )  # .AllowAnonymous() wins over the fallback policy
    assert (
        r[("GET", "/base/items/admin")]["auth"] == "Admin"
    )  # a named policy requiring the Admin role
    assert (
        r[("GET", "/base/items/custom")]["auth"] == "Member"
    )  # a custom requirement: never guessed Admin
    assert r[("GET", "/base/items/missing")]["auth"] == "Member"
    whys = {(u["route"], u["category"]) for u in doc["unread"] if u.get("route")}
    assert ("GET /base/items/custom", "auth") in whys and (
        "GET /base/items/missing",
        "auth",
    ) in whys
    assert (
        r[("GET", "/base/a/ping")]["auth"] == r[("GET", "/base/b/ping")]["auth"] == "Member"
    )  # the fallback policy
    assert (
        "GET",
        "/base/b/{x:int:min(5)}",
    ) in r  # one method called from two places: both prefixes (LM1)
    x = r[("GET", "/base/a/{x:int:min(5)}")]["params"][0]
    assert (x["type"], x["ge"], x["parse"]) == ("Int", 5, "Int")
    assert not any(
        k[1].startswith("/base/loop") for k in r
    )  # a registration inside a loop is unread
    assert any(u["what"] == "MapGet inside a loop" for u in doc["unread"])
    assert r[("GET", "/base/dev")]["condition"] == "builder.Environment.IsDevelopment()"
    assert r[("GET", "/base/prod")]["condition"] == "!(builder.Environment.IsDevelopment())"
    body = {p["name"]: p for p in r[("POST", "/base/items")]["params"]}
    assert (body["qty"]["ge"], body["qty"]["le"], body["qty"]["enforced_by"]) == (
        1,
        9,
        "handler-guard",
    )
    assert (body["name"]["required"], body["name"]["max_length"]) == (True, 50)
    assert any("UsePathBase" in n for n in doc["notes"])


def test_identity_api_is_read_from_its_table(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "Program.cs": 'var app = WebApplication.CreateBuilder(args).Build();\napp.MapGroup("/id").MapIdentityApi<IdentityUser>();\napp.Run();\npublic class IdentityUser {}\n'
        },
    )
    doc = dncheck.extract_json(root)
    r = routes(doc)
    assert (
        r[("POST", "/id/login")]["auth"] == "Public"
        and r[("GET", "/id/manage/info")]["auth"] == "Member"
    )
    assert doc["auth"]["login_path"] == "/id/login" and doc["auth"]["token_field"] == "accessToken"
    assert doc["dotnet"]["auth"]["users_table_sql"] == "AspNetUsers"
    assert any(e["what"] == "ASP.NET Identity tables" for e in doc["excluded"])


def test_unmapped_controllers_are_excluded(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "Program.cs": 'var app = WebApplication.CreateBuilder(args).Build();\napp.MapGet("/x", () => 1);\napp.Run();\n',
            "C.cs": 'using Microsoft.AspNetCore.Mvc;\n[ApiController, Route("c")]\npublic class CController : ControllerBase { [HttpGet("{id}")] public IActionResult A(int id) => Ok(); }\n',
        },
    )
    doc = dncheck.extract_json(root)
    assert list(routes(doc)) == [("GET", "/x")]
    assert [(e["kind"], e["method"], e["path"]) for e in doc["excluded"]] == [
        ("controllers", "GET", "/c/{id}")
    ]


def test_filters_and_hand_rolled_auth(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "Program.cs": """
            var app = WebApplication.CreateBuilder(args).Build();
            app.MapGet("/f", () => "x").AddEndpointFilter<Gate>();
            app.MapGet("/h", (HttpRequest r) => r.Headers.Authorization.ToString() == "" ? Results.Unauthorized() : Results.Ok());
            app.MapGet("/open", () => "x");
            app.Run();
            public class Gate : IEndpointFilter { public ValueTask<object?> InvokeAsync(EndpointFilterInvocationContext c, EndpointFilterDelegate next) => ValueTask.FromResult<object?>(Results.StatusCode(418)); }
            """,
        },
    )
    r = routes(dncheck.extract_json(root))
    assert [(x["status"], x["kind"]) for x in r[("GET", "/f")]["raises"]] == [(418, "middleware")]
    assert [m["func"] for m in r[("GET", "/f")]["middleware"]] == ["Gate"] and not r[
        ("GET", "/open")
    ]["middleware"]
    assert r[("GET", "/h")]["auth"] == "Member" and r[("GET", "/open")]["auth"] == "Public"


def test_ef_names_conventions_and_the_snapshot_wins(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "Program.cs": "var app = WebApplication.CreateBuilder(args).Build();\napp.Run();\n",
            "Db.cs": """
            using System.ComponentModel.DataAnnotations;
            using System.ComponentModel.DataAnnotations.Schema;
            using Microsoft.EntityFrameworkCore;
            public class Db : DbContext
            {
                public Db(DbContextOptions<Db> o) : base(o) {}
                public DbSet<Widget> Widgets { get; set; } = null!;
                public DbSet<Gadget> Gadgets { get; set; } = null!;
                protected override void OnModelCreating(ModelBuilder b) { b.Entity<Widget>().HasIndex(w => w.Code).IsUnique(); }
            }
            public class Widget { [Key] public int WidgetNo { get; set; } [Column("code_x")] [MaxLength(8)] public string Code { get; set; } = ""; public string? Note { get; set; } }
            [Table("gadget_tbl")] public class Gadget { public int Id { get; set; } public int WidgetNo { get; set; } public Widget? Widget { get; set; } }
            """,
            "Migrations/DbModelSnapshot.cs": """
            namespace M {
            [DbContext(typeof(Db))]
            partial class DbModelSnapshot : ModelSnapshot
            {
                protected override void BuildModel(ModelBuilder modelBuilder)
                {
                    modelBuilder.Entity("Widget", b =>
                    {
                        b.Property<int>("WidgetNo").HasColumnName("widget_no");
                        b.Property<string>("Code").IsRequired().HasMaxLength(12).HasColumnName("code");
                        b.HasKey("WidgetNo");
                        b.ToTable("widgets_snap", (string)null);
                    });
                }
            } }
            """,
        },
    )
    doc = dncheck.extract_json(root)
    t = {x["struct"]: x for x in doc["tables"]}
    g = {c["field"]: c for c in t["Gadget"]["columns"]}
    assert t["Gadget"]["table"] == "gadget_tbl"
    assert g["WidgetNo"]["foreign_key"] == "widgets_snap.widget_no"
    w = {c["field"]: c for c in t["Widget"]["columns"]}
    assert t["Widget"]["table"] == "widgets_snap" and t["Widget"]["source"] == "efcore"
    assert (w["Code"]["name"], w["Code"]["max_length"], w["Code"]["unique"]) == (
        "code",
        12,
        True,
    )  # the snapshot says so
    assert w["WidgetNo"]["primary_key"] and w["Note"]["nullable"] and not w["Code"]["nullable"]
    assert any(
        "widgets_snap" in n and "Widgets" in n for n in doc["notes"]
    )  # the code and the snapshot disagree
    assert doc["dotnet"]["snapshot"] == "Migrations/DbModelSnapshot.cs"


def test_calls_that_fail_overload_resolution_bind_lexically(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "Program.cs": """
            using Microsoft.EntityFrameworkCore;
            var app = WebApplication.CreateBuilder(args).Build();
            app.MapGet("/one", async (Db db, Svc s) => { var it = await db.Items.FirstAsync(); return s.Only(it); });
            app.MapGet("/two", async (Db db, Svc s) => { var it = await db.Items.FirstAsync(); return s.Both(it); });
            app.Run();
            public class Svc { public int Only(Item i) => 1; public int Both(Item i) => 1; public int Both(Item i, int j = 0) => 2; }
            """,
            "Db.cs": DB,
        },
    )
    doc = dncheck.extract_json(root)
    r = routes(doc)
    assert r[("GET", "/one")]["calls"] == ["Svc.Only"]  # N-bind: one candidate: bound
    assert r[("GET", "/two")]["calls"] == []
    assert any(
        u["category"] == "call" and "Svc.Both" in u["reason"] for u in r[("GET", "/two")]["unread"]
    )  # N-bind2: several: unread


def test_project_references_stay_inside_the_workspace(tmp_path: Path) -> None:
    lib = tmp_path / "repo" / "Lib"
    lib.mkdir(parents=True)
    (lib / "Lib.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>'
    )
    (lib / "Shared.cs").write_text(
        'public static class Shared { public const string Tpl = "/lib"; }\n'
    )
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "Out.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>'
    )
    api = project(
        tmp_path / "repo",
        {
            "Program.cs": "var app = WebApplication.CreateBuilder(args).Build();\napp.MapGet(Shared.Tpl, () => 1);\napp.Run();\n"
        },
        csproj=WEB.replace(
            "</Project>",
            '<ItemGroup><ProjectReference Include="../Lib/Lib.csproj" /><ProjectReference Include="../../elsewhere/Out.csproj" /></ItemGroup></Project>',
        ),
        name="Api",
    )
    doc = dncheck.extract_json(api, workspace=tmp_path / "repo")
    assert ("GET", "/lib") in routes(
        doc
    )  # the constant lives in a referenced project (its source is compiled)
    assert doc["dotnet"]["project_references"] == ["../Lib/Lib.csproj"]
    assert any(
        "Out.csproj" in u["what"] and u["reason"] == "referenced project outside the import root"
        for u in doc["unread"]
    )
    # without a workspace the project directory is the import root: both references are outside
    plain = dncheck.extract_json(api)
    assert ("GET", "/lib") not in routes(plain) and sum(
        u["category"] == "project" for u in plain["unread"]
    ) == 2


def test_syntax_errors_and_bad_roots(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "Program.cs": "var app = WebApplication.CreateBuilder(args).Build();\napp.Run();\n",
            "Bad.cs": "class X { void M( { }\n",
        },
    )
    doc = dncheck.extract_json(root)
    assert any(
        u["file"] == "Bad.cs" and u["what"] == "file" and u["reason"].startswith("syntax error CS")
        for u in doc["unread"]
    )
    with pytest.raises(dncheck.DnCheckError):
        dncheck.extract_json(tmp_path / "missing")
    two = project(tmp_path, {}, name="two")
    (two / "other.csproj").write_text(WEB)
    with pytest.raises(dncheck.DnCheckError, match="more than one"):
        dncheck.extract_json(two)


def test_a_missing_sdk_and_a_failed_build_are_errors_never_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "src"
    shutil.copytree(dncheck.SRC, broken, ignore=shutil.ignore_patterns("bin", "obj"))
    (broken / "Json.cs").write_text("this is not C#")
    monkeypatch.setattr(dncheck, "SRC", broken)
    monkeypatch.setenv("LATTICE_DNCHECK_CACHE", str(tmp_path / "cache"))
    with pytest.raises(dncheck.DnCheckError, match="dotnet build of dncheck failed"):
        dncheck.build_dncheck()
    assert not (tmp_path / "cache").exists() or not any((tmp_path / "cache").glob("*/dncheck.dll"))
    dncheck.sdk_version.cache_clear()
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    with pytest.raises(dncheck.DotnetToolchainMissing):
        dncheck.build_dncheck()
    dncheck.sdk_version.cache_clear()


REQUIRED_APP = """
using System.Text.Json.Nodes;
public sealed class ApiException(int status, string msg) : Exception(msg) { public int Status { get; } = status; }
public sealed class JsonBody
{
    private readonly JsonObject _o;
    private JsonBody(JsonObject o) => _o = o;
    public static JsonBody Of(JsonObject o) => new(o);
    public static async Task<JsonBody> ReadAsync(HttpRequest r)
    {
        using var rd = new StreamReader(r.Body);
        return new JsonBody(JsonNode.Parse(await rd.ReadToEndAsync()) as JsonObject ?? new JsonObject());
    }
    public bool Has(string k) => _o.ContainsKey(k);
    public string? OptionalString(string k) => _o.TryGetPropertyValue(k, out var n) && n is not null ? n.GetValue<string>() : null;
    public string RequiredString(string k) => OptionalString(k) ?? throw new ApiException(422, k + " is required");
    public JsonArray RequiredArray(string k) => _o.TryGetPropertyValue(k, out var n) && n is JsonArray a ? a : throw new ApiException(422, k);
}
public static class Svc
{
    static string ValidName(string? n) => n is null || n.Trim().Length == 0 ? throw new ApiException(422, "name") : n;
    static string? ValidNote(string? n) => n is not null && n.Length > 9 ? throw new ApiException(422, "note") : n;
    public static void Create(JsonBody body)
    {
        if (!body.Has("version")) throw new ApiException(422, "version is required");
        var name = ValidName(body.OptionalString("name"));
        var note = ValidNote(body.OptionalString("note"));
        var tag = body.OptionalString("tag");
        var token = body.OptionalString("token");
        if (string.IsNullOrEmpty(token)) throw new ApiException(422, "token");
        var city = body.OptionalString("city") ?? "";
        if (city.Length == 0) throw new ApiException(422, "city");
        var lang = body.OptionalString("lang") ?? "en";
        if (lang.Length == 0) throw new ApiException(422, "lang");
        if (body.Has("nick"))
        {
            var nick = body.OptionalString("nick");
            if (nick is null) throw new ApiException(422, "nick");
        }
        foreach (var item in body.RequiredArray("items"))
        {
            var o = JsonBody.Of((JsonObject)item!);
            o.RequiredString("sku");
        }
    }
}
"""


def test_a_body_accessor_is_required_when_the_handler_refuses_its_missing_value(
    tmp_path: Path,
) -> None:
    root = project(
        tmp_path,
        {
            "Program.cs": """
            var builder = WebApplication.CreateBuilder(args);
            var app = builder.Build();
            app.MapPost("/things", async (HttpRequest req) => { Svc.Create(await JsonBody.ReadAsync(req)); return Results.Ok(); });
            app.Run();
            """,
            "App.cs": REQUIRED_APP,
        },
    )
    doc = dncheck.extract_json(root)
    params = {p["name"]: p for p in routes(doc)[("POST", "/things")]["params"]}
    required = {k for k, p in params.items() if p.get("required")}
    # refused when missing: a helper that throws on null, a null/empty check, a default the check rejects, a `!Has` guard, `Required*`
    assert {"version", "name", "token", "city", "items"} <= required
    # not refused (the helper passes null, the default passes, the check is under `Has`, an item's own field)
    assert {"note", "tag", "lang", "nick", "sku"} <= set(params)
    assert not required & {"note", "tag", "lang", "nick", "sku"}
