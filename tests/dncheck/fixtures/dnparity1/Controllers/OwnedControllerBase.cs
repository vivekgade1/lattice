using DnParity1.Auth;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace DnParity1.Controllers;

[ApiController]
[Authorize]
[Route("api/v2/[controller]")]
public abstract class OwnedControllerBase : ControllerBase
{
    protected int UserId => User.GetUserId();
}
