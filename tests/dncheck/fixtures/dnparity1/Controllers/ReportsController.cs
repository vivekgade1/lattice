using DnParity1.Services;
using Microsoft.AspNetCore.Mvc;

namespace DnParity1.Controllers;

public class ReportsController : OwnedControllerBase
{
    private readonly INoteStore _store;

    public ReportsController(INoteStore store)
    {
        _store = store;
    }

    [HttpGet("{id:int}")]
    public async Task<IActionResult> Get(int id)
    {
        var note = await _store.GetOwnedAsync(id, UserId);
        if (note is null)
        {
            return NotFound();
        }
        return Ok(new { note.Id, note.Title, note.Status });
    }

    [HttpGet("{id:int}/audit")]
    public async Task<IActionResult> Audit(int id)
    {
        var note = await _store.GetOwnedAsync(id, UserId);
        if (note is null)
        {
            return NotFound();
        }
        return Ok(new { note.Id, note.Title, note.ModerationNote });
    }
}
