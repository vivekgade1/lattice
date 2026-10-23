using DnFindAct.Auth;
using DnFindAct.Models;
using DnFindAct.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace DnFindAct.Controllers;

[ApiController]
[Authorize]
[Route("docs")]
public sealed class DocsController(DocService docs) : ControllerBase
{
    [HttpGet]
    public async Task<ActionResult> List() => Ok(await docs.ListMine(User.GetUserId()));

    [HttpGet("{id:int}")]
    public async Task<ActionResult> Get(int id) => Ok(await docs.GetMine(User.GetUserId(), id));

    [HttpGet("{id:int}/raw")]
    public async Task<ActionResult> Raw(int id) => Ok(await docs.GetRaw(id));

    [HttpDelete("{id:int}")]
    public async Task<ActionResult> Delete(int id)
    {
        await docs.Remove(User.GetUserId(), id);
        return NoContent();
    }

    [HttpPost("discard")]
    public async Task<ActionResult> Discard(Doc doc)
    {
        await docs.RemoveBody(doc);
        return NoContent();
    }

    [HttpPost("{id:int}/touch")]
    public async Task<ActionResult> Touch(int id)
    {
        await docs.Touch(User.GetUserId(), id);
        return NoContent();
    }

    [HttpPost("{id:int}/touch-inline")]
    public async Task<ActionResult> TouchInline(int id)
    {
        await docs.TouchInline(User.GetUserId(), id);
        return NoContent();
    }

    [HttpPost("{id:int}/touch-other/{otherId:int}")]
    public async Task<ActionResult> TouchOther(int id, int otherId)
    {
        await docs.TouchOther(User.GetUserId(), id, otherId);
        return NoContent();
    }

    [HttpPost("{id:int}/touch-unchecked")]
    public async Task<ActionResult> TouchUnchecked(int id)
    {
        await docs.TouchUnchecked(User.GetUserId(), id);
        return NoContent();
    }

    [HttpPost]
    public async Task<ActionResult> Create(CreateDocRequest request) =>
        StatusCode(StatusCodes.Status201Created, await docs.Create(User.GetUserId(), request));

    [HttpPost("for")]
    public async Task<ActionResult> CreateFor(CreateDocRequest request) =>
        Ok(await docs.CreateFor(User.GetUserId(), request));

    [HttpGet("{id:int}/view")]
    public async Task<ActionResult> View(int id) => StatusCode(StatusCodes.Status200OK, await docs.View(User.GetUserId(), id));

    [HttpGet("{id:int}/leak")]
    public async Task<ActionResult> Leak(int id) => StatusCode(StatusCodes.Status200OK, await docs.Leak(User.GetUserId(), id));
}
