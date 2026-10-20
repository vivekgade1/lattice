// A toy .NET service for lattice.spec.check's process runtime and layers R and B: the port of tests/spec/goapp
// (the Go S1 app), answering the same API on the same SQLite schema. ASP.NET Core minimal APIs and ADO.NET.
// Its schema is written by the harness's db factory; the service only reads and writes rows.
//
//	GET  /health                  200 once the database opens
//	POST /login                   {"username", "password"} -> {"access_token", "token_type"}
//	POST /configs/{cid}/runs      queue a run; at most one live (queued) run per config
//	POST /runs/{rid}/cancel       cancel a queued run
//	GET  /runs/{rid}              a run
//
// Environment: PORT, DB_PATH; MODE=safe (a lock around check-then-insert) or racy (a sleep between the check and
// the insert); DNAPP_BUG=nocheck (create skips the check), nocancel (cancel answers 200 but writes nothing),
// noauth (GET /runs/{rid} skips the caller) or forbidden (GET /runs/{rid} answers 403 for a missing run);
// STARTUP_FAIL=1 exits at once.
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Data.Sqlite;

if (!string.IsNullOrEmpty(Environment.GetEnvironmentVariable("STARTUP_FAIL")))
{
    Console.Error.WriteLine("dnapp: refusing to start: STARTUP_FAIL is set");
    return 3;
}

var port = Environment.GetEnvironmentVariable("PORT");
var path = Environment.GetEnvironmentVariable("DB_PATH");
if (string.IsNullOrEmpty(port) || string.IsNullOrEmpty(path))
{
    Console.Error.WriteLine("dnapp: PORT and DB_PATH are required");
    return 2;
}

var connectionString = new SqliteConnectionStringBuilder { DataSource = path, DefaultTimeout = 10 }.ToString();
try
{
    using var probe = new SqliteConnection(connectionString);
    probe.Open();
}
catch (SqliteException e)
{
    Console.Error.WriteLine("dnapp: cannot open the database: " + e.Message);
    return 1;
}

var mode = Environment.GetEnvironmentVariable("MODE") is { Length: > 0 } m ? m : "safe";
var bug = Environment.GetEnvironmentVariable("DNAPP_BUG") ?? "";
var gate = new object();
var wire = new JsonSerializerOptions { PropertyNamingPolicy = null };

var builder = WebApplication.CreateSlimBuilder();
builder.WebHost.UseUrls("http://127.0.0.1:" + port);
builder.Logging.ClearProviders();
var app = builder.Build();

IResult Json(int status, object body) => Results.Json(body, wire, statusCode: status);
IResult Detail(int status, string message) => Json(status, new Dictionary<string, string> { ["detail"] = message });
SqliteConnection Open() { var c = new SqliteConnection(connectionString); c.Open(); return c; }

object? Scalar(SqliteConnection c, string sql, params object[] args)
{
    using var cmd = c.CreateCommand();
    cmd.CommandText = sql;
    for (var i = 0; i < args.Length; i++) cmd.Parameters.AddWithValue("$" + i, args[i]);
    return cmd.ExecuteScalar() is { } v and not DBNull ? v : null;
}

long Exec(SqliteConnection c, string sql, params object[] args)
{
    using var cmd = c.CreateCommand();
    cmd.CommandText = sql + "; SELECT last_insert_rowid()";
    for (var i = 0; i < args.Length; i++) cmd.Parameters.AddWithValue("$" + i, args[i]);
    return (long)cmd.ExecuteScalar()!;
}

bool Caller(HttpRequest request, SqliteConnection c)
{
    var header = request.Headers.Authorization.ToString().Split(' ', 2);
    return header.Length == 2 && header[0].Equals("bearer", StringComparison.OrdinalIgnoreCase)
        && Scalar(c, "SELECT id FROM users WHERE token = $0", header[1]) != null;
}

bool Id(string text, out long id) => long.TryParse(text, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out id);

