// dnbuild: the Shelf HTTP service (scaffold: the service starts and answers /health; the /items routes are not built yet).
var port = Environment.GetEnvironmentVariable("PORT");
var path = Environment.GetEnvironmentVariable("DB_PATH");
if (string.IsNullOrEmpty(port) || string.IsNullOrEmpty(path))
{
    Console.Error.WriteLine("dnbuild: PORT and DB_PATH are required");
    return 2;
}

var builder = WebApplication.CreateSlimBuilder();
builder.WebHost.UseUrls("http://127.0.0.1:" + port);
builder.Logging.ClearProviders();
var app = builder.Build();

app.MapGet("/health", () => Results.Json(new Dictionary<string, bool> { ["ok"] = true }));

app.Run();
return 0;
