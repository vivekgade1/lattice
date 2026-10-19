using Microsoft.AspNetCore.Mvc;

namespace DnParity1.Controllers;

public class LegacyController : ControllerBase
{
    public IActionResult Ping() => Ok("pong");
}
