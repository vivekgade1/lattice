"""Gaps in what the rules import needs from the extract on an unfamiliar app (node F1, found on the
held-out service H1 and the dev service D1): each is a small synthetic project, only read by the
helper, never restored or built."""

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


def project(tmp: Path, files: dict[str, str], name: str = "app") -> Path:
    root = tmp / name
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.csproj").write_text(WEB)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))
    return root


def routes(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(r["method"], r["path"]): r for r in doc["routes"]}


LOGIN_APP = {
    "Program.cs": """
        var builder = WebApplication.CreateBuilder(args);
        builder.Services.AddControllers();
        builder.Services.AddScoped<AuthService>();
        var app = builder.Build();
        app.MapControllers();
        app.Run();
        """,
    "Data.cs": """
        using Microsoft.EntityFrameworkCore;
        public class Db : DbContext { public Db(DbContextOptions<Db> o) : base(o) {} public DbSet<User> Users { get; set; } = null!; }
        public class User { public int Id { get; set; } public string Email { get; set; } = ""; public string Password { get; set; } = ""; }
        """,
    "Auth.cs": """
        using System.ComponentModel.DataAnnotations;
        using System.Security.Cryptography;
        using System.Text;
        using System.Text.Json.Serialization;
        using Microsoft.AspNetCore.Authorization;
        using Microsoft.AspNetCore.Mvc;
        using Microsoft.EntityFrameworkCore;
        [ApiController, Route("auth")]
        public sealed class AuthController(AuthService auth) : ControllerBase
        {
            [AllowAnonymous, HttpPost("login")]
            public async Task<ActionResult<LoginResponse>> Login(LoginRequest request, CancellationToken ct) => Ok(await auth.Login(request, ct));
        }
        public sealed class LoginRequest
        {
            [Required, StringLength(254, MinimumLength = 3)] public string? Email { get; init; }
            [Required, StringLength(200, MinimumLength = 1)] public string? Password { get; init; }
        }
        public sealed record LoginResponse([property: JsonPropertyName("access_token")] string AccessToken, string TokenType);
        public sealed class AuthService(Db db)
        {
            public async Task<LoginResponse> Login(LoginRequest request, CancellationToken ct)
            {
                var email = request.Email!.Trim();
                var user = await db.Users.AsNoTracking().SingleOrDefaultAsync(u => u.Email == email, ct);
                if (user is null || !SamePassword(user.Password, request.Password!)) throw new UnauthorizedAccessException("no");
                return new LoginResponse("t" + user.Id, "Bearer");
            }
            private static bool SamePassword(string stored, string typed) =>
                CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(stored), Encoding.UTF8.GetBytes(typed));
        }
        """,
}


def test_login_route_is_found_when_a_project_helper_compares_the_stored_password(tmp_path: Path) -> None:
    """H1: the controller hands the DTO to a service, which passes ``user.Password`` and the typed
    password to a project helper (``SamePassword``) that compares them."""
    doc = dncheck.extract_json(project(tmp_path, LOGIN_APP))
    a = doc["auth"]
    assert (a["login_path"], a["users_table"], a["password_column"]) == ("/auth/login", "User", "Password")
    assert (a["username_field"], a["password_field"], a["token_field"]) == ("email", "password", "access_token")


ROLE_APP = {
    "Program.cs": """
        var builder = WebApplication.CreateBuilder(args);
        builder.Services.AddAuthorization(o => o.AddPolicy("reviewer", p => p.RequireRole("Admin")));
        var app = builder.Build();
        app.MapGet("/review", () => "x").RequireAuthorization("reviewer");
        app.Run();
        """,
    "Data.cs": """
        using Microsoft.EntityFrameworkCore;
        public enum UserRole { Member = 0, Admin = 1 }
        public enum Tier { Free, Paid }
        public class Db : DbContext
        {
            public Db(DbContextOptions<Db> o) : base(o) {}
            public DbSet<User> Users { get; set; } = null!;
            protected override void OnModelCreating(ModelBuilder b) => b.Entity<User>().Property(u => u.Role).HasConversion<string>();
        }
        public class User { public int Id { get; set; } public string Email { get; set; } = ""; public string Password { get; set; } = ""; public Tier Tier { get; set; } public UserRole Role { get; set; } }
        """,
}


def test_the_users_enum_column_naming_the_admin_role_is_the_role_column(tmp_path: Path) -> None:
    """H1: ``User.Role`` is an enum with an ``Admin`` member and the policy requires the ``Admin`` role: the seed
    must be able to make a user an admin."""
    doc = dncheck.extract_json(project(tmp_path, ROLE_APP))
    role = doc["dotnet"]["auth"]["role_column"]
    assert role == {
        "column": "Role",
        "members": [{"name": "Member", "value": 0}, {"name": "Admin", "value": 1}],
        "storage": "string",
    }


def test_no_role_column_without_an_admin_member_in_an_enum_of_the_users_table(tmp_path: Path) -> None:
    files = {**ROLE_APP, "Data.cs": ROLE_APP["Data.cs"].replace("Admin = 1", "Owner = 1")}
    doc = dncheck.extract_json(project(tmp_path, files))
    assert "role_column" not in doc["dotnet"]["auth"]


PATTERN_APP = {
    "Program.cs": """
        using System.ComponentModel.DataAnnotations;
        var builder = WebApplication.CreateBuilder(args);
        builder.Services.AddControllers();
        var app = builder.Build();
        app.MapControllers();
        app.MapPost("/min", (MinIn i) => "x");
        app.Run();
        public record MinIn([property: RegularExpression("^[a-z]+$")] string Code);
        """,
    "C.cs": """
        using System.ComponentModel.DataAnnotations;
        using Microsoft.AspNetCore.Mvc;
        [ApiController, Route("accounts")]
        public class AccountsController : ControllerBase
        {
            [HttpPost] public IActionResult Create(AccountIn body) => Ok();
        }
        public sealed class AccountIn
        {
            [Required, StringLength(34, MinimumLength = 6)]
            [RegularExpression("^[0-9A-Za-z]+$", ErrorMessage = "letters and digits only")]
            public string? Number { get; init; }
            [StringLength(10)] public string? Note { get; init; }
        }
        """,
}


def test_a_regular_expression_attribute_is_the_fields_pattern(tmp_path: Path) -> None:
    """H1: ``[RegularExpression]`` on a DTO property refuses the importer's invented text."""
    doc = dncheck.extract_json(project(tmp_path, PATTERN_APP))
    params = {p["name"]: p for p in routes(doc)[("POST", "/accounts")]["params"]}
    assert params["number"]["pattern"] == "^[0-9A-Za-z]+$" and "pattern" not in params["note"]
    fields = {f["name"]: f for m in doc["models"] if m["name"].endswith("AccountIn") for f in m["fields"]}
    assert fields["number"]["pattern"] == "^[0-9A-Za-z]+$"
