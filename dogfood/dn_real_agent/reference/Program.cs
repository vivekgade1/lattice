// Reference solution of the dnbuild fixture (calibration only: the real-agent run never sees this file).
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.EntityFrameworkCore;

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
builder.Services.AddDbContext<ShelfDb>(o => o.UseSqlite("Data Source=" + path + ";Default Timeout=10"));
var app = builder.Build();

IResult Detail(int status, string message) => Results.Json(new Dictionary<string, string> { ["detail"] = message }, statusCode: status);

app.MapGet("/health", () => Results.Json(new Dictionary<string, bool> { ["ok"] = true }));

app.MapPost("/items", async (HttpRequest request, ShelfDb db) =>
{
    JsonElement body;
    try { body = (await JsonDocument.ParseAsync(request.Body)).RootElement; }
    catch (JsonException) { return Detail(422, "bad body"); }
    if (body.ValueKind != JsonValueKind.Object) return Detail(422, "bad body");
    if (!body.TryGetProperty("name", out var n) || n.ValueKind != JsonValueKind.String || string.IsNullOrEmpty(n.GetString())) return Detail(422, "name is required");
    if (!body.TryGetProperty("qty", out var q) || q.ValueKind != JsonValueKind.Number || !q.TryGetInt64(out var qty) || qty < 0) return Detail(422, "qty must be a non-negative integer");
    var name = n.GetString()!;
    if (await db.Items.AnyAsync(i => i.Name == name)) return Detail(409, "name already used");
    var item = new Item { Name = name, Qty = qty };
    db.Items.Add(item);
    try { await db.SaveChangesAsync(); }
    catch (DbUpdateException) { return Detail(409, "name already used"); }
    return Results.Json(item, statusCode: 201);
});

app.MapGet("/items/{id}", async (string id, ShelfDb db) =>
{
    if (!long.TryParse(id, out var key)) return Detail(422, "bad id");
    var item = await db.Items.AsNoTracking().FirstOrDefaultAsync(i => i.Id == key);
    return item is null ? Detail(404, "Not found") : Results.Json(item);
});

app.MapGet("/items", async (ShelfDb db) =>
    Results.Json(new Dictionary<string, object> { ["items"] = await db.Items.AsNoTracking().OrderBy(i => i.Id).ToListAsync() }));

app.MapDelete("/items/{id}", async (string id, ShelfDb db) =>
{
    if (!long.TryParse(id, out var key)) return Detail(422, "bad id");
    var item = await db.Items.FirstOrDefaultAsync(i => i.Id == key);
    if (item is null) return Detail(404, "Not found");
    db.Items.Remove(item);
    await db.SaveChangesAsync();
    return Results.Json(new Dictionary<string, long> { ["id"] = key });
});

app.Run();
return 0;

class ShelfDb(DbContextOptions<ShelfDb> options) : DbContext(options)
{
    public DbSet<Item> Items => Set<Item>();
    protected override void OnModelCreating(ModelBuilder b) => b.Entity<Item>().ToTable("items");
}

class Item
{
    [JsonPropertyName("id")] public long Id { get; set; }
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("qty")] public long Qty { get; set; }
}
