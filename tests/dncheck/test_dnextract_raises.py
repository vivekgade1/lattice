"""Refusal recall (node F2): a refusal raised in a service method the handler reaches through DI, as a typed exception
whose status is carried by its constructor chain and turned into a response by an exception handler that reads that
status (IExceptionHandler, UseExceptionHandler lambda or middleware). Synthetic projects, only read by the helper.

Precision is the other half: a thrown type the app never maps, or a status that is not constant, stays ``unread``.
"""

# ruff: noqa: E501
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from lattice import dncheck
from test_dnextract_idioms import DB, helper, project  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("dotnet") is None, reason="needs the .NET SDK")

ERRORS = """
public abstract class ApiException(int status, string title, string detail) : Exception(detail)
{
    public int Status { get; } = status;
    public string Title { get; } = title;
}
public sealed class MissingException(string detail) : ApiException(StatusCodes.Status404NotFound, "Not Found", detail);
public sealed class ClashException : ApiException
{
    public ClashException(string detail) : base(409, "Conflict", detail) { }
}
public sealed class OddException(int code, string detail) : ApiException(code, "Odd", detail);
public sealed class Unmapped : Exception { }
"""

HANDLER = """
using Microsoft.AspNetCore.Diagnostics;
public sealed class ApiExceptionHandler(IProblemDetailsService problems) : IExceptionHandler
{
    public async ValueTask<bool> TryHandleAsync(HttpContext http, Exception error, CancellationToken ct)
    {
        if (error is not ApiException api) return false;
        http.Response.StatusCode = api.Status;
        return await problems.TryWriteAsync(new ProblemDetailsContext { HttpContext = http, Exception = error });
    }
}
"""

SERVICE = """
public sealed class ItemService(Db db)
{
    public Item Get(int owner, int id)
    {
        var item = db.Items.SingleOrDefault(i => i.Id == id && i.OwnerId == owner) ?? throw NotFound(id);
        return item;
    }
    public void Rename(int owner, int id, string name)
    {
        var item = Get(owner, id);
        if (item.Name == name) throw new ClashException("same name");
        item.Name = name;
        db.SaveChanges();
    }
    public void Odd(int code) { throw new OddException(code, "x"); }
    public void Raw() { throw new Unmapped(); }
    internal static MissingException NotFound(int id) => new($"item {id} not found");
}
"""

CONTROLLER = """
using Microsoft.AspNetCore.Mvc;
[ApiController, Route("items")]
public sealed class ItemsController(ItemService items) : ControllerBase
{
    [HttpGet("{id:int}")] public IActionResult Get(int id) => Ok(items.Get(1, id));
    [HttpPatch("{id:int}")] public IActionResult Rename(int id, string name) { items.Rename(1, id, name); return NoContent(); }
    [HttpGet("odd")] public IActionResult Odd(int code) { items.Odd(code); return Ok(); }
    [HttpGet("raw")] public IActionResult Raw() { items.Raw(); return Ok(); }
}
"""


def program(handler: str) -> str:
    return f"""
    var builder = WebApplication.CreateBuilder(args);
    builder.Services.AddControllers();
    builder.Services.AddScoped<ItemService>();
    {handler}
    var app = builder.Build();
    app.UseExceptionHandler();
    app.MapControllers();
    app.Run();
    """


def refusals(doc: dict[str, Any]) -> dict[str, set[int]]:
    return {f"{r['method']} {r['path']}": {x["status"] for x in r["raises"] if x["status"] in (404, 409)} for r in doc["routes"]}


def build(tmp_path: Path, handler: str, files: dict[str, str] | None = None) -> dict[str, Any]:
    base = {
        "Program.cs": program(handler),
        "Db.cs": DB,
        "Errors.cs": ERRORS,
        "Service.cs": SERVICE,
        "Controller.cs": CONTROLLER,
    }
    return dncheck.extract_json(project(tmp_path, {**base, **(files or {})}))


def test_status_carried_by_the_constructor_chain_and_read_by_an_exception_handler(tmp_path: Path) -> None:
    doc = build(tmp_path, "builder.Services.AddExceptionHandler<ApiExceptionHandler>();", {"Handler.cs": HANDLER})
    got = refusals(doc)
    assert got["GET /items/{id:int}"] == {404}  # service method, factory returning the exception, primary-constructor base call
    assert got["PATCH /items/{id:int}"] == {404, 409}  # a helper two calls deep, and a `: base(409, ...)` initializer
    raised = [x for r in doc["routes"] if r["path"] == "/items/{id:int}" and r["method"] == "PATCH" for x in r["raises"] if x["status"] in (404, 409)]
    assert all(x["kind"] == "exception" and x["file"] == "Service.cs" for x in raised)
    assert {x["sentinel"] for x in raised} == {"MissingException", "ClashException"}


def test_a_status_that_is_not_a_constant_and_an_unmapped_type_stay_unread(tmp_path: Path) -> None:
    doc = build(tmp_path, "builder.Services.AddExceptionHandler<ApiExceptionHandler>();", {"Handler.cs": HANDLER})
    got = refusals(doc)
    assert got["GET /items/odd"] == set()  # `new OddException(code, ..)` with a caller-supplied status
    assert got["GET /items/raw"] == set()  # `new Unmapped()`: no status anywhere
    whats = {u["what"] for u in doc["unread"]}
    assert "throw new OddException" in whats and "throw new Unmapped" in whats


def test_no_handler_that_reads_the_status_means_no_claim(tmp_path: Path) -> None:
    # without the handler the exception ends as a 500; claiming 404/409 would be a refusal the app never raises
    doc = build(tmp_path, "")
    assert all(not v for v in refusals(doc).values())
    assert any(u["what"].startswith("throw new ClashException") for u in doc["unread"])


def test_a_catch_in_middleware_that_writes_the_carried_status(tmp_path: Path) -> None:
    mw = """
    public sealed class ErrorMiddleware(RequestDelegate next)
    {
        public async Task InvokeAsync(HttpContext ctx)
        {
            try { await next(ctx); }
            catch (ApiException e) { ctx.Response.StatusCode = e.Status; }
        }
    }
    """
    doc = build(tmp_path, "", {"Mw.cs": mw})
    got = refusals(doc)
    assert got["GET /items/{id:int}"] == {404}
    assert got["PATCH /items/{id:int}"] == {404, 409}
