using DnParity1.Data;
using DnParity1.Models;
using Microsoft.EntityFrameworkCore;

namespace DnParity1.Services;

public interface INoteStore
{
    Task<Note?> GetOwnedAsync(int id, int ownerId);
}

public class EfNoteStore : INoteStore
{
    private readonly AppDb _db;

    public EfNoteStore(AppDb db)
    {
        _db = db;
    }

    public Task<Note?> GetOwnedAsync(int id, int ownerId)
    {
        return _db.Notes.FirstOrDefaultAsync(n => n.Id == id && n.OwnerId == ownerId);
    }
}
