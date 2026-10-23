namespace DnFindAct.Models;

public class User
{
    public int Id { get; set; }
    public string Email { get; set; } = "";
}

public class Doc
{
    public int Id { get; set; }
    public int OwnerId { get; set; }
    public string Title { get; set; } = "";
    public string Status { get; set; } = "draft";
    public string Secret { get; set; } = "";
}
