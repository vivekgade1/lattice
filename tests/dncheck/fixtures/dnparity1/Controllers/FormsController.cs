using DnParity1.Errors;
using Microsoft.AspNetCore.Mvc;

namespace DnParity1.Controllers;

public class FormsController : OwnedControllerBase
{
    [HttpGet("bad/{id:int}")]
    public IActionResult Bad(int id)
    {
        if (id <= 0)
        {
            return BadRequest(new { code = "bad_id", message = "id must be positive" });
        }
        return Ok(new { id });
    }

    [HttpGet("unauthorized")]
    public IActionResult Unauth() => Unauthorized();

    [HttpGet("forbidden")]
    public IActionResult Denied() => Forbid();

    [HttpGet("unprocessable")]
    public IActionResult Semantic() => UnprocessableEntity(new { error = "semantic" });

    [HttpGet("teapot")]
    public IActionResult Teapot() => StatusCode(418);

    [HttpGet("gone")]
    public IActionResult Gone() => Problem(statusCode: 410, detail: "gone");

    [HttpPost("accepted")]
    public IActionResult Queue() => Accepted();

    [HttpPost("created")]
    public IActionResult Make() => Created("/api/v2/Forms/created", null);

    [HttpDelete("nothing")]
    public IActionResult Nothing() => NoContent();

    [HttpGet("invalid")]
    public IActionResult Invalid() => ValidationProblem();

    [HttpGet("missing/{id:int}")]
    public IActionResult Missing(int id) => throw new NotFoundException("note " + id);

    [HttpGet("clash")]
    public IActionResult Clash() => throw new ConflictException("clash");

    [HttpGet("explode")]
    public IActionResult Explode() => throw new InvalidOperationException("boom");
}
