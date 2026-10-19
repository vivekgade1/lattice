using System.Security.Claims;
using DnParity1.Auth;
using DnParity1.Services;

namespace DnParity1.Endpoints;

public static class NoteHandlers
{
    public static async Task<IResult> Summary(int id, ClaimsPrincipal user, INoteStore store)
    {
        var note = await store.GetOwnedAsync(id, user.GetUserId());
        if (note is null)
        {
            return Results.NotFound();
        }
        return Results.Ok(new { note.Id, note.Title, note.Status });
    }
}
