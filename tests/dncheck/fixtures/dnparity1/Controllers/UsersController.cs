using DnParity1.Data;
using Microsoft.AspNetCore.Mvc;

namespace DnParity1.Controllers;

public class UsersController : OwnedControllerBase
{
    private readonly AppDb _db;

    public UsersController(AppDb db)
    {
        _db = db;
    }

    [HttpGet("me")]
    public async Task<IActionResult> Me()
    {
        var me = await _db.Users.FindAsync(UserId);
        if (me is null)
        {
            return NotFound();
        }
        return Ok(me);
    }
}
