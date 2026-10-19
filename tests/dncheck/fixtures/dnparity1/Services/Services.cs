using DnParity1.Models;

namespace DnParity1.Services;

public interface IClock
{
    DateTime Now();
}

public class SystemClock : IClock
{
    public DateTime Now() => DateTime.UtcNow;
}

public interface ICurrentUser
{
    int UserId { get; }
}

public class CurrentUser : ICurrentUser
{
    private readonly IHttpContextAccessor _http;

    public CurrentUser(IHttpContextAccessor http)
    {
        _http = http;
    }

    public int UserId => int.Parse(_http.HttpContext!.User.FindFirst("sub")!.Value);
}

public interface INoteNotifier
{
    Task NotifyAsync(Note note);
}

public class EmailNotifier : INoteNotifier
{
    public Task NotifyAsync(Note note) => Task.CompletedTask;
}

public class SmsNotifier : INoteNotifier
{
    public Task NotifyAsync(Note note) => Task.FromResult(note.Id);
}

public class PingService
{
}
