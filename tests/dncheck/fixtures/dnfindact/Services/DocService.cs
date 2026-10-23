using DnFindAct.Data;
using DnFindAct.Models;
using Microsoft.EntityFrameworkCore;

namespace DnFindAct.Services;

public sealed record DocView(int Id, string Title);

public sealed record DocWithSecret(int Id, string Title, string Secret);

public sealed class CreateDocRequest
{
    public string Title { get; init; } = "";
    public int? OwnerId { get; init; }
}

public sealed class DocService(AppDb db)
{
    // ---- covered: the owner is compared in the query, or the row was loaded for the caller first ----

    public async Task<IReadOnlyList<DocView>> ListMine(int userId) =>
        await Shape(db.Docs.Where(d => d.OwnerId == userId)).ToListAsync();

    public async Task<DocView> GetMine(int userId, int id) =>
        await Shape(db.Docs.Where(d => d.Id == id && d.OwnerId == userId)).SingleOrDefaultAsync()
        ?? throw new KeyNotFoundException("doc");

    public async Task Remove(int userId, int id)
    {
        var doc = await FindOwned(userId, id);
        db.Docs.Remove(doc);
        await db.SaveChangesAsync();
    }

    public async Task Touch(int userId, int id)
    {
        await FindOwned(userId, id);
        await db.Docs.Where(d => d.Id == id).ExecuteUpdateAsync(s => s.SetProperty(d => d.Status, "touched"));
    }

    public async Task TouchInline(int userId, int id)
    {
        var doc = await db.Docs.FirstOrDefaultAsync(d => d.Id == id && d.OwnerId == userId);
        if (doc == null) throw new KeyNotFoundException("doc");
        await db.Docs.Where(d => d.Id == id).ExecuteUpdateAsync(s => s.SetProperty(d => d.Status, "touched"));
    }

    public async Task<DocView> Create(int userId, CreateDocRequest request)
    {
        var doc = new Doc { OwnerId = userId, Title = request.Title };
        db.Docs.Add(doc);
        await db.SaveChangesAsync();
        return await Summary(doc.Id);
    }

    public async Task<DocView> View(int userId, int id)
    {
        var doc = await FindOwned(userId, id);
        return new DocView(doc.Id, doc.Title);
    }

    // ---- not covered ----

    public async Task<DocView?> GetRaw(int id) =>
        await Shape(db.Docs.Where(d => d.Id == id)).SingleOrDefaultAsync();

    public async Task RemoveBody(Doc doc)
    {
        db.Docs.Remove(doc);
        await db.SaveChangesAsync();
    }

    public async Task TouchOther(int userId, int id, int otherId)
    {
        await FindOwned(userId, id);
        await db.Docs.Where(d => d.Id == otherId).ExecuteUpdateAsync(s => s.SetProperty(d => d.Status, "touched"));
    }

    public async Task TouchUnchecked(int userId, int id)
    {
        var doc = await db.Docs.FirstOrDefaultAsync(d => d.Id == id && d.OwnerId == userId);
        await db.Docs.Where(d => d.Id == id).ExecuteUpdateAsync(s => s.SetProperty(d => d.Status, "touched"));
    }

    public async Task<DocView> CreateFor(int userId, CreateDocRequest request)
    {
        var doc = new Doc { OwnerId = request.OwnerId ?? userId, Title = request.Title };
        db.Docs.Add(doc);
        await db.SaveChangesAsync();
        return new DocView(doc.Id, doc.Title);
    }

    public async Task<DocWithSecret> Leak(int userId, int id)
    {
        var doc = await FindOwned(userId, id);
        return new DocWithSecret(doc.Id, doc.Title, doc.Secret);
    }

    // ---- shared ----

    private static IQueryable<DocView> Shape(IQueryable<Doc> docs) => docs.AsNoTracking().Select(d => new DocView(d.Id, d.Title));

    private Task<DocView> Summary(int id) => Shape(db.Docs.Where(d => d.Id == id)).SingleAsync();

    private async Task<Doc> FindOwned(int userId, int id) =>
        await db.Docs.SingleOrDefaultAsync(d => d.Id == id && d.OwnerId == userId) ?? throw new KeyNotFoundException("doc");
}