Run? ReadRun(SqliteConnection c, long id)
{
    using var cmd = c.CreateCommand();
    cmd.CommandText = "SELECT id, config_id, status FROM runs WHERE id = $0";
    cmd.Parameters.AddWithValue("$0", id);
    using var r = cmd.ExecuteReader();
    return r.Read() ? new Run(r.GetInt64(0), r.GetInt64(1), r.GetString(2)) : null;
}

app.MapGet("/health", () => Json(200, new Dictionary<string, bool> { ["ok"] = true }));

app.MapPost("/login", async (HttpRequest request) =>
{
    string username = "", password = "";
    try
    {
        using var doc = await JsonDocument.ParseAsync(request.Body);
        if (doc.RootElement.ValueKind != JsonValueKind.Object) return Detail(422, "bad body");
        foreach (var p in doc.RootElement.EnumerateObject())
        {
            var key = p.Name.ToLowerInvariant();
            if (key is not ("username" or "password") || p.Value.ValueKind == JsonValueKind.Null) continue;
            if (p.Value.ValueKind != JsonValueKind.String) return Detail(422, "bad body");
            if (key == "username") username = p.Value.GetString()!; else password = p.Value.GetString()!;
        }
    }
    catch (JsonException)
    {
        return Detail(422, "bad body");
    }

    using var c = Open();
    var token = Scalar(c, "SELECT token FROM users WHERE name = $0 AND password = $1", username, password) as string;
    return token == null
        ? Detail(401, "Incorrect username or password")
        : Json(200, new Dictionary<string, string> { ["access_token"] = token, ["token_type"] = "bearer" });
});

app.MapPost("/configs/{cid}/runs", (HttpRequest request, string cid) =>
{
    using var c = Open();
    if (!Caller(request, c)) return Detail(401, "Not authenticated");
    if (!Id(cid, out var configId)) return Detail(422, "bad id");
    if (Scalar(c, "SELECT 1 FROM configs WHERE id = $0", configId) == null) return Detail(404, "Not found");
    if (mode == "safe") Monitor.Enter(gate);
    try
    {
        if (bug != "nocheck" && (long)Scalar(c, "SELECT COUNT(*) FROM runs WHERE config_id = $0 AND status = 'queued'", configId)! > 0)
            return Detail(409, "already live");
        if (mode == "racy") Thread.Sleep(20); // widen the check-then-insert window
        var id = Exec(c, "INSERT INTO runs (config_id, status) VALUES ($0, 'queued')", configId);
        return ReadRun(c, id) is { } run ? Json(201, run) : Detail(500, "run not found");
    }
    finally
    {
        if (mode == "safe") Monitor.Exit(gate);
    }
});

app.MapPost("/runs/{rid}/cancel", (HttpRequest request, string rid) =>
{
    using var c = Open();
    if (!Caller(request, c)) return Detail(401, "Not authenticated");
    if (!Id(rid, out var runId)) return Detail(422, "bad id");
    if (ReadRun(c, runId) is not { } run) return Detail(404, "Not found");
    if (run.status != "queued") return Detail(409, "already final");
    if (bug != "nocancel") Exec(c, "UPDATE runs SET status = 'cancelled' WHERE id = $0 AND status = 'queued'", runId);
    run = ReadRun(c, runId)!;
    return Json(200, bug == "nocancel" ? run with { status = "cancelled" } : run); // nocancel: the answer lies, the row stays queued
});

app.MapGet("/runs/{rid}", (HttpRequest request, string rid) =>
{
    using var c = Open();
    if (bug != "noauth" && !Caller(request, c)) return Detail(401, "Not authenticated"); // noauth: the read skips the caller
    if (!Id(rid, out var runId)) return Detail(422, "bad id");
    return ReadRun(c, runId) is { } run ? Json(200, run) : bug == "forbidden" ? Detail(403, "Forbidden") : Detail(404, "Not found");
});

app.Run();
return 0;

record Run([property: JsonPropertyName("id")] long id, [property: JsonPropertyName("config_id")] long config_id, [property: JsonPropertyName("status")] string status);
