using System.Security.Claims;

namespace DnFindAct.Auth;

public static class ClaimsExtensions
{
    public static int GetUserId(this ClaimsPrincipal user) => int.Parse(user.FindFirst("sub")!.Value);
}
