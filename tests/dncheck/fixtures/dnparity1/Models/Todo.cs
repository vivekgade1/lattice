using System.ComponentModel.DataAnnotations;

namespace DnParity1.Models;

public class Todo
{
    public int Id { get; set; }

    public int OwnerId { get; set; }

    [MaxLength(200)]
    public string Text { get; set; } = "";

    public DateTime? DueAt { get; set; }

    public bool Done { get; set; }
}
