using System.Security.Claims;

namespace DnParity1.Auth;

public static class ClaimsExtensions
{
    public static int GetUserId(this ClaimsPrincipal user)
    {
        return int.Parse(user.FindFirst("sub")!.Value);
    }
}
