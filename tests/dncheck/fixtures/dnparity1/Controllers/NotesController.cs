using DnParity1.Auth;
using DnParity1.Data;
using DnParity1.Dtos;
using DnParity1.Models;
using DnParity1.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace DnParity1.Controllers;

[ApiController]
[Route("api/[controller]")]
[Authorize]
public class NotesController : ControllerBase
{
    private readonly AppDb _db;
    private readonly IClock _clock;
    private readonly INoteNotifier _notifier;
    private readonly ILogger<NotesController> _log;

    public NotesController(AppDb db, IClock clock, INoteNotifier notifier, ILogger<NotesController> log)
    {
        _db = db;
        _clock = clock;
        _notifier = notifier;
        _log = log;
    }

    private int UserId => User.GetUserId();

    [HttpGet]
    public async Task<IActionResult> List()
    {
        var notes = await _db.Notes.Where(n => n.OwnerId == UserId).ToListAsync();
        return Ok(notes);
    }

    [HttpGet("{id:int}")]
    public async Task<IActionResult> Get(int id)
    {
        var note = await _db.Notes.FirstOrDefaultAsync(n => n.Id == id && n.OwnerId == UserId);
        if (note is null)
        {
            return NotFound();
        }
        return Ok(note);
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] NoteInput input)
    {
        _log.LogInformation("note created in {Category}", input.Category);
        var note = new Note
        {
            OwnerId = UserId,
            Title = input.Title,
            Body = input.Body,
            Priority = input.Priority,
            Status = NoteStatus.Draft,
            CreatedAt = _clock.Now(),
        };
        _db.Notes.Add(note);
        await _db.SaveChangesAsync();
        return CreatedAtAction(nameof(Get), new { id = note.Id }, note);
    }

    [HttpPut("{id:int}")]
    public async Task<IActionResult> Update(int id, [FromBody] NoteInput input)
    {
        var row = await _db.Notes.FindAsync(id);
        if (row is null || row.OwnerId != UserId)
        {
            return NotFound();
        }
        row.Title = input.Title;
        row.Body = input.Body;
        row.Priority = input.Priority;
        await _db.SaveChangesAsync();
        return Ok(row);
    }

    [HttpDelete("{id:int}")]
    public async Task<IActionResult> Delete(int id)
    {
        var row = await _db.Notes.FindAsync(id);
        if (row is null)
        {
            return NotFound();
        }
        _db.Notes.Remove(row);
        await _db.SaveChangesAsync();
        return NoContent();
    }

    [HttpPost("{id:int}/publish")]
    public async Task<IActionResult> Publish(int id)
    {
        var note = await _db.Notes.FirstOrDefaultAsync(n => n.Id == id && n.OwnerId == UserId);
        if (note is null)
        {
            return NotFound();
        }
        if (note.Status != NoteStatus.Draft)
        {
            return Conflict();
        }
        note.Status = NoteStatus.Published;
        await _db.SaveChangesAsync();
        await _notifier.NotifyAsync(note);
        return Ok(note);
    }

    [HttpPost("{id:int}/restore")]
    public async Task<IActionResult> Restore(int id)
    {
        var note = await _db.Notes.FirstOrDefaultAsync(n => n.Id == id && n.OwnerId == UserId);
        if (note is null)
        {
            return NotFound();
        }
        if (note.Status != NoteStatus.Archived)
        {
            return Conflict();
        }
        note.Status = NoteStatus.Draft;
        await _db.SaveChangesAsync();
        return Ok(note);
    }

    [AllowAnonymous]
    [HttpGet("public/{id:int}")]
    public async Task<IActionResult> PublicView(int id)
    {
        var view = await _db.Notes
            .Where(n => n.Id == id && n.Status == NoteStatus.Published)
            .Select(n => new { n.Id, n.Title })
            .FirstOrDefaultAsync();
        return view is null ? NotFound() : Ok(view);
    }

    [HttpGet("{id:int}/tags")]
    public async Task<IActionResult> Tags(int id)
    {
        var tags = await _db.Tags
            .Where(t => t.NoteId == id && t.Note!.OwnerId == UserId)
            .Select(t => new TagView(t.Id, t.Label))
            .ToListAsync();
        return Ok(tags);
    }
}
