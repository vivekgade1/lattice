using System.Security.Claims;
using System.Text.Json;
using DnParity1.Auth;
using DnParity1.Data;
using DnParity1.Dtos;
using DnParity1.Endpoints;
using DnParity1.Errors;
using DnParity1.Hubs;
using DnParity1.Models;
using DnParity1.Services;
using FluentValidation;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddDbContext<AppDb>(o => o.UseMySql(builder.Configuration.GetConnectionString("Default"), new MySqlServerVersion(new Version(8, 4, 0))));
builder.Services.AddHttpContextAccessor();
builder.Services.AddScoped<ICurrentUser, CurrentUser>();
builder.Services.AddScoped<IClock, SystemClock>();
builder.Services.AddScoped<INoteStore, EfNoteStore>();
builder.Services.AddScoped<INoteNotifier, EmailNotifier>();
builder.Services.AddScoped<INoteNotifier, SmsNotifier>();
builder.Services.AddScoped<IValidator<NoteInput>, NoteInputValidator>();

builder.Services.ConfigureHttpJsonOptions(o => o.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower);
builder.Services.AddControllers().AddJsonOptions(o => o.JsonSerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower);
builder.Services.AddAuthentication("Bearer").AddJwtBearer();
builder.Services.AddAuthorization();
builder.Services.AddExceptionHandler<DomainExceptionHandler>();
builder.Services.AddProblemDetails();
builder.Services.AddHealthChecks();
builder.Services.AddGrpc();
builder.Services.AddSignalR();

var app = builder.Build();

app.UseExceptionHandler();
app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();
app.MapControllerRoute(name: "legacy", pattern: "legacy/{action=Ping}/{id?}", defaults: new { controller = "Legacy" });

app.MapHealthChecks("/health");
app.MapGrpcService<PingService>();
app.MapHub<NoteHub>("/hubs/notes");

if (app.Environment.IsDevelopment())
{
    app.MapGet("/dev/ping", () => Results.Ok("pong"));
}

var v1 = app.MapGroup("/v1").RequireAuthorization();
var admin = v1.MapGroup("/admin").RequireAuthorization(p => p.RequireRole("Admin"));

admin.MapGet("/notes", async (AppDb db) => Results.Ok(await db.Notes.ToListAsync()));

v1.MapTodos();

v1.MapGet("/version", () => Results.Ok(new { version = "1" })).AllowAnonymous();

var statusPath = builder.Configuration["StatusPath"] ?? "/status";
v1.MapGet(statusPath, () => Results.Ok(new { ok = true }));

v1.MapGet("/notes/{id:int}/summary", NoteHandlers.Summary);

v1.MapMethods("/notes/{id:int}", new[] { "PATCH" }, async (int id, ClaimsPrincipal user, AppDb db) =>
{
    var uid = user.GetUserId();
    var rows = await db.Notes
        .Where(n => n.Id == id && n.OwnerId == uid && n.Status == NoteStatus.Published)
        .ExecuteUpdateAsync(s => s.SetProperty(n => n.Status, NoteStatus.Archived));
    return rows == 0 ? Results.NotFound() : TypedResults.NoContent();
});

v1.MapPost("/notes/quick", async (NoteInput input, ClaimsPrincipal user, AppDb db) =>
{
    var note = new Note { OwnerId = user.GetUserId(), Title = input.Title, Priority = 3 };
    db.Notes.Add(note);
    await db.SaveChangesAsync();
    return TypedResults.Created($"/api/Notes/{note.Id}", new { note.Id });
});

v1.MapPost("/notes/validated", async (NoteInput input, IValidator<NoteInput> validator, ClaimsPrincipal user, AppDb db) =>
{
    var result = await validator.ValidateAsync(input);
    if (!result.IsValid)
    {
        return Results.ValidationProblem(result.ToDictionary());
    }
    var note = new Note { OwnerId = user.GetUserId(), Title = input.Title, Priority = input.Priority };
    db.Notes.Add(note);
    await db.SaveChangesAsync();
    return Results.Created($"/api/Notes/{note.Id}", new { note.Id });
});

v1.MapPost("/notes/{id:int}/split", async (int id, ClaimsPrincipal user, AppDb db) =>
{
    var uid = user.GetUserId();
    var src = await db.Notes.FirstOrDefaultAsync(n => n.Id == id && n.OwnerId == uid);
    if (src is null)
    {
        return Results.NotFound();
    }
    db.Notes.Add(new Note { OwnerId = uid, Title = src.Title + " (a)", Priority = src.Priority });
    await db.SaveChangesAsync();
    if (src.Body is null)
    {
        return Results.BadRequest();
    }
    db.Notes.Add(new Note { OwnerId = uid, Title = src.Title + " (b)", Body = src.Body, Priority = src.Priority });
    await db.SaveChangesAsync();
    return Results.Ok();
});

v1.MapPost("/notes/{id:int}/split-safe", async (int id, ClaimsPrincipal user, AppDb db) =>
{
    var uid = user.GetUserId();
    var src = await db.Notes.FirstOrDefaultAsync(n => n.Id == id && n.OwnerId == uid);
    if (src is null)
    {
        return Results.NotFound();
    }
    await using var tx = await db.Database.BeginTransactionAsync();
    db.Notes.Add(new Note { OwnerId = uid, Title = src.Title + " (a)", Priority = src.Priority });
    await db.SaveChangesAsync();
    if (src.Body is null)
    {
        return Results.BadRequest();
    }
    db.Notes.Add(new Note { OwnerId = uid, Title = src.Title + " (b)", Body = src.Body, Priority = src.Priority });
    await db.SaveChangesAsync();
    await tx.CommitAsync();
    return Results.Ok();
});

v1.MapGet("/forms/{kind}", (string kind) => kind switch
{
    "unauthorized" => Results.Unauthorized(),
    "unprocessable" => TypedResults.UnprocessableEntity(),
    "teapot" => Results.StatusCode(418),
    "quota" => Results.Problem(statusCode: 429, detail: "quota exceeded"),
    "conflict" => TypedResults.Conflict(),
    _ => Results.NotFound(),
});

app.Run();
