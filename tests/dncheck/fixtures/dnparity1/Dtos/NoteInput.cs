using System.ComponentModel.DataAnnotations;

namespace DnParity1.Dtos;

public class NoteInput
{
    [Required]
    [StringLength(120, MinimumLength = 1)]
    public string Title { get; set; } = "";

    [MaxLength(2000)]
    public string? Body { get; set; }

    [Range(1, 5)]
    public int Priority { get; set; }

    public string Category { get; set; } = "";
}

public record TagView(int Id, string Label);
