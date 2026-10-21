var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.MapGet("/v1/ping", () => Results.Ok(new { ok = true }));

app.Run();
