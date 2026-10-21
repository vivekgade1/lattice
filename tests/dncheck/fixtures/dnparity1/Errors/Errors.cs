using Microsoft.AspNetCore.Diagnostics;

namespace DnParity1.Errors;

public class NotFoundException : Exception
{
    public NotFoundException(string what) : base(what)
    {
    }
}

public class ConflictException : Exception
{
    public ConflictException(string what) : base(what)
    {
    }
}

public class DomainExceptionHandler : IExceptionHandler
{
    public async ValueTask<bool> TryHandleAsync(HttpContext context, Exception exception, CancellationToken cancellationToken)
    {
        int? status = exception switch
        {
            NotFoundException => StatusCodes.Status404NotFound,
            ConflictException => StatusCodes.Status409Conflict,
            _ => null,
        };
        if (status is null)
        {
            return false;
        }
        context.Response.StatusCode = status.Value;
        await context.Response.WriteAsJsonAsync(new { message = exception.Message }, cancellationToken);
        return true;
    }
}
