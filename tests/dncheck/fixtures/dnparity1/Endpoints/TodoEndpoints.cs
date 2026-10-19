using System.Security.Claims;
using DnParity1.Auth;
using DnParity1.Data;
using Microsoft.EntityFrameworkCore;

namespace DnParity1.Endpoints;

public static class TodoEndpoints
{
    public static RouteGroupBuilder MapTodos(this RouteGroupBuilder g)
    {
        var todos = g.MapGroup("/todos");

        todos.MapGet("/open", async (AppDb db) => Results.Ok(await db.Todos.ToListAsync()));

        todos.MapGet("/all", async (AppDb db) => Results.Ok(await db.Todos.IgnoreQueryFilters().ToListAsync()));

        todos.MapGet("/{id:int}", async (int id, ClaimsPrincipal user, AppDb db) =>
        {
            var todo = await db.Todos.FirstOrDefaultAsync(t => t.Id == id && t.OwnerId == user.GetUserId());
            return todo is null ? Results.NotFound() : Results.Ok(todo);
        });

        todos.MapGet("/overdue", async (AppDb db) =>
        {
            var skew = (DateTime.Now - DateTime.UtcNow).TotalMinutes;
            var offsetSkew = (DateTimeOffset.UtcNow - DateTimeOffset.Now).TotalMinutes;
            var open = await db.Todos.Where(t => !t.Done && t.DueAt < DateTime.UtcNow).ToListAsync();
            return Results.Ok(new { skew, offsetSkew, open });
        });

        return g;
    }
}
